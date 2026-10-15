//! Python bindings for the FIFO book, the fee schedule, and PnL attribution.
//! The wrappers in `quant` turn these tuples back into the dataclasses the rest
//! of the engine already uses.

use cockpit_book::{match_order, Aggressive, Book, BookError, Order, SelfTrade, Side, TimeInForce};
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyInt;

mod costs;
mod ingest;

pyo3::create_exception!(cockpit_quant, NotFound, PyException);
pyo3::create_exception!(cockpit_quant, IntegrityError, PyException);
pyo3::create_exception!(cockpit_quant, BookValueError, PyException);

fn raise(err: BookError) -> PyErr {
    match err {
        BookError::NotFound(id) => NotFound::new_err(id),
        BookError::Integrity(message) => IntegrityError::new_err(message),
        BookError::Value(message) => BookValueError::new_err(message),
        BookError::Partial {
            source,
            stp_cancelled,
            touched,
        } => {
            let err = raise(*source);
            Python::with_gil(|py| {
                let _ = err.value(py).setattr("stp_cancelled", stp_cancelled);
                let _ = err.value(py).setattr("touched", touched);
            });
            err
        }
    }
}

/// A Python int that fits in `i64`. Out-of-range ints are `ValueError`, not
/// PyO3's `OverflowError`. The book domain is signed 64-bit.
struct FitI64(i64);

impl<'py> FromPyObject<'py> for FitI64 {
    fn extract_bound(ob: &Bound<'py, PyAny>) -> PyResult<Self> {
        if let Ok(value) = ob.extract::<i64>() {
            return Ok(Self(value));
        }
        if ob.is_instance_of::<PyInt>() {
            return Err(BookValueError::new_err(
                "value must fit in a signed 64-bit integer",
            ));
        }
        ob.extract::<i64>().map(Self)
    }
}

fn parse_side(value: &str) -> Result<Side, BookError> {
    Side::parse(value).ok_or_else(|| BookError::Value(format!("unknown side {value}")))
}

type OrderTuple = (String, String, i64, i64, i64, i64, Option<String>, i64, i64);
type MatchTuple = (
    String,
    i64,
    i64,
    Vec<(i64, i64)>,
    Vec<(String, String, i64, i64, String, i64)>,
    Option<String>,
    Vec<String>,
    Vec<(String, i64)>,
);

fn order_tuple(order: Order) -> OrderTuple {
    (
        order.order_id,
        order.side.as_str().to_string(),
        order.price_ticks,
        order.qty,
        order.entry_seq,
        order.entry_ts,
        order.owner,
        order.original_qty,
        order.qty_ahead_at_entry,
    )
}

#[pyclass(name = "BookCore")]
struct BookCore {
    book: Book,
}

#[pymethods]
impl BookCore {
    #[new]
    fn new(symbol: String) -> Self {
        Self {
            book: Book::new(symbol),
        }
    }

