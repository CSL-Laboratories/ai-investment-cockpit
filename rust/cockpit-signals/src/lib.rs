//! Online estimators from `app/quant/signals.py`.
//!
//! Each one sees one observation at a time. A cold estimator returns `None`
//! rather than a number that would read as confidence. Windows are recomputed
//! from the retained points, oldest first, so the float sums follow Python's
//! `sum`.
//!
//! Signed flow quantities are signed 64-bit. The running net and gross are
//! wider (`i128`) so a window of in-range prints does not wrap.

#![forbid(unsafe_code)]

use std::collections::VecDeque;

#[derive(Clone, Debug)]
pub struct EwmaVolatility {
    lam: f64,
    variance: f64,
    last: Option<f64>,
    count: u64,
}

impl EwmaVolatility {
    pub fn new(lam: f64) -> Self {
        Self {
            lam,
            variance: 0.0,
            last: None,
            count: 0,
        }
    }

    pub fn update(&mut self, price: f64) -> Result<Option<f64>, &'static str> {
        if price <= 0.0 {
            return self.value();
        }
        let Some(last) = self.last else {
            self.last = Some(price);
            return Ok(None);
        };
        // ``math.log`` raises on a non-positive ratio. A non-finite ratio would
        // publish an infinite volatility, so it is the same error.
        let ratio = price / last;
        if !(ratio.is_finite() && ratio > 0.0) {
            return Err("math domain error");
        }
        let ret = ratio.ln();
        if !ret.is_finite() {
            return Err("math domain error");
        }
        self.last = Some(price);
        self.count = self.count.saturating_add(1);
        let decay = 1.0 - self.lam;
        self.variance = self.lam * self.variance + decay * ret * ret;
        self.value()
    }

    pub fn lam(&self) -> f64 {
        self.lam
    }

    pub fn variance(&self) -> f64 {
        self.variance
    }

    pub fn last(&self) -> Option<f64> {
        self.last
    }

    pub fn count(&self) -> u64 {
        self.count
    }

    pub fn value(&self) -> Result<Option<f64>, &'static str> {
        if self.count < 2 {
            return Ok(None);
        }
        if self.variance < 0.0 {
            return Err("math domain error");
        }
        let vol = self.variance.sqrt();
        // ``math.sqrt(nan)`` is nan. An infinite volatility is refused.
        if vol.is_infinite() {
            return Err("math domain error");
        }
        Ok(Some(vol))
    }

    pub fn is_warm(&self) -> bool {
        self.count >= 2
    }
}

#[derive(Clone, Debug)]
pub struct RollingZScore {
    window: usize,
    compensated: bool,
    values: VecDeque<f64>,
}

impl RollingZScore {
    pub fn new(window: usize, compensated: bool) -> Self {
        assert!(window >= 2, "window must be >= 2");
        Self {
            window,
            compensated,
            values: VecDeque::with_capacity(window.min(4096)),
        }
    }

    pub fn update(&mut self, value: f64) -> Option<f64> {
        if self.values.len() == self.window {
            self.values.pop_front();
        }
        self.values.push_back(value);
        self.value()
    }

    pub fn is_warm(&self) -> bool {
        self.values.len() >= self.window
    }

    pub fn mean(&self) -> Option<f64> {
        if self.values.is_empty() {
            return None;
        }
        Some(sum_f64(&self.values, self.compensated) / self.values.len() as f64)
    }

    pub fn stdev(&self) -> Option<f64> {
        let n = self.values.len();
        if n < 2 {
            return None;
        }
        let mu = self.mean()?;
        let acc = sum_mapped(self.values.iter().copied(), self.compensated, |value| {
            let delta = value - mu;
            delta * delta
        });
        Some((acc / (n as f64 - 1.0)).sqrt())
    }

    pub fn value(&self) -> Option<f64> {
        if !self.is_warm() {
            return None;
        }
        let sd = self.stdev()?;
        if sd <= 0.0 {
            return None;
        }
        let mean = self.mean()?;
        Some((self.values.back().copied().unwrap_or(0.0) - mean) / sd)
    }

    pub fn samples(&self) -> Vec<f64> {
        self.values.iter().copied().collect()
    }
}

