//! PyO3 entry points for the raw/normalized market-event adapter.

use cockpit_ingest::{
    content_hash, escape_untrusted, market_event, token_bucket_allow, Dedup, Json,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};

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

#[pyfunction]
#[pyo3(name = "escape_untrusted")]
fn escape_untrusted_py(text: &str) -> String {
    escape_untrusted(text)
}

#[pyfunction]
#[pyo3(name = "content_hash")]
fn content_hash_py(value: &Bound<'_, PyAny>) -> PyResult<String> {
    Ok(content_hash(&read_json(value)?))
}

#[pyfunction]
#[pyo3(name = "market_event", signature = (source, article_id, symbols, headline, summary, event_type, raw=None))]
fn market_event_py(
    source: &str,
    article_id: &str,
    symbols: &Bound<'_, PyAny>,
    headline: &str,
    summary: &str,
    event_type: &str,
    raw: Option<&Bound<'_, PyAny>>,
) -> PyResult<EventOut> {
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
    rows: Vec<(String, String, Vec<String>, String, String, String)>,
) -> Vec<EventOut> {
    rows.into_iter()
        .map(
            |(source, article_id, symbols, headline, summary, event_type)| {
                pack(market_event(
                    &source,
                    &article_id,
                    None,
                    &symbols,
                    &headline,
                    &summary,
                    &event_type,
                    None,
                ))
            },
        )
        .collect()
}

#[pyclass]
struct DedupCore {
    inner: Dedup,
}

#[pymethods]
impl DedupCore {
    #[new]
    fn new(capacity: i64) -> Self {
        Self {
            inner: Dedup::new(capacity),
        }
    }

    fn add(&mut self, key: &str) -> bool {
        self.inner.add(key)
    }

    fn discard(&mut self, key: &str) -> bool {
        self.inner.discard(key)
    }
}

#[pyfunction]
#[pyo3(name = "token_bucket_allow")]
fn token_bucket_allow_py(
    rate: f64,
    capacity: f64,
    tokens: f64,
    last: f64,
    now: f64,
) -> (bool, f64, f64) {
    token_bucket_allow(rate, capacity, tokens, last, now)
}

#[pyfunction]
fn dedup_replay(capacity: i64, ops: Vec<(String, String)>) -> PyResult<Vec<bool>> {
    let mut seen = Dedup::new(capacity);
    let mut out = Vec::with_capacity(ops.len());
    for (op, key) in ops {
        match op.as_str() {
            "a" => out.push(seen.add(&key)),
            "d" => out.push(seen.discard(&key)),
            _ => return Err(invalid(format!("unknown dedup op {op}"))),
        }
    }
    Ok(out)
}

#[pyfunction]
fn token_bucket_replay(
    rate: f64,
    capacity: f64,
    tokens: f64,
    last: f64,
    times: Vec<f64>,
) -> (Vec<bool>, f64, f64) {
    let mut tokens = tokens;
    let mut last = last;
    let mut out = Vec::with_capacity(times.len());
    for now in times {
        let (allowed, next_tokens, next_last) =
            token_bucket_allow(rate, capacity, tokens, last, now);
        tokens = next_tokens;
        last = next_last;
        out.push(allowed);
    }
    (out, tokens, last)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<DedupCore>()?;
    m.add_function(wrap_pyfunction!(escape_untrusted_py, m)?)?;
    m.add_function(wrap_pyfunction!(content_hash_py, m)?)?;
    m.add_function(wrap_pyfunction!(market_event_py, m)?)?;
    m.add_function(wrap_pyfunction!(market_event_many, m)?)?;
    m.add_function(wrap_pyfunction!(token_bucket_allow_py, m)?)?;
    m.add_function(wrap_pyfunction!(dedup_replay, m)?)?;
    m.add_function(wrap_pyfunction!(token_bucket_replay, m)?)?;
    Ok(())
}
