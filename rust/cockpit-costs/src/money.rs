//! Decimal arithmetic matching CPython's default `decimal` context.
//!
//! Precision is 28 significant digits and the rounding mode is half-even, which
//! is also what ``Decimal.quantize(Decimal("0.01"))`` uses for the SEC fee.
//! Values are a sign, a base-10 coefficient, and an exponent:
//! ``(-1)^neg * int(digits) * 10^exp``.
//!
//! The context is fixed. A caller that changes precision, rounding, the exponent
//! range, clamp, or the default trap set is not computed here: ``quant.costs``
//! keeps that call on the Python implementation, which honors the active context.

use std::cmp::Ordering;

const PREC: usize = 28;

#[derive(Clone, Debug)]
pub struct Dec {
    neg: bool,
    digs: Vec<u8>,
    exp: i32,
}

impl PartialEq for Dec {
    fn eq(&self, other: &Self) -> bool {
        let a = self.clone().canon();
        let b = other.clone().canon();
        if a.is_zero() && b.is_zero() {
            return true;
        }
        a.neg == b.neg && a.digs == b.digs && a.exp == b.exp
    }
}

impl Dec {
    pub fn zero() -> Self {
        Self {
            neg: false,
            digs: vec![0],
            exp: 0,
        }
    }

    pub fn is_zero(&self) -> bool {
        self.digs.iter().all(|d| *d == 0)
    }

    pub fn parse(raw: &str) -> Result<Self, String> {
        let text = raw.trim();
        if text.is_empty() {
            return Err(format!("invalid decimal {raw}"));
        }
        let (neg, body) = if let Some(rest) = text.strip_prefix('-') {
            (true, rest)
        } else if let Some(rest) = text.strip_prefix('+') {
            (false, rest)
        } else {
            (false, text)
        };
        let (coeff, exp_adj) = match split_exp(body) {
            Some((coeff, exp)) => (coeff, exp),
            None => return Err(format!("invalid decimal {raw}")),
        };
        if coeff.is_empty() || coeff == "." {
            return Err(format!("invalid decimal {raw}"));
        }
        let (ip, fp) = match coeff.split_once('.') {
            Some((ip, fp)) => (ip, fp),
            None => (coeff, ""),
        };
        if ip.is_empty() && fp.is_empty() {
            return Err(format!("invalid decimal {raw}"));
        }
        let mut digs = Vec::new();
        for ch in ip.chars().chain(fp.chars()) {
            let Some(d) = ch.to_digit(10) else {
                return Err(format!("invalid decimal {raw}"));
            };
            digs.push(d as u8);
        }
        if digs.is_empty() {
            return Err(format!("invalid decimal {raw}"));
        }
        let exp = exp_adj - fp.len() as i32;
        Ok(Self { neg, digs, exp }.canon())
    }

    pub fn from_i64(value: i64) -> Self {
        Self::parse(&value.to_string()).expect("i64 formats as a decimal")
    }

    /// Render a form ``Decimal(text)`` in Python accepts without changing the value.
    pub fn to_plain_string(&self) -> String {
        let value = self.clone().canon();
        if value.is_zero() {
            return "0".to_string();
        }
        let digits: String = value.digs.iter().map(|d| char::from(b'0' + d)).collect();
        let body = if value.exp >= 0 {
            let mut out = digits;
            out.extend(std::iter::repeat('0').take(value.exp as usize));
            out
        } else {
            let frac = (-value.exp) as usize;
            if frac < digits.len() {
                let split = digits.len() - frac;
                format!("{}.{}", &digits[..split], &digits[split..])
            } else {
                let zeros = frac - digits.len();
                format!("0.{}{}", "0".repeat(zeros), digits)
            }
        };
        if value.neg {
            format!("-{body}")
        } else {
            body
        }
    }

    pub fn is_le_zero(&self) -> bool {
        self.is_zero() || self.neg
    }

    fn canon(mut self) -> Self {
        if self.is_zero() {
            return Self::zero();
        }
        while self.digs.first() == Some(&0) && self.digs.len() > 1 {
            self.digs.remove(0);
        }
        while self.digs.last() == Some(&0) && self.digs.len() > 1 {
            self.digs.pop();
            self.exp += 1;
        }
        if self.is_zero() {
            return Self::zero();
        }
        self
    }

