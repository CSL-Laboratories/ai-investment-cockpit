//! Canonical JSON matching CPython's `json.dumps` for the types the normalizer hashes.

use ryu::Buffer;
use sha2::{Digest, Sha256};

#[derive(Clone, Debug, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    Int(i64),
    Big(String),
    Float(f64),
    Str(String),
    Array(Vec<Json>),
    Object(Vec<(String, Json)>),
}

pub fn dumps(value: &Json) -> String {
    let mut out = String::new();
    write_json(&mut out, value, true);
    out
}

pub fn content_hash(value: &Json) -> String {
    let raw = dumps(value);
    let digest = Sha256::digest(raw.as_bytes());
    hex_encode(&digest)
}

fn write_json(out: &mut String, value: &Json, sort_keys: bool) {
    match value {
        Json::Null => out.push_str("null"),
        Json::Bool(true) => out.push_str("true"),
        Json::Bool(false) => out.push_str("false"),
        Json::Int(value) => out.push_str(&value.to_string()),
        Json::Big(value) => out.push_str(value),
        Json::Float(value) => out.push_str(&format_f64(*value)),
        Json::Str(value) => push_string(out, value),
        Json::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_json(out, item, sort_keys);
            }
            out.push(']');
        }
        Json::Object(entries) => {
            let mut entries: Vec<&(String, Json)> = entries.iter().collect();
            if sort_keys {
                entries.sort_by(|a, b| a.0.cmp(&b.0));
            }
            out.push('{');
            for (i, (key, value)) in entries.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                push_string(out, key);
                out.push(':');
                write_json(out, value, sort_keys);
            }
            out.push('}');
        }
    }
}

fn push_string(out: &mut String, text: &str) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{0008}' => out.push_str("\\b"),
            '\u{000c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                push_u_escape(out, c as u32);
            }
            c if (c as u32) > 0x7F => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    push_u_escape(out, *unit as u32);
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

fn push_u_escape(out: &mut String, unit: u32) {
    out.push_str(&format!("\\u{unit:04x}"));
}

/// Python `json.dumps` of a finite float: shortest round-trip, `e+`/`e-` with a
/// two-digit exponent, and `.0` when the text would otherwise be an integer.
pub fn format_f64(value: f64) -> String {
    if value.is_nan() {
        return "NaN".to_string();
    }
    if value.is_infinite() {
        return if value.is_sign_positive() {
            "Infinity".to_string()
        } else {
            "-Infinity".to_string()
        };
    }
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0".to_string()
        } else {
            "0.0".to_string()
        };
    }
    let mut buffer = Buffer::new();
    let printed = buffer.format_finite(value).to_string();
    python_float_text(&printed)
}

fn python_float_text(printed: &str) -> String {
    let Some((body, exp)) = printed.split_once(['e', 'E']) else {
        // Integer-valued floats with no exponent (`9007199254740992`) gain `.0`.
        // An exponent of its own already marks the token as a float (`1e+16`).
        if printed.contains('.') {
            return printed.to_string();
        }
        return if let Some(stripped) = printed.strip_prefix('-') {
            format!("-{stripped}.0")
        } else {
            format!("{printed}.0")
        };
    };
    let (sign, digits) = if let Some(rest) = exp.strip_prefix('-') {
        ('-', rest)
    } else if let Some(rest) = exp.strip_prefix('+') {
        ('+', rest)
    } else {
        ('+', exp)
    };
    let width = digits.len().max(2);
    format!("{body}e{sign}{digits:0>width$}")
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compact_object_matches_python() {
        let value = Json::Array(vec![Json::Object(vec![("x".into(), Json::Int(1))])]);
        assert_eq!(dumps(&value), r#"[{"x":1}]"#);
        assert_eq!(
            content_hash(&value),
            "45fa679977289675167a1e277c1febba4c59b00c64853a8462116f46f1a5f4f7"
        );
    }

    #[test]
    fn floats_match_python_json() {
        let samples = [
            (0.1, "0.1"),
            (1.0, "1.0"),
            (-0.0, "-0.0"),
            (1e16, "1e+16"),
            (1e20, "1e+20"),
            (1e-7, "1e-07"),
            (1e100, "1e+100"),
            (std::f64::consts::PI, "3.141592653589793"),
            (9007199254740992.0, "9007199254740992.0"),
        ];
        for (value, expected) in samples {
            assert_eq!(format_f64(value), expected, "{value}");
        }
    }

    #[test]
    fn strings_escape_like_python() {
        let value = Json::Str("a\nb\t\"\\é\u{1F600}".into());
        assert_eq!(dumps(&value), r#""a\nb\t\"\\\u00e9\ud83d\ude00""#);
    }
}
