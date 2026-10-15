use std::fmt;

/// Failures the Python book raises as `OrderNotFound`, `ValueError`, or
/// `BookIntegrityError`. Messages for value errors match the Python text the
/// tests pin (`duplicate`, `only N remaining`, `cached total`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BookError {
    NotFound(String),
    Value(String),
    Integrity(String),
}

impl fmt::Display for BookError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            BookError::NotFound(id) => write!(f, "{id}"),
            BookError::Value(message) | BookError::Integrity(message) => write!(f, "{message}"),
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
