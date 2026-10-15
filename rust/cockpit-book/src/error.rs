use std::fmt;

/// Failures the Python book raises as `OrderNotFound`, `ValueError`, or
/// `BookIntegrityError`. Messages for value errors match the Python text the
/// tests pin (`duplicate`, `only N remaining`, `cached total`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BookError {
    NotFound(String),
    Value(String),
    Integrity(String),
    /// A match changed the book and then failed (the remainder could not rest).
    /// `touched` is `(order_id, qty_after)` for trades. `qty_after == 0` is a
    /// full fill. `stp_cancelled` ids were removed without a trade, so their
    /// held qty stays put.
    Partial {
        message: String,
        stp_cancelled: Vec<String>,
        touched: Vec<(String, i64)>,
    },
}

impl fmt::Display for BookError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            BookError::NotFound(id) => write!(f, "{id}"),
            BookError::Value(message) | BookError::Integrity(message) => write!(f, "{message}"),
            BookError::Partial { message, .. } => write!(f, "{message}"),
        }
    }
}

impl std::error::Error for BookError {}

/// Python `!r` for a str that does not need escapes. The book tests match on
/// substrings, but the full sentence is still the Python one.
pub fn py_str_repr(value: &str) -> String {
    if !value.contains('\'') {
        format!("'{value}'")
    } else if !value.contains('"') && !value.contains('\\') {
        format!("\"{value}\"")
    } else {
        let escaped = value.replace('\\', "\\\\").replace('\'', "\\'");
        format!("'{escaped}'")
    }
}
