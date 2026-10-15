//! One FFI call for a sequence of book operations.
//!
//! Each op is a tuple:
//! * `("a", order_id, side, price, qty, seq, ts, owner)`
//! * `("c", order_id)`
//! * `("r", order_id, qty)`
//! * `("p", order_id, new_price, new_qty, seq, ts)` — price or qty may be `None`
//! * `("m", order_id, side, qty, price, tif, owner, stp, seq, exchange_ts)`
//!   — `price` `None` is a market order
//!
//! An error leaves every earlier op applied and raises the same exception the
//! single-call method would.

use crate::{match_tuple, order_tuple, parse_side, raise};
use cockpit_book::{match_order, Aggressive, Book, BookError, Order, SelfTrade, TimeInForce};
use pyo3::prelude::*;
use pyo3::types::{PyList, PyTuple};

pub(crate) fn apply_many<'py>(
    py: Python<'py>,
    book: &mut Book,
    ops: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyList>> {
    let out = PyList::empty(py);
    if let Err(err) = drive(book, ops, Some(&out)) {
        // The prefix is already applied. Keep those rows on the exception so
        // the Python adapter can update orders the caller still holds.
        let _ = err.value(py).setattr("applied", &out);
        return Err(err);
    }
    Ok(out)
}

pub(crate) fn apply_quiet(book: &mut Book, ops: &Bound<'_, PyAny>) -> PyResult<usize> {
    drive(book, ops, None)
}

fn drive(
    book: &mut Book,
    ops: &Bound<'_, PyAny>,
    out: Option<&Bound<'_, PyList>>,
) -> PyResult<usize> {
    let mut applied = 0usize;
    for item in ops.try_iter()? {
        apply_one(book, &item?, out)?;
        applied += 1;
    }
    Ok(applied)
}

fn apply_one(
    book: &mut Book,
    item: &Bound<'_, PyAny>,
    out: Option<&Bound<'_, PyList>>,
) -> PyResult<()> {
    let tup = item
        .downcast::<PyTuple>()
        .map_err(|_| raise(BookError::Value("batch op must be a tuple".into())))?;
    if tup.is_empty() {
        return Err(raise(BookError::Value("batch op is empty".into())));
    }
    let kind: String = tup
        .get_item(0)?
        .extract()
        .map_err(|_| raise(BookError::Value("batch op kind must be a str".into())))?;
    match kind.as_str() {
        "a" => apply_add(book, tup, out),
        "c" => apply_cancel(book, tup, out),
        "r" => apply_reduce(book, tup, out),
        "p" => apply_replace(book, tup, out),
        "m" => apply_match(book, tup, out),
        other => Err(raise(BookError::Value(format!("unknown batch op {other}")))),
    }
}

fn expect_len(tup: &Bound<'_, PyTuple>, kind: &str, n: usize) -> PyResult<()> {
    if tup.len() != n {
        return Err(raise(BookError::Value(format!(
            "batch op {kind} expects {n} fields, got {}",
            tup.len()
        ))));
    }
    Ok(())
}

fn take_str(tup: &Bound<'_, PyTuple>, index: usize) -> PyResult<String> {
    tup.get_item(index)?.extract()
}

fn take_i64(tup: &Bound<'_, PyTuple>, index: usize) -> PyResult<i64> {
    tup.get_item(index)?.extract()
}

fn take_opt_str(tup: &Bound<'_, PyTuple>, index: usize) -> PyResult<Option<String>> {
    tup.get_item(index)?.extract()
}

fn take_opt_i64(tup: &Bound<'_, PyTuple>, index: usize) -> PyResult<Option<i64>> {
    tup.get_item(index)?.extract()
}

fn emit_order(out: Option<&Bound<'_, PyList>>, kind: &str, order: Order) -> PyResult<()> {
    let Some(out) = out else {
        return Ok(());
    };
    let (order_id, side, price, qty, seq, ts, owner, original, ahead) = order_tuple(order);
    out.append((
        kind, order_id, side, price, qty, seq, ts, owner, original, ahead,
    ))
}

fn apply_add(
    book: &mut Book,
    tup: &Bound<'_, PyTuple>,
    out: Option<&Bound<'_, PyList>>,
) -> PyResult<()> {
    expect_len(tup, "a", 8)?;
    let side = parse_side(&take_str(tup, 2)?).map_err(raise)?;
    let order = book
        .add(
            take_str(tup, 1)?,
            side,
            take_i64(tup, 3)?,
            take_i64(tup, 4)?,
            take_i64(tup, 5)?,
            take_i64(tup, 6)?,
            take_opt_str(tup, 7)?,
        )
        .map_err(raise)?;
    emit_order(out, "a", order)
}

fn apply_cancel(
    book: &mut Book,
    tup: &Bound<'_, PyTuple>,
    out: Option<&Bound<'_, PyList>>,
) -> PyResult<()> {
    expect_len(tup, "c", 2)?;
    let order = book.cancel(&take_str(tup, 1)?).map_err(raise)?;
    emit_order(out, "c", order)
}

fn apply_reduce(
    book: &mut Book,
    tup: &Bound<'_, PyTuple>,
    out: Option<&Bound<'_, PyList>>,
) -> PyResult<()> {
    expect_len(tup, "r", 3)?;
    let order = book
        .reduce(&take_str(tup, 1)?, take_i64(tup, 2)?)
        .map_err(raise)?;
    emit_order(out, "r", order)
}

fn apply_replace(
    book: &mut Book,
    tup: &Bound<'_, PyTuple>,
    out: Option<&Bound<'_, PyList>>,
) -> PyResult<()> {
    expect_len(tup, "p", 6)?;
    let order = book
        .replace(
            &take_str(tup, 1)?,
            take_opt_i64(tup, 2)?,
            take_opt_i64(tup, 3)?,
            take_i64(tup, 4)?,
            take_i64(tup, 5)?,
        )
        .map_err(raise)?;
    emit_order(out, "p", order)
}

fn apply_match(
    book: &mut Book,
    tup: &Bound<'_, PyTuple>,
    out: Option<&Bound<'_, PyList>>,
) -> PyResult<()> {
    expect_len(tup, "m", 10)?;
    let side_name = take_str(tup, 2)?;
    let side = parse_side(&side_name).map_err(raise)?;
    let tif_name = take_str(tup, 5)?;
    let tif = TimeInForce::parse(&tif_name)
        .ok_or_else(|| BookError::Value(format!("unknown tif {tif_name}")))
        .map_err(raise)?;
    let stp_name = take_str(tup, 7)?;
    let stp = SelfTrade::parse(&stp_name)
        .ok_or_else(|| BookError::Value(format!("unknown stp {stp_name}")))
        .map_err(raise)?;
    let aggressive = Aggressive {
        order_id: take_str(tup, 1)?,
        side,
        qty: take_i64(tup, 3)?,
        price_ticks: take_opt_i64(tup, 4)?,
        tif,
        owner: take_opt_str(tup, 6)?,
        stp,
    };
    let result =
        match_order(book, &aggressive, take_i64(tup, 8)?, take_i64(tup, 9)?).map_err(raise)?;
    if let Some(out) = out {
        let row = match_tuple(result);
        // `touched` is stripped before the public row. It is how a retained
        // order learns its qty without scanning the book.
        out.append((
            "m", row.0, row.1, row.2, row.3, row.4, row.5, row.6, row.7,
        ))?;
    }
    Ok(())
}
