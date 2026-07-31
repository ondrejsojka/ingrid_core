//! Bounded set of canonical slot-indexed fill keys used as certified evidence.
//!
//! Both the search scheduler and the variant estimator collect distinct valid fills to report a
//! certified lower bound. Distinctness is by canonical key; insertion past the memory cap flips a
//! marker instead of growing unboundedly.

use std::collections::BTreeSet;

use crate::types::WordId;

/// Maximum retained distinct fill keys.
pub const MAX_DISTINCT_FILLS: usize = 100_000;

/// A set of distinct fill keys with a "more fills existed than fit" marker.
#[derive(Debug, Clone, Default)]
pub struct DistinctFillSet {
    fills: BTreeSet<Box<[WordId]>>,
    capped: bool,
}

impl DistinctFillSet {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_fill(fill: Box<[WordId]>) -> Self {
        let mut set = Self::new();
        set.insert(fill);
        set
    }

    /// Insert `fill` if distinct; at the cap, remember that evidence was dropped.
    pub fn insert(&mut self, fill: Box<[WordId]>) {
        if self.fills.contains(&fill) {
            return;
        }
        if self.fills.len() >= MAX_DISTINCT_FILLS {
            self.capped = true;
        } else {
            self.fills.insert(fill);
        }
    }

    pub fn capped(&self) -> bool {
        self.capped
    }

    pub fn len(&self) -> usize {
        self.fills.len()
    }

    pub fn is_empty(&self) -> bool {
        self.fills.is_empty()
    }

    pub fn iter(&self) -> impl Iterator<Item = &Box<[WordId]>> {
        self.fills.iter()
    }
}

impl FromIterator<Box<[WordId]>> for DistinctFillSet {
    fn from_iter<T: IntoIterator<Item = Box<[WordId]>>>(iter: T) -> Self {
        let mut set = Self::new();
        for fill in iter {
            set.insert(fill);
        }
        set
    }
}
