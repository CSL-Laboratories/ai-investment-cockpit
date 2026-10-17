//! Synthetic order-flow tape. Same stream as `quant.feed.synthetic.SyntheticFeed`.
//!
//! The shadow book stays in this process. The Python generator was paying an
//! interpreter crossing for every add, cancel, and trade just to decide the
//! next event. Callers still receive the same events, in the same order.

use std::collections::HashMap;

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

pub struct SynthCursor {
    remaining: i64,
    rng: Mt,
    symbols: Vec<String>,
    /// Position in `symbols` -> shared state. A repeated name is one book.
    slot_for: Vec<usize>,
    state: Vec<SymbolState>,
    params: SynthParams,
    exchange_ts: i64,
    last_recv: i64,
    last_ingest: i64,
    /// Raw `expovariate` and fair from the event just drawn. The clearance
    /// test reads these so it checks the tape's own cuts, not a side stream.
    #[cfg(test)]
    last_raw_gap: f64,
    #[cfg(test)]
    last_fair: f64,
}

impl SynthCursor {
    pub fn open(
        symbols: &[String],
        n_events: i64,
        seed: i64,
        params: &SynthParams,
    ) -> Result<Self, SynthError> {
        if n_events <= 0 {
            return Ok(Self::empty());
        }
        if symbols.is_empty() {
            return Err(SynthError("synthetic feed has no symbols".into()));
        }
        if params.exchange_gap_ns == 0 {
            return Err(SynthError("exchange_gap_ns must not be zero".into()));
        }
        let mut state = Vec::new();
        let mut slot_for = Vec::with_capacity(symbols.len());
        let mut seen: HashMap<String, usize> = HashMap::new();
        for symbol in symbols {
            if let Some(&slot) = seen.get(symbol) {
                slot_for.push(slot);
                continue;
            }
            let slot = state.len();
            seen.insert(symbol.clone(), slot);
            state.push(SymbolState {
                fair: params.initial_ticks as f64,
                book: Book::new(symbol.clone()),
                next_ref: 1,
                seq: 0,
            });
            slot_for.push(slot);
        }
        Ok(Self {
            remaining: n_events,
            rng: Mt::from_seed(seed),
            symbols: symbols.to_vec(),
            slot_for,
            state,
            params: params.clone(),
            exchange_ts: 1_700_000_000 * 1_000_000_000,
            last_recv: 0,
            last_ingest: 0,
            #[cfg(test)]
            last_raw_gap: 0.0,
            #[cfg(test)]
            last_fair: 0.0,
        })
    }

    fn empty() -> Self {
        Self {
            remaining: 0,
            rng: Mt::from_seed(0),
            symbols: Vec::new(),
            slot_for: Vec::new(),
            state: Vec::new(),
            params: SynthParams::default(),
            exchange_ts: 0,
            last_recv: 0,
            last_ingest: 0,
            #[cfg(test)]
            last_raw_gap: 0.0,
            #[cfg(test)]
            last_fair: 0.0,
        }
    }

    #[cfg(test)]
    fn last_raw_draw(&self) -> (f64, f64) {
        (self.last_raw_gap, self.last_fair)
    }

    pub fn next_event(&mut self) -> Result<Option<SynthEvent>, SynthError> {
        if self.remaining <= 0 {
            return Ok(None);
        }
        self.remaining -= 1;
        let index = self.rng.randbelow(self.symbols.len() as u64) as usize;
        let symbol = self.symbols[index].clone();
        let slot_index = self.slot_for[index];
        let raw_gap = self
            .rng
            .expovariate(1.0 / self.params.exchange_gap_ns as f64);
        #[cfg(test)]
        {
            self.last_raw_gap = raw_gap;
        }
        let gap = trunc_i64(raw_gap);
        self.exchange_ts = self
            .exchange_ts
            .checked_add(gap.max(1))
            .ok_or_else(|| SynthError("exchange timestamp overflow".into()))?;
        let recv_draw = self
            .rng
            .randint(-self.params.feed_jitter_ns, self.params.feed_jitter_ns)
            .ok_or_else(|| SynthError("feed jitter range is empty".into()))?;
        let recv_ts = self
            .exchange_ts
            .checked_add(self.params.feed_latency_ns)
            .and_then(|base| base.checked_add(recv_draw))
            .ok_or_else(|| SynthError("recv timestamp overflow".into()))?;
        let recv_floor = self
            .last_recv
            .checked_add(1)
            .ok_or_else(|| SynthError("recv timestamp overflow".into()))?;
        let recv_ts = recv_ts.max(recv_floor);
        let ingest_draw = self
            .rng
            .randint(-self.params.decode_jitter_ns, self.params.decode_jitter_ns)
            .ok_or_else(|| SynthError("decode jitter range is empty".into()))?;
        let ingest_ts = recv_ts
            .checked_add(self.params.decode_latency_ns)
            .and_then(|base| base.checked_add(ingest_draw))
            .ok_or_else(|| SynthError("ingest timestamp overflow".into()))?;
        let ingest_floor = self
            .last_ingest
            .checked_add(1)
            .ok_or_else(|| SynthError("ingest timestamp overflow".into()))?;
        let ingest_ts = ingest_ts.max(ingest_floor);
        self.last_recv = recv_ts;
        self.last_ingest = ingest_ts;
        let exchange_ts = self.exchange_ts;
        let params = self.params.clone();
        let event = Self::step(
            &mut self.rng,
            &mut self.state[slot_index],
            symbol,
            exchange_ts,
            recv_ts,
            ingest_ts,
            &params,
        )?;
        #[cfg(test)]
        {
            self.last_fair = self.state[slot_index].fair;
        }
        Ok(Some(event))
    }

