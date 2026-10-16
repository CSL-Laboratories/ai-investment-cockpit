//! PyO3 entry points for the raw/normalized market-event adapter.

use std::collections::HashSet;
use std::ffi::CString;

use cockpit_ingest::{
    content_hash, escape_untrusted, market_event, source_allowlisted, Json, MAX_LEN, MAX_SYMBOLS,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::sync::GILOnceCell;
use pyo3::types::{
    PyBool, PyDict, PyFloat, PyInt, PyList, PyModule, PySequence, PyString, PyTuple, PyType,
};
use pyo3::PyTypeInfo;

fn invalid(message: impl Into<String>) -> PyErr {
    PyValueError::new_err(message.into())
}

enum ReadError {
    Limit(&'static str),
    Py(PyErr),
}

impl From<PyErr> for ReadError {
    fn from(err: PyErr) -> Self {
        ReadError::Py(err)
    }
}

const MAX_JSON_DEPTH: u32 = 2048;

fn read_json(obj: &Bound<'_, PyAny>) -> Result<Json, ReadError> {
    let mut visiting = HashSet::new();
    read_json_at(obj, &mut visiting, 0)
}

fn read_json_at(
    obj: &Bound<'_, PyAny>,
    visiting: &mut HashSet<usize>,
    depth: u32,
) -> Result<Json, ReadError> {
    if obj.is_none() {
        return Ok(Json::Null);
    }
    if obj.is_instance_of::<PyBool>() {
        return Ok(Json::Bool(obj.extract()?));
    }
    if obj.is_instance_of::<PyInt>() {
        if let Ok(value) = obj.extract::<i64>() {
            return Ok(Json::Int(value));
        }
        // A subclass can override `__str__` with different digits. An exact
        // `int` past i64 uses its decimal text; anything else is `json.dumps`.
        if !obj.get_type().is(&PyInt::type_object(obj.py())) {
            return Err(ReadError::Py(invalid("int subclass")));
        }
        let text = obj.str()?.to_string();
        if let Some(rest) = text.strip_prefix('-') {
            if !rest.is_empty() && rest.bytes().all(|b| b.is_ascii_digit()) {
                return Ok(Json::Big(text));
            }
        } else if !text.is_empty() && text.bytes().all(|b| b.is_ascii_digit()) {
            return Ok(Json::Big(text));
        }
        return Err(ReadError::Py(invalid(format!("unsupported int {text}"))));
    }
    if obj.is_instance_of::<PyFloat>() {
        return Ok(Json::Float(obj.extract()?));
    }
    if obj.is_instance_of::<PyString>() {
        return Ok(Json::Str(obj.extract()?));
    }
    if obj.is_instance_of::<PyDict>() {
        // Separate frame: the dict iterator must not sit on the list walk.
        // A 2048-deep list is at the native limit and the default stack.
        return read_dict(obj, visiting, depth);
    }
    if obj.is_instance_of::<PyList>() || obj.is_instance_of::<PyTuple>() {
        // A subclass iterator can yield an unsupported value and then different
        // data. Decide before the first walk so the fallback dump is the only pass.
        if sequence_needs_python(obj) {
            return Err(ReadError::Py(invalid("sequence subclass")));
        }
        let id = enter_container(obj, visiting, depth)?;
        let mut items = Vec::new();
        for item in obj.try_iter()? {
            match read_json_at(&item?, visiting, depth + 1) {
                Ok(parsed) => items.push(parsed),
                Err(err) => {
                    visiting.remove(&id);
                    return Err(err);
                }
            }
        }
        visiting.remove(&id);
        return Ok(Json::Array(items));
    }
    Err(ReadError::Py(invalid("unsupported json value")))
}

#[inline(never)]
fn read_dict(
    obj: &Bound<'_, PyAny>,
    visiting: &mut HashSet<usize>,
    depth: u32,
) -> Result<Json, ReadError> {
    let Ok(dict) = obj.downcast::<PyDict>() else {
        return Err(ReadError::Py(invalid("dict subclass")));
    };
    // `dict.iter()` reads stored entries. `json.dumps` calls `items()`.
    // Only an inherited `dict.items` is the same view. Calling an override
    // here would change a stateful dict before the fallback dump, and a
    // list-shaped item would be accepted where Python rejects it.
    if !obj.get_type().is(&PyDict::type_object(obj.py())) && !items_are_inherited(obj) {
        return Err(ReadError::Py(invalid("dict subclass")));
    }
    let id = enter_container(obj, visiting, depth)?;
    let mut entries = Vec::new();
    for (key, value) in dict.iter() {
        let key: String = key
            .extract()
            .map_err(|_| invalid("json object key must be a str"))?;
        // Call this function directly so a deep object is one frame per level.
        let parsed = if value.is_instance_of::<PyDict>() {
            read_dict(&value, visiting, depth + 1)
        } else {
            read_json_at(&value, visiting, depth + 1)
        };
        match parsed {
            Ok(parsed) => entries.push((key, parsed)),
            Err(err) => {
                visiting.remove(&id);
                return Err(err);
            }
        }
    }
    visiting.remove(&id);
    Ok(Json::Object(entries))
}

#[inline(never)]
fn items_are_inherited(obj: &Bound<'_, PyAny>) -> bool {
    // Class-dict presence, not `==` and not a call. `__getattribute__` can
    // supply `items` without putting that name in the class dict.
    inherited_slot(obj, "items", &PyDict::type_object(obj.py()))
}

#[inline(never)]
fn sequence_needs_python(obj: &Bound<'_, PyAny>) -> bool {
    let py = obj.py();
    let ty = obj.get_type();
    if ty.is(&PyList::type_object(py)) || ty.is(&PyTuple::type_object(py)) {
        return false;
    }
    let base = if obj.is_instance_of::<PyList>() {
        PyList::type_object(py)
    } else {
        PyTuple::type_object(py)
    };
    !inherited_slot(obj, "__iter__", &base)
}

fn inherited_slot(obj: &Bound<'_, PyAny>, name: &str, base: &Bound<'_, PyType>) -> bool {
    let Ok(mro) = obj.get_type().getattr("__mro__") else {
        return false;
    };
    let Ok(classes) = mro.try_iter() else {
        return false;
    };
    for cls in classes {
        let Ok(cls) = cls else {
            return false;
        };
        if cls.is(base) {
            return !instance_shadows(obj, name);
        }
        let Ok(cls_dict) = cls.getattr("__dict__") else {
            return false;
        };
        match mapping_contains(&cls_dict, name) {
            Some(true) | None => return false,
            Some(false) => {}
        }
        match mapping_contains(&cls_dict, "__getattribute__") {
            Some(true) | None => return false,
            Some(false) => {}
        }
    }
    false
}

fn instance_shadows(obj: &Bound<'_, PyAny>, name: &str) -> bool {
    let Ok(instance_dict) = obj.getattr("__dict__") else {
        return false;
    };
    !matches!(mapping_contains(&instance_dict, name), Some(false))
}

fn mapping_contains(mapping: &Bound<'_, PyAny>, key: &str) -> Option<bool> {
    let flag = mapping.call_method1("__contains__", (key,)).ok()?;
    flag.is_truthy().ok()
}

fn enter_container(
    obj: &Bound<'_, PyAny>,
    visiting: &mut HashSet<usize>,
    depth: u32,
) -> Result<usize, ReadError> {
    if depth >= MAX_JSON_DEPTH {
        return Err(ReadError::Limit("JSON nesting too deep"));
    }
    let id = obj.as_ptr() as usize;
    if !visiting.insert(id) {
        return Err(ReadError::Limit("Circular reference detected"));
    }
    Ok(id)
}

fn read_symbols(
    py: Python<'_>,
    symbols: &Bound<'_, PyAny>,
) -> PyResult<(Option<String>, Vec<String>)> {
    let ty = symbols.get_type();
    if ty.is(&PyString::type_object(py)) {
        return Ok((Some(symbols.extract()?), Vec::new()));
    }
    if !ty.is(&PyList::type_object(py)) && !ty.is(&PyTuple::type_object(py)) {
        return Ok((None, Vec::new()));
    }
    let seq = symbols.downcast::<PySequence>()?;
    let n = std::cmp::min(seq.len()?, MAX_SYMBOLS);
    let mut items = Vec::with_capacity(n);
    let str_type = PyString::type_object(py);
    for index in 0..n {
        // `_canonical_ticker` keeps an exact `str` and drops everything else.
        // Extracting each entry as `String` turned `None` into a TypeError and
        // aborted `market_event_many`.
        let item = seq.get_item(index)?;
        if !item.get_type().is(&str_type) {
            continue;
        }
        if let Ok(text) = item.extract::<String>() {
            items.push(text);
        }
    }
    Ok((None, items))
}

type EventOut = (String, Vec<String>, String, String, String, String, String);

fn pack(parts: cockpit_ingest::EventParts) -> EventOut {
    (
        parts.event_type,
        parts.symbols,
        parts.headline,
        parts.summary,
        parts.raw_ref,
        parts.content_hash,
        parts.raw_json,
    )
}

fn ensure_source(source: &str) -> PyResult<()> {
    if source_allowlisted(source) {
        return Ok(());
    }
    // Match Python `f"{source!r}"` for the feed names this gate sees.
    let shown = source.replace('\\', "\\\\").replace('\'', "\\'");
    Err(invalid(format!("source not allowlisted: '{shown}'")))
}

fn contains_unassigned(py: Python<'_>, text: &str) -> PyResult<bool> {
    if text.is_ascii() {
        return Ok(false);
    }
    static CHECK: GILOnceCell<Py<PyAny>> = GILOnceCell::new();
    let checker = CHECK.get_or_try_init(py, || -> PyResult<Py<PyAny>> {
        let code = CString::new(
            "import unicodedata\n\ndef has_unassigned(text):\n    return any(unicodedata.category(ch) == 'Cn' for ch in text)\n",
        )?;
        let module = PyModule::from_code(
            py,
            &code,
            c"cockpit_unicode_gate.py",
            c"cockpit_unicode_gate",
        )?;
        Ok(module.getattr("has_unassigned")?.unbind())
    })?;
    checker.bind(py).call1((text,))?.extract()
}

fn role_pattern(py: Python<'_>) -> PyResult<Py<PyAny>> {
    static PATTERN: GILOnceCell<Py<PyAny>> = GILOnceCell::new();
    let compiled = PATTERN.get_or_try_init(py, || -> PyResult<Py<PyAny>> {
        let pattern = concat!(
            r"(?i)\b(?:system|assistant|human|developer|user|tool|function)\s*:",
            r"|</?untrusted-content|<\|im_(?:start|end)\|>|\[/?INST\]|###\s*instruction"
        );
        Ok(py
            .import("re")?
            .getattr("compile")?
            .call1((pattern,))?
            .unbind())
    })?;
    Ok(compiled.clone_ref(py))
}

/// NFKC and role-marker replacement for text this interpreter has not assigned.
///
/// Uses the stdlib `unicodedata` and `re` modules. The wheel does not import
/// `ingest.normalize`, so a standalone install still returns escaped text.
fn escape_with_python_unicode(py: Python<'_>, text: &str) -> PyResult<String> {
    if text.is_empty() {
        return Ok(String::new());
    }
    let folded: String = py
        .import("unicodedata")?
        .getattr("normalize")?
        .call1(("NFKC", text))?
        .extract()?;
    let marked: String = role_pattern(py)?
        .bind(py)
        .call_method1("sub", ("[role-marker]", &folded))?
        .extract()?;
    let mut escaped = String::with_capacity(marked.len());
    for ch in marked.chars() {
        match ch {
            '&' => escaped.push_str("&amp;"),
            '<' => escaped.push_str("&lt;"),
            '>' => escaped.push_str("&gt;"),
            other => escaped.push(other),
        }
    }
    Ok(escaped.chars().take(MAX_LEN).collect())
}

fn json_dump_kwargs(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let kwargs = PyDict::new(py);
    kwargs.set_item("sort_keys", true)?;
    kwargs.set_item("separators", (",", ":"))?;
    kwargs.set_item("ensure_ascii", true)?;
    kwargs.set_item("default", py.eval(c"str", None, None)?)?;
    Ok(kwargs)
}

fn sha256_hex(py: Python<'_>, text: &str) -> PyResult<String> {
    py.import("hashlib")?
        .getattr("sha256")?
        .call1((text.as_bytes(),))?
        .call_method0("hexdigest")?
        .extract()
}

/// Detached `raw_input` image for a value `read_json` cannot take.
///
/// `python_to_market_event` does `json.loads(json.dumps(..., default=str))`
/// and hashes that object. The load turns numeric keys into strings, so
/// `2` before `10` becomes `"10"` before `"2"`, and duplicate stringified
/// keys collapse to the last one. Hashing the first dump disagrees with
/// `python_content_hash(raw_input)`.
fn python_canonical_raw(py: Python<'_>, raw: &Bound<'_, PyAny>) -> PyResult<(String, String)> {
    let json = py.import("json")?;
    let dumps = json.getattr("dumps")?;
    let kwargs = json_dump_kwargs(py)?;
    let first: String = dumps.call((raw,), Some(&kwargs))?.extract()?;
    let detached = json.getattr("loads")?.call1((&first,))?;
    let raw_json: String = dumps.call((detached,), Some(&kwargs))?.extract()?;
    let digest = sha256_hex(py, &raw_json)?;
    Ok((raw_json, digest))
}

fn overlay_python_text(
    py: Python<'_>,
    mut parts: EventOut,
    headline: &str,
    summary: &str,
    event_type: &str,
) -> PyResult<EventOut> {
    let escaped_type = escape_with_python_unicode(py, event_type)?;
    parts.0 = if escaped_type.is_empty() {
        "news".to_string()
    } else {
        escaped_type
    };
    parts.2 = escape_with_python_unicode(py, headline)?;
    parts.3 = escape_with_python_unicode(py, summary)?;
    Ok(parts)
}

fn needs_python_unicode(py: Python<'_>, parts: &[&str]) -> PyResult<bool> {
    for part in parts {
        if contains_unassigned(py, part)? {
            return Ok(true);
        }
    }
    Ok(false)
}

#[pyfunction]
#[pyo3(name = "escape_untrusted")]
fn escape_untrusted_py(py: Python<'_>, text: &str) -> PyResult<String> {
    if contains_unassigned(py, text)? {
        return escape_with_python_unicode(py, text);
    }
    Ok(escape_untrusted(text))
}

#[pyfunction]
#[pyo3(name = "content_hash")]
fn content_hash_py(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<String> {
    match read_json(value) {
        Ok(parsed) => Ok(content_hash(&parsed)),
        Err(ReadError::Limit(message)) => Err(invalid(message)),
        Err(ReadError::Py(err)) => {
            drop(err);
            // `python_content_hash` hashes one `default=str` dump. That is not
            // the detached raw image `market_event` stores after `json.loads`.
            // Cycles and too-deep trees are `ReadError::Limit`, so they never
            // reach `json.dumps` (Python 3.11 recurses and raises `RecursionError`).
            let raw_json: String = py
                .import("json")?
                .getattr("dumps")?
                .call((value,), Some(&json_dump_kwargs(py)?))?
                .extract()?;
            sha256_hex(py, &raw_json)
        }
    }
}

#[pyfunction]
#[pyo3(name = "market_event", signature = (source, article_id, symbols, headline, summary, event_type, raw=None))]
fn market_event_py(
    py: Python<'_>,
    source: &str,
    article_id: &str,
    symbols: &Bound<'_, PyAny>,
    headline: &str,
    summary: &str,
    event_type: &str,
    raw: Option<&Bound<'_, PyAny>>,
) -> PyResult<EventOut> {
    ensure_source(source)?;
    let skewed = needs_python_unicode(py, &[headline, summary, event_type])?;
    // A date or an integer key is not JSON. `python_to_market_event` still
    // accepts it. Falling back only for unassigned text made that acceptance
    // depend on the headline.
    let (python_raw, raw_json) = match raw {
        None => (None, None),
        Some(raw) => match read_json(raw) {
            Ok(value) => (None, Some(value)),
            Err(ReadError::Limit(message)) => return Err(invalid(message)),
            Err(ReadError::Py(err)) => {
                drop(err);
                (Some(python_canonical_raw(py, raw)?), None)
            }
        },
    };
    let (csv, items) = read_symbols(py, symbols)?;
    let mut parts = pack(market_event(
        source,
        article_id,
        csv.as_deref(),
        &items,
        headline,
        summary,
        event_type,
        raw_json.as_ref(),
    ));
    if skewed {
        parts = overlay_python_text(py, parts, headline, summary, event_type)?;
    }
    if let Some((raw_json, digest)) = python_raw {
        parts.5 = digest;
        parts.6 = raw_json;
    }
    Ok(parts)
}

#[pyfunction]
fn market_event_many(
    py: Python<'_>,
    rows: Vec<(String, String, Py<PyAny>, String, String, String)>,
) -> PyResult<Vec<EventOut>> {
    for (source, _, _, _, _, _) in &rows {
        ensure_source(source)?;
    }
    let mut out = Vec::with_capacity(rows.len());
    for (source, article_id, symbols, headline, summary, event_type) in rows {
        let (csv, items) = read_symbols(py, symbols.bind(py))?;
        let parts = pack(market_event(
            &source,
            &article_id,
            csv.as_deref(),
            &items,
            &headline,
            &summary,
            &event_type,
            None,
        ));
        if needs_python_unicode(py, &[&headline, &summary, &event_type])? {
            out.push(overlay_python_text(
                py,
                parts,
                &headline,
                &summary,
                &event_type,
            )?);
        } else {
            out.push(parts);
        }
    }
    Ok(out)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(escape_untrusted_py, m)?)?;
    m.add_function(wrap_pyfunction!(content_hash_py, m)?)?;
    m.add_function(wrap_pyfunction!(market_event_py, m)?)?;
    m.add_function(wrap_pyfunction!(market_event_many, m)?)?;
    Ok(())
}
