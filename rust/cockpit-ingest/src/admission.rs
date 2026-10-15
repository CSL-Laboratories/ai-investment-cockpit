//! Bounded dedup and a token bucket.
//!
//! Port of `app/ingest/dedup.py` and `app/ingest/ratelimit.py`. The dedup map
//! is a hash index plus an explicit oldest-to-newest list, so a repeat moves
//! to the tail in constant time. The bucket starts empty. No clock, no socket,
//! and no broker: the caller passes `now`.

#![forbid(unsafe_code)]

use std::collections::HashMap;

#[derive(Clone, Debug)]
struct Slot {
    key: String,
    prev: Option<u32>,
    next: Option<u32>,
}

#[derive(Clone, Debug)]
pub struct Dedup {
    cap: i64,
    index: HashMap<String, u32>,
    slots: Vec<Slot>,
    free: Vec<u32>,
    head: Option<u32>,
    tail: Option<u32>,
    len: i64,
}

impl Dedup {
    pub fn new(cap: i64) -> Self {
        Self {
            cap,
            index: HashMap::new(),
            slots: Vec::new(),
            free: Vec::new(),
            head: None,
            tail: None,
            len: 0,
        }
    }

    pub fn add(&mut self, key: &str) -> bool {
        if let Some(&id) = self.index.get(key) {
            self.move_to_tail(id);
            return false;
        }
        let id = self.alloc(key.to_owned());
        self.index.insert(key.to_owned(), id);
        self.attach_tail(id);
        self.len += 1;
        if self.len > self.cap {
            self.evict_head();
        }
        true
    }

    pub fn discard(&mut self, key: &str) -> bool {
        let Some(id) = self.index.remove(key) else {
            return false;
        };
        self.detach(id);
        self.free.push(id);
        self.len -= 1;
        true
    }

    fn alloc(&mut self, key: String) -> u32 {
        if let Some(id) = self.free.pop() {
            self.slots[id as usize] = Slot {
                key,
                prev: None,
                next: None,
            };
            return id;
        }
        let id = u32::try_from(self.slots.len()).expect("dedup slot index fits in u32");
        self.slots.push(Slot {
            key,
            prev: None,
            next: None,
        });
        id
    }

    fn move_to_tail(&mut self, id: u32) {
        if self.tail == Some(id) {
            return;
        }
        self.detach(id);
        self.attach_tail(id);
    }

    fn attach_tail(&mut self, id: u32) {
        self.slots[id as usize].prev = self.tail;
        self.slots[id as usize].next = None;
        if let Some(tail) = self.tail {
            self.slots[tail as usize].next = Some(id);
        } else {
            self.head = Some(id);
        }
        self.tail = Some(id);
    }

    fn detach(&mut self, id: u32) {
        let prev = self.slots[id as usize].prev;
        let next = self.slots[id as usize].next;
        if let Some(prev) = prev {
            self.slots[prev as usize].next = next;
        } else {
            self.head = next;
        }
        if let Some(next) = next {
            self.slots[next as usize].prev = prev;
        } else {
            self.tail = prev;
        }
        self.slots[id as usize].prev = None;
        self.slots[id as usize].next = None;
    }

    fn evict_head(&mut self) {
        let Some(id) = self.head else {
            return;
        };
        let key = self.slots[id as usize].key.clone();
        self.index.remove(&key);
        self.detach(id);
        self.free.push(id);
        self.len -= 1;
    }
}

/// One token-bucket step. Comparisons follow Python `max` / `min`: a NaN on
/// the right does not replace the left-hand value.
pub fn token_bucket_allow(
    rate: f64,
    capacity: f64,
    tokens: f64,
    last: f64,
    now: f64,
) -> (bool, f64, f64) {
    let elapsed = py_max(0.0, now - last);
    let tokens = py_min(capacity, tokens + elapsed * rate);
    if tokens >= 1.0 {
        (true, tokens - 1.0, now)
    } else {
        (false, tokens, now)
    }
}

fn py_max(left: f64, right: f64) -> f64 {
    if right > left {
        right
    } else {
        left
    }
}

fn py_min(left: f64, right: f64) -> f64 {
    if right < left {
        right
    } else {
        left
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_repeat_moves_to_the_tail_so_the_other_key_is_evicted() {
        let mut seen = Dedup::new(2);
        assert!(seen.add("a"));
        assert!(seen.add("b"));
        assert!(!seen.add("a"));
        assert!(seen.add("c"));
        assert!(!seen.add("a"));
        assert!(seen.add("b"));
        assert!(!seen.discard("c"));
        assert!(seen.discard("a"));
    }

    #[test]
    fn a_non_positive_cap_records_nothing() {
        for cap in [0, -1, -50] {
            let mut seen = Dedup::new(cap);
            assert!(seen.add("k"));
            assert!(seen.add("k"));
            assert!(!seen.discard("k"));
        }
    }

    #[test]
    fn discard_of_the_middle_does_not_resurrect_an_evicted_tail() {
        let mut seen = Dedup::new(3);
        assert!(seen.add("a"));
        assert!(seen.add("b"));
        assert!(seen.add("c"));
        assert!(seen.add("d"));
        assert!(!seen.discard("a"));
        assert!(seen.discard("c"));
        assert!(seen.add("c"));
        assert!(!seen.add("b"));
    }

    #[test]
    fn the_bucket_starts_empty_and_ignores_a_backwards_clock() {
        let (ok, tokens, last) = token_bucket_allow(1.0, 1.0, 0.0, 0.0, 0.0);
        assert!(!ok);
        assert_eq!(tokens, 0.0);
        assert_eq!(last, 0.0);
        let (ok, tokens, last) = token_bucket_allow(2.0, 1.0, 0.0, 0.0, 0.5);
        assert!(ok);
        assert_eq!(tokens, 0.0);
        assert_eq!(last, 0.5);
        let (ok, tokens, _) = token_bucket_allow(2.0, 1.0, tokens, last, 0.25);
        assert!(!ok);
        assert_eq!(tokens, 0.0);
    }
}
