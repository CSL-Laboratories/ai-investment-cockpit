//! Insertion-ordered list with O(1) unlink.
//!
//! Python's book relies on dict insertion order plus O(1) `pop`. A `HashMap`
//! would make `_index` iteration non-deterministic (and the synthetic feed with
//! it). A `Vec` remove would make cancels quadratic. This list is both.

#[derive(Clone, Debug)]
struct Link {
    prev: Option<u32>,
    next: Option<u32>,
}

#[derive(Clone, Debug)]
pub struct List<T> {
    items: Vec<Option<(T, Link)>>,
    free: Vec<u32>,
    head: Option<u32>,
    tail: Option<u32>,
    len: usize,
}

impl<T> Default for List<T> {
    fn default() -> Self {
        Self {
            items: Vec::new(),
            free: Vec::new(),
            head: None,
            tail: None,
            len: 0,
        }
    }
}

impl<T> List<T> {
    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    pub fn push_back(&mut self, value: T) -> u32 {
        let idx = if let Some(idx) = self.free.pop() {
            idx
        } else {
            let idx = self.items.len() as u32;
            self.items.push(None);
            idx
        };
        let link = Link {
            prev: self.tail,
            next: None,
        };
        self.items[idx as usize] = Some((value, link));
        if let Some(tail) = self.tail {
            self.items[tail as usize].as_mut().expect("tail").1.next = Some(idx);
        } else {
            self.head = Some(idx);
        }
        self.tail = Some(idx);
        self.len += 1;
        idx
    }

    pub fn remove(&mut self, idx: u32) -> T {
        let (value, link) = self.items[idx as usize]
            .take()
            .expect("removing an order that is not on the list");
        match link.prev {
            Some(prev) => self.items[prev as usize].as_mut().expect("prev").1.next = link.next,
            None => self.head = link.next,
        }
        match link.next {
            Some(next) => self.items[next as usize].as_mut().expect("next").1.prev = link.prev,
            None => self.tail = link.prev,
        }
        self.free.push(idx);
        self.len -= 1;
        value
    }

    pub fn get(&self, idx: u32) -> &T {
        &self.items[idx as usize].as_ref().expect("live node").0
    }

    pub fn get_mut(&mut self, idx: u32) -> &mut T {
        &mut self.items[idx as usize].as_mut().expect("live node").0
    }

    pub fn iter(&self) -> ListIter<'_, T> {
        ListIter {
            list: self,
            next: self.head,
        }
    }
}

pub struct ListIter<'a, T> {
    list: &'a List<T>,
    next: Option<u32>,
}

impl<'a, T> Iterator for ListIter<'a, T> {
    type Item = &'a T;

    fn next(&mut self) -> Option<Self::Item> {
        let idx = self.next?;
        let (value, link) = self.list.items[idx as usize].as_ref().expect("live node");
        self.next = link.next;
        Some(value)
    }
}