    fn round_prec(mut self) -> Self {
        if self.digs.len() <= PREC {
            return self.canon();
        }
        let extra = self.digs.len() - PREC;
        let round_digit = self.digs[PREC];
        let sticky = self.digs[PREC + 1..].iter().any(|d| *d != 0);
        self.digs.truncate(PREC);
        self.exp += extra as i32;
        apply_round(&mut self, round_digit, sticky);
        self.canon()
    }

    /// Round half-even so the result's exponent is at least `target` (quantize).
    pub fn quantize_exp(&self, target: i32) -> Self {
        let value = self.clone().canon();
        if value.is_zero() {
            return Self::zero();
        }
        if value.exp >= target {
            let mut digs = value.digs;
            digs.extend(std::iter::repeat(0).take((value.exp - target) as usize));
            return Self {
                neg: value.neg,
                digs,
                exp: target,
            }
            .canon();
        }
        let drop_n = (target - value.exp) as usize;
        if drop_n >= value.digs.len() {
            let pad = drop_n - value.digs.len();
            let mut out = Self {
                neg: value.neg,
                digs: vec![0],
                exp: target,
            };
            // `pad == 0` means the leading coefficient digit is the rounding digit.
            // `pad > 0` means that digit sits strictly below a half-unit.
            let (round_digit, sticky) = if pad > 0 {
                (0, value.digs.iter().any(|d| *d != 0))
            } else {
                (value.digs[0], value.digs[1..].iter().any(|d| *d != 0))
            };
            apply_round(&mut out, round_digit, sticky);
            return out.canon();
        }
        let round_digit = value.digs[value.digs.len() - drop_n];
        let sticky = value.digs[value.digs.len() - drop_n + 1..]
            .iter()
            .any(|d| *d != 0);
        let mut digs = value.digs[..value.digs.len() - drop_n].to_vec();
        if digs.is_empty() {
            digs.push(0);
        }
        let mut out = Self {
            neg: value.neg,
            digs,
            exp: target,
        };
        apply_round(&mut out, round_digit, sticky);
        out.canon()
    }
}

fn apply_round(value: &mut Dec, round_digit: u8, sticky: bool) {
    let tie = round_digit == 5 && !sticky;
    let round_up = if tie {
        value.digs.last().copied().unwrap_or(0) % 2 == 1
    } else {
        round_digit > 5 || (round_digit == 5 && sticky)
    };
    if !round_up {
        return;
    }
    let mut carry = 1u8;
    for digit in value.digs.iter_mut().rev() {
        let sum = *digit + carry;
        *digit = sum % 10;
        carry = sum / 10;
        if carry == 0 {
            break;
        }
    }
    if carry == 1 {
        value.digs.insert(0, 1);
        // Inserting a leading 1 makes the coefficient one digit longer. If that
        // crosses the precision, the trailing 0 is an exact place shift.
        if value.digs.len() > PREC {
            value.digs.pop();
            value.exp += 1;
        }
    }
}

fn split_exp(body: &str) -> Option<(&str, i32)> {
    if let Some(pos) = body.find(['e', 'E']) {
        let exp: i32 = body[pos + 1..].parse().ok()?;
        Some((&body[..pos], exp))
    } else {
        Some((body, 0))
    }
}

pub fn add(left: &Dec, right: &Dec) -> Dec {
    if left.is_zero() {
        return right.clone().canon();
    }
    if right.is_zero() {
        return left.clone().canon();
    }
    let exp = left.exp.min(right.exp);
    let a = coeff_at(left, exp);
    let b = coeff_at(right, exp);
    let (neg, digs) = if left.neg == right.neg {
        (left.neg, add_digits(&a, &b))
    } else {
        match cmp_digits(&a, &b) {
            Ordering::Equal => return Dec::zero(),
            Ordering::Greater => (left.neg, sub_digits(&a, &b)),
            Ordering::Less => (right.neg, sub_digits(&b, &a)),
        }
    };
    Dec { neg, digs, exp }.round_prec()
}

pub fn sub(left: &Dec, right: &Dec) -> Dec {
    let mut flipped = right.clone();
    if !flipped.is_zero() {
        flipped.neg = !flipped.neg;
    }
    add(left, &flipped)
}

pub fn mul(left: &Dec, right: &Dec) -> Dec {
    if left.is_zero() || right.is_zero() {
        return Dec::zero();
    }
    let digs = mul_digits(&left.digs, &right.digs);
    let exp = left.exp + right.exp;
    let neg = left.neg ^ right.neg;
    Dec { neg, digs, exp }.round_prec()
}