#[derive(Clone, Debug)]
pub struct OrderFlowImbalance {
    window: usize,
    flow: VecDeque<i64>,
    net: i128,
    gross: i128,
}

impl OrderFlowImbalance {
    pub fn new(window: usize) -> Self {
        assert!(window >= 1, "window must be >= 1");
        Self {
            window,
            flow: VecDeque::with_capacity(window.min(4096)),
            net: 0,
            gross: 0,
        }
    }

    pub fn update(&mut self, signed_qty: i64) -> Result<f64, &'static str> {
        // Apply the window slide only after both totals fit, so a refusal
        // leaves the estimator where it was.
        let removing = if self.flow.len() == self.window {
            self.flow.front().copied()
        } else {
            None
        };
        let mut net = self.net;
        let mut gross = self.gross;
        if let Some(old) = removing {
            net = fit(net, -i128::from(old))?;
            gross = fit(gross, -magnitude(old))?;
        }
        net = fit(net, i128::from(signed_qty))?;
        gross = fit(gross, magnitude(signed_qty))?;
        if removing.is_some() {
            self.flow.pop_front();
        }
        self.flow.push_back(signed_qty);
        self.net = net;
        self.gross = gross;
        Ok(self.value())
    }

    pub fn value(&self) -> f64 {
        if self.gross == 0 {
            return 0.0;
        }
        // |net| <= gross, so the ratio is in [-1, 1]. Python divides the two
        // ints with round-ties-to-even; casting each total to f64 first is a
        // different rounding once either total exceeds 2^53.
        ratio_f64(self.net, self.gross)
    }

    pub fn quantities(&self) -> Vec<i64> {
        self.flow.iter().copied().collect()
    }

    pub fn is_warm(&self) -> bool {
        self.flow.len() >= self.window
    }
}

#[derive(Clone, Debug)]
pub struct RollingBeta {
    window: usize,
    compensated: bool,
    xs: VecDeque<f64>,
    ys: VecDeque<f64>,
}

impl RollingBeta {
    pub fn new(window: usize, compensated: bool) -> Self {
        assert!(window >= 3, "window must be >= 3");
        Self {
            window,
            compensated,
            xs: VecDeque::with_capacity(window.min(4096)),
            ys: VecDeque::with_capacity(window.min(4096)),
        }
    }

    pub fn update(&mut self, x: f64, y: f64) -> Option<f64> {
        if self.xs.len() == self.window {
            self.xs.pop_front();
            self.ys.pop_front();
        }
        self.xs.push_back(x);
        self.ys.push_back(y);
        self.beta()
    }

    pub fn is_warm(&self) -> bool {
        self.xs.len() >= self.window
    }

    pub fn beta(&self) -> Option<f64> {
        let n = self.xs.len();
        if n < 3 {
            return None;
        }
        let mx = sum_f64(&self.xs, self.compensated) / n as f64;
        let my = sum_f64(&self.ys, self.compensated) / n as f64;
        let var = sum_mapped(self.xs.iter().copied(), self.compensated, |x| {
            let dx = x - mx;
            dx * dx
        });
        let cov = sum_mapped(
            self.xs.iter().copied().zip(self.ys.iter().copied()),
            self.compensated,
            |(x, y)| (x - mx) * (y - my),
        );
        if var <= 0.0 {
            None
        } else {
            Some(cov / var)
        }
    }

    pub fn alpha(&self) -> Option<f64> {
        let beta = self.beta()?;
        let n = self.xs.len() as f64;
        Some(
            sum_f64(&self.ys, self.compensated) / n
                - beta * (sum_f64(&self.xs, self.compensated) / n),
        )
    }

    pub fn residual(&self, x: f64, y: f64) -> Option<f64> {
        let beta = self.beta()?;
        let alpha = self.alpha()?;
        Some(y - (alpha + beta * x))
    }

    pub fn xs(&self) -> Vec<f64> {
        self.xs.iter().copied().collect()
    }

    pub fn ys(&self) -> Vec<f64> {
        self.ys.iter().copied().collect()
    }
}

fn sum_f64(values: &VecDeque<f64>, compensated: bool) -> f64 {
    sum_mapped(values.iter().copied(), compensated, |value| value)
}

