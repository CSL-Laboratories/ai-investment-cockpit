//! FIFO price-time matching. Policy, kept off the book type on purpose.
//!
//! The resting order sets the trade price. A partial fill stays at the front
//! of its level. Fill-or-kill is decided before any mutation. Self-trade
//! prevention defaults to cancelling the resting order.

use crate::book::{fit_add, fit_sub, Book};
use crate::error::BookError;
use crate::side::Side;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TimeInForce {
    Day,
    Gtc,
    Ioc,
    Fok,
}

impl TimeInForce {
    pub fn rests(self) -> bool {
        matches!(self, TimeInForce::Day | TimeInForce::Gtc)
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "day" => Some(TimeInForce::Day),
            "gtc" => Some(TimeInForce::Gtc),
            "ioc" => Some(TimeInForce::Ioc),
            "fok" => Some(TimeInForce::Fok),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SelfTrade {
    CancelResting,
    CancelAggressing,
    Allow,
}

impl SelfTrade {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "cancel_resting" => Some(SelfTrade::CancelResting),
            "cancel_aggressing" => Some(SelfTrade::CancelAggressing),
            "allow" => Some(SelfTrade::Allow),
            _ => None,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Aggressive {
    pub order_id: String,
    pub side: Side,
    pub qty: i64,
    pub price_ticks: Option<i64>,
    pub tif: TimeInForce,
    pub owner: Option<String>,
    pub stp: SelfTrade,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FillOut {
    pub order_id: String,
    pub side: Side,
    pub price_ticks: i64,
    pub qty: i64,
    pub liquidity: &'static str,
    pub queue_position_at_entry: i64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MatchOutput {
    pub status: &'static str,
    pub filled_qty: i64,
    pub remaining_qty: i64,
    pub fills: Vec<FillOut>,
    pub trades: Vec<(i64, i64)>,
    pub resting_order_id: Option<String>,
    pub stp_cancelled: Vec<String>,
    /// Resting orders a trade changed, as `(order_id, qty_after)`.
    /// `qty_after == 0` means the order was fully filled. Self-trade cancels
    /// are not listed; those ids stay in `stp_cancelled` and keep their qty.
    pub touched: Vec<(String, i64)>,
}

fn crosses(side: Side, limit: Option<i64>, level: i64) -> bool {
    match limit {
        None => true,
        Some(limit) if side == Side::Buy => level <= limit,
        Some(limit) => level >= limit,
    }
}

fn is_self_trade(order: &Aggressive, resting_owner: Option<&str>) -> bool {
    match (&order.owner, resting_owner) {
        (Some(incoming), Some(resting_owner)) => incoming == resting_owner,
        _ => false,
    }
}

fn available_qty(book: &Book, order: &Aggressive) -> Result<i64, BookError> {
    let opposite = order.side.opposite();
    let mut total = 0;
    for price in book.prices(opposite) {
        if !crosses(order.side, order.price_ticks, price) {
            break;
        }
        let Some(ids) = book.level_ids(opposite, price) else {
            continue;
        };
        for id in ids {
            let Some(resting) = book.get(&id) else {
                continue;
            };
            if is_self_trade(order, resting.owner.as_deref()) && order.stp != SelfTrade::Allow {
                continue;
            }
            total = fit_add(total, resting.qty)?;
            if total >= order.qty {
                return Ok(total);
            }
        }
    }
    Ok(total)
}

fn partial(err: BookError, stp_cancelled: Vec<String>, touched: Vec<(String, i64)>) -> BookError {
    BookError::Partial {
        source: Box::new(err),
        stp_cancelled,
        touched,
    }
}

pub fn match_order(
    book: &mut Book,
    order: &Aggressive,
    seq: i64,
    exchange_ts: i64,
) -> Result<MatchOutput, BookError> {
    if order.qty <= 0 {
        return Err(BookError::Value(format!(
            "aggressive order '{}' must have positive qty, got {}",
            order.order_id, order.qty
        )));
    }
    if order.price_ticks.is_none() && order.tif == TimeInForce::Fok {
        return Err(BookError::Value(
            "market FOK is ambiguous; specify a limit price".into(),
        ));
    }

    if order.tif == TimeInForce::Fok && available_qty(book, order)? < order.qty {
        return Ok(MatchOutput {
            status: "rejected",
            filled_qty: 0,
            remaining_qty: order.qty,
            fills: Vec::new(),
            trades: Vec::new(),
            resting_order_id: None,
            stp_cancelled: Vec::new(),
            touched: Vec::new(),
        });
    }

    let mut remaining = order.qty;
    let mut filled = 0;
    let mut fills = Vec::new();
    let mut trades = Vec::new();
    let mut stp_cancelled = Vec::new();
    let mut touched = Vec::new();
    let opposite = order.side.opposite();
    let prices = book.prices(opposite);

    for price in prices {
        if remaining <= 0 || !crosses(order.side, order.price_ticks, price) {
            break;
        }
        loop {
            if remaining <= 0 {
                break;
            }
            let Some(resting) = book.level_head(opposite, price) else {
                break;
            };
            if is_self_trade(order, resting.owner.as_deref()) && order.stp != SelfTrade::Allow {
                if order.stp == SelfTrade::CancelResting {
                    book.cancel(&resting.order_id)
                        .map_err(|err| partial(err, stp_cancelled.clone(), touched.clone()))?;
                    stp_cancelled.push(resting.order_id);
                    continue;
                }
                let status = if filled > 0 {
                    "partially_filled"
                } else {
                    "cancelled"
                };
                return Ok(MatchOutput {
                    status,
                    filled_qty: filled,
                    remaining_qty: remaining,
                    fills,
                    trades,
                    resting_order_id: None,
                    stp_cancelled,
                    touched,
                });
            }

            let traded = remaining.min(resting.qty);
            let resting_id = resting.order_id.clone();
            if let Err(err) = book.reduce_qty(&resting_id, traded) {
                // The order may already be gone before the failure; that is a fill.
                if book.get(&resting_id).is_none() {
                    touched.push((resting_id, 0));
                }
                return Err(partial(err, stp_cancelled, touched));
            }
            let left = book.get(&resting_id).map(|order| order.qty).unwrap_or(0);
            touched.push((resting_id, left));
            remaining = fit_sub(remaining, traded)
                .map_err(|err| partial(err, stp_cancelled.clone(), touched.clone()))?;
            filled = fit_add(filled, traded)
                .map_err(|err| partial(err, stp_cancelled.clone(), touched.clone()))?;
            trades.push((resting.price_ticks, traded));

            if resting.owner.is_some() {
                fills.push(FillOut {
                    order_id: resting.order_id.clone(),
                    side: resting.side,
                    price_ticks: resting.price_ticks,
                    qty: traded,
                    liquidity: "maker",
                    queue_position_at_entry: resting.qty_ahead_at_entry,
                });
            }
            if order.owner.is_some() {
                fills.push(FillOut {
                    order_id: order.order_id.clone(),
                    side: order.side,
                    price_ticks: resting.price_ticks,
                    qty: traded,
                    liquidity: "taker",
                    queue_position_at_entry: 0,
                });
            }
        }
    }

    if remaining <= 0 {
        return Ok(MatchOutput {
            status: "filled",
            filled_qty: filled,
            remaining_qty: 0,
            fills,
            trades,
            resting_order_id: None,
            stp_cancelled,
            touched,
        });
    }

    if order.tif.rests() && order.price_ticks.is_some() {
        let price = order.price_ticks.expect("checked");
        if let Err(err) = book.add(
            order.order_id.clone(),
            order.side,
            price,
            remaining,
            seq,
            exchange_ts,
            order.owner.clone(),
        ) {
            return Err(partial(err, stp_cancelled, touched));
        }
        return Ok(MatchOutput {
            status: "resting",
            filled_qty: filled,
            remaining_qty: remaining,
            fills,
            trades,
            resting_order_id: Some(order.order_id.clone()),
            stp_cancelled,
            touched,
        });
    }

    let status = if filled > 0 {
        "partially_filled"
    } else {
        "cancelled"
    };
    Ok(MatchOutput {
        status,
        filled_qty: filled,
        remaining_qty: remaining,
        fills,
        trades,
        resting_order_id: None,
        stp_cancelled,
        touched,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::book::Book;

    fn seed() -> Book {
        let mut book = Book::new("TEST");
        book.add("a1", Side::Sell, 101, 100, 0, 0, None).unwrap();
        book.add("a2", Side::Sell, 101, 200, 0, 0, None).unwrap();
        book.add("a3", Side::Sell, 102, 500, 0, 0, None).unwrap();
        book.add("b1", Side::Buy, 99, 400, 0, 0, None).unwrap();
        book
    }

    fn run(book: &mut Book, order: &Aggressive) -> Result<MatchOutput, BookError> {
        match_order(book, order, 0, 0)
    }

    fn buy(qty: i64, price: Option<i64>) -> Aggressive {
        Aggressive {
            order_id: "agg".into(),
            side: Side::Buy,
            qty,
            price_ticks: price,
            tif: TimeInForce::Day,
            owner: None,
            stp: SelfTrade::CancelResting,
        }
    }

    #[test]
    fn resting_price_and_fifo_and_partial_front() {
        let mut book = seed();
        let result = run(&mut book, &buy(60, Some(101))).unwrap();
        assert_eq!(result.trades, vec![(101, 60)]);
        assert_eq!(book.get("a1").unwrap().qty, 40);
        assert_eq!(book.queue_position("a1").unwrap(), 0);
        assert_eq!(book.queue_position("a2").unwrap(), 40);

        let mut book = seed();
        let result = run(&mut book, &buy(100, Some(105))).unwrap();
        assert_eq!(result.trades, vec![(101, 100)]);
        assert_eq!(result.status, "filled");
    }

    #[test]
    fn limit_bounds_the_walk_and_the_remainder_rests() {
        let mut book = seed();
        let result = run(&mut book, &buy(500, Some(101))).unwrap();
        assert_eq!(result.status, "resting");
        assert_eq!(result.filled_qty, 300);
        assert_eq!(result.remaining_qty, 200);
        assert_eq!(book.level_qty(Side::Sell, 102), 500);
        assert_eq!(book.level_qty(Side::Buy, 101), 200);
        assert_eq!(book.best_bid(), Some(101));
        book.check_integrity().unwrap();
    }

    #[test]
    fn market_never_rests_and_fok_is_atomic() {
        let mut book = seed();
        let mut order = buy(1_000, None);
        order.tif = TimeInForce::Day;
        let result = run(&mut book, &order).unwrap();
        assert_eq!(result.status, "partially_filled");
        assert_eq!(result.filled_qty, 800);
        assert!(result.resting_order_id.is_none());
        assert!(book.best_ask().is_none());

        let mut book = seed();
        let before = book.len();
        let mut fok = buy(400, Some(101));
        fok.tif = TimeInForce::Fok;
        let result = run(&mut book, &fok).unwrap();
        assert_eq!(result.status, "rejected");
        assert_eq!(book.len(), before);
        assert!(result.fills.is_empty());

        let mut book = seed();
        let mut fok = buy(300, Some(101));
        fok.tif = TimeInForce::Fok;
        let result = run(&mut book, &fok).unwrap();
        assert_eq!(result.status, "filled");
        assert_eq!(result.filled_qty, 300);
    }

    #[test]
    fn self_trade_prevention_pulls_resting_and_fok_counts_it_unavailable() {
        let mut book = Book::new("TEST");
        book.add("mine", Side::Sell, 101, 100, 0, 0, Some("mm".into()))
            .unwrap();
        let mut order = buy(100, Some(101));
        order.owner = Some("mm".into());
        let result = run(&mut book, &order).unwrap();
        assert_eq!(result.filled_qty, 0);
        assert_eq!(result.stp_cancelled, vec!["mine".to_string()]);
        assert_eq!(result.status, "resting");
        assert!(!book.contains("mine"));

        let mut book = Book::new("TEST");
        book.add("mine", Side::Sell, 101, 100, 0, 0, Some("mm".into()))
            .unwrap();
        let mut fok = buy(100, Some(101));
        fok.owner = Some("mm".into());
        fok.tif = TimeInForce::Fok;
        let result = run(&mut book, &fok).unwrap();
        assert_eq!(result.status, "rejected");
        assert!(book.contains("mine"));
    }

    #[test]
    fn maker_and_taker_fills_carry_queue_at_entry() {
        let mut book = Book::new("TEST");
        book.add("other", Side::Sell, 101, 400, 0, 0, None).unwrap();
        book.add("ours", Side::Sell, 101, 100, 0, 0, Some("mm".into()))
            .unwrap();
        let result = run(&mut book, &buy(500, Some(101))).unwrap();
        assert_eq!(result.fills.len(), 1);
        assert_eq!(result.fills[0].order_id, "ours");
        assert_eq!(result.fills[0].liquidity, "maker");
        assert_eq!(result.fills[0].queue_position_at_entry, 400);
        assert_eq!(result.fills[0].side, Side::Sell);
    }

    #[test]
    fn sweep_agrees_with_a_naive_book() {
        let prices = [98_i64, 99, 100, 101, 102];
        let mut state = 0x1234_5678_u64;
        let mut next = || {
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
            state
        };
        let mut book = Book::new("SWEEP");
        let mut naive: Vec<(Side, i64, String, i64)> = Vec::new();
        let mut counter = 0_i64;
        for step in 0..800 {
            let live: Vec<String> = naive.iter().map(|row| row.2.clone()).collect();
            let mut op = (next() % 100) as i64;
            if live.is_empty() {
                op = 0;
            }
            if op < 45 {
                counter += 1;
                let id = format!("o{counter}");
                let side = if next() % 2 == 0 {
                    Side::Buy
                } else {
                    Side::Sell
                };
                let price = prices[(next() as usize) % prices.len()];
                let qty = 1 + (next() % 500) as i64;
                book.add(id.clone(), side, price, qty, 0, 0, None).unwrap();
                naive.push((side, price, id, qty));
            } else if op < 65 {
                let id = live[(next() as usize) % live.len()].clone();
                book.cancel(&id).unwrap();
                naive.retain(|row| row.2 != id);
            } else if op < 80 {
                let id = live[(next() as usize) % live.len()].clone();
                let have = book.get(&id).unwrap().qty;
                let qty = 1 + (next() % have as u64) as i64;
                book.reduce(&id, qty).unwrap();
                for row in &mut naive {
                    if row.2 == id {
                        row.3 -= qty;
                    }
                }
                naive.retain(|row| row.3 > 0);
            } else {
                let side = if next() % 2 == 0 {
                    Side::Buy
                } else {
                    Side::Sell
                };
                let limit = prices[(next() as usize) % prices.len()];
                let qty = 1 + (next() % 800) as i64;
                let mut order = buy(qty, Some(limit));
                order.order_id = format!("agg{step}");
                order.side = side;
                order.tif = TimeInForce::Ioc;
                let result = run(&mut book, &order).unwrap();
                let expected = naive_cross(&mut naive, side, limit, qty);
                assert_eq!(result.trades, expected, "step {step}");
            }
            book.check_integrity().unwrap();
            assert_eq!(book.len(), naive.len());
            for (side, price, id, qty) in &naive {
                let order = book.get(id).unwrap();
                assert_eq!(order.qty, *qty);
                assert_eq!(order.side, *side);
                assert_eq!(order.price_ticks, *price);
                assert_eq!(book.queue_position(id).unwrap(), naive_ahead(&naive, id));
            }
        }
    }

    fn naive_ahead(rows: &[(Side, i64, String, i64)], id: &str) -> i64 {
        let (_, price, _, _) = rows.iter().find(|row| row.2 == id).unwrap();
        let side = rows.iter().find(|row| row.2 == id).unwrap().0;
        let mut ahead = 0;
        for row in rows {
            if row.2 == id {
                return ahead;
            }
            if row.0 == side && row.1 == *price {
                ahead += row.3;
            }
        }
        panic!("missing");
    }

    fn naive_cross(
        rows: &mut Vec<(Side, i64, String, i64)>,
        side: Side,
        limit: i64,
        qty: i64,
    ) -> Vec<(i64, i64)> {
        let opposite = side.opposite();
        let mut levels: Vec<i64> = rows
            .iter()
            .filter(|row| row.0 == opposite)
            .map(|row| row.1)
            .collect();
        levels.sort();
        levels.dedup();
        if opposite == Side::Buy {
            levels.reverse();
        }
        let mut remaining = qty;
        let mut trades = Vec::new();
        for price in levels {
            if remaining <= 0 {
                break;
            }
            let crosses = if side == Side::Buy {
                price <= limit
            } else {
                price >= limit
            };
            if !crosses {
                break;
            }
            let mut index = 0;
            while index < rows.len() && remaining > 0 {
                if rows[index].0 == opposite && rows[index].1 == price {
                    let traded = remaining.min(rows[index].3);
                    rows[index].3 -= traded;
                    remaining -= traded;
                    trades.push((price, traded));
                    if rows[index].3 == 0 {
                        rows.remove(index);
                        continue;
                    }
                }
                index += 1;
            }
        }
        trades
    }
}