pub fn div(left: &Dec, right: &Dec) -> Result<Dec, String> {
    if right.is_zero() {
        return Err("division by zero".to_string());
    }
    if left.is_zero() {
        return Ok(Dec::zero());
    }
    let neg = left.neg ^ right.neg;
    let mut need = (PREC + 4 + right.digs.len()).saturating_sub(left.digs.len());
    loop {
        let mut scaled = left.digs.clone();
        scaled.extend(std::iter::repeat(0).take(need));
        let (quot, rem) = div_digits(&scaled, &right.digs);
        let exact = rem.iter().all(|d| *d == 0);
        if exact || quot.len() >= PREC + 2 {
            let exp = left.exp - right.exp - need as i32;
            let mut out = Dec {
                neg,
                digs: quot,
                exp,
            };
            if !exact {
                // Fold a non-zero remainder into the sticky bit. Every digit past
                // PREC + 1 only feeds `sticky`, so turning a trailing 0 into a 1
                // records the remainder without changing the round digit.
                if out.digs.len() > PREC + 1 {
                    if let Some(last) = out.digs.last_mut() {
                        if *last == 0 {
                            *last = 1;
                        }
                    }
                }
            }
            return Ok(out.round_prec());
        }
        need += PREC;
        if need > 10_000 {
            return Err("division did not converge".to_string());
        }
    }
}

fn coeff_at(value: &Dec, exp: i32) -> Vec<u8> {
    debug_assert!(exp <= value.exp);
    let mut digs = value.digs.clone();
    digs.extend(std::iter::repeat(0).take((value.exp - exp) as usize));
    digs
}

fn add_digits(left: &[u8], right: &[u8]) -> Vec<u8> {
    let n = left.len().max(right.len());
    let mut out = vec![0u8; n + 1];
    let mut carry = 0u16;
    for i in 0..n {
        let a = digit_from_end(left, i);
        let b = digit_from_end(right, i);
        let sum = a as u16 + b as u16 + carry;
        out[n - i] = (sum % 10) as u8;
        carry = sum / 10;
    }
    out[0] = carry as u8;
    strip_leading(out)
}

fn sub_digits(left: &[u8], right: &[u8]) -> Vec<u8> {
    let mut out = vec![0u8; left.len()];
    let mut borrow = 0i16;
    for i in 0..left.len() {
        let a = digit_from_end(left, i) as i16;
        let b = digit_from_end(right, i) as i16;
        let mut diff = a - b - borrow;
        if diff < 0 {
            diff += 10;
            borrow = 1;
        } else {
            borrow = 0;
        }
        out[left.len() - 1 - i] = diff as u8;
    }
    strip_leading(out)
}

fn mul_digits(left: &[u8], right: &[u8]) -> Vec<u8> {
    let mut acc = vec![0u8];
    for (i, digit) in right.iter().rev().enumerate() {
        let mut prod = mul_digit(left, *digit);
        prod.extend(std::iter::repeat(0).take(i));
        acc = add_digits(&acc, &prod);
    }
    acc
}

fn mul_digit(digits: &[u8], factor: u8) -> Vec<u8> {
    if factor == 0 || digits.is_empty() {
        return vec![0];
    }
    let mut out = vec![0u8; digits.len() + 1];
    let mut carry = 0u16;
    for i in 0..digits.len() {
        let prod = digit_from_end(digits, i) as u16 * factor as u16 + carry;
        out[digits.len() - i] = (prod % 10) as u8;
        carry = prod / 10;
    }
    out[0] = carry as u8;
    strip_leading(out)
}

fn div_digits(numer: &[u8], denom: &[u8]) -> (Vec<u8>, Vec<u8>) {
    let mut acc: Vec<u8> = Vec::new();
    let mut quot: Vec<u8> = Vec::new();
    for &digit in numer {
        acc.push(digit);
        acc = strip_leading(acc);
        let (digit_q, rem) = div_one(&acc, denom);
        if digit_q > 0 || !quot.is_empty() {
            quot.push(digit_q);
        }
        acc = rem;
    }
    if quot.is_empty() {
        quot.push(0);
    }
    (quot, acc)
}

