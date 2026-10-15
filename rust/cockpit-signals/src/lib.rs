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
    count: u32,
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

    pub fn update(&mut self, price: f64) -> Option<f64> {
        if price <= 0.0 {
            return self.value();
        }
        let Some(last) = self.last else {
            self.last = Some(price);
            return None;
        };
        let ret = (price / last).ln();
        self.last = Some(price);
        self.count += 1;
        let decay = 1.0 - self.lam;
        self.variance = self.lam * self.variance + decay * ret * ret;
        self.value()
    }

    pub fn value(&self) -> Option<f64> {
        if self.count < 2 {
            None
        } else {
            Some(self.variance.sqrt())
        }
    }

    pub fn is_warm(&self) -> bool {
        self.count >= 2
    }
}

#[derive(Clone, Debug)]
pub struct RollingZScore {
    window: usize,
    values: VecDeque<f64>,
}

impl RollingZScore {
    pub fn new(window: usize) -> Self {
        Self {
            window,
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
        Some(sum_f64(&self.values) / self.values.len() as f64)
    }

    pub fn stdev(&self) -> Option<f64> {
        let n = self.values.len();
        if n < 2 {
            return None;
        }
        let mu = self.mean()?;
        let mut acc = 0.0;
        for value in &self.values {
            let delta = value - mu;
            acc += delta * delta;
        }
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
        Self {
            window,
            flow: VecDeque::with_capacity(window.min(4096)),
            net: 0,
            gross: 0,
        }
    }

    pub fn update(&mut self, signed_qty: i64) -> Result<f64, &'static str> {
        if self.flow.len() == self.window {
            if let Some(old) = self.flow.pop_front() {
                self.remove(old)?;
            }
        }
        self.push(signed_qty)?;
        self.flow.push_back(signed_qty);
        Ok(self.value())
    }

    pub fn value(&self) -> f64 {
        if self.gross == 0 {
            return 0.0;
        }
        self.net as f64 / self.gross as f64
    }

    pub fn is_warm(&self) -> bool {
        self.flow.len() >= self.window
    }

    fn push(&mut self, qty: i64) -> Result<(), &'static str> {
        self.net = fit(self.net, i128::from(qty))?;
        self.gross = fit(self.gross, magnitude(qty))?;
        Ok(())
    }

    fn remove(&mut self, qty: i64) -> Result<(), &'static str> {
        self.net = fit(self.net, -i128::from(qty))?;
        self.gross = fit(self.gross, -magnitude(qty))?;
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct RollingBeta {
    window: usize,
    xs: VecDeque<f64>,
    ys: VecDeque<f64>,
}

impl RollingBeta {
    pub fn new(window: usize) -> Self {
        Self {
            window,
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
        let mx = sum_f64(&self.xs) / n as f64;
        let my = sum_f64(&self.ys) / n as f64;
        let mut var = 0.0;
        let mut cov = 0.0;
        for (x, y) in self.xs.iter().zip(self.ys.iter()) {
            let dx = x - mx;
            var += dx * dx;
            cov += dx * (y - my);
        }
        if var <= 0.0 {
            None
        } else {
            Some(cov / var)
        }
    }

    pub fn alpha(&self) -> Option<f64> {
        let beta = self.beta()?;
        let n = self.xs.len() as f64;
        Some(sum_f64(&self.ys) / n - beta * (sum_f64(&self.xs) / n))
    }

    pub fn residual(&self, x: f64, y: f64) -> Option<f64> {
        let beta = self.beta()?;
        let alpha = self.alpha()?;
        Some(y - (alpha + beta * x))
    }
}

fn sum_f64(values: &VecDeque<f64>) -> f64 {
    let mut total = 0.0;
    for value in values {
        total += value;
    }
    total
}

fn magnitude(qty: i64) -> i128 {
    i128::from(qty).abs()
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
        assert!(vol.update(100.0).is_none());
        assert!(vol.update(101.0).is_none());
        assert!(vol.update(100.5).unwrap() > 0.0);
        assert!(vol.is_warm());
    }

    #[test]
    fn zscore_of_a_constant_window_is_none() {
        let mut z = RollingZScore::new(5);
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
    fn beta_recovers_a_line() {
        let mut beta = RollingBeta::new(20);
        for i in 0..20 {
            let x = i as f64;
            beta.update(x, 3.0 + 2.5 * x);
        }
        assert!((beta.beta().unwrap() - 2.5).abs() < 1e-9);
        assert!((beta.alpha().unwrap() - 3.0).abs() < 1e-9);
    }
}