/// Python 3.12 ``sum`` uses Neumaier compensation. 3.11 adds left to right.
fn sum_mapped<T, I, F>(values: I, compensated: bool, mut term: F) -> f64
where
    I: IntoIterator<Item = T>,
    F: FnMut(T) -> f64,
{
    if !compensated {
        let mut total = 0.0;
        for value in values {
            total += term(value);
        }
        return total;
    }
    let mut sum = 0.0;
    let mut correction = 0.0;
    for value in values {
        let value = term(value);
        let next = sum + value;
        if sum.abs() >= value.abs() {
            correction += (sum - next) + value;
        } else {
            correction += (value - next) + sum;
        }
        sum = next;
    }
    sum + correction
}

fn magnitude(qty: i64) -> i128 {
    i128::from(qty).unsigned_abs() as i128
}

/// Nearest `f64` to `net/gross`, ties to even. `gross` is positive and `|net| <= gross`.
fn ratio_f64(net: i128, gross: i128) -> f64 {
    debug_assert!(gross > 0);
    if net == 0 {
        return 0.0;
    }
    const EXACT: i128 = 1 << 53;
    let negative = net < 0;
    let numer = net.unsigned_abs();
    let denom = gross.unsigned_abs();
    if numer <= (1u128 << 53) && gross <= EXACT {
        return (net as f64) / (gross as f64);
    }
    let n_bits = 128 - numer.leading_zeros();
    let d_bits = 128 - denom.leading_zeros();
    // `|net| <= gross`, so this stays non-negative. Add before subtracting:
    // `64 - n_bits` underflows once the numerator has more than 64 bits.
    let shift = (64 + d_bits) - n_bits;
    let (quot, rem_nonzero) = div_shifted(numer, denom, shift);
    let Some(mut bits) = f64_from_ratio_bits(quot, rem_nonzero, shift) else {
        return if negative {
            f64::NEG_INFINITY
        } else {
            f64::INFINITY
        };
    };
    if negative {
        bits |= 1 << 63;
    }
    f64::from_bits(bits)
}

fn div_shifted(numer: u128, denom: u128, shift: u32) -> (u128, bool) {
    let (hi, lo) = shl_u192(numer, shift);
    let (quot, rem) = div_u192(hi, lo, denom);
    (quot, rem != 0)
}

/// `numer << shift` as a 192-bit value `(hi, lo)`, with `hi` holding bits 128..192.
fn shl_u192(numer: u128, shift: u32) -> (u64, u128) {
    if shift >= 128 {
        let moved = numer << (shift - 128);
        return (moved as u64, 0);
    }
    if shift == 0 {
        return (0, numer);
    }
    let hi = (numer >> (128 - shift)) as u64;
    let lo = numer << shift;
    (hi, lo)
}

fn div_u192(hi: u64, lo: u128, denom: u128) -> (u128, u128) {
    if hi == 0 {
        return (lo / denom, lo % denom);
    }
    let mut rem_hi: u64 = 0;
    let mut rem: u128 = 0;
    let mut quot: u128 = 0;
    for bit in (0..192).rev() {
        let carry = rem >> 127;
        rem <<= 1;
        rem_hi = (rem_hi << 1) | u64::from(carry != 0);
        let incoming = if bit >= 128 {
            (hi >> (bit - 128)) & 1
        } else {
            u64::from((lo >> bit) & 1 == 1)
        };
        rem |= u128::from(incoming);
        if rem_hi > 0 || rem >= denom {
            if rem_hi == 0 {
                rem -= denom;
            } else {
                rem = rem.wrapping_sub(denom);
                rem_hi -= 1;
            }
            if bit < 128 {
                quot |= 1u128 << bit;
            }
        }
    }
    (quot, rem)
}

