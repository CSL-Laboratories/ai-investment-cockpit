//! PyO3 face of the online estimators. State stays in Rust across updates.

use cockpit_signals::{EwmaVolatility, OrderFlowImbalance, RollingBeta, RollingZScore};
use pyo3::exceptions::{PyOverflowError, PyValueError};
use pyo3::prelude::*;

fn invalid(message: &str) -> PyErr {
    PyValueError::new_err(message.to_string())
}

fn overflow(message: &str) -> PyErr {
    PyOverflowError::new_err(message.to_string())
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

    fn update(&mut self, price: f64) -> PyResult<Option<f64>> {
        self.inner.update(price).map_err(invalid)
    }

    #[getter]
    fn value(&self) -> PyResult<Option<f64>> {
        self.inner.value().map_err(invalid)
    }

    #[getter]
    fn is_warm(&self) -> bool {
        self.inner.is_warm()
    }

    #[getter]
    fn variance(&self) -> f64 {
        self.inner.variance()
    }

    #[getter]
    fn last(&self) -> Option<f64> {
        self.inner.last()
    }

    #[getter]
    fn count(&self) -> u64 {
        self.inner.count()
    }

    #[getter]
    fn lam(&self) -> f64 {
        self.inner.lam()
    }
}

#[pyclass]
struct ZScoreCore {
    inner: RollingZScore,
}

#[pymethods]
impl ZScoreCore {
    #[new]
    fn new(window: usize, compensated: bool) -> PyResult<Self> {
        if window < 2 {
            return Err(invalid(&format!("window must be >= 2, got {window}")));
        }
        Ok(Self {
            inner: RollingZScore::new(window, compensated),
        })
    }

    fn update(&mut self, value: f64) -> PyResult<Option<f64>> {
        self.inner.update(value).map_err(overflow)
    }

    #[getter]
    fn value(&self) -> PyResult<Option<f64>> {
        self.inner.value().map_err(overflow)
    }

    #[getter]
    fn mean(&self) -> Option<f64> {
        self.inner.mean()
    }

    #[getter]
    fn stdev(&self) -> PyResult<Option<f64>> {
        self.inner.stdev().map_err(overflow)
    }

    #[getter]
    fn window(&self) -> usize {
        self.inner.window()
    }

    #[getter]
    fn is_warm(&self) -> bool {
        self.inner.is_warm()
    }

    fn samples(&self) -> Vec<f64> {
        self.inner.samples()
    }
}

#[pyclass]
struct OfiCore {
    inner: OrderFlowImbalance,
}

#[pymethods]
impl OfiCore {
    #[new]
    fn new(window: usize) -> PyResult<Self> {
        if window < 1 {
            return Err(invalid(&format!("window must be >= 1, got {window}")));
        }
        Ok(Self {
            inner: OrderFlowImbalance::new(window),
        })
    }

    fn update(&mut self, signed_qty: i64) -> PyResult<f64> {
        self.inner.update(signed_qty).map_err(invalid)
    }

    #[getter]
    fn value(&self) -> f64 {
        self.inner.value()
    }

    #[getter]
    fn window(&self) -> usize {
        self.inner.window()
    }

    #[getter]
    fn is_warm(&self) -> bool {
        self.inner.is_warm()
    }

    fn quantities(&self) -> Vec<i64> {
        self.inner.quantities()
    }
}

#[pyclass]
struct BetaCore {
    inner: RollingBeta,
}

#[pymethods]
impl BetaCore {
    #[new]
    fn new(window: usize, compensated: bool) -> PyResult<Self> {
        if window < 3 {
            return Err(invalid(&format!("window must be >= 3, got {window}")));
        }
        Ok(Self {
            inner: RollingBeta::new(window, compensated),
        })
    }

    fn update(&mut self, x: f64, y: f64) -> PyResult<Option<f64>> {
        self.inner.update(x, y).map_err(overflow)
    }

    #[getter]
    fn beta(&self) -> PyResult<Option<f64>> {
        self.inner.beta().map_err(overflow)
    }

    #[getter]
    fn alpha(&self) -> PyResult<Option<f64>> {
        self.inner.alpha().map_err(overflow)
    }

    #[getter]
    fn window(&self) -> usize {
        self.inner.window()
    }

    #[getter]
    fn is_warm(&self) -> bool {
        self.inner.is_warm()
    }

    fn residual(&self, x: f64, y: f64) -> PyResult<Option<f64>> {
        self.inner.residual(x, y).map_err(overflow)
    }

    fn xs(&self) -> Vec<f64> {
        self.inner.xs()
    }

    fn ys(&self) -> Vec<f64> {
        self.inner.ys()
    }
}

#[pyfunction]
fn ewma_replay(lam: f64, prices: Vec<f64>) -> PyResult<Vec<Option<f64>>> {
    let mut vol = EwmaVolatility::new(lam);
    let mut out = Vec::with_capacity(prices.len());
    for price in prices {
        out.push(vol.update(price).map_err(invalid)?);
    }
    Ok(out)
}

#[pyfunction]
#[pyo3(signature = (window, values, compensated=true))]
fn zscore_replay(window: usize, values: Vec<f64>, compensated: bool) -> PyResult<Vec<Option<f64>>> {
    if window < 2 {
        return Err(invalid(&format!("window must be >= 2, got {window}")));
    }
    let mut z = RollingZScore::new(window, compensated);
    let mut out = Vec::with_capacity(values.len());
    for value in values {
        out.push(z.update(value).map_err(overflow)?);
    }
    Ok(out)
}

#[pyfunction]
fn ofi_replay(window: usize, quantities: Vec<i64>) -> PyResult<Vec<f64>> {
    if window < 1 {
        return Err(invalid(&format!("window must be >= 1, got {window}")));
    }
    let mut ofi = OrderFlowImbalance::new(window);
    let mut out = Vec::with_capacity(quantities.len());
    for qty in quantities {
        out.push(ofi.update(qty).map_err(invalid)?);
    }
    Ok(out)
}

#[pyfunction]
#[pyo3(signature = (window, xs, ys, compensated=true))]
fn beta_replay(
    window: usize,
    xs: Vec<f64>,
    ys: Vec<f64>,
    compensated: bool,
) -> PyResult<Vec<Option<f64>>> {
    if window < 3 {
        return Err(invalid(&format!("window must be >= 3, got {window}")));
    }
    let n = xs.len().min(ys.len());
    let mut beta = RollingBeta::new(window, compensated);
    let mut out = Vec::with_capacity(n);
    for i in 0..n {
        out.push(beta.update(xs[i], ys[i]).map_err(overflow)?);
    }
    Ok(out)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<EwmaCore>()?;
    m.add_class::<ZScoreCore>()?;
    m.add_class::<OfiCore>()?;
    m.add_class::<BetaCore>()?;
    m.add_function(wrap_pyfunction!(ewma_replay, m)?)?;
    m.add_function(wrap_pyfunction!(zscore_replay, m)?)?;
    m.add_function(wrap_pyfunction!(ofi_replay, m)?)?;
    m.add_function(wrap_pyfunction!(beta_replay, m)?)?;
    Ok(())
}