    fn symbol(&self) -> &str {
        self.book.symbol()
    }

    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (order_id, side_name, price_ticks, qty, seq, ts, owner=None))]
    fn add(
        &mut self,
        order_id: String,
        side_name: String,
        price_ticks: FitI64,
        qty: FitI64,
        seq: i64,
        ts: i64,
        owner: Option<String>,
    ) -> PyResult<OrderTuple> {
        let side = parse_side(&side_name).map_err(raise)?;
        self.book
            .add(order_id, side, price_ticks.0, qty.0, seq, ts, owner)
            .map(order_tuple)
            .map_err(raise)
    }

    fn cancel(&mut self, order_id: &str) -> PyResult<OrderTuple> {
        self.book.cancel(order_id).map(order_tuple).map_err(raise)
    }

    fn reduce(&mut self, order_id: &str, qty: FitI64) -> PyResult<OrderTuple> {
        self.book
            .reduce(order_id, qty.0)
            .map(order_tuple)
            .map_err(raise)
    }

    #[pyo3(signature = (order_id, new_price_ticks, new_qty, seq, ts))]
    fn replace(
        &mut self,
        order_id: &str,
        new_price_ticks: Option<FitI64>,
        new_qty: Option<FitI64>,
        seq: i64,
        ts: i64,
    ) -> PyResult<OrderTuple> {
        self.book
            .replace(
                order_id,
                new_price_ticks.map(|value| value.0),
                new_qty.map(|value| value.0),
                seq,
                ts,
            )
            .map(order_tuple)
            .map_err(raise)
    }

    fn get(&self, order_id: &str) -> Option<OrderTuple> {
        self.book.get(order_id).map(order_tuple)
    }

    fn contains(&self, order_id: &str) -> bool {
        self.book.contains(order_id)
    }

    fn __len__(&self) -> usize {
        self.book.len()
    }

    fn queue_position(&self, order_id: &str) -> PyResult<i64> {
        self.book.queue_position(order_id).map_err(raise)
    }

    fn queue_progress(&self, order_id: &str) -> PyResult<f64> {
        self.book.queue_progress(order_id).map_err(raise)
    }

    fn best(&self, side_name: &str) -> PyResult<Option<i64>> {
        let side = parse_side(side_name).map_err(raise)?;
        Ok(self.book.best(side))
    }

    fn spread_ticks(&self) -> Option<i64> {
        self.book.spread_ticks()
    }

    fn mid_ticks(&self) -> Option<f64> {
        self.book.mid_ticks()
    }

    fn is_crossed(&self) -> bool {
        self.book.is_crossed()
    }

    fn level_qty(&self, side_name: &str, price: i64) -> PyResult<i64> {
        let side = parse_side(side_name).map_err(raise)?;
        Ok(self.book.level_qty(side, price))
    }

    fn has_level(&self, side_name: &str, price: i64) -> PyResult<bool> {
        let side = parse_side(side_name).map_err(raise)?;
        Ok(self.book.has_level(side, price))
    }

    fn level_orders(&self, side_name: &str, price: i64) -> PyResult<Option<Vec<OrderTuple>>> {
        let side = parse_side(side_name).map_err(raise)?;
        let Some(ids) = self.book.level_ids(side, price) else {
            return Ok(None);
        };
        let mut rows = Vec::with_capacity(ids.len());
        for id in ids {
            if let Some(order) = self.book.get(&id) {
                rows.push(order_tuple(order));
            }
        }
        Ok(Some(rows))
    }

    fn prices(&self, side_name: &str) -> PyResult<Vec<i64>> {
        let side = parse_side(side_name).map_err(raise)?;
        Ok(self.book.prices(side))
    }

    fn append_price(&mut self, side_name: &str, price: i64) -> PyResult<()> {
        let side = parse_side(side_name).map_err(raise)?;
        self.book.append_price(side, price);
        Ok(())
    }

    fn set_level_total(&mut self, side_name: &str, price: i64, total: i64) -> PyResult<()> {
        let side = parse_side(side_name).map_err(raise)?;
        self.book
            .set_cached_total(side, price, total)
            .map_err(raise)
    }

    fn depth(&self, side_name: &str, levels: usize) -> PyResult<Vec<(i64, i64, usize)>> {
        let side = parse_side(side_name).map_err(raise)?;
        Ok(self
            .book
            .depth(side, levels)
            .into_iter()
            .map(|level| (level.price_ticks, level.qty, level.order_count))
            .collect())
    }

    fn total_qty(&self, side_name: &str) -> PyResult<i64> {
        let side = parse_side(side_name).map_err(raise)?;
        self.book.total_qty(side).map_err(raise)
    }

    fn orders(&self) -> Vec<OrderTuple> {
        self.book.orders().into_iter().map(order_tuple).collect()
    }

    fn last_stamps(&self) -> (i64, i64) {
        self.book.last_stamps()
    }

    fn check_integrity(&self) -> PyResult<()> {
        self.book.check_integrity().map_err(raise)
    }

    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        order_id,
        side_name,
        qty,
        price_ticks,
        tif_name,
        owner,
        stp_name,
        seq,
        exchange_ts,
    ))]
    fn match_order(
        &mut self,
        order_id: String,
        side_name: String,
        qty: FitI64,
        price_ticks: Option<FitI64>,
        tif_name: String,
        owner: Option<String>,
        stp_name: String,
        seq: i64,
        exchange_ts: i64,
    ) -> PyResult<MatchTuple> {
        let side = parse_side(&side_name).map_err(raise)?;
        let tif = TimeInForce::parse(&tif_name)
            .ok_or_else(|| BookError::Value(format!("unknown tif {tif_name}")))
            .map_err(raise)?;
        let stp = SelfTrade::parse(&stp_name)
            .ok_or_else(|| BookError::Value(format!("unknown stp {stp_name}")))
            .map_err(raise)?;
        let aggressive = Aggressive {
            order_id,
            side,
            qty: qty.0,
            price_ticks: price_ticks.map(|value| value.0),
            tif,
            owner,
            stp,
        };
        let result = match_order(&mut self.book, &aggressive, seq, exchange_ts).map_err(raise)?;
        let fills = result
            .fills
            .into_iter()
            .map(|fill| {
                (
                    fill.order_id,
                    fill.side.as_str().to_string(),
                    fill.price_ticks,
                    fill.qty,
                    fill.liquidity.to_string(),
                    fill.queue_position_at_entry,
                )
            })
            .collect();
        Ok((
            result.status.to_string(),
            result.filled_qty,
            result.remaining_qty,
            result.trades,
            fills,
            result.resting_order_id,
            result.stp_cancelled,
            result.touched,
        ))
    }
}

#[pymodule]
fn cockpit_quant(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<BookCore>()?;
    m.add("NotFound", m.py().get_type::<NotFound>())?;
    m.add("IntegrityError", m.py().get_type::<IntegrityError>())?;
    m.add("BookValueError", m.py().get_type::<BookValueError>())?;
    costs::register(m)?;
    ingest::register(m)?;
    Ok(())
}
