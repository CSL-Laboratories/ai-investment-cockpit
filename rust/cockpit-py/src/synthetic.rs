//! PyO3 entry point for the synthetic tape. The book crate owns the stream.

use cockpit_book::{synthetic_events as generate, SynthAction, SynthParams};
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
    Ok(events
        .into_iter()
        .map(|event| {
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
        })
        .collect())
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(synthetic_events, m)?)?;
    Ok(())
}