fn div_one(acc: &[u8], den: &[u8]) -> (u8, Vec<u8>) {
    if cmp_digits(acc, den) == Ordering::Less {
        return (
            0,
            if acc.is_empty() {
                vec![0]
            } else {
                acc.to_vec()
            },
        );
    }
    let mut best = 0u8;
    for q in 1..=9 {
        let prod = mul_digit(den, q);
        if cmp_digits(&prod, acc) == Ordering::Greater {
            break;
        }
        best = q;
    }
    let rem = sub_digits(acc, &mul_digit(den, best));
    (best, rem)
}

fn cmp_digits(left: &[u8], right: &[u8]) -> Ordering {
    let l = strip_view(left);
    let r = strip_view(right);
    if l.is_empty() && r.is_empty() {
        return Ordering::Equal;
    }
    l.len().cmp(&r.len()).then_with(|| l.cmp(r))
}

fn strip_view(digits: &[u8]) -> &[u8] {
    let start = digits.iter().position(|d| *d != 0).unwrap_or(digits.len());
    &digits[start..]
}

fn strip_leading(digits: Vec<u8>) -> Vec<u8> {
    let start = digits.iter().position(|d| *d != 0).unwrap_or(digits.len());
    if start >= digits.len() {
        vec![0]
    } else {
        digits[start..].to_vec()
    }
}

