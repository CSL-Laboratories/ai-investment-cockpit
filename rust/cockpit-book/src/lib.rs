//! FIFO limit order book and price-time matcher.
//!
//! This crate is the Rust port of `app/quant/book`. It is a simulator: it has no
//! network, no clock, and no broker handle. Python tests remain the behavior spec.

#![forbid(unsafe_code)]

mod book;
mod error;
mod list;
mod matching;
mod mt;
mod side;
mod synthetic;

pub use book::{Book, DepthLevel, Order};
pub use error::BookError;
pub use matching::{match_order, Aggressive, FillOut, MatchOutput, SelfTrade, TimeInForce};
pub use mt::{box_muller, unit_exponential};
pub use side::Side;
pub use synthetic::{
    synthetic_events, SynthAction, SynthCursor, SynthError, SynthEvent, SynthParams,
};