fn f64_from_ratio_bits(quot: u128, rem_nonzero: bool, shift: u32) -> Option<u64> {
    if quot == 0 {
        return Some(0);
    }
    let q_bits = 128 - quot.leading_zeros();
    let mut exp_unbiased = q_bits as i32 - 1 - shift as i32;
    let excess = q_bits as i32 - 53;
    let (mut sig, round_up) = if excess <= 0 {
        (quot << excess.unsigned_abs(), false)
    } else {
        let sig = quot >> excess as u32;
        let round_shift = excess as u32 - 1;
        let round_bit = ((quot >> round_shift) & 1) == 1;
        let sticky = rem_nonzero || (round_shift > 0 && (quot & ((1u128 << round_shift) - 1)) != 0);
        let round_up = round_bit && (sticky || (sig & 1) == 1);
        (sig, round_up)
    };
    if round_up {
        sig += 1;
        if sig == (1u128 << 53) {
            sig >>= 1;
            exp_unbiased += 1;
        }
    }
    if !(-1022..=1023).contains(&exp_unbiased) {
        return None;
    }
    let frac = (sig as u64) & ((1u64 << 52) - 1);
    let biased = (exp_unbiased + 1023) as u64;
    Some((biased << 52) | frac)
}

fn fit(left: i128, right: i128) -> Result<i128, &'static str> {
    left.checked_add(right)
        .ok_or("flow total does not fit in a signed 128-bit integer")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ewma_stays_cold_for_the_first_two_prices() {
        let mut vol = EwmaVolatility::new(0.94);
        assert!(vol.update(100.0).unwrap().is_none());
        assert!(vol.update(101.0).unwrap().is_none());
        assert!(vol.update(100.5).unwrap().unwrap() > 0.0);
        assert!(vol.is_warm());
        assert!(vol.update(f64::INFINITY).is_err());
        assert!(vol.value().unwrap().is_some());
    }

    #[test]
    fn zscore_of_a_constant_window_is_none() {
        let mut z = RollingZScore::new(5, true);
        for _ in 0..5 {
            z.update(3.0);
        }
        assert_eq!(z.stdev(), Some(0.0));
        assert!(z.value().is_none());
    }

    #[test]
    fn ofi_sign_follows_the_aggressor() {
        let mut buys = OrderFlowImbalance::new(4);
        let mut sells = OrderFlowImbalance::new(4);
        for _ in 0..4 {
            assert_eq!(buys.update(100).unwrap(), 1.0);
            assert_eq!(sells.update(-100).unwrap(), -1.0);
        }
    }

    #[test]
    fn neumaier_sum_matches_cpython_3_12() {
        let repeated = std::iter::repeat(100.37).take(200);
        let total = sum_mapped(repeated, true, |value| value);
        assert_eq!(total, 20074.0);
        let partial = [1e16, 1.0, -1e16, 2.0, 3.0];
        assert_eq!(sum_mapped(partial, true, |value| value), 6.0);
        assert_eq!(sum_mapped(partial, false, |value| value), 5.0);
    }

    #[test]
    fn ratio_matches_python_true_division() {
        // (net, gross, bits of Python `net / gross`).
        let vectors = [
            (
                9007199254740991i128,
                9007199254740993i128,
                0x3feffffffffffffeu64,
            ),
            (-9007199254740991, 9007199254740993, 0xbfeffffffffffffe),
            (1, 3, 0x3fd5555555555555),
            (2, 3, 0x3fe5555555555555),
            (-2, 3, 0xbfe5555555555555),
            (4611686018427387904, 9223372036854775807, 0x3fe0000000000000),
            (
                -9223372036854775808,
                9223372036854775808,
                0xbff0000000000000,
            ),
            (1, 1267650600228229401496703205379, 0x39b0000000000000),
            (
                1208925819614629174718521,
                2417851639229258349412451,
                0x3fe0000000000000,
            ),
            (0, 5, 0),
            (9007199254740992, 9007199254740995, 0x3feffffffffffffd),
            (
                -4611686018427387911,
                4611686018427387913,
                0xbff0000000000000,
            ),
            (1000000000000000000, 1000000000000000007, 0x3ff0000000000000),
            (
                1,
                170141183460469231731687303715884105727,
                0x3800000000000000,
            ),
        ];
        for (net, gross, bits) in vectors {
            assert_eq!(
                ratio_f64(net, gross).to_bits(),
                bits,
                "net={net} gross={gross}"
            );
        }
    }

    #[test]
    fn beta_recovers_a_line() {
        let mut beta = RollingBeta::new(20, true);
        for i in 0..20 {
            let x = i as f64;
            beta.update(x, 3.0 + 2.5 * x);
        }
        assert!((beta.beta().unwrap() - 2.5).abs() < 1e-9);
        assert!((beta.alpha().unwrap() - 3.0).abs() < 1e-9);
    }
}
