//! Price-level limit order book. State only — matching lives in `matching`.
//!
//! Mirrors `app/quant/book/lob.py`: integer ticks, FIFO queues, incremental
//! level totals, and a sorted price ladder. Cancels of unknown orders fail
//! loudly (`BookError::NotFound`) instead of being ignored.

use std::collections::HashMap;

use crate::error::{py_str_repr, BookError};
use crate::list::List;
use crate::side::Side;

pub(crate) fn fit_add(left: i64, right: i64) -> Result<i64, BookError> {
    left.checked_add(right).ok_or_else(|| {
        BookError::Value(format!(
            "quantity {left} + {right} does not fit in a signed 64-bit integer"
        ))
    })
}

pub(crate) fn fit_sub(left: i64, right: i64) -> Result<i64, BookError> {
    left.checked_sub(right).ok_or_else(|| {
        BookError::Value(format!(
            "quantity {left} - {right} does not fit in a signed 64-bit integer"
        ))
    })
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Order {
    pub order_id: String,
    pub side: Side,
    pub price_ticks: i64,
    pub qty: i64,
    pub entry_seq: i64,
    pub entry_ts: i64,
    pub owner: Option<String>,
    pub original_qty: i64,
    pub qty_ahead_at_entry: i64,
}

impl Order {
    pub fn filled_qty(&self) -> i64 {
        self.original_qty - self.qty
    }

    pub fn is_ours(&self) -> bool {
        self.owner.is_some()
    }
}

/// Front-of-level view used by the matcher. Owned strings so the book can be mutated next.
#[derive(Clone, Debug)]
pub struct LevelHead {
    pub order_id: String,
    pub qty: i64,
    pub price_ticks: i64,
    pub qty_ahead_at_entry: i64,
    pub side: Side,
    pub owner: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DepthLevel {
    pub price_ticks: i64,
    pub qty: i64,
    pub order_count: usize,
}

struct Level {
    orders: List<Order>,
    by_id: HashMap<String, u32>,
    total_qty: i64,
}

impl Level {
    fn new() -> Self {
        Self {
            orders: List::default(),
            by_id: HashMap::new(),
            total_qty: 0,
        }
    }

    fn is_empty(&self) -> bool {
        self.orders.is_empty()
    }

    fn len(&self) -> usize {
        self.orders.len()
    }

    fn true_total(&self) -> Result<i64, BookError> {
        let mut total = 0i64;
        for order in self.orders.iter() {
            total = fit_add(total, order.qty)?;
        }
        Ok(total)
    }

    fn append(&mut self, order: Order) -> Result<(), BookError> {
        if self.by_id.contains_key(&order.order_id) {
            return Err(BookError::Value(format!(
                "duplicate order_id {} at level {}",
                py_str_repr(&order.order_id),
                order.price_ticks
            )));
        }
        self.total_qty = fit_add(self.total_qty, order.qty)?;
        let id = order.order_id.clone();
        let idx = self.orders.push_back(order);
        self.by_id.insert(id, idx);
        Ok(())
    }

    fn remove(&mut self, order_id: &str) -> Result<Order, BookError> {
        let idx = self
            .by_id
            .remove(order_id)
            .ok_or_else(|| BookError::NotFound(order_id.to_string()))?;
        let order = self.orders.remove(idx);
        self.total_qty = fit_sub(self.total_qty, order.qty)?;
        Ok(order)
    }

    fn reduce(&mut self, order_id: &str, qty: i64) -> Result<i64, BookError> {
        if qty <= 0 {
            return Err(BookError::Value(format!(
                "reduce qty must be positive, got {qty}"
            )));
        }
        let idx = *self
            .by_id
            .get(order_id)
            .ok_or_else(|| BookError::NotFound(order_id.to_string()))?;
        let current = self.orders.get(idx).qty;
        if qty > current {
            return Err(BookError::Value(format!(
                "cannot reduce order {} by {qty}: only {current} remaining",
                py_str_repr(order_id)
            )));
        }
        {
            let order = self.orders.get_mut(idx);
            order.qty = fit_sub(order.qty, qty)?;
        }
        self.total_qty = fit_sub(self.total_qty, qty)?;
        let remaining = self.orders.get(idx).qty;
        if remaining == 0 {
            self.by_id.remove(order_id);
            self.orders.remove(idx);
        }
        Ok(remaining)
    }

    fn qty_ahead(&self, order_id: &str) -> Result<i64, BookError> {
        let mut ahead = 0;
        let mut found = false;
        for order in self.orders.iter() {
            if order.order_id == order_id {
                found = true;
                break;
            }
            ahead = fit_add(ahead, order.qty)?;
        }
        if found {
            Ok(ahead)
        } else {
            Err(BookError::NotFound(order_id.to_string()))
        }
    }

    fn ids(&self) -> Vec<String> {
        self.orders
            .iter()
            .map(|order| order.order_id.clone())
            .collect()
    }
}

struct SideBook {
    levels: HashMap<i64, Level>,
    prices: Vec<i64>,
}

impl SideBook {
    fn new() -> Self {
        Self {
            levels: HashMap::new(),
            prices: Vec::new(),
        }
    }
}

struct Loc {
    node: u32,
    side: Side,
    price: i64,
}

pub struct Book {
    symbol: String,
    bids: SideBook,
    asks: SideBook,
    /// Insertion order of resting ids. Iteration order is part of the Python
    /// contract (`_index.values()`), including the synthetic feed's cancel draw.
    index: List<String>,
    by_id: HashMap<String, Loc>,
    last_seq: i64,
    last_ts: i64,
    /// Bumps on every successful mutation. Snapshot caches key on this, not on
    /// `last_seq`: two adds can share a sequence number and still change depth.
    generation: u64,
}

impl Book {
    pub fn new(symbol: impl Into<String>) -> Self {
        Self {
            symbol: symbol.into(),
            bids: SideBook::new(),
            asks: SideBook::new(),
            index: List::default(),
            by_id: HashMap::new(),
            last_seq: 0,
            last_ts: 0,
            generation: 0,
        }
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    fn bump(&mut self) {
        self.generation = self.generation.wrapping_add(1);
    }

    pub fn symbol(&self) -> &str {
        &self.symbol
    }

    pub fn len(&self) -> usize {
        self.by_id.len()
    }

    pub fn is_empty(&self) -> bool {
        self.by_id.is_empty()
    }

    pub fn contains(&self, order_id: &str) -> bool {
        self.by_id.contains_key(order_id)
    }

    pub fn last_stamps(&self) -> (i64, i64) {
        (self.last_seq, self.last_ts)
    }

    fn side_book(&self, side: Side) -> &SideBook {
        match side {
            Side::Buy => &self.bids,
            Side::Sell => &self.asks,
        }
    }

    fn side_book_mut(&mut self, side: Side) -> &mut SideBook {
        match side {
            Side::Buy => &mut self.bids,
            Side::Sell => &mut self.asks,
        }
    }

    fn level_for_write(&mut self, side: Side, price: i64) -> &mut Level {
        {
            let book = self.side_book_mut(side);
            if let std::collections::hash_map::Entry::Vacant(entry) = book.levels.entry(price) {
                entry.insert(Level::new());
                let pos = book.prices.partition_point(|&existing| existing < price);
                book.prices.insert(pos, price);
            }
        }
        self.side_book_mut(side)
            .levels
            .get_mut(&price)
            .expect("level just inserted")
    }

    fn drop_level_if_empty(&mut self, side: Side, price: i64) -> Result<(), BookError> {
        let book = self.side_book_mut(side);
        let empty = book
            .levels
            .get(&price)
            .map(|level| level.is_empty())
            .unwrap_or(false);
        if !empty {
            return Ok(());
        }
        book.levels.remove(&price);
        let pos = book.prices.partition_point(|&existing| existing < price);
        if pos >= book.prices.len() || book.prices[pos] != price {
            return Err(BookError::Integrity(format!(
                "price index desync: level {price} on {} missing from the sorted index while its dict entry existed",
                side.as_str()
            )));
        }
        book.prices.remove(pos);
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    pub fn add(
        &mut self,
        order_id: impl Into<String>,
        side: Side,
        price_ticks: i64,
        qty: i64,
        seq: i64,
        ts: i64,
        owner: Option<String>,
    ) -> Result<Order, BookError> {
        let order_id = order_id.into();
        if self.by_id.contains_key(&order_id) {
            return Err(BookError::Value(format!(
                "duplicate order_id {} already resting in the book",
                py_str_repr(&order_id)
            )));
        }
        if qty <= 0 {
            return Err(BookError::Value(format!(
                "resting order {} must have positive qty, got {qty}",
                py_str_repr(&order_id)
            )));
        }
        let ahead = self
            .side_book(side)
            .levels
            .get(&price_ticks)
            .map(|level| level.total_qty)
            .unwrap_or(0);
        // Reject an overflowing level total before any level is created.
        fit_add(ahead, qty)?;
        let order = Order {
            order_id: order_id.clone(),
            side,
            price_ticks,
            qty,
            entry_seq: seq,
            entry_ts: ts,
            owner,
            original_qty: qty,
            qty_ahead_at_entry: ahead,
        };
        if let Err(err) = self
            .level_for_write(side, price_ticks)
            .append(order.clone())
        {
            self.drop_level_if_empty(side, price_ticks)?;
            return Err(err);
        }
        let node = self.index.push_back(order_id.clone());
        self.by_id.insert(
            order_id,
            Loc {
                node,
                side,
                price: price_ticks,
            },
        );
        self.last_seq = self.last_seq.max(seq);
        self.last_ts = self.last_ts.max(ts);
        self.bump();
        Ok(order)
    }

    pub fn cancel(&mut self, order_id: &str) -> Result<Order, BookError> {
        let loc = self
            .by_id
            .remove(order_id)
            .ok_or_else(|| BookError::NotFound(order_id.to_string()))?;
        let order = {
            let level = self
                .side_book_mut(loc.side)
                .levels
                .get_mut(&loc.price)
                .ok_or_else(|| {
                    BookError::Integrity(format!(
                        "order {} indexed at {} but that level is gone",
                        py_str_repr(order_id),
                        loc.price
                    ))
                })?;
            level.remove(order_id)?
        };
        self.index.remove(loc.node);
        // The order is already gone. Bump before cleanup: a missing ladder
        // entry removes the level and then returns Integrity.
        self.bump();
        self.drop_level_if_empty(loc.side, loc.price)?;
        Ok(order)
    }

    pub fn reduce(&mut self, order_id: &str, qty: i64) -> Result<Order, BookError> {
        let mut order = self
            .get(order_id)
            .ok_or_else(|| BookError::NotFound(order_id.to_string()))?;
        self.reduce_qty(order_id, qty)?;
        order.qty = fit_sub(order.qty, qty)?;
        Ok(order)
    }

    /// Shrink an order without cloning it. The matcher uses this on the hot path.
    pub fn reduce_qty(&mut self, order_id: &str, qty: i64) -> Result<(), BookError> {
        let loc = self
            .by_id
            .get(order_id)
            .map(|loc| Loc {
                node: loc.node,
                side: loc.side,
                price: loc.price,
            })
            .ok_or_else(|| BookError::NotFound(order_id.to_string()))?;
        let remaining = {
            let level = self
                .side_book_mut(loc.side)
                .levels
                .get_mut(&loc.price)
                .ok_or_else(|| BookError::Integrity(format!("missing level for {order_id}")))?;
            level.reduce(order_id, qty)?
        };
        // Quantity already changed. Bump before cleanup so a failed
        // drop_level_if_empty cannot leave the cached snapshot on the old depth.
        self.bump();
        if remaining == 0 {
            self.by_id.remove(order_id);
            self.index.remove(loc.node);
            self.drop_level_if_empty(loc.side, loc.price)?;
        }
        Ok(())
    }

    /// Front of a price level, for the matcher. `None` when the level is empty or absent.
    pub fn level_head(&self, side: Side, price: i64) -> Option<LevelHead> {
        let level = self.side_book(side).levels.get(&price)?;
        let order = level.orders.iter().next()?;
        Some(LevelHead {
            order_id: order.order_id.clone(),
            qty: order.qty,
            price_ticks: order.price_ticks,
            qty_ahead_at_entry: order.qty_ahead_at_entry,
            side: order.side,
            owner: order.owner.clone(),
        })
    }

    pub fn replace(
        &mut self,
        order_id: &str,
        new_price_ticks: Option<i64>,
        new_qty: Option<i64>,
        seq: i64,
        ts: i64,
    ) -> Result<Order, BookError> {
        let existing = self
            .get(order_id)
            .ok_or_else(|| BookError::NotFound(order_id.to_string()))?;
        let price = new_price_ticks.unwrap_or(existing.price_ticks);
        let qty = new_qty.unwrap_or(existing.qty);
        if qty <= 0 {
            return Err(BookError::Value(format!(
                "replace qty must be positive, got {qty}"
            )));
        }
        let side = existing.side;
        let owner = existing.owner.clone();
        // Check the destination level can take the new qty before the cancel,
        // so a rejected replace leaves the original order and its queue slot.
        let mut dest_total = self
            .side_book(side)
            .levels
            .get(&price)
            .map(|level| level.total_qty)
            .unwrap_or(0);
        if price == existing.price_ticks {
            dest_total = fit_sub(dest_total, existing.qty)?;
        }
        fit_add(dest_total, qty)?;
        self.cancel(order_id)?;
        self.add(order_id, side, price, qty, seq, ts, owner)
    }

    pub fn get(&self, order_id: &str) -> Option<Order> {
        let loc = self.by_id.get(order_id)?;
        let level = self.side_book(loc.side).levels.get(&loc.price)?;
        let idx = *level.by_id.get(order_id)?;
        Some(level.orders.get(idx).clone())
    }

    /// `get(id).is_some()` without cloning the order.
    ///
    /// `contains` only checks the book map. An id that is mapped but missing
    /// from its level is not resting.
    pub(crate) fn on_level(&self, order_id: &str) -> bool {
        let Some(loc) = self.by_id.get(order_id) else {
            return false;
        };
        self.side_book(loc.side)
            .levels
            .get(&loc.price)
            .is_some_and(|level| level.by_id.contains_key(order_id))
    }

    /// Test hook: keep the order on the level's list and in the book map, and
    /// drop it from the level's id map. `contains` stays true. `get` is None.
    #[cfg(test)]
    pub(crate) fn detach_level_index(&mut self, order_id: &str) {
        let loc = self
            .by_id
            .get(order_id)
            .expect("order is in the book index");
        let side = loc.side;
        let price = loc.price;
        let level = self
            .side_book_mut(side)
            .levels
            .get_mut(&price)
            .expect("level exists");
        assert!(
            level.by_id.remove(order_id).is_some(),
            "level index had the id"
        );
    }

    pub fn queue_position(&self, order_id: &str) -> Result<i64, BookError> {
        let loc = self
            .by_id
            .get(order_id)
            .ok_or_else(|| BookError::NotFound(order_id.to_string()))?;
        let level = self
            .side_book(loc.side)
            .levels
            .get(&loc.price)
            .ok_or_else(|| BookError::Integrity(format!("missing level for {order_id}")))?;
        level.qty_ahead(order_id)
    }

    pub fn queue_progress(&self, order_id: &str) -> Result<f64, BookError> {
        let order = self
            .get(order_id)
            .ok_or_else(|| BookError::NotFound(order_id.to_string()))?;
        if order.qty_ahead_at_entry <= 0 {
            return Ok(1.0);
        }
        let remaining = self.queue_position(order_id)? as f64;
        let progress = 1.0 - remaining / (order.qty_ahead_at_entry as f64);
        Ok(progress.clamp(0.0, 1.0))
    }

    pub fn best_bid(&self) -> Option<i64> {
        self.bids.prices.last().copied()
    }

    pub fn best_ask(&self) -> Option<i64> {
        self.asks.prices.first().copied()
    }

    pub fn best(&self, side: Side) -> Option<i64> {
        match side {
            Side::Buy => self.best_bid(),
            Side::Sell => self.best_ask(),
        }
    }

    pub fn spread_ticks(&self) -> Option<i64> {
        Some(self.best_ask()? - self.best_bid()?)
    }

    pub fn mid_ticks(&self) -> Option<f64> {
        let bid = self.best_bid()?;
        let ask = self.best_ask()?;
        Some((i128::from(bid) + i128::from(ask)) as f64 / 2.0)
    }

    pub fn is_crossed(&self) -> bool {
        match (self.best_bid(), self.best_ask()) {
            (Some(bid), Some(ask)) => bid >= ask,
            _ => false,
        }
    }

    pub fn level_qty(&self, side: Side, price: i64) -> i64 {
        self.side_book(side)
            .levels
            .get(&price)
            .map(|level| level.total_qty)
            .unwrap_or(0)
    }

    pub fn level_ids(&self, side: Side, price: i64) -> Option<Vec<String>> {
        self.side_book(side)
            .levels
            .get(&price)
            .map(|level| level.ids())
    }

    pub fn has_level(&self, side: Side, price: i64) -> bool {
        self.side_book(side).levels.contains_key(&price)
    }

    /// Best-first active prices. A fresh `Vec`, same as Python's copy.
    pub fn prices(&self, side: Side) -> Vec<i64> {
        let prices = &self.side_book(side).prices;
        match side {
            Side::Buy => prices.iter().rev().copied().collect(),
            Side::Sell => prices.clone(),
        }
    }

    /// Push a price onto the ladder without a level. The integrity control
    /// (`_bid_prices.append`) uses this to prove the check can fail.
    pub fn append_price(&mut self, side: Side, price: i64) {
        self.side_book_mut(side).prices.push(price);
        self.bump();
    }

    /// Drop a price from the ladder and leave its level. Tests use this to
    /// make ``drop_level_if_empty`` fail after it has already removed the level.
    #[cfg(test)]
    fn forget_indexed_price(&mut self, side: Side, price: i64) {
        let prices = &mut self.side_book_mut(side).prices;
        let pos = prices
            .iter()
            .position(|&existing| existing == price)
            .expect("price is on the ladder");
        prices.remove(pos);
    }

    pub fn set_cached_total(
        &mut self,
        side: Side,
        price: i64,
        total: i64,
    ) -> Result<(), BookError> {
        let changed = {
            let level = self
                .side_book_mut(side)
                .levels
                .get_mut(&price)
                .ok_or_else(|| BookError::NotFound(format!("level {price}")))?;
            if level.total_qty == total {
                false
            } else {
                level.total_qty = total;
                true
            }
        };
        if changed {
            self.bump();
        }
        Ok(())
    }

    pub fn depth(&self, side: Side, levels: usize) -> Vec<DepthLevel> {
        let mut out = Vec::new();
        for price in self.prices(side).into_iter().take(levels) {
            if let Some(level) = self.side_book(side).levels.get(&price) {
                out.push(DepthLevel {
                    price_ticks: price,
                    qty: level.total_qty,
                    order_count: level.len(),
                });
            }
        }
        out
    }

    pub fn total_qty(&self, side: Side) -> Result<i64, BookError> {
        let mut total = 0i64;
        for level in self.side_book(side).levels.values() {
            total = fit_add(total, level.total_qty)?;
        }
        Ok(total)
    }

    /// Resting orders in index insertion order.
    pub fn orders(&self) -> Vec<Order> {
        let mut out = Vec::with_capacity(self.index.len());
        for entry in self.index.iter() {
            if let Some(order) = self.get(entry) {
                out.push(order);
            }
        }
        out
    }

    /// One resting order in the same insertion order as [`Book::orders`].
    ///
    /// Callers that need a single id (a synthetic cancel picks one at random)
    /// must not clone the whole index across the interpreter to get it.
    pub fn order_at(&self, index: usize) -> Option<Order> {
        let mut seen = 0usize;
        for entry in self.index.iter() {
            // `get` clones the order. Skipped ids only need the level check.
            if !self.on_level(entry) {
                continue;
            }
            if seen == index {
                return self.get(entry);
            }
            seen += 1;
        }
        None
    }

    pub fn check_integrity(&self) -> Result<(), BookError> {
        let mut seen: Vec<String> = Vec::new();
        for side in [Side::Buy, Side::Sell] {
            let book = self.side_book(side);
            let mut sorted = book.prices.clone();
            sorted.sort();
            if sorted != book.prices {
                return Err(BookError::Integrity(format!(
                    "{} price index is not sorted: {:?}",
                    side.as_str(),
                    book.prices
                )));
            }
            if book
                .prices
                .iter()
                .collect::<std::collections::HashSet<_>>()
                .len()
                != book.prices.len()
            {
                return Err(BookError::Integrity(format!(
                    "{} price index has duplicates: {:?}",
                    side.as_str(),
                    book.prices
                )));
            }
            let level_prices: std::collections::HashSet<i64> =
                book.levels.keys().copied().collect();
            let index_prices: std::collections::HashSet<i64> =
                book.prices.iter().copied().collect();
            if level_prices != index_prices {
                let mut levels_sorted: Vec<i64> = level_prices.iter().copied().collect();
                levels_sorted.sort();
                let mut prices_sorted = book.prices.clone();
                prices_sorted.sort();
                return Err(BookError::Integrity(format!(
                    "{} price index {:?} disagrees with levels {:?}",
                    side.as_str(),
                    prices_sorted,
                    levels_sorted
                )));
            }
            for (&price, level) in &book.levels {
                if level.is_empty() {
                    return Err(BookError::Integrity(format!(
                        "empty level {price} on {} was not dropped",
                        side.as_str()
                    )));
                }
                let true_total = level.true_total()?;
                if level.total_qty != true_total {
                    return Err(BookError::Integrity(format!(
                        "level {price} on {}: cached total {} != true total {true_total}",
                        side.as_str(),
                        level.total_qty
                    )));
                }
                for order in level.orders.iter() {
                    if order.price_ticks != price || order.side != side {
                        return Err(BookError::Integrity(format!(
                            "order {} filed at {}/{price} but claims {}/{}",
                            py_str_repr(&order.order_id),
                            side.as_str(),
                            order.side.as_str(),
                            order.price_ticks
                        )));
                    }
                    match self.by_id.get(&order.order_id) {
                        Some(loc) if loc.side == side && loc.price == price => {}
                        _ => {
                            return Err(BookError::Integrity(format!(
                                "order {} in a level but not (or differently) indexed",
                                py_str_repr(&order.order_id)
                            )));
                        }
                    }
                    seen.push(order.order_id.clone());
                }
            }
        }
        let mut indexed: Vec<String> = self.index.iter().cloned().collect();
        let mut seen_sorted = seen.clone();
        seen_sorted.sort();
        indexed.sort();
        if seen_sorted != indexed {
            let extra: Vec<&String> = indexed.iter().filter(|id| !seen.contains(id)).collect();
            return Err(BookError::Integrity(format!(
                "order index holds {extra:?} not present in any level"
            )));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fresh() -> Book {
        Book::new("TEST")
    }

    fn queue(book: &mut Book) {
        book.add("ahead", Side::Buy, 100, 500, 0, 0, None).unwrap();
        book.add("ours", Side::Buy, 100, 200, 0, 0, Some("mm".into()))
            .unwrap();
        book.add("behind", Side::Buy, 100, 300, 0, 0, None).unwrap();
    }

    #[test]
    fn generation_moves_only_when_the_book_changes() {
        let mut book = fresh();
        assert_eq!(book.generation(), 0);
        book.add("a", Side::Buy, 100, 10, 1, 1, None).unwrap();
        assert_eq!(book.generation(), 1);
        assert!(book.add("a", Side::Buy, 100, 10, 1, 1, None).is_err());
        assert_eq!(book.generation(), 1);
        book.set_cached_total(Side::Buy, 100, 10).unwrap();
        assert_eq!(book.generation(), 1);
        book.set_cached_total(Side::Buy, 100, 11).unwrap();
        assert_eq!(book.generation(), 2);
        assert_eq!(book.depth(Side::Buy, 1)[0].qty, 11);
        book.reduce_qty("a", 4).unwrap();
        assert_eq!(book.generation(), 3);
        book.cancel("a").unwrap();
        assert_eq!(book.generation(), 4);
        let before_ladder = book.generation();
        book.append_price(Side::Buy, 105);
        assert_eq!(book.generation(), before_ladder + 1);
        assert!(book.depth(Side::Buy, 1).is_empty());
    }

    #[test]
    fn a_failed_cleanup_still_moves_generation() {
        let mut book = fresh();
        book.add("a", Side::Buy, 100, 10, 0, 0, None).unwrap();
        let before = book.generation();
        book.forget_indexed_price(Side::Buy, 100);
        let err = book.cancel("a").unwrap_err();
        assert!(matches!(err, BookError::Integrity(_)));
        assert_eq!(book.generation(), before + 1);
        assert!(book.depth(Side::Buy, 1).is_empty());

        book.add("b", Side::Buy, 100, 10, 0, 0, None).unwrap();
        let before_reduce = book.generation();
        book.forget_indexed_price(Side::Buy, 100);
        let err = book.reduce_qty("b", 10).unwrap_err();
        assert!(matches!(err, BookError::Integrity(_)));
        assert_eq!(book.generation(), before_reduce + 1);
        assert!(book.depth(Side::Buy, 1).is_empty());
    }

    #[test]
    fn order_at_follows_insertion_order_and_skips_cancels() {
        let mut book = fresh();
        queue(&mut book);
        assert_eq!(book.order_at(0).unwrap().order_id, "ahead");
        assert_eq!(book.order_at(1).unwrap().order_id, "ours");
        assert_eq!(book.order_at(2).unwrap().order_id, "behind");
        assert!(book.order_at(3).is_none());
        book.cancel("ours").unwrap();
        assert_eq!(book.order_at(0).unwrap().order_id, "ahead");
        assert_eq!(book.order_at(1).unwrap().order_id, "behind");
        assert!(book.order_at(2).is_none());
    }

    #[test]
    fn rejected_replace_keeps_the_original_order_and_queue_slot() {
        let mut book = fresh();
        book.add("big", Side::Buy, 100, i64::MAX - 1, 0, 0, None)
            .unwrap();
        book.add("mine", Side::Buy, 101, 5, 0, 0, None).unwrap();
        assert!(book.replace("mine", Some(100), Some(5), 1, 1).is_err());
        assert_eq!(book.get("mine").unwrap().price_ticks, 101);
        assert_eq!(book.level_qty(Side::Buy, 101), 5);
        book.check_integrity().unwrap();

        // Same-level replace nets out the old qty and still fits.
        book.add("tail", Side::Buy, 101, 1, 0, 0, None).unwrap();
        book.replace("mine", None, Some(9), 2, 2).unwrap();
        assert_eq!(book.level_qty(Side::Buy, 101), 10);
        book.check_integrity().unwrap();
    }

    #[test]
    fn rejected_add_leaves_no_phantom_level() {
        let mut book = fresh();
        book.add("big", Side::Buy, 100, i64::MAX, 0, 0, None)
            .unwrap();
        assert!(book.add("more", Side::Buy, 100, 1, 0, 0, None).is_err());
        assert_eq!(book.len(), 1);
        book.check_integrity().unwrap();
    }

    #[test]
    fn queue_position_counts_shares_ahead() {
        let mut book = fresh();
        queue(&mut book);
        assert_eq!(book.queue_position("ahead").unwrap(), 0);
        assert_eq!(book.queue_position("ours").unwrap(), 500);
        assert_eq!(book.queue_position("behind").unwrap(), 700);
        book.check_integrity().unwrap();
    }

    #[test]
    fn cancel_ahead_advances_and_cancel_behind_does_not() {
        let mut book = fresh();
        queue(&mut book);
        book.cancel("behind").unwrap();
        assert_eq!(book.queue_position("ours").unwrap(), 500);
        book.cancel("ahead").unwrap();
        assert_eq!(book.queue_position("ours").unwrap(), 0);
        book.check_integrity().unwrap();
    }

    #[test]
    fn reduce_keeps_priority_and_replace_loses_it() {
        let mut book = fresh();
        queue(&mut book);
        book.reduce("ours", 50).unwrap();
        assert_eq!(book.queue_position("ours").unwrap(), 500);
        assert_eq!(book.get("ours").unwrap().qty, 150);
        assert_eq!(book.get("ours").unwrap().filled_qty(), 50);
        book.replace("ours", None, Some(150), 0, 0).unwrap();
        assert_eq!(book.queue_position("ours").unwrap(), 800);
        assert_eq!(book.get("ours").unwrap().owner.as_deref(), Some("mm"));
        book.check_integrity().unwrap();
    }

    #[test]
    fn empty_level_is_full_progress_and_unknown_cancel_is_not_found() {
        let mut book = fresh();
        book.add("first", Side::Buy, 100, 100, 0, 0, Some("mm".into()))
            .unwrap();
        assert_eq!(book.queue_progress("first").unwrap(), 1.0);
        let err = book.cancel("missing").unwrap_err();
        assert!(matches!(err, BookError::NotFound(_)));
    }

    #[test]
    fn prices_are_best_first_and_a_crossed_book_is_representable() {
        let mut book = fresh();
        for tick in [98, 100, 99] {
            book.add(format!("b{tick}"), Side::Buy, tick, 100, 0, 0, None)
                .unwrap();
        }
        for tick in [103, 101, 102] {
            book.add(format!("a{tick}"), Side::Sell, tick, 100, 0, 0, None)
                .unwrap();
        }
        assert_eq!(book.prices(Side::Buy), vec![100, 99, 98]);
        assert_eq!(book.prices(Side::Sell), vec![101, 102, 103]);
        assert_eq!(book.spread_ticks(), Some(1));
        book.add("xb", Side::Buy, 104, 10, 0, 0, None).unwrap();
        assert!(book.is_crossed());
        assert_eq!(book.spread_ticks(), Some(-3));
    }

    #[test]
    fn integrity_detects_a_corrupted_total_and_a_desynced_ladder() {
        let mut book = fresh();
        book.add("o", Side::Buy, 100, 100, 0, 0, None).unwrap();
        book.set_cached_total(Side::Buy, 100, 999).unwrap();
        let err = book.check_integrity().unwrap_err();
        assert!(err.to_string().contains("cached total"));
        let mut book = fresh();
        book.add("o", Side::Buy, 100, 100, 0, 0, None).unwrap();
        book.append_price(Side::Buy, 105);
        assert!(book.check_integrity().is_err());
    }

    #[test]
    fn index_iteration_is_insertion_order_after_a_middle_cancel() {
        let mut book = fresh();
        queue(&mut book);
        book.cancel("ours").unwrap();
        let ids: Vec<_> = book
            .orders()
            .into_iter()
            .map(|order| order.order_id)
            .collect();
        assert_eq!(ids, vec!["ahead".to_string(), "behind".to_string()]);
    }

    #[test]
    fn duplicate_and_non_positive_qty_are_refused() {
        let mut book = fresh();
        book.add("dup", Side::Buy, 100, 100, 0, 0, None).unwrap();
        let err = book
            .add("dup", Side::Sell, 101, 100, 0, 0, None)
            .unwrap_err();
        assert!(err.to_string().contains("duplicate"));
        let err = book.add("z", Side::Buy, 100, 0, 0, 0, None).unwrap_err();
        assert!(matches!(err, BookError::Value(_)));
        book.add("o", Side::Buy, 99, 100, 0, 0, None).unwrap();
        let err = book.reduce("o", 101).unwrap_err();
        assert!(err.to_string().contains("only 100 remaining"));
    }
}
