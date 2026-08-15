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
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
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

    #[must_use]
    pub fn capped(&self) -> bool {
        self.capped
    }

    /// Mark that evidence was dropped elsewhere, e.g. when this set was rebuilt from another
    /// capped set and only the marker needs to carry over.
    pub fn mark_capped(&mut self) {
        self.capped = true;
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.fills.len()
    }

    #[must_use]
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

#[cfg(test)]
mod tests {
    use super::{DistinctFillSet, MAX_DISTINCT_FILLS};

    #[test]
    fn deduplicates_identical_fills_without_capping() {
        let mut set = DistinctFillSet::new();
        set.insert(vec![1, 2].into_boxed_slice());
        set.insert(vec![1, 2].into_boxed_slice());
        set.insert(vec![1, 3].into_boxed_slice());
        assert_eq!(set.len(), 2);
        assert!(!set.capped());
    }

    #[test]
    fn insertion_past_the_cap_marks_the_set_capped_but_duplicates_do_not() {
        let mut set = DistinctFillSet::new();
        for index in 0..MAX_DISTINCT_FILLS {
            set.insert(vec![index].into_boxed_slice());
        }
        // Duplicates of already-retained fills are not dropped evidence.
        set.insert(vec![0].into_boxed_slice());
        assert!(!set.capped());
        set.insert(vec![MAX_DISTINCT_FILLS].into_boxed_slice());
        assert_eq!(set.len(), MAX_DISTINCT_FILLS);
        assert!(set.capped());
    }
}
