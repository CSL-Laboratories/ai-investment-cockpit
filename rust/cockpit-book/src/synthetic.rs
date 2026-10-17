//! Synthetic order-flow tape. Same stream as `quant.feed.synthetic.SyntheticFeed`.
//!
//! The shadow book stays in this process. The Python generator was paying an
//! interpreter crossing for every add, cancel, and trade just to decide the
//! next event. Callers still receive the same events, in the same order.

use crate::book::Book;
use crate::matching::{match_order, Aggressive, SelfTrade, TimeInForce};
use crate::mt::Mt;
use crate::side::Side;

#[derive(Clone, Debug)]
pub struct SynthParams {
    pub initial_ticks: i64,
    pub tick_volatility: f64,
    pub depth_levels: i64,
    pub arrival_decay: f64,
    pub min_size: i64,
    pub max_size: i64,
    pub cancel_probability: f64,
    pub target_resting_orders: i64,
    pub trade_probability: f64,
    pub exchange_gap_ns: i64,
    pub feed_latency_ns: i64,
    pub feed_jitter_ns: i64,
    pub decode_latency_ns: i64,
    pub decode_jitter_ns: i64,
}

impl Default for SynthParams {
    fn default() -> Self {
        Self {
            initial_ticks: 10_000,
            tick_volatility: 0.35,
            depth_levels: 5,
            arrival_decay: 0.55,
            min_size: 100,
            max_size: 1_000,
            cancel_probability: 0.35,
            target_resting_orders: 40,
            trade_probability: 0.08,
            exchange_gap_ns: 250_000,
            feed_latency_ns: 180_000,
            feed_jitter_ns: 40_000,
            decode_latency_ns: 12_000,
            decode_jitter_ns: 4_000,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SynthAction {
    Add,
    Cancel,
    Trade,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SynthEvent {
    pub action: SynthAction,
    pub symbol: String,
    pub seq: i64,
    pub exchange_ts: i64,
    pub recv_ts: i64,
    pub ingest_ts: i64,
    pub side: Side,
    pub price_ticks: i64,
    pub qty: i64,
    pub order_ref: i64,
}

#[derive(Debug)]
pub struct SynthError(pub String);

struct SymbolState {
    fair: f64,
    book: Book,
    next_ref: i64,
    seq: i64,
}

struct Stamps {
    symbol: String,
    seq: i64,
    exchange_ts: i64,
    recv_ts: i64,
    ingest_ts: i64,
}

pub fn synthetic_events(
    symbols: &[String],
    n_events: i64,
    seed: i64,
    params: &SynthParams,
) -> Result<Vec<SynthEvent>, SynthError> {
    if symbols.is_empty() {
        return Err(SynthError("synthetic feed has no symbols".into()));
    }
    if n_events <= 0 {
        return Ok(Vec::new());
    }
    if params.exchange_gap_ns == 0 {
        return Err(SynthError("exchange_gap_ns must not be zero".into()));
    }
    let mut rng = Mt::from_seed(seed);
    let mut state: Vec<SymbolState> = symbols
        .iter()
        .map(|symbol| SymbolState {
            fair: params.initial_ticks as f64,
            book: Book::new(symbol.clone()),
            next_ref: 1,
            seq: 0,
        })
        .collect();
    let mut exchange_ts: i64 = 1_700_000_000 * 1_000_000_000;
    let mut last_recv: i64 = 0;
    let mut last_ingest: i64 = 0;
    let mut out = Vec::with_capacity(n_events as usize);
    for _ in 0..n_events {
        let index = rng.randbelow(symbols.len() as u64) as usize;
        let symbol = symbols[index].clone();
        let gap = trunc_i64(rng.expovariate(1.0 / params.exchange_gap_ns as f64));
        exchange_ts = exchange_ts
            .checked_add(gap.max(1))
            .ok_or_else(|| SynthError("exchange timestamp overflow".into()))?;
        let recv_draw = rng
            .randint(-params.feed_jitter_ns, params.feed_jitter_ns)
            .ok_or_else(|| SynthError("feed jitter range is empty".into()))?;
        let recv_ts = (exchange_ts + params.feed_latency_ns)
            .checked_add(recv_draw)
            .ok_or_else(|| SynthError("recv timestamp overflow".into()))?
            .max(last_recv + 1);
        let ingest_draw = rng
            .randint(-params.decode_jitter_ns, params.decode_jitter_ns)
            .ok_or_else(|| SynthError("decode jitter range is empty".into()))?;
        let ingest_ts = (recv_ts + params.decode_latency_ns)
            .checked_add(ingest_draw)
            .ok_or_else(|| SynthError("ingest timestamp overflow".into()))?
            .max(last_ingest + 1);
        last_recv = recv_ts;
        last_ingest = ingest_ts;
        let slot = &mut state[index];
        slot.seq += 1;
        let seq = slot.seq;
        slot.fair += rng.gauss(0.0, params.tick_volatility);
        let resting = slot.book.len() as i64;
        let denom = params.target_resting_orders.max(1) as f64;
        let cancel_intensity = (params.cancel_probability * resting as f64 / denom).min(0.90);
        let roll = rng.random();
        let stamps = Stamps {
            symbol,
            seq,
            exchange_ts,
            recv_ts,
            ingest_ts,
        };
        let event = if roll < params.trade_probability && resting > 0 {
            match trade(&mut rng, slot, params, &stamps)? {
                Some(event) => event,
                None => add(&mut rng, slot, params, &stamps)?,
            }
        } else if roll < params.trade_probability + cancel_intensity && resting > 0 {
            cancel(&mut rng, slot, &stamps)?
        } else {
            add(&mut rng, slot, params, &stamps)?
        };
        out.push(event);
    }
    Ok(out)
}

fn add(
    rng: &mut Mt,
    state: &mut SymbolState,
    params: &SynthParams,
    stamps: &Stamps,
) -> Result<SynthEvent, SynthError> {
    let side = if rng.random() < 0.5 {
        Side::Buy
    } else {
        Side::Sell
    };
    let mut distance = 0i64;
    let depth_limit = params.depth_levels - 1;
    while distance < depth_limit && rng.random() > params.arrival_decay {
        distance += 1;
    }
    let base = py_round(state.fair);
    let mut price = if side == Side::Buy {
        base - 1 - distance
    } else {
        base + 1 + distance
    };
    if side == Side::Buy {
        if let Some(best_ask) = state.book.best_ask() {
            price = price.min(best_ask - 1);
        }
    } else if let Some(best_bid) = state.book.best_bid() {
        price = price.max(best_bid + 1);
    }
    let qty = draw_size(rng, params)?;
    let order_ref = state.next_ref;
    state.next_ref += 1;
    state
        .book
        .add(
            order_ref.to_string(),
            side,
            price,
            qty,
            stamps.seq,
            stamps.exchange_ts,
            None,
        )
        .map_err(|err| SynthError(err.to_string()))?;
    Ok(event(SynthAction::Add, stamps, side, price, qty, order_ref))
}

fn cancel(
    rng: &mut Mt,
    state: &mut SymbolState,
    stamps: &Stamps,
) -> Result<SynthEvent, SynthError> {
    let n = state.book.len() as u64;
    let index = rng.randbelow(n) as usize;
    let order = state
        .book
        .order_at(index)
        .ok_or_else(|| SynthError(format!("resting index {index} missing")))?;
    let order_ref = order
        .order_id
        .parse::<i64>()
        .map_err(|_| SynthError(format!("order id {} is not an integer", order.order_id)))?;
    state
        .book
        .cancel(&order.order_id)
        .map_err(|err| SynthError(err.to_string()))?;
    Ok(event(
        SynthAction::Cancel,
        stamps,
        order.side,
        order.price_ticks,
        order.qty,
        order_ref,
    ))
}

fn trade(
    rng: &mut Mt,
    state: &mut SymbolState,
    params: &SynthParams,
    stamps: &Stamps,
) -> Result<Option<SynthEvent>, SynthError> {
    let aggressor = if rng.random() < 0.5 {
        Side::Buy
    } else {
        Side::Sell
    };
    let qty = draw_size(rng, params)?;
    let touch = match aggressor {
        Side::Buy => state.book.best_ask(),
        Side::Sell => state.book.best_bid(),
    };
    let touch = match touch {
        Some(price) => price,
        None => {
            let base = py_round(state.fair);
            if aggressor == Side::Buy {
                base + 1
            } else {
                base - 1
            }
        }
    };
    let result = match_order(
        &mut state.book,
        &Aggressive {
            order_id: format!("agg{}", stamps.seq),
            side: aggressor,
            qty,
            price_ticks: Some(touch),
            tif: TimeInForce::Ioc,
            owner: None,
            stp: SelfTrade::CancelResting,
        },
        stamps.seq,
        stamps.exchange_ts,
    )
    .map_err(|err| SynthError(err.to_string()))?;
    if result.filled_qty <= 0 {
        return Ok(None);
    }
    Ok(Some(event(
        SynthAction::Trade,
        stamps,
        aggressor,
        touch,
        result.filled_qty,
        0,
    )))
}

fn event(
    action: SynthAction,
    stamps: &Stamps,
    side: Side,
    price_ticks: i64,
    qty: i64,
    order_ref: i64,
) -> SynthEvent {
    SynthEvent {
        action,
        symbol: stamps.symbol.clone(),
        seq: stamps.seq,
        exchange_ts: stamps.exchange_ts,
        recv_ts: stamps.recv_ts,
        ingest_ts: stamps.ingest_ts,
        side,
        price_ticks,
        qty,
        order_ref,
    }
}

fn draw_size(rng: &mut Mt, params: &SynthParams) -> Result<i64, SynthError> {
    rng.randrange(params.min_size, params.max_size + 1, 100)
        .ok_or_else(|| SynthError("size range is empty".into()))
}

fn trunc_i64(value: f64) -> i64 {
    value as i64
}

/// Python 3 `round` of a finite float: half goes to the even integer.
fn py_round(value: f64) -> i64 {
    let floor = value.floor();
    let frac = value - floor;
    let mut n = floor as i64;
    if frac > 0.5 || (frac == 0.5 && n % 2 != 0) {
        n += 1;
    }
    n
}
