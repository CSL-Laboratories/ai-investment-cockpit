//! PyO3 entry points for the raw/normalized market-event adapter.

use std::ffi::CString;

use cockpit_ingest::{
    content_hash, escape_untrusted, market_event, source_allowlisted, Json, MAX_LEN, MAX_SYMBOLS,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::sync::GILOnceCell;
use pyo3::types::{
    PyBool, PyDict, PyFloat, PyInt, PyList, PyModule, PySequence, PyString, PyTuple,
};
use pyo3::PyTypeInfo;

fn invalid(message: impl Into<String>) -> PyErr {
    PyValueError::new_err(message.into())
}

fn read_json(obj: &Bound<'_, PyAny>) -> PyResult<Json> {
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
        let text = obj.str()?.to_string();
        if let Some(rest) = text.strip_prefix('-') {
            if !rest.is_empty() && rest.bytes().all(|b| b.is_ascii_digit()) {
                return Ok(Json::Big(text));
            }
        } else if !text.is_empty() && text.bytes().all(|b| b.is_ascii_digit()) {
            return Ok(Json::Big(text));
        }
        return Err(invalid(format!("unsupported int {text}")));
    }
    if obj.is_instance_of::<PyFloat>() {
        return Ok(Json::Float(obj.extract()?));
    }
    if obj.is_instance_of::<PyString>() {
        return Ok(Json::Str(obj.extract()?));
    }
    if let Ok(dict) = obj.downcast::<PyDict>() {
        let mut entries = Vec::new();
        for (key, value) in dict.iter() {
            let key: String = key
                .extract()
                .map_err(|_| invalid("json object key must be a str"))?;
            entries.push((key, read_json(&value)?));
        }
        return Ok(Json::Object(entries));
    }
    if obj.is_instance_of::<PyList>() || obj.is_instance_of::<PyTuple>() {
        let mut items = Vec::new();
        for item in obj.try_iter()? {
            items.push(read_json(&item?)?);
        }
        return Ok(Json::Array(items));
    }
    Err(invalid("unsupported json value"))
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
    for index in 0..n {
        items.push(seq.get_item(index)?.extract()?);
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
    let kwargs = PyDict::new(py);
    kwargs.set_item("sort_keys", true)?;
    kwargs.set_item("separators", (",", ":"))?;
    kwargs.set_item("ensure_ascii", true)?;
    kwargs.set_item("default", py.eval(c"str", None, None)?)?;
    let first: String = dumps.call((raw,), Some(&kwargs))?.extract()?;
    let detached = json.getattr("loads")?.call1((&first,))?;
    let raw_json: String = dumps.call((detached,), Some(&kwargs))?.extract()?;
    let digest: String = py
        .import("hashlib")?
        .getattr("sha256")?
        .call1((raw_json.as_bytes(),))?
        .call_method0("hexdigest")?
        .extract()?;
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
fn content_hash_py(value: &Bound<'_, PyAny>) -> PyResult<String> {
    Ok(content_hash(&read_json(value)?))
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
    let python_raw = match raw {
        Some(raw) if skewed && read_json(raw).is_err() => Some(python_canonical_raw(py, raw)?),
        _ => None,
    };
    let (csv, items) = read_symbols(py, symbols)?;
    let raw_json = if python_raw.is_some() {
        None
    } else {
        raw.map(read_json).transpose()?
    };
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
