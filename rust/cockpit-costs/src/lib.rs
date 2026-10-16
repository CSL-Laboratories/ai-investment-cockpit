//! Fee schedule and PnL attribution.
//!
//! Port of `app/quant/costs.py` and `app/quant/pnl.py`. Decimal strings are the
//! boundary: Python passes ``str(Decimal)`` and, for elapsed seconds, ``str(float)``
//! so this crate parses the same text Python would. No clock, no network, and no
//! broker handle — paper arithmetic only.

#![forbid(unsafe_code)]

mod money;

pub use money::{
    borrow_cost, cost_bps, fee_for, fee_for_many, Dec, Liquidity, Pnl, PnlEvent, Schedule, Side,
};
