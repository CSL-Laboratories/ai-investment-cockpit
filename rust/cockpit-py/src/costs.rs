//! PyO3 entry points for the fee schedule and the PnL tracker.
//!
//! Money crosses the boundary as decimal text. Elapsed seconds arrive as the
//! string Python's ``str(float)`` already produced, so borrow matches
//! ``Decimal(str(seconds))``.

use cockpit_costs::{
    borrow_cost as borrow_cost_dec, cost_bps as cost_bps_dec, fee_for as fee_for_dec, Dec,
    Liquidity, Pnl, PnlEvent, Schedule, Side,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn invalid(message: String) -> PyErr {
    PyValueError::new_err(message)
}

fn schedule(
    maker: &str,
    taker: &str,
    sec: &str,
    taf: &str,
    cap: &str,
    clearing: &str,
    borrow: &str,
) -> PyResult<Schedule> {
    Schedule::parse(maker, taker, sec, taf, cap, clearing, borrow).map_err(invalid)
}

fn side(name: &str) -> PyResult<Side> {
    Side::parse(name).ok_or_else(|| invalid(format!("unknown side {name}")))
}

fn liquidity(name: &str) -> PyResult<Liquidity> {
    Liquidity::parse(name).ok_or_else(|| invalid(format!("unknown liquidity {name}")))
}

fn price(text: &str) -> PyResult<Dec> {
    Dec::parse(text).map_err(invalid)
}

type PnlOut = (
    String,
    String,
    String,
    String,
    i64,
    i64,
    i64,
    i64,
    Option<String>,
    Option<i64>,
    String,
);

fn dump(pnl: &Pnl) -> PnlOut {
    (
        pnl.spread_capture.to_plain_string(),
        pnl.inventory_drift.to_plain_string(),
        pnl.fees.to_plain_string(),
        pnl.realized_notional.to_plain_string(),
        pnl.position,
        pnl.fills,
        pnl.maker_fills,
        pnl.taker_fills,
        pnl.last_mid.as_ref().map(Dec::to_plain_string),
        pnl.last_ts,
        pnl.cash.to_plain_string(),
    )
}

#[pyfunction]
#[pyo3(signature = (maker, taker, sec, taf, cap, clearing, side_name, qty, price_text, liquidity_name))]
#[allow(clippy::too_many_arguments)]
fn fee_for(
    maker: &str,
    taker: &str,
    sec: &str,
    taf: &str,
    cap: &str,
    clearing: &str,
    side_name: &str,
    qty: i64,
    price_text: &str,
    liquidity_name: &str,
) -> PyResult<String> {
    let sched = schedule(maker, taker, sec, taf, cap, clearing, "0")?;
    let got = fee_for_dec(
        &sched,
        side(side_name)?,
        qty,
        &price(price_text)?,
        liquidity(liquidity_name)?,
    )
    .map_err(invalid)?;
    Ok(got.to_plain_string())
}

#[pyfunction]
#[pyo3(signature = (maker, taker, sec, taf, cap, clearing, rows))]
#[allow(clippy::too_many_arguments)]
fn fee_for_many(
    maker: &str,
    taker: &str,
    sec: &str,
    taf: &str,
    cap: &str,
    clearing: &str,
    rows: Vec<(String, i64, String, String)>,
) -> PyResult<Vec<String>> {
    let sched = schedule(maker, taker, sec, taf, cap, clearing, "0")?;
    let mut out = Vec::with_capacity(rows.len());
    for (side_name, qty, price_text, liquidity_name) in rows {
        let got = fee_for_dec(
            &sched,
            side(&side_name)?,
            qty,
            &price(&price_text)?,
            liquidity(&liquidity_name)?,
        )
        .map_err(invalid)?;
        out.push(got.to_plain_string());
    }
    Ok(out)
}

#[pyfunction]
fn borrow_cost(rate: &str, short_notional: &str, seconds: &str) -> PyResult<String> {
    let got = borrow_cost_dec(&price(rate)?, &price(short_notional)?, seconds).map_err(invalid)?;
    Ok(got.to_plain_string())
}

#[pyfunction]
fn cost_bps(cost: &str, notional: &str) -> PyResult<String> {
    let got = cost_bps_dec(&price(cost)?, &price(notional)?).map_err(invalid)?;
    Ok(got.to_plain_string())
}

#[pyclass]
struct PnlCore {
    pnl: Pnl,
}

#[pymethods]
impl PnlCore {
    #[new]
    #[allow(clippy::too_many_arguments)]
    fn new(
        maker: &str,
        taker: &str,
        sec: &str,
        taf: &str,
        cap: &str,
        clearing: &str,
        borrow: &str,
    ) -> PyResult<Self> {
        Ok(Self {
            pnl: Pnl::new(schedule(maker, taker, sec, taf, cap, clearing, borrow)?),
        })
    }

    #[pyo3(signature = (mid_ticks, tick_size, ts=None, elapsed=None))]
    fn mark(
        &mut self,
        mid_ticks: &str,
        tick_size: &str,
        ts: Option<i64>,
        elapsed: Option<String>,
    ) -> PyResult<PnlOut> {
        self.pnl
            .mark(mid_ticks, tick_size, ts, elapsed.as_deref())
            .map_err(invalid)?;
        Ok(dump(&self.pnl))
    }

    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        side_sell, qty, price_ticks, maker, signed_qty, tick_size,
        ingest_ts=None, mid_ticks=None, elapsed=None
    ))]
    fn on_fill(
        &mut self,
        side_sell: bool,
        qty: i64,
        price_ticks: i64,
        maker: bool,
        signed_qty: i64,
        tick_size: &str,
        ingest_ts: Option<i64>,
        mid_ticks: Option<String>,
        elapsed: Option<String>,
    ) -> PyResult<PnlOut> {
        self.pnl
            .on_fill(
                side_sell,
                qty,
                price_ticks,
                maker,
                signed_qty,
                ingest_ts,
                mid_ticks.as_deref(),
                tick_size,
                elapsed.as_deref(),
            )
            .map_err(invalid)?;
        Ok(dump(&self.pnl))
    }

    fn apply_many(&mut self, tick_size: &str, rows: Vec<ReplayRow>) -> PyResult<PnlOut> {
        for row in rows {
            self.pnl
                .apply(&row.into_event()?, tick_size)
                .map_err(invalid)?;
        }
        Ok(dump(&self.pnl))
    }
}

