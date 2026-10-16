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

/// Python `json.dumps` of a finite float: shortest round-trip, scientific notation
/// when the decimal point is at or left of `10^-4` or right of 16 digits, `e+`/`e-`
/// with at least two exponent digits, and `.0` when the text would otherwise be an integer.
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

/// Rewrite Ryu's shortest text with CPython's fixed/scientific cutoff.
///
/// `repr(float)` (what `json.dumps` calls) uses scientific notation when `decpt <= -4`
/// or `decpt > 16`, where the value is `0.digits * 10^decpt`. Ryu switches later on
/// the small side, so `0.00001` would otherwise stay fixed while Python emits `1e-05`
/// and the raw-input hash would not match.
fn python_float_text(printed: &str) -> String {
    let (negative, unsigned) = match printed.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, printed),
    };
    let Some((digits, decpt)) = significand(unsigned) else {
        return printed.to_string();
    };
    let body = if decpt <= -4 || decpt > 16 {
        scientific(&digits, decpt)
    } else {
        fixed(&digits, decpt)
    };
    if negative {
        format!("-{body}")
    } else {
        body
    }
}

/// Significant digits and Python's `decpt` for Ryu's fixed or scientific text.
fn significand(unsigned: &str) -> Option<(String, i32)> {
    if unsigned.is_empty() {
        return None;
    }
    let (mantissa, exp) = if let Some((body, exp_text)) = unsigned.split_once(['e', 'E']) {
        (body, exp_text.parse::<i32>().ok()?)
    } else {
        (unsigned, 0)
    };
    let (int_part, frac_part) = match mantissa.split_once('.') {
        Some((int_part, frac_part)) => (int_part, frac_part),
        None => (mantissa, ""),
    };
    if int_part.is_empty()
        || !int_part.bytes().all(|byte| byte.is_ascii_digit())
        || !frac_part.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    let mut all = String::with_capacity(int_part.len() + frac_part.len());
    all.push_str(int_part);
    all.push_str(frac_part);
    let lead = all.bytes().position(|byte| byte != b'0')?;
    let point_index = i32::try_from(int_part.len()).ok()? + exp;
    let decpt = point_index - i32::try_from(lead).ok()?;
    let digits = all[lead..].trim_end_matches('0');
    if digits.is_empty() {
        return None;
    }
    Some((digits.to_string(), decpt))
}

fn scientific(digits: &str, decpt: i32) -> String {
    let exp = decpt - 1;
    let mut out = String::new();
    let mut chars = digits.chars();
    out.push(chars.next().expect("significand is non-empty"));
    let rest: String = chars.collect();
    if !rest.is_empty() {
        out.push('.');
        out.push_str(&rest);
    }
    let sign = if exp < 0 { '-' } else { '+' };
    out.push_str(&format!("e{sign}{:02}", exp.unsigned_abs()));
    out
}

fn fixed(digits: &str, decpt: i32) -> String {
    if decpt <= 0 {
        let mut out = String::from("0.");
        for _ in 0..(-decpt) {
            out.push('0');
        }
        out.push_str(digits);
        return out;
    }
    let point = decpt as usize;
    if point < digits.len() {
        let mut out = String::new();
        out.push_str(&digits[..point]);
        out.push('.');
        out.push_str(&digits[point..]);
        out
    } else {
        let mut out = digits.to_string();
        out.extend(std::iter::repeat('0').take(point - digits.len()));
        out.push_str(".0");
        out
    }
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
            (1e-4, "0.0001"),
            (1e-5, "1e-05"),
            (-1e-5, "-1e-05"),
            (9.9e-5, "9.9e-05"),
            (1.23e-5, "1.23e-05"),
            (1e-6, "1e-06"),
            (1e-7, "1e-07"),
            (5e-324, "5e-324"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (-1e16, "-1e+16"),
            (1e20, "1e+20"),
            (1e100, "1e+100"),
            (f64::MAX, "1.7976931348623157e+308"),
            (std::f64::consts::PI, "3.141592653589793"),
            (9007199254740992.0, "9007199254740992.0"),
        ];
        for (value, expected) in samples {
            assert_eq!(format_f64(value), expected, "{value}");
        }
    }

    #[test]
    fn small_float_hash_matches_python_json() {
        let value = Json::Object(vec![("price_change".into(), Json::Float(1e-5))]);
        assert_eq!(dumps(&value), r#"{"price_change":1e-05}"#);
        assert_eq!(
            content_hash(&value),
            "04b71df3954421435a67d28d917976e969f52155b9c8b1f7418c8ceb5a30d343"
        );
    }

    #[test]
    fn strings_escape_like_python() {
        let value = Json::Str("a\nb\t\"\\é\u{1F600}".into());
        assert_eq!(dumps(&value), r#""a\nb\t\"\\\u00e9\ud83d\ude00""#);
    }
}
