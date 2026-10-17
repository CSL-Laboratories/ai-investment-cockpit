//! PyO3 entry point for the synthetic tape. The book crate owns the stream.

use cockpit_book::{
    box_muller, synthetic_events as generate, unit_exponential, SynthAction, SynthCursor,
    SynthEvent, SynthParams,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

type EventRow = (String, String, i64, i64, i64, i64, String, i64, i64, i64);

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn synthetic_events(
    symbols: Vec<String>,
    n_events: i64,
    seed: i64,
    initial_ticks: i64,
    tick_volatility: f64,
    depth_levels: i64,
    arrival_decay: f64,
    min_size: i64,
    max_size: i64,
    cancel_probability: f64,
    target_resting_orders: i64,
    trade_probability: f64,
    exchange_gap_ns: i64,
    feed_latency_ns: i64,
    feed_jitter_ns: i64,
    decode_latency_ns: i64,
    decode_jitter_ns: i64,
) -> PyResult<Vec<EventRow>> {
    let params = SynthParams {
        initial_ticks,
        tick_volatility,
        depth_levels,
        arrival_decay,
        min_size,
        max_size,
        cancel_probability,
        target_resting_orders,
        trade_probability,
        exchange_gap_ns,
        feed_latency_ns,
        feed_jitter_ns,
        decode_latency_ns,
        decode_jitter_ns,
    };
    let events =
        generate(&symbols, n_events, seed, &params).map_err(|err| PyValueError::new_err(err.0))?;
    Ok(events.into_iter().map(event_row).collect())
}

#[pyclass]
struct SyntheticCursor {
    inner: SynthCursor,
    /// A batch that already returned earlier events keeps the later failure
    /// here. The next read raises it, so those events are not dropped.
    pending_error: Option<String>,
}

#[pymethods]
impl SyntheticCursor {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self) -> PyResult<Option<EventRow>> {
        self.pull()
    }

    fn next_batch(&mut self, limit: usize) -> PyResult<Vec<EventRow>> {
        self.raise_pending()?;
        let mut out = Vec::new();
        for _ in 0..limit {
            match self.inner.next_event() {
                Ok(Some(event)) => out.push(event_row(event)),
                Ok(None) => break,
                Err(err) => {
                    if out.is_empty() {
                        return Err(PyValueError::new_err(err.0));
                    }
                    self.pending_error = Some(err.0);
                    break;
                }
            }
        }
        Ok(out)
    }

    fn pull(&mut self) -> PyResult<Option<EventRow>> {
        self.raise_pending()?;
        match self.inner.next_event() {
            Ok(Some(event)) => Ok(Some(event_row(event))),
            Ok(None) => Ok(None),
            Err(err) => Err(PyValueError::new_err(err.0)),
        }
    }

    fn raise_pending(&mut self) -> PyResult<()> {
        match &self.pending_error {
            Some(err) => Err(PyValueError::new_err(err.clone())),
            None => Ok(()),
        }
    }
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn synthetic_cursor(
    symbols: Vec<String>,
    n_events: i64,
    seed: i64,
    initial_ticks: i64,
    tick_volatility: f64,
    depth_levels: i64,
    arrival_decay: f64,
    min_size: i64,
    max_size: i64,
    cancel_probability: f64,
    target_resting_orders: i64,
    trade_probability: f64,
    exchange_gap_ns: i64,
    feed_latency_ns: i64,
    feed_jitter_ns: i64,
    decode_latency_ns: i64,
    decode_jitter_ns: i64,
) -> PyResult<SyntheticCursor> {
    let params = SynthParams {
        initial_ticks,
        tick_volatility,
        depth_levels,
        arrival_decay,
        min_size,
        max_size,
        cancel_probability,
        target_resting_orders,
        trade_probability,
        exchange_gap_ns,
        feed_latency_ns,
        feed_jitter_ns,
        decode_latency_ns,
        decode_jitter_ns,
    };
    let inner = SynthCursor::open(&symbols, n_events, seed, &params)
        .map_err(|err| PyValueError::new_err(err.0))?;
    Ok(SyntheticCursor {
        inner,
        pending_error: None,
    })
}

fn event_row(event: SynthEvent) -> EventRow {
    let action = match event.action {
        SynthAction::Add => "add",
        SynthAction::Cancel => "cancel",
        SynthAction::Trade => "trade",
    };
    (
        action.to_string(),
        event.symbol,
        event.seq,
        event.exchange_ts,
        event.recv_ts,
        event.ingest_ts,
        event.side.as_str().to_string(),
        event.price_ticks,
        event.qty,
        event.order_ref,
    )
}

/// Box-Muller pair from the libm kernel `SynthCursor` uses.
///
/// The first value is `cos`, which CPython's `gauss` returns. The second is
/// `sin`, which `gauss` caches. Uniforms are angle, then radius.
#[pyfunction]
fn portable_box_muller(u_angle: f64, u_radius: f64) -> (f64, f64) {
    box_muller(u_angle, u_radius)
}

/// `-ln(1 - u)` from the same libm kernel as `portable_box_muller`.
#[pyfunction]
fn portable_unit_exponential(u: f64) -> f64 {
    unit_exponential(u)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(synthetic_events, m)?)?;
    m.add_function(wrap_pyfunction!(synthetic_cursor, m)?)?;
    m.add_function(wrap_pyfunction!(portable_box_muller, m)?)?;
    m.add_function(wrap_pyfunction!(portable_unit_exponential, m)?)?;
    Ok(())
}
