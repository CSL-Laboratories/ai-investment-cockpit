//! Normalize a raw feed item into a market event.
//!
//! Port of `app/ingest/normalize.py`. `raw_input` is the detached source image.
//! Headline, summary, and event type are escaped separately. `content_hash` is
//! SHA-256 of the same canonical JSON Python's `json.dumps(..., sort_keys=True,
//! separators=(",", ":"), default=str)` produces. No socket and no broker.

#![forbid(unsafe_code)]

mod json;
mod normalize;

pub use json::{content_hash, dumps, Json};
pub use normalize::{escape_untrusted, market_event, EventParts};