    fn step(
        rng: &mut Mt,
        slot: &mut SymbolState,
        symbol: String,
        exchange_ts: i64,
        recv_ts: i64,
        ingest_ts: i64,
        params: &SynthParams,
    ) -> Result<SynthEvent, SynthError> {
        slot.seq += 1;
        let seq = slot.seq;
        slot.fair += rng.gauss(0.0, params.tick_volatility);
        if !slot.fair.is_finite() {
            return Err(SynthError("fair value is not finite".into()));
        }
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
        if roll < params.trade_probability && resting > 0 {
            match trade(rng, slot, params, &stamps)? {
                Some(event) => Ok(event),
                None => add(rng, slot, params, &stamps),
            }
        } else if roll < params.trade_probability + cancel_intensity && resting > 0 {
            cancel(rng, slot, &stamps)
        } else {
            add(rng, slot, params, &stamps)
        }
    }
}

pub fn synthetic_events(
    symbols: &[String],
    n_events: i64,
    seed: i64,
    params: &SynthParams,
) -> Result<Vec<SynthEvent>, SynthError> {
    let mut cursor = SynthCursor::open(symbols, n_events, seed, params)?;
    let mut out = Vec::new();
    while let Some(event) = cursor.next_event()? {
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
    let price = quote_price(
        state.fair,
        side,
        distance,
        state.book.best_ask(),
        state.book.best_bid(),
    )?;
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
        None => invented_touch(state.fair, aggressor)?,
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
    let start = i128::from(params.min_size);
    let stop = i128::from(params.max_size) + 1;
    let step: i128 = 100;
    if stop <= start {
        return Err(SynthError("size range is empty".into()));
    }
    let n = (stop - start + step - 1) / step;
    if n <= 0 || n > i128::from(u64::MAX) {
        return Err(SynthError("size range is empty".into()));
    }
    let size = start + step * i128::from(rng.randbelow(n as u64));
    i64::try_from(size).map_err(|_| SynthError("drawn size does not fit in i64".into()))
}

fn trunc_i64(value: f64) -> i64 {
    value as i64
}

/// Python 3 `round` of a finite float, kept wide so a later clamp can still
/// land on `best_ask - 1` the way `SyntheticFeed._add` does.
fn quote_price(
    fair: f64,
    side: Side,
    distance: i64,
    best_ask: Option<i64>,
    best_bid: Option<i64>,
) -> Result<i64, SynthError> {
    let delta = if side == Side::Buy {
        -1 - i128::from(distance)
    } else {
        1 + i128::from(distance)
    };
    let raw = WideInt::from_fair(fair)?.add_i128(delta);
    if side == Side::Buy {
        if let Some(ask) = best_ask {
            return raw.min_narrow(i128::from(ask) - 1);
        }
    } else if let Some(bid) = best_bid {
        return raw.max_narrow(i128::from(bid) + 1);
    }
    raw.narrow()
}

fn invented_touch(fair: f64, aggressor: Side) -> Result<i64, SynthError> {
    let delta = if aggressor == Side::Buy { 1 } else { -1 };
    WideInt::from_fair(fair)?.add_i128(delta).narrow()
}

fn narrow_price(price: i128) -> Result<i64, SynthError> {
    i64::try_from(price).map_err(|_| {
        SynthError(format!(
            "price {price} does not fit in a signed 64-bit integer"
        ))
    })
}

/// Rounded fair value. `Big` is only for magnitudes above `i128`.
enum WideInt {
    Small(i128),
    Big { neg: bool, digits: Vec<u8> },
}

impl WideInt {
    fn from_fair(value: f64) -> Result<Self, SynthError> {
        if !value.is_finite() {
            return Err(SynthError("fair value is not finite".into()));
        }
        // 2^127. The next smaller finite value still fits in `i128`.
        const LIM: f64 = 170141183460469231731687303715884105728.0;
        if value.abs() < LIM {
            return Ok(Self::Small(f64_to_i128_banker(value)));
        }
        Ok(Self::Big {
            neg: value.is_sign_negative(),
            digits: abs_f64_to_digits(value.abs()),
        })
    }

    fn add_i128(self, delta: i128) -> Self {
        match self {
            Self::Small(n) => match n.checked_add(delta) {
                Some(sum) => Self::Small(sum),
                None => {
                    let mut digits = u128_to_digits(n.unsigned_abs());
                    add_u128_digits(&mut digits, delta.unsigned_abs());
                    Self::Big {
                        neg: n.is_negative(),
                        digits,
                    }
                }
            },
            Self::Big { neg, mut digits } => {
                if delta == 0 {
                    return Self::Big { neg, digits };
                }
                let mag = delta.unsigned_abs();
                if neg == delta.is_negative() {
                    add_u128_digits(&mut digits, mag);
                } else {
                    // `|digits|` is above 2^127 and `|delta|` is at most that,
                    // so the sign does not flip.
                    sub_u128_digits(&mut digits, mag);
                }
                Self::Big { neg, digits }
            }
        }
    }

    fn min_narrow(self, cap: i128) -> Result<i64, SynthError> {
        match self {
            Self::Small(n) => narrow_price(n.min(cap)),
            Self::Big { neg: false, .. } => narrow_price(cap),
            big => big.narrow(),
        }
    }

    fn max_narrow(self, floor: i128) -> Result<i64, SynthError> {
        match self {
            Self::Small(n) => narrow_price(n.max(floor)),
            Self::Big { neg: true, .. } => narrow_price(floor),
            big => big.narrow(),
        }
    }

    fn narrow(self) -> Result<i64, SynthError> {
        match self {
            Self::Small(n) => narrow_price(n),
            Self::Big { neg, digits } => Err(SynthError(format!(
                "price {}{} does not fit in a signed 64-bit integer",
                if neg { "-" } else { "" },
                format_digits(&digits),
            ))),
        }
    }
}

fn f64_to_i128_banker(value: f64) -> i128 {
    let abs = value.abs();
    if abs >= (1u64 << 53) as f64 {
        let bits = abs.to_bits();
        let exp = ((bits >> 52) & 0x7ff) as i32;
        let mant = (bits & ((1u64 << 52) - 1)) | (1u64 << 52);
        let shift = (exp - 1023 - 52) as u32;
        let mag = i128::from(mant) << shift;
        return if value.is_sign_negative() { -mag } else { mag };
    }
    let floor = value.floor();
    let frac = value - floor;
    let mut n = floor as i128;
    if frac > 0.5 || (frac == 0.5 && n % 2 != 0) {
        n += 1;
    }
    n
}

fn abs_f64_to_digits(abs: f64) -> Vec<u8> {
    let bits = abs.to_bits();
    let exp = ((bits >> 52) & 0x7ff) as i32;
    let mant = (bits & ((1u64 << 52) - 1)) | (1u64 << 52);
    let shift = (exp - 1023 - 52) as u32;
    let mut digits = u128_to_digits(u128::from(mant));
    for _ in 0..shift {
        mul2_digits(&mut digits);
    }
    digits
}

fn u128_to_digits(mut n: u128) -> Vec<u8> {
    if n == 0 {
        return vec![0];
    }
    let mut digits = Vec::new();
    while n > 0 {
        digits.push((n % 10) as u8);
        n /= 10;
    }
    digits
}

fn add_u128_digits(digits: &mut Vec<u8>, mut n: u128) {
    let mut index = 0;
    let mut carry = 0u16;
    while n > 0 || carry > 0 {
        if index == digits.len() {
            digits.push(0);
        }
        let sum = u16::from(digits[index]) + (n % 10) as u16 + carry;
        digits[index] = (sum % 10) as u8;
        carry = sum / 10;
        n /= 10;
        index += 1;
    }
}

fn sub_u128_digits(digits: &mut Vec<u8>, mut n: u128) {
    let mut index = 0;
    let mut borrow = 0u16;
    while n > 0 || borrow > 0 {
        let digit = u16::from(digits[index]);
        let sub = (n % 10) as u16 + borrow;
        if digit >= sub {
            digits[index] = (digit - sub) as u8;
            borrow = 0;
        } else {
            digits[index] = (digit + 10 - sub) as u8;
            borrow = 1;
        }
        n /= 10;
        index += 1;
    }
    while digits.len() > 1 && digits.last() == Some(&0) {
        digits.pop();
    }
}

fn mul2_digits(digits: &mut Vec<u8>) {
    let mut carry = 0u8;
    for digit in digits.iter_mut() {
        let sum = *digit * 2 + carry;
        *digit = sum % 10;
        carry = sum / 10;
    }
    if carry > 0 {
        digits.push(carry);
    }
}

fn format_digits(digits: &[u8]) -> String {
    let mut out = String::with_capacity(digits.len());
    for digit in digits.iter().rev() {
        out.push(char::from(b'0' + digit));
    }
    if out.is_empty() {
        out.push('0');
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn max_fair_rounds_to_two_to_the_63_and_a_sell_does_not_wrap() {
        let fair = i64::MAX as f64;
        let WideInt::Small(base) = WideInt::from_fair(fair).unwrap() else {
            panic!("2^63 fits in i128");
        };
        assert_eq!(base, i128::from(i64::MAX) + 1);
        assert_eq!(narrow_price(base - 1).unwrap(), i64::MAX);
        let err = narrow_price(base + 1).unwrap_err();
        assert_eq!(
            err.0,
            "price 9223372036854775809 does not fit in a signed 64-bit integer"
        );
    }

    #[test]
    fn a_buy_past_two_to_the_63_clamps_to_the_ask() {
        let fair = (i64::MAX as f64) + 4096.0;
        let ask = (1i64 << 62) + 100;
        let price = quote_price(fair, Side::Buy, 0, Some(ask), None).unwrap();
        assert_eq!(price, ask - 1);
        let err = quote_price(fair, Side::Sell, 0, None, Some(ask)).unwrap_err();
        assert!(err.0.starts_with("price 9223372036854779905"));
        assert!(err.0.contains("does not fit in a signed 64-bit integer"));
    }

    #[test]
    fn a_one_ulp_step_crosses_round_once_volatility_scales_it() {
        // Seed 3's fourth `gauss(0, 0.35)`: libm 0.2.15 is one ulp under
        // CPython on glibc. At the default volatility both samples sit in
        // the same rounding bin. Scale the underlying normal by 2^52 and
        // the bins split, which is why the reference tape calls this kernel
        // instead of the host libm.
        let libm_at_default = f64::from_bits(4_599_928_597_848_966_274);
        let glibc_at_default = f64::from_bits(4_599_928_597_848_966_275);
        assert_eq!(
            f64_to_i128_banker(libm_at_default),
            f64_to_i128_banker(glibc_at_default)
        );
        let z_libm = libm_at_default / 0.35;
        let z_glibc = glibc_at_default / 0.35;
        let sigma = 0.35 * ((1u64 << 52) as f64);
        assert_ne!(
            f64_to_i128_banker(z_libm * sigma),
            f64_to_i128_banker(z_glibc * sigma)
        );
    }

    #[test]
    fn the_default_tape_stays_clear_of_the_round_and_gap_cuts() {
        // Walks `SynthCursor`, so the symbol pick, both jitters, `gauss`, the
        // roll, and the add, cancel, or trade draws all happen before the
        // next gap. A few ulps of `libm` error sit well below 1e-8.
        let cases = [
            (vec!["S".to_string()], 4_000),
            (vec!["AAA".to_string(), "BBB".to_string()], 2_000),
            (vec!["S".to_string(), "S".to_string()], 2_000),
        ];
        for seed in [0, 3, 5, -1, 42] {
            for (symbols, n_events) in &cases {
                let mut cursor =
                    SynthCursor::open(symbols, *n_events, seed, &SynthParams::default()).unwrap();
                let mut seen = 0;
                while cursor.next_event().unwrap().is_some() {
                    seen += 1;
                    let (gap, fair) = cursor.last_raw_draw();
                    let frac = gap - (gap as i64 as f64);
                    let dist_int = frac.min(1.0 - frac);
                    assert!(
                        dist_int > 1e-8,
                        "seed {seed} gap {gap} sits {dist_int} from an integer"
                    );
                    let fair_frac = fair - fair.floor();
                    let dist_half = (fair_frac - 0.5).abs();
                    assert!(
                        dist_half > 1e-8,
                        "seed {seed} fair {fair} sits {dist_half} from a half tick"
                    );
                }
                assert_eq!(seen, *n_events);
            }
        }
    }
}
