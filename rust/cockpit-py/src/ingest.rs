//! PyO3 entry points for the raw/normalized market-event adapter.

use std::ffi::CString;

use cockpit_ingest::{content_hash, escape_untrusted, market_event, source_allowlisted, Json};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::sync::GILOnceCell;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyModule, PyString, PyTuple};

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

fn read_symbols(symbols: &Bound<'_, PyAny>) -> PyResult<(Option<String>, Vec<String>)> {
    if symbols.is_instance_of::<PyString>() {
        return Ok((Some(symbols.extract()?), Vec::new()));
    }
    Ok((None, symbols.extract()?))
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

fn event_via_python(
    py: Python<'_>,
    source: &str,
    article_id: &str,
    symbols: &Bound<'_, PyAny>,
    headline: &str,
    summary: &str,
    event_type: &str,
    raw: Option<&Bound<'_, PyAny>>,
) -> PyResult<EventOut> {
    let func = py
        .import("ingest.normalize")?
        .getattr("python_to_market_event")?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("source", source)?;
    kwargs.set_item("article_id", article_id)?;
    kwargs.set_item("symbols", symbols)?;
    kwargs.set_item("headline", headline)?;
    kwargs.set_item("summary", summary)?;
    kwargs.set_item("ts", py.None())?;
    kwargs.set_item("event_type", event_type)?;
    if let Some(raw) = raw {
        kwargs.set_item("raw", raw)?;
    }
    let event = func.call((), Some(&kwargs))?.downcast_into::<PyDict>()?;
    let raw_input = event
        .get_item("raw_input")?
        .ok_or_else(|| invalid("missing raw_input"))?;
    let dumps = py.import("json")?.getattr("dumps")?;
    let dump_kwargs = PyDict::new(py);
    dump_kwargs.set_item("sort_keys", true)?;
    dump_kwargs.set_item("separators", (",", ":"))?;
    let raw_json: String = dumps.call((raw_input,), Some(&dump_kwargs))?.extract()?;
    let take = |key: &str| -> PyResult<Bound<'_, PyAny>> {
        event
            .get_item(key)?
            .ok_or_else(|| invalid(format!("missing {key}")))
    };
    Ok((
        take("event_type")?.extract()?,
        take("symbols")?.extract()?,
        take("headline")?.extract()?,
        take("summary")?.extract()?,
        take("raw_ref")?.extract()?,
        take("content_hash")?.extract()?,
        raw_json,
    ))
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
        return py
            .import("ingest.normalize")?
            .getattr("python_escape_untrusted")?
            .call1((text,))?
            .extract();
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
    if needs_python_unicode(py, &[headline, summary, event_type])? {
        return event_via_python(
            py, source, article_id, symbols, headline, summary, event_type, raw,
        );
    }
    let (csv, items) = read_symbols(symbols)?;
    let raw_json = raw.map(read_json).transpose()?;
    Ok(pack(market_event(
        source,
        article_id,
        csv.as_deref(),
        &items,
        headline,
        summary,
        event_type,
        raw_json.as_ref(),
    )))
}

#[pyfunction]
fn market_event_many(
    py: Python<'_>,
    rows: Vec<(String, String, Vec<String>, String, String, String)>,
) -> PyResult<Vec<EventOut>> {
    for (source, _, _, _, _, _) in &rows {
        ensure_source(source)?;
    }
    let mut out = Vec::with_capacity(rows.len());
    for (source, article_id, symbols, headline, summary, event_type) in rows {
        if needs_python_unicode(py, &[&headline, &summary, &event_type])? {
            let symbols_obj = PyList::new(py, &symbols)?.into_any();
            out.push(event_via_python(
                py,
                &source,
                &article_id,
                &symbols_obj,
                &headline,
                &summary,
                &event_type,
                None,
            )?);
        } else {
            out.push(pack(market_event(
                &source,
                &article_id,
                None,
                &symbols,
                &headline,
                &summary,
                &event_type,
                None,
            )));
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