fn digit_from_end(digits: &[u8], index: usize) -> u8 {
    if index >= digits.len() {
        0
    } else {
        digits[digits.len() - 1 - index]
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

impl Side {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "buy" => Some(Self::Buy),
            "sell" => Some(Self::Sell),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Liquidity {
    Maker,
    Taker,
}

impl Liquidity {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "maker" => Some(Self::Maker),
            "taker" => Some(Self::Taker),
            _ => None,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Schedule {
    pub maker_rebate_per_share: Dec,
    pub taker_fee_per_share: Dec,
    pub sec_fee_rate: Dec,
    pub taf_per_share: Dec,
    pub taf_cap: Dec,
    pub clearing_per_share: Dec,
    pub borrow_rate_annual: Dec,
}

impl Schedule {
    pub fn parse(
        maker: &str,
        taker: &str,
        sec: &str,
        taf: &str,
        cap: &str,
        clearing: &str,
        borrow: &str,
    ) -> Result<Self, String> {
        Ok(Self {
            maker_rebate_per_share: Dec::parse(maker)?,
            taker_fee_per_share: Dec::parse(taker)?,
            sec_fee_rate: Dec::parse(sec)?,
            taf_per_share: Dec::parse(taf)?,
            taf_cap: Dec::parse(cap)?,
            clearing_per_share: Dec::parse(clearing)?,
            borrow_rate_annual: Dec::parse(borrow)?,
        })
    }
}

#[derive(Clone, Debug)]
pub struct Pnl {
    pub spread_capture: Dec,
    pub inventory_drift: Dec,
    pub fees: Dec,
    pub realized_notional: Dec,
    pub position: i64,
    pub fills: i64,
    pub maker_fills: i64,
    pub taker_fills: i64,
    pub last_mid: Option<Dec>,
    pub last_ts: Option<i64>,
    pub cash: Dec,
    schedule: Schedule,
}

#[derive(Clone, Debug)]
pub enum PnlEvent {
    Mark {
        mid_ticks: String,
        ts: Option<i64>,
        elapsed: Option<String>,
    },
    Fill {
        side_sell: bool,
        qty: i64,
        price_ticks: i64,
        maker: bool,
        signed_qty: i64,
        ingest_ts: Option<i64>,
        mid_ticks: Option<String>,
        elapsed: Option<String>,
    },
}

pub fn fee_for(
    schedule: &Schedule,
    side: Side,
    qty: i64,
    price: &Dec,
    liquidity: Liquidity,
) -> Result<Dec, String> {
    if qty <= 0 {
        return Err(format!("fee_for needs a positive qty, got {qty}"));
    }
    let shares = Dec::from_i64(qty);
    let notional = mul(price, &shares);
    let exchange = if liquidity == Liquidity::Maker {
        mul(&neg(&schedule.maker_rebate_per_share), &shares)
    } else {
        mul(&schedule.taker_fee_per_share, &shares)
    };
    let mut regulatory = Dec::zero();
    if side == Side::Sell {
        let sec = mul(&notional, &schedule.sec_fee_rate).quantize_exp(-2);
        let taf_raw = mul(&schedule.taf_per_share, &shares);
        let taf = if cmp(&taf_raw, &schedule.taf_cap) == Ordering::Less {
            taf_raw
        } else {
            schedule.taf_cap.clone()
        };
        regulatory = add(&sec, &taf);
    }
    Ok(add(
        &add(&exchange, &regulatory),
        &mul(&schedule.clearing_per_share, &shares),
    ))
}

pub fn borrow_cost(rate: &Dec, short_notional: &Dec, seconds: &str) -> Result<Dec, String> {
    let seconds_dec = Dec::parse(seconds)?;
    if short_notional.is_le_zero() || seconds_dec.is_le_zero() {
        return Ok(Dec::zero());
    }
    let year = Dec::from_i64(365 * 24 * 3600);
    div(&mul(&mul(short_notional, rate), &seconds_dec), &year)
}

pub fn cost_bps(cost: &Dec, notional: &Dec) -> Result<Dec, String> {
    if notional.is_le_zero() {
        return Ok(Dec::zero());
    }
    Ok(mul(&div(cost, notional)?, &Dec::from_i64(10_000)))
}

fn neg(value: &Dec) -> Dec {
    let mut out = value.clone();
    if !out.is_zero() {
        out.neg = !out.neg;
    }
    out
}

fn cmp(left: &Dec, right: &Dec) -> Ordering {
    if left.is_zero() && right.is_zero() {
        return Ordering::Equal;
    }
    if left.neg != right.neg {
        return if left.neg {
            Ordering::Less
        } else {
            Ordering::Greater
        };
    }
    let exp = left.exp.min(right.exp);
    let ordering = cmp_digits(&coeff_at(left, exp), &coeff_at(right, exp));
    if left.neg {
        ordering.reverse()
    } else {
        ordering
    }
}

impl Pnl {
    pub fn new(schedule: Schedule) -> Self {
        Self {
            spread_capture: Dec::zero(),
            inventory_drift: Dec::zero(),
            fees: Dec::zero(),
            realized_notional: Dec::zero(),
            position: 0,
            fills: 0,
            maker_fills: 0,
            taker_fills: 0,
            last_mid: None,
            last_ts: None,
            cash: Dec::zero(),
            schedule,
        }
    }

    pub fn mark(
        &mut self,
        mid_ticks: &str,
        tick_size: &str,
        ts: Option<i64>,
        elapsed: Option<&str>,
    ) -> Result<(), String> {
        let mid = mul(&Dec::parse(mid_ticks)?, &Dec::parse(tick_size)?);
        if let Some(last) = self.last_mid.clone() {
            if self.position != 0 {
                let delta = mul(&Dec::from_i64(self.position), &sub(&mid, &last));
                self.inventory_drift = add(&self.inventory_drift, &delta);
                if self.position < 0 && ts.is_some() && self.last_ts.is_some() {
                    if let Some(elapsed) = elapsed {
                        let short_notional = mul(&Dec::from_i64(-self.position), &mid);
                        let carry = borrow_cost(
                            &self.schedule.borrow_rate_annual,
                            &short_notional,
                            elapsed,
                        )?;
                        self.fees = add(&self.fees, &carry);
                    }
                }
            }
        }
        self.last_mid = Some(mid);
        self.last_ts = ts;
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    pub fn on_fill(
        &mut self,
        side_sell: bool,
        qty: i64,
        price_ticks: i64,
        maker: bool,
        signed_qty: i64,
        ingest_ts: Option<i64>,
        mid_ticks: Option<&str>,
        tick_size: &str,
        elapsed: Option<&str>,
    ) -> Result<(), String> {
        let new_position = self
            .position
            .checked_add(signed_qty)
            .ok_or_else(|| "position overflow".to_string())?;
        let price = mul(&Dec::from_i64(price_ticks), &Dec::parse(tick_size)?);
        let side = if side_sell { Side::Sell } else { Side::Buy };
        let liquidity = if maker {
            Liquidity::Maker
        } else {
            Liquidity::Taker
        };
        let fee = fee_for(&self.schedule, side, qty, &price, liquidity)?;
        if let Some(mid_ticks) = mid_ticks {
            self.mark(mid_ticks, tick_size, ingest_ts, elapsed)?;
        }
        if let Some(last) = self.last_mid.clone() {
            let edge = mul(&sub(&last, &price), &Dec::from_i64(signed_qty));
            self.spread_capture = add(&self.spread_capture, &edge);
        }
        self.fees = add(&self.fees, &fee);
        self.realized_notional = add(&self.realized_notional, &mul(&price, &Dec::from_i64(qty)));
        self.cash = sub(&self.cash, &mul(&price, &Dec::from_i64(signed_qty)));
        self.position = new_position;
        self.fills += 1;
        if maker {
            self.maker_fills += 1;
        } else {
            self.taker_fills += 1;
        }
        Ok(())
    }

    pub fn mark_to_market(&self) -> Dec {
        let mid = self.last_mid.clone().unwrap_or_else(Dec::zero);
        add(&self.cash, &mul(&Dec::from_i64(self.position), &mid))
    }

    pub fn gross(&self) -> Dec {
        add(&self.spread_capture, &self.inventory_drift)
    }

    pub fn apply(&mut self, event: &PnlEvent, tick_size: &str) -> Result<(), String> {
        match event {
            PnlEvent::Mark {
                mid_ticks,
                ts,
                elapsed,
            } => self.mark(mid_ticks, tick_size, *ts, elapsed.as_deref()),
            PnlEvent::Fill {
                side_sell,
                qty,
                price_ticks,
                maker,
                signed_qty,
                ingest_ts,
                mid_ticks,
                elapsed,
            } => self.on_fill(
                *side_sell,
                *qty,
                *price_ticks,
                *maker,
                *signed_qty,
                *ingest_ts,
                mid_ticks.as_deref(),
                tick_size,
                elapsed.as_deref(),
            ),
        }
    }
}

pub fn fee_for_many(
    schedule: &Schedule,
    rows: &[(Side, i64, Dec, Liquidity)],
) -> Result<Vec<Dec>, String> {
    rows.iter()
        .map(|(side, qty, price, liquidity)| fee_for(schedule, *side, *qty, price, *liquidity))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sched() -> Schedule {
        Schedule::parse(
            "0.0020",
            "0.0030",
            "0.0000278",
            "0.000166",
            "8.30",
            "0.0002",
            "0.005",
        )
        .unwrap()
    }

    fn d(raw: &str) -> Dec {
        Dec::parse(raw).unwrap()
    }

    #[test]
    fn parses_python_float_text() {
        assert_eq!(d("1e-09"), d("0.000000001"));
        assert_eq!(d("3600.0"), d("3600"));
        assert_eq!(d("-1.80"), d("-1.8"));
    }

    #[test]
    fn maker_rebate_is_negative() {
        let fee = fee_for(&sched(), Side::Buy, 1_000, &d("100.00"), Liquidity::Maker).unwrap();
        assert_eq!(fee, d("-1.80"));
    }

    #[test]
    fn taker_fee_matches_python() {
        let fee = fee_for(&sched(), Side::Buy, 1_000, &d("100.00"), Liquidity::Taker).unwrap();
        assert_eq!(fee, d("3.20"));
    }

    #[test]
    fn sell_regulatory_leg_matches_python() {
        let sell = fee_for(&sched(), Side::Sell, 1_000, &d("100.00"), Liquidity::Taker).unwrap();
        let buy = fee_for(&sched(), Side::Buy, 1_000, &d("100.00"), Liquidity::Taker).unwrap();
        assert_eq!(sub(&sell, &buy), add(&d("2.78"), &d("0.166")));
    }

    #[test]
    fn sec_half_even_matches_python_quantize() {
        let reg = |qty| {
            let sell = fee_for(&sched(), Side::Sell, qty, &d("1"), Liquidity::Maker).unwrap();
            let buy = fee_for(&sched(), Side::Buy, qty, &d("1"), Liquidity::Maker).unwrap();
            sub(&sell, &buy)
        };
        assert_eq!(
            reg(25_000),
            add(&d("0.70"), &mul(&d("0.000166"), &d("25000")))
        );
        // 75000 * 0.000166 = 12.45, which is above the $8.30 TAF cap.
        assert_eq!(reg(75_000), add(&d("2.08"), &d("8.30")));
    }

    #[test]
    fn quantize_half_even_ties() {
        assert_eq!(d("0.005").quantize_exp(-2), d("0.00"));
        assert_eq!(d("0.015").quantize_exp(-2), d("0.02"));
        assert_eq!(d("2.085").quantize_exp(-2), d("2.08"));
        assert_eq!(d("0.695").quantize_exp(-2), d("0.70"));
    }

    #[test]
    fn division_matches_python_default_context() {
        assert_eq!(
            div(&d("1"), &d("3")).unwrap(),
            d("0.3333333333333333333333333333")
        );
        assert_eq!(
            div(&d("2"), &d("3")).unwrap(),
            d("0.6666666666666666666666666667")
        );
        assert_eq!(
            div(&d("1"), &d("6")).unwrap(),
            d("0.1666666666666666666666666667")
        );
        assert_eq!(div(&d("1"), &d("2")).unwrap(), d("0.5"));
        assert_eq!(
            cost_bps(&d("1"), &d("3")).unwrap(),
            d("3333.333333333333333333333333")
        );
    }

    #[test]
    fn borrow_one_hour_matches_python_decimal() {
        let cost = borrow_cost(&d("0.005"), &d("1000000"), "3600").unwrap();
        assert_eq!(cost, d("0.5707762557077625570776255708"));
        let twice = borrow_cost(&d("0.005"), &d("1000000"), "7200").unwrap();
        assert_eq!(twice, mul(&cost, &d("2")));
        assert_eq!(
            borrow_cost(&d("0.005"), &d("123.45"), "0.001").unwrap(),
            d("1.957286910197869101978691020E-11")
        );
    }

    #[test]
    fn flat_or_long_borrow_is_zero() {
        assert_eq!(borrow_cost(&d("0.005"), &d("0"), "3600").unwrap(), d("0"));
        assert_eq!(
            borrow_cost(&d("0.005"), &d("-500"), "3600").unwrap(),
            d("0")
        );
        assert_eq!(borrow_cost(&d("0.005"), &d("10"), "0").unwrap(), d("0"));
    }

    #[test]
    fn hand_pnl_identity_holds() {
        let mut pnl = Pnl::new(Schedule::parse("0", "0", "0", "0", "0", "0", "0").unwrap());
        pnl.mark("10000.0", "0.01", Some(0), None).unwrap();
        pnl.on_fill(false, 200, 9_999, true, 200, None, None, "0.01", None)
            .unwrap();
        pnl.mark("10004.0", "0.01", Some(1), Some("1e-09")).unwrap();
        pnl.on_fill(true, 100, 10_006, true, -100, None, None, "0.01", None)
            .unwrap();
        pnl.mark("10002.0", "0.01", Some(2), Some("1e-09")).unwrap();
        assert_eq!(pnl.gross(), pnl.mark_to_market());
        assert_eq!(pnl.inventory_drift, d("6"));
    }

    #[test]
    fn overflow_on_fill_does_not_mutate_the_ledger() {
        let mut pnl = Pnl::new(Schedule::parse("0", "0.003", "0", "0", "0", "0", "0").unwrap());
        pnl.mark("100", "0.01", Some(0), None).unwrap();
        pnl.on_fill(
            false,
            1 << 62,
            100,
            false,
            1 << 62,
            Some(0),
            Some("100"),
            "0.01",
            None,
        )
        .unwrap();
        let fees_before = pnl.fees.clone();
        let notional_before = pnl.realized_notional.clone();
        let cash_before = pnl.cash.clone();
        let spread_before = pnl.spread_capture.clone();
        let last_mid_before = pnl.last_mid.clone();
        let position_before = pnl.position;
        let fills_before = pnl.fills;
        let err = pnl
            .on_fill(
                false,
                1 << 62,
                102,
                false,
                1 << 62,
                Some(1),
                Some("102"),
                "0.01",
                None,
            )
            .unwrap_err();
        assert!(err.contains("overflow"), "{err}");
        assert_eq!(pnl.position, position_before);
        assert_eq!(pnl.fills, fills_before);
        assert_eq!(pnl.fees, fees_before);
        assert_eq!(pnl.realized_notional, notional_before);
        assert_eq!(pnl.cash, cash_before);
        assert_eq!(pnl.spread_capture, spread_before);
        assert_eq!(pnl.last_mid, last_mid_before);
    }

    #[test]
    fn batch_fee_matches_single_calls() {
        let rows = vec![
            (Side::Buy, 1_000, d("100"), Liquidity::Maker),
            (Side::Sell, 10_000_000, d("1"), Liquidity::Taker),
        ];
        let many = fee_for_many(&sched(), &rows).unwrap();
        for (row, got) in rows.iter().zip(many) {
            assert_eq!(fee_for(&sched(), row.0, row.1, &row.2, row.3).unwrap(), got);
        }
    }
}