struct ReplayRow {
    kind: String,
    mid_ticks: Option<String>,
    ts: Option<i64>,
    elapsed: Option<String>,
    side_sell: bool,
    qty: i64,
    price_ticks: i64,
    maker: bool,
    signed_qty: i64,
}

impl ReplayRow {
    fn into_event(self) -> PyResult<PnlEvent> {
        match self.kind.as_str() {
            "m" => Ok(PnlEvent::Mark {
                mid_ticks: self
                    .mid_ticks
                    .ok_or_else(|| invalid("mark needs a mid".to_string()))?,
                ts: self.ts,
                elapsed: self.elapsed,
            }),
            "f" => Ok(PnlEvent::Fill {
                side_sell: self.side_sell,
                qty: self.qty,
                price_ticks: self.price_ticks,
                maker: self.maker,
                signed_qty: self.signed_qty,
                ingest_ts: self.ts,
                mid_ticks: self.mid_ticks,
                elapsed: self.elapsed,
            }),
            other => Err(invalid(format!("unknown pnl event {other}"))),
        }
    }
}

type ReplayTuple = (
    String,
    Option<String>,
    Option<i64>,
    Option<String>,
    bool,
    i64,
    i64,
    bool,
    i64,
);

impl<'py> FromPyObject<'py> for ReplayRow {
    fn extract_bound(obj: &Bound<'py, PyAny>) -> PyResult<Self> {
        let (
            kind,
            mid_ticks,
            ts,
            elapsed,
            side_sell,
            qty,
            price_ticks,
            maker,
            signed_qty,
        ): ReplayTuple = obj.extract()?;
        Ok(Self {
            kind,
            mid_ticks,
            ts,
            elapsed,
            side_sell,
            qty,
            price_ticks,
            maker,
            signed_qty,
        })
    }
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn pnl_replay(
    maker: &str,
    taker: &str,
    sec: &str,
    taf: &str,
    cap: &str,
    clearing: &str,
    borrow: &str,
    tick_size: &str,
    rows: Vec<ReplayRow>,
) -> PyResult<PnlOut> {
    let mut core = PnlCore::new(maker, taker, sec, taf, cap, clearing, borrow)?;
    core.apply_many(tick_size, rows)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fee_for, m)?)?;
    m.add_function(wrap_pyfunction!(fee_for_many, m)?)?;
    m.add_function(wrap_pyfunction!(borrow_cost, m)?)?;
    m.add_function(wrap_pyfunction!(cost_bps, m)?)?;
    m.add_function(wrap_pyfunction!(pnl_replay, m)?)?;
    m.add_class::<PnlCore>()?;
    Ok(())
}
