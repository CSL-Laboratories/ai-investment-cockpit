//! PyO3 face of the online estimators. State stays in Rust across updates.

use cockpit_signals::{EwmaVolatility, OrderFlowImbalance, RollingBeta, RollingZScore};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn invalid(message: &str) -> PyErr {
    PyValueError::new_err(message.to_string())
}

#[pyclass]
struct EwmaCore {
    inner: EwmaVolatility,
}

#[pymethods]
impl EwmaCore {
    #[new]
    fn new(lam: f64) -> Self {
        Self {
            inner: EwmaVolatility::new(lam),
        }
    }

    fn update(&mut self, price: f64) -> Option<f64> {
        self.inner.update(price)
    }

    fn feed(&mut self, prices: Vec<f64>) -> Vec<Option<f64>> {
        prices
            .into_iter()
            .map(|price| self.inner.update(price))
            .collect()
    }

    #[getter]
    fn value(&self) -> Option<f64> {
        self.inner.value()
    }

    #[getter]
    fn is_warm(&self) -> bool {
        self.inner.is_warm()
    }
}

#[pyclass]
struct ZScoreCore {
    inner: RollingZScore,
}

#[pymethods]
impl ZScoreCore {
    #[new]
    fn new(window: usize) -> Self {
        Self {
            inner: RollingZScore::new(window),
        }
    }

    fn update(&mut self, value: f64) -> Option<f64> {
        self.inner.update(value)
    }

    #[getter]
    fn value(&self) -> Option<f64> {
        self.inner.value()
    }

    #[getter]
    fn mean(&self) -> Option<f64> {
        self.inner.mean()
    }

    #[getter]
    fn stdev(&self) -> Option<f64> {
        self.inner.stdev()
    }

    #[getter]
    fn is_warm(&self) -> bool {
        self.inner.is_warm()
    }
}

#[pyclass]
struct OfiCore {
    inner: OrderFlowImbalance,
}

#[pymethods]
impl OfiCore {
    #[new]
    fn new(window: usize) -> Self {
        Self {
            inner: OrderFlowImbalance::new(window),
        }
    }

    fn update(&mut self, signed_qty: i64) -> PyResult<f64> {
        self.inner.update(signed_qty).map_err(invalid)
    }

    #[getter]
    fn value(&self) -> f64 {
        self.inner.value()
    }

    #[getter]
    fn is_warm(&self) -> bool {
        self.inner.is_warm()
    }
}

#[pyclass]
struct BetaCore {
    inner: RollingBeta,
}

#[pymethods]
impl BetaCore {
    #[new]
    fn new(window: usize) -> Self {
        Self {
            inner: RollingBeta::new(window),
        }
    }

    fn update(&mut self, x: f64, y: f64) -> Option<f64> {
        self.inner.update(x, y)
    }

    #[getter]
    fn beta(&self) -> Option<f64> {
        self.inner.beta()
    }

    #[getter]
    fn alpha(&self) -> Option<f64> {
        self.inner.alpha()
    }

    #[getter]
    fn is_warm(&self) -> bool {
        self.inner.is_warm()
    }

    fn residual(&self, x: f64, y: f64) -> Option<f64> {
        self.inner.residual(x, y)
    }
}

#[pyfunction]
fn ewma_replay(lam: f64, prices: Vec<f64>) -> Vec<Option<f64>> {
    let mut vol = EwmaVolatility::new(lam);
    prices.into_iter().map(|price| vol.update(price)).collect()
}

type EwmaFeedOut = (Vec<Option<f64>>, f64, Option<f64>, u32);

/// Continue an estimator and return the series plus the fields Python keeps.
///
/// `last` is a float or None. It is not an optional argument: a missing previous
/// price is a real estimator state.
#[pyfunction]
fn ewma_feed(
    lam: f64,
    variance: f64,
    last: &Bound<'_, PyAny>,
    count: u32,
    prices: Vec<f64>,
) -> PyResult<EwmaFeedOut> {
    let previous = if last.is_none() {
        None
    } else {
        Some(last.extract::<f64>()?)
    };
    let mut vol = EwmaVolatility::from_parts(lam, variance, previous, count);
    let series: Vec<Option<f64>> = prices.into_iter().map(|price| vol.update(price)).collect();
    let (variance, previous, count) = vol.parts();
    Ok((series, variance, previous, count))
}

#[pyfunction]
fn zscore_replay(window: usize, values: Vec<f64>) -> Vec<Option<f64>> {
    let mut z = RollingZScore::new(window);
    values.into_iter().map(|value| z.update(value)).collect()
}

#[pyfunction]
fn ofi_replay(window: usize, quantities: Vec<i64>) -> PyResult<Vec<f64>> {
    let mut ofi = OrderFlowImbalance::new(window);
    let mut out = Vec::with_capacity(quantities.len());
    for qty in quantities {
        out.push(ofi.update(qty).map_err(invalid)?);
    }
    Ok(out)
}

#[pyfunction]
fn beta_replay(window: usize, xs: Vec<f64>, ys: Vec<f64>) -> Vec<Option<f64>> {
    let n = xs.len().min(ys.len());
    let mut beta = RollingBeta::new(window);
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        out.push(beta.update(xs[i], ys[i]));
    }
    out
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<EwmaCore>()?;
    m.add_class::<ZScoreCore>()?;
    m.add_class::<OfiCore>()?;
    m.add_class::<BetaCore>()?;
    m.add_function(wrap_pyfunction!(ewma_replay, m)?)?;
    m.add_function(wrap_pyfunction!(ewma_feed, m)?)?;
    m.add_function(wrap_pyfunction!(zscore_replay, m)?)?;
    m.add_function(wrap_pyfunction!(ofi_replay, m)?)?;
    m.add_function(wrap_pyfunction!(beta_replay, m)?)?;
    Ok(())
}
