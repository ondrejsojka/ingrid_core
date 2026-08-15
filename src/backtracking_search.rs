//! This module implements grid-filling using a backtracking search algorithm that's mostly based on
//! recommendations in "Adaptive Strategies for Solving Constraint Satisfaction Problems" by
//! Thanasis Balafoutis. In addition to maintaining arc consistency using AC-3 and ordering
//! variables with a variant of the `dom/wdeg` heuristic, we incorporate Balafoutis's "adaptive
//! branching" concept and randomized restarts.

use float_ord::FloatOrd;
use rand::distr::weighted::WeightedIndex;
use rand::prelude::*;
use std::fmt;
use std::fmt::{Debug, Formatter};
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use crate::arc_consistency::{
    establish_arc_consistency, ArcConsistencyAdapter, ArcConsistencyFailure, EliminationSet,
};
use crate::grid_config::{Choice, Crossing, GridConfig, SlotId};
use crate::live_state::PreparedSearch;
use crate::types::WordId;
use crate::util::{build_glyph_counts_by_cell, GlyphCountsByCell};

/// If the previously-attempted slot is within this distance of the "best" (lowest-priority-value)
/// slot, we should stick with the previous one instead of switching (per Balafoutis).
pub const ADAPTIVE_BRANCHING_THRESHOLD: f32 = 0.15;

/// How many times should we loop before checking whether we've passed our deadline?
pub const INTERRUPT_FREQUENCY: usize = 10;

/// How much do we decrease the weight of each crossing every time we wipe out a domain?
/// The lower this is, the more we prioritize recent information over older information.
pub const WEIGHT_AGE_FACTOR: f32 = 0.99;

/// How do we weigh the highest-ranked N slots when choosing which one to fill next?
pub const RANDOM_SLOT_WEIGHTS: [u8; 3] = [4, 2, 1];

/// How do we weigh the highest-ranked N words when choosing a word for a given slot?
pub const RANDOM_WORD_WEIGHTS: [u8; 3] = [4, 2, 1];

/// How many live candidates, in the static ranking's order, form the pool that the dynamic value
/// ordering re-ranks by live crossing support before each word choice.
const DYNAMIC_ORDERING_POOL_SIZE: usize = 12;

/// Live crossing-support metric for placing `word_id` in `slot_id`: the sum, over the word's
/// crossings, of log1p of the number of live crossing-slot options carrying the word's glyph at
/// the crossing cell. Words whose glyphs are currently rare at crossings score low; they are the
/// likeliest to be forced and to fail fast, so the dynamic value ordering tries them first.
fn live_crossing_support(
    config: &GridConfig,
    slots: &[Slot],
    slot_id: SlotId,
    word_id: WordId,
) -> f32 {
    let slot_config = &config.slot_configs[slot_id];
    let word = &config.word_list.words[slot_config.length][word_id];
    let mut support = 0.0f32;
    let mut has_crossing = false;
    for (cell_idx, &glyph) in word.glyphs.iter().enumerate() {
        let Some(crossing) = &slot_config.crossings[cell_idx] else {
            continue;
        };
        has_crossing = true;
        let crossing_slot = &slots[crossing.other_slot_id];
        let glyph_counts_by_cell = crossing_slot
            .fixed_glyph_counts_by_cell
            .as_ref()
            .unwrap_or(&crossing_slot.glyph_counts_by_cell);
        support += (glyph_counts_by_cell[crossing.other_slot_cell][glyph] as f32).ln_1p();
    }
    // An uncrossed word cannot fail at a crossing and thus sorts as maximally supported
    // under the ordering's descending support comparison.
    if has_crossing {
        support
    } else {
        f32::INFINITY
    }
}

/// How much do we increase the backtrack limit when retrying?
pub const RETRY_GROWTH_FACTOR: f32 = 1.1;

/// A struct tracking stats about the filling process.
#[derive(Debug, Clone, Default)]
#[allow(dead_code)]
pub struct Statistics {
    pub states: usize,
    pub backtracks: usize,
    pub restricted_branchings: usize,
    pub retries: usize,
    pub total_time: Duration,
    pub try_time: Duration,
    pub initial_arc_consistency_time: Duration,
    pub choice_arc_consistency_time: Duration,
    pub elimination_arc_consistency_time: Duration,
}

/// A struct tracking the live state of a single slot during filling.
#[derive(Clone)]
pub struct Slot {
    /// Properties duplicated from `SlotConfig` for convenience.
    pub(crate) id: SlotId,
    pub(crate) length: usize,

    /// Record of which options from `slot_options` have been eliminated from this slot, stored as
    /// a Vec indexed by `WordId`, with a compact 2-byte-per-word encoding of the blame state:
    /// * `LIVE_WORD` (0) means "this option has not been eliminated (or was never available)"
    /// * `UNBLAMED_ELIMINATION` (1) means "this option has been eliminated regardless of choices"
    /// * `slot_id + 2` means "this option has been eliminated by the choice in slot `slot_id`"
    pub(crate) eliminations: Vec<u16>,

    /// To enable us to quickly validate crossing slots, we maintain a count of the number of
    /// instances of each glyph in each cell in our remaining options.
    pub(crate) glyph_counts_by_cell: GlyphCountsByCell,

    /// How many options are still available for this slot? Note that this is based on the
    /// `slot_options` from `GridConfig`, not the `words` from `WordList`, since the latter also
    /// includes hidden words that aren't available for this fill attempt.
    pub(crate) remaining_option_count: usize,

    /// How many of those remaining options are in the Preferred tier? Kept incrementally by
    /// `add_elimination`/`remove_elimination` so that `can_satisfy_minimum_preferred_words` is
    /// O(#slots) instead of rescanning every slot's options.
    pub(crate) preferred_remaining: usize,

    /// Cache of each word's tier over this slot's whole word domain, mirroring
    /// `word_list.word_tier((length, word_id)) == WordTier::Preferred` as a flat lookup for the
    /// hot elimination path and for fixed slots in the global preferred-word bound.
    pub(crate) preferred_by_word: Vec<bool>,

    // The word id explicitly chosen for this slot during the fill process (or as part of the input
    // to the fill process), if there is one. This takes precedence over `eliminations`,
    // `glyph_counts_by_cell`, and `remaining_option_count`, which will be kept in the state they
    // were in before the choice was made.
    pub(crate) fixed_word_id: Option<WordId>,
    pub(crate) fixed_glyph_counts_by_cell: Option<GlyphCountsByCell>,
}

/// `Slot::eliminations` value for a word that has not been eliminated.
pub(crate) const LIVE_WORD: u16 = 0;

/// `Slot::eliminations` value for a word eliminated regardless of any slot choices (formerly
/// `Some(None)` blame).
pub(crate) const UNBLAMED_ELIMINATION: u16 = 1;

/// The largest slot id the elimination-state encoding can represent (as `slot_id + 2` in a u16).
pub(crate) const MAX_ENCODED_SLOT_ID: usize = (u16::MAX - 2) as usize;

/// Encode the blame for an elimination into the compact `Slot::eliminations` representation.
#[inline]
fn encode_blame(blamed_slot_id: Option<SlotId>) -> u16 {
    match blamed_slot_id {
        Some(slot_id) => {
            debug_assert!(slot_id <= MAX_ENCODED_SLOT_ID);
            (slot_id as u16) + 2
        }
        None => UNBLAMED_ELIMINATION,
    }
}

impl Debug for Slot {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        f.debug_struct("Slot")
            .field("id", &self.id)
            .field(
                "eliminations",
                &format!(
                    "({} eliminations)",
                    self.eliminations
                        .iter()
                        .filter(|&&state| state != LIVE_WORD)
                        .count()
                ),
            )
            .field("remaining_option_count", &self.remaining_option_count)
            .field("fixed_word_id", &self.fixed_word_id)
            .finish_non_exhaustive()
    }
}

impl Slot {
    /// Record that a word is unavailable for a slot, along with the slot id responsible so that we
    /// can roll it back if we backtrack the relevant decision.
    pub(crate) fn add_elimination(
        &mut self,
        config: &GridConfig,
        word_id: WordId,
        blamed_slot_id: Option<SlotId>,
    ) {
        #[cfg(feature = "check_invariants")]
        assert!(
            self.fixed_word_id.is_none() && self.fixed_glyph_counts_by_cell.is_none(),
            "Editing eliminations for a fixed slot?"
        );

        self.eliminations[word_id] = encode_blame(blamed_slot_id);
        self.remaining_option_count -= 1;
        self.preferred_remaining -= usize::from(self.preferred_by_word[word_id]);

        let word = &config.word_list.words[self.length][word_id];
        for (cell_idx, &glyph) in word.glyphs.iter().enumerate() {
            self.glyph_counts_by_cell[cell_idx][glyph] -= 1;
        }
    }

    /// Record that a word is now available again for this slot.
    pub(crate) fn remove_elimination(&mut self, config: &GridConfig, word_id: WordId) {
        #[cfg(feature = "check_invariants")]
        assert!(
            self.fixed_word_id.is_none() && self.fixed_glyph_counts_by_cell.is_none(),
            "Editing eliminations for a fixed slot?"
        );

        self.eliminations[word_id] = LIVE_WORD;
        self.remaining_option_count += 1;
        self.preferred_remaining += usize::from(self.preferred_by_word[word_id]);

        let word = &config.word_list.words[self.length][word_id];
        for (cell_idx, &glyph) in word.glyphs.iter().enumerate() {
            self.glyph_counts_by_cell[cell_idx][glyph] += 1;
        }
    }

    /// Remove all eliminations that were created because of the last choice in the given slot.
    pub(crate) fn clear_eliminations(&mut self, config: &GridConfig, slot_id: SlotId) {
        let blamed_encoding = encode_blame(Some(slot_id));
        for word_id in 0..self.eliminations.len() {
            if self.eliminations[word_id] == blamed_encoding {
                self.remove_elimination(config, word_id);
            }
        }
    }

    /// Record a choice, shadowing the existing eliminations, glyph counts, etc.
    pub(crate) fn choose_word(&mut self, config: &GridConfig, word_id: WordId) {
        self.fixed_word_id = Some(word_id);
        self.fixed_glyph_counts_by_cell = Some(build_glyph_counts_by_cell(
            config.word_list,
            self.length,
            &[word_id],
        ));
    }

    /// Clear a choice. Since we only ever backtrack linearly, the previously-stored eliminations,
    /// glyph counts, etc., should still be correct.
    pub(crate) fn clear_choice(&mut self) {
        self.fixed_word_id = None;
        self.fixed_glyph_counts_by_cell = None;
    }

    /// Build a Choice struct representing this slot's single remaining word.
    pub(crate) fn get_choice(&self, config: &GridConfig) -> Option<Choice> {
        self.fixed_word_id
            .map(|word_id| Choice {
                slot_id: self.id,
                word_id,
            })
            .or_else(|| {
                if self.remaining_option_count == 1 {
                    #[cfg(feature = "check_invariants")]
                    {
                        assert_eq!(
                            config.slot_options[self.id]
                                .iter()
                                .filter(|&&word_id| self.eliminations[word_id] == LIVE_WORD)
                                .count(),
                            1,
                            "slot with one remaining option must have eliminations for all others"
                        );
                    }

                    let word_id = config.slot_options[self.id]
                        .iter()
                        .find(|&&word_id| self.eliminations[word_id] == LIVE_WORD);

                    word_id.map(|&word_id| Choice {
                        slot_id: self.id,
                        word_id,
                    })
                } else {
                    None
                }
            })
    }
}

/// Calculate the weight of a slot as defined in the `wdeg` heuristic, which is the sum of the
/// weights of any crossings it has where the other slot is still undetermined.
pub(crate) fn calculate_slot_weight(
    config: &GridConfig,
    slots: &[Slot],
    crossing_weights: &[f32],
    slot_id: SlotId,
) -> f32 {
    config.slot_configs[slot_id]
        .crossings
        .iter()
        .map(|crossing| match crossing {
            Some(Crossing {
                other_slot_id,
                crossing_id,
                ..
            }) if slots[*other_slot_id].remaining_option_count > 1 => {
                crossing_weights[*crossing_id]
            }
            _ => 0.0,
        })
        .sum()
}

/// Calculate the weights of all slots as defined in the `wdeg` heuristic.
pub(crate) fn calculate_slot_weights(
    config: &GridConfig,
    slots: &[Slot],
    crossing_weights: &[f32],
) -> Vec<f32> {
    (0..slots.len())
        .map(|slot_id| calculate_slot_weight(config, slots, crossing_weights, slot_id))
        .collect()
}

/// Calculate the priority of a slot, a measurement of how good a candidate it is to fill
/// next (where lower is better). This is an implementation of a version of the `dom/wdeg`
/// heuristic, although the specific meaning of the "weight" of each crossing depends on
/// our implementation of arc consistency.
pub(crate) fn calculate_slot_priority(
    slots: &[Slot],
    slot_weights: &[f32],
    slot_id: SlotId,
) -> f32 {
    (slots[slot_id].remaining_option_count as f32) / slot_weights[slot_id]
}

/// Preferred-tier steering of slot priority: a slot's `dom/wdeg` priority is divided by up to
/// `1 + PREFERRED_STEERING_BETA * PREFERRED_STEERING_CAP` the more live Preferred options its
/// domain still holds, so the search commits early to the slots where the preferred-word target
/// is actually attainable.
const PREFERRED_STEERING_BETA: f32 = 1.0;
const PREFERRED_STEERING_CAP: f32 = 8.0;

/// `dom/wdeg` priority (lower is better) rescaled by the preferred-tier steering term above.
fn preferred_steered_slot_priority(slots: &[Slot], slot_weights: &[f32], slot_id: SlotId) -> f32 {
    let preferred_live = (slots[slot_id].preferred_remaining as f32).min(PREFERRED_STEERING_CAP);
    calculate_slot_priority(slots, slot_weights, slot_id)
        / (1.0 + PREFERRED_STEERING_BETA * preferred_live)
}

#[derive(Debug)]
pub(crate) enum ArcConsistencyMode {
    Initial,
    Choice(Choice),
    Elimination(Choice, Option<SlotId>),
}

fn undo_provisional(
    slots: &mut [Slot],
    config: &GridConfig,
    mode: &ArcConsistencyMode,
    applied_eliminations: Option<&[EliminationSet]>,
) {
    if let Some(elimination_sets) = applied_eliminations {
        for (slot_id, eliminations) in elimination_sets.iter().enumerate() {
            for &word_id in &eliminations.eliminated_ids {
                slots[slot_id].remove_elimination(config, word_id);
            }
        }
    }
    match mode {
        ArcConsistencyMode::Choice(choice) => slots[choice.slot_id].clear_choice(),
        ArcConsistencyMode::Elimination(choice, ..) => {
            slots[choice.slot_id].remove_elimination(config, choice.word_id);
        }
        ArcConsistencyMode::Initial => {}
    }
}

/// Check the global Preferred-tier cardinality bound: a fixed slot counts if its chosen word is
/// Preferred, an unfixed slot if at least one of its live options is Preferred. Per-slot counts
/// are maintained incrementally on `Slot`, so this is O(#slots).
pub(crate) fn can_satisfy_minimum_preferred_words(
    slots: &[Slot],
    minimum_preferred_words: usize,
) -> bool {
    if minimum_preferred_words == 0 {
        return true;
    }

    slots
        .iter()
        .filter(|slot| {
            slot.fixed_word_id.map_or_else(
                || slot.preferred_remaining > 0,
                |word_id| slot.preferred_by_word[word_id],
            )
        })
        .take(minimum_preferred_words)
        .count()
        == minimum_preferred_words
}

/// Within the context of a fill attempt, either establish initial arc consistency, propagate the
/// impact of a choice, or propagate the impact of an elimination. Also update crossing weights
/// if it turns out to be impossible to achieve consistency (a "domain wipeout").
#[allow(clippy::too_many_arguments, clippy::too_many_lines)]
pub(crate) fn maintain_arc_consistency(
    config: &GridConfig,
    slots: &mut [Slot],
    crossing_weights: &mut [f32],
    slot_weights: &[f32],
    mode: &ArcConsistencyMode,
    time: &mut Duration,
    elimination_sets: &mut [EliminationSet],
    minimum_preferred_words: usize,
) -> bool {
    struct Adapter<'a> {
        config: &'a GridConfig<'a>,
        slots: &'a mut [Slot],
    }

    impl ArcConsistencyAdapter for Adapter<'_> {
        fn is_word_eliminated(&self, slot_id: SlotId, word_id: WordId) -> bool {
            self.slots[slot_id].eliminations[word_id] != LIVE_WORD
        }

        fn get_glyph_counts(&self, slot_id: SlotId) -> GlyphCountsByCell {
            self.slots[slot_id]
                .fixed_glyph_counts_by_cell
                .clone()
                .unwrap_or_else(|| self.slots[slot_id].glyph_counts_by_cell.clone())
        }

        fn get_single_option(
            &self,
            slot_id: SlotId,
            eliminations: &EliminationSet,
        ) -> Option<WordId> {
            self.slots[slot_id].fixed_word_id.or_else(|| {
                #[cfg(feature = "check_invariants")]
                {
                    let first_two = self.config.slot_options[slot_id]
                        .iter()
                        .filter(|&word_id| {
                            self.slots[slot_id].eliminations[*word_id] == LIVE_WORD
                                && !eliminations.contains(*word_id)
                        })
                        .copied()
                        .take(2)
                        .collect::<Vec<_>>();

                    assert_eq!(
                        first_two.len(),
                        1,
                        "get_single_option: called with slot that had multiple options",
                    );

                    Some(first_two[0])
                }

                #[cfg(not(feature = "check_invariants"))]
                self.config.slot_options[slot_id]
                    .iter()
                    .find(|&word_id| {
                        self.slots[slot_id].eliminations[*word_id] == LIVE_WORD
                            && !eliminations.contains(*word_id)
                    })
                    .copied()
            })
        }
    }

    let start = Instant::now();

    // First, if we're testing a choice or elimination, update the relevant state provisionally.
    match mode {
        ArcConsistencyMode::Choice(choice) => {
            slots[choice.slot_id].choose_word(config, choice.word_id);
        }

        ArcConsistencyMode::Elimination(choice, blamed_slot_id) => {
            slots[choice.slot_id].add_elimination(config, choice.word_id, *blamed_slot_id);
        }

        ArcConsistencyMode::Initial => {}
    }

    let remaining_option_counts = slots
        .iter()
        .map(|slot| {
            if slot.fixed_word_id.is_some() {
                1
            } else {
                slot.remaining_option_count
            }
        })
        .collect::<Vec<_>>();

    let fixed_slots: Vec<bool> = match mode {
        ArcConsistencyMode::Initial => {
            // When establishing initial consistency, only slots whose contents were provided verbatim
            // should be considered fixed -- other slots might happen to only have one available option,
            // but then that option could be ruled out by crossings.
            slots
                .iter()
                .map(|slot| slot.fixed_word_id.is_some())
                .collect()
        }
        _ => {
            // When maintaining consistency later on, we can treat all slots with exactly one option as
            // fixed, because all of their crossings will already have been pruned to only compatible
            // options and we'll already have removed any possible dupe-rule violations from the rest of
            // the grid. Also if we're evaluating a choice we'll treat that choice's slot as fixed.
            slots
                .iter()
                .map(|slot| remaining_option_counts[slot.id] == 1)
                .collect()
        }
    };

    let starting_slot_id = match mode {
        ArcConsistencyMode::Initial => None,
        ArcConsistencyMode::Choice(choice) | ArcConsistencyMode::Elimination(choice, _) => {
            Some(choice.slot_id)
        }
    };

    let blamed_slot_id = match mode {
        ArcConsistencyMode::Initial => None,
        ArcConsistencyMode::Choice(choice) => Some(choice.slot_id),
        ArcConsistencyMode::Elimination(_, blamed_slot_id) => *blamed_slot_id,
    };

    let success = match establish_arc_consistency(
        config,
        &Adapter { config, slots },
        &remaining_option_counts,
        crossing_weights,
        slot_weights,
        &fixed_slots,
        starting_slot_id,
        elimination_sets,
    ) {
        // If we succeeded, apply the new eliminations and then check the global preferred-word
        // bound. The latter is not a binary arc constraint, so it must be checked against all live
        // slot domains after AC has settled.
        Ok(()) => {
            for (slot_id, eliminations) in elimination_sets.iter().enumerate() {
                for &word_id in &eliminations.eliminated_ids {
                    slots[slot_id].add_elimination(config, word_id, blamed_slot_id);
                }
            }

            if can_satisfy_minimum_preferred_words(slots, minimum_preferred_words) {
                true
            } else {
                // The cardinality check rejected an otherwise arc-consistent provisional update.
                undo_provisional(slots, config, mode, Some(elimination_sets));
                false
            }
        }

        // If we failed, we need to undo any provisional changes we made above and update our
        // crossing weights to reflect the causes of the failure.
        Err(ArcConsistencyFailure { weight_updates }) => {
            undo_provisional(slots, config, mode, None);

            for (slot_id, weight) in crossing_weights.iter_mut().enumerate() {
                *weight = 1.0 + ((*weight - 1.0) * WEIGHT_AGE_FACTOR) + weight_updates[slot_id];
            }

            false
        }
    };

    *time += start.elapsed();

    success
}

/// Identify the next slot we should try to fill, based on a combination of the `dom/wdeg` priority
/// algorithm with an "adaptive branching" strategy that stays on the same slot if the "best" one
/// is close enough in priority.
fn choose_next_slot(
    slots: &[Slot],
    slot_weights: &[f32],
    last_slot_id: Option<SlotId>,
    adaptive_branching_threshold: f32,
    rng: &mut SmallRng,
    dist: &WeightedIndex<u8>,
    statistics: &mut Statistics,
) -> Option<SlotId> {
    let mut best_slot_priority: Option<f32> = None;
    let mut last_slot_priority: Option<f32> = None;

    let mut sorted_slot_ids: Vec<_> = (0..slots.len())
        .filter(|&slot_id| {
            // If the slot only has one option, whether it was chosen explicitly or implicitly, we can
            // just leave it alone.
            slots[slot_id].fixed_word_id.is_none() && slots[slot_id].remaining_option_count > 1
        })
        .collect();

    // If there are no slots left to choose from, we're done.
    if sorted_slot_ids.is_empty() {
        return None;
    }

    // Otherwise, sort the remaining slots by priority.
    sorted_slot_ids.sort_by_cached_key(|&slot_id| {
        let priority = preferred_steered_slot_priority(slots, slot_weights, slot_id);

        if best_slot_priority.is_none_or(|best_priority| best_priority > priority) {
            best_slot_priority = Some(priority);
        }

        if last_slot_id.is_some_and(|last_id| last_id == slot_id) {
            last_slot_priority = Some(priority);
        }

        FloatOrd(priority)
    });

    // If the best slot isn't that much better than the one we're on, stay with the one we're on.
    if let Some(best_slot_priority) = best_slot_priority {
        if let (Some(last_slot_id), Some(last_slot_priority)) = (last_slot_id, last_slot_priority) {
            if (last_slot_priority - best_slot_priority) < adaptive_branching_threshold {
                statistics.restricted_branchings += 1;
                return Some(last_slot_id);
            }
        }
    }

    // Otherwise, take one of the best few slots at random.
    Some(sorted_slot_ids[dist.sample(rng).min(sorted_slot_ids.len() - 1)])
}

/// A struct representing the results of a fill operation.
#[derive(Debug)]
#[allow(dead_code)]
pub struct FillSuccess {
    pub statistics: Statistics,
    pub choices: Vec<Choice>,
}

#[derive(Debug)]
pub enum FillFailure {
    HardFailure,
    Timeout,
    Abort,
    ExceededBacktrackLimit(usize),
}

/// Per-attempt constraints and controls that do not change the static grid configuration.
#[derive(Debug, Clone, Copy)]
pub struct FillOptions<'a> {
    /// Minimum number of slots whose chosen word must come from a preferred source.
    pub minimum_preferred_words: usize,
    /// An additional cancellation flag, independent of `GridConfig::abort`.
    pub abort: Option<&'a std::sync::atomic::AtomicBool>,
    /// Offset added to retry RNG seeds so concurrent workers explore different paths.
    pub rng_seed_offset: u64,
    /// How much better the best slot must be than the current one before we switch to it; see
    /// `ADAPTIVE_BRANCHING_THRESHOLD`. Parallel workers vary this to diversify search order.
    pub adaptive_branching_threshold: f32,
}

impl Default for FillOptions<'_> {
    fn default() -> Self {
        FillOptions {
            minimum_preferred_words: 0,
            abort: None,
            rng_seed_offset: 0,
            adaptive_branching_threshold: ADAPTIVE_BRANCHING_THRESHOLD,
        }
    }
}

/// Search for a valid fill for the given grid, bailing out if we reach the deadline or the
/// specified number of backtracks. We receive some state as arguments that can be shared between
/// multiple retries of the same overall search attempt.
/// Search with the legacy defaults: no preferred-word minimum and no additional cancellation flag.
#[allow(clippy::too_many_arguments)]
pub fn find_fill_for_seed(
    config: &GridConfig,
    slots: &Vec<Slot>,
    deadline: Option<Instant>,
    max_backtracks: usize,
    rng_seed: u64,
    crossing_weights: &mut [f32],
    elimination_sets: &mut [EliminationSet],
) -> Result<FillSuccess, FillFailure> {
    find_fill_for_seed_with_options(
        config,
        slots,
        deadline,
        max_backtracks,
        rng_seed,
        crossing_weights,
        elimination_sets,
        FillOptions::default(),
    )
}

#[allow(clippy::too_many_arguments)]
#[allow(clippy::too_many_lines)]
fn find_fill_for_seed_with_options(
    config: &GridConfig,
    slots: &Vec<Slot>,
    deadline: Option<Instant>,
    max_backtracks: usize,
    rng_seed: u64,
    crossing_weights: &mut [f32],
    elimination_sets: &mut [EliminationSet],
    options: FillOptions,
) -> Result<FillSuccess, FillFailure> {
    let start = Instant::now();
    let mut rng: SmallRng =
        SeedableRng::seed_from_u64(options.rng_seed_offset.wrapping_add(rng_seed));
    let mut statistics = Statistics::default();

    let mut slots: Vec<Slot> = (*slots).clone();

    // Track slot choices made so far in the process.
    let mut choices: Vec<Choice> = Vec::with_capacity(config.slot_configs.len());

    let mut last_slot_id: Option<SlotId> = None;
    let mut last_starting_word_idx: Option<usize> = None;

    let slot_dist = WeightedIndex::new(RANDOM_SLOT_WEIGHTS).unwrap();
    let word_dist = WeightedIndex::new(RANDOM_WORD_WEIGHTS).unwrap();

    // Enter the main loop:
    // * Choose an option for a slot and try to propagate constraints for it. If we succeed, we keep
    //   the choice and continue the loop.
    // * If we failed to choose an option, record that the option is unavailable and try to
    //   propagate constraints for that. If we succeed, we continue the loop, most likely trying to
    //   pick another option for the same slot but also potentially changing slots.
    // * If we also failed to propagate constraints with the chosen option being *un*available, it
    //   means the previous choice we made is untenable. Try to undo it and propagate the
    //   information that *that* choice is unavailable. Repeat until we reach a viable state, or
    //   abandon the fill attempt if we can't.
    loop {
        statistics.states += 1;

        if statistics.states % INTERRUPT_FREQUENCY == 0 {
            if let Some(deadline) = deadline {
                if Instant::now() > deadline {
                    return Err(FillFailure::Timeout);
                }
            }
        }
        if config
            .abort
            .is_some_and(|abort| abort.load(Ordering::Relaxed))
            || options
                .abort
                .is_some_and(|abort| abort.load(Ordering::Relaxed))
        {
            return Err(FillFailure::Abort);
        }

        // Choose which slot to try to fill.
        let slot_weights = calculate_slot_weights(config, &slots, crossing_weights);
        let Some(slot_id) = choose_next_slot(
            &slots,
            &slot_weights,
            last_slot_id,
            options.adaptive_branching_threshold,
            &mut rng,
            &slot_dist,
            &mut statistics,
        ) else {
            // If there are no more slots to fill, it means we're done.
            statistics.total_time = start.elapsed();

            // We need to build a `choices` array that includes both choices we made explicitly
            // and ones that were made implicitly by maintaining arc consistency.
            let choices = slots
                .into_iter()
                .map(|slot| {
                    slot.get_choice(config)
                        .expect("Failed to identify single choice for slot")
                })
                .collect();

            return Ok(FillSuccess {
                statistics,
                choices,
            });
        };

        // If we're still on the same slot as last time, start from where we left off instead of
        // rechecking previously-evaluated words.
        let starting_word_idx: usize = if Some(slot_id) == last_slot_id {
            last_starting_word_idx.unwrap_or(0)
        } else {
            0
        };

        // Take a pool of live candidates in the static ranking's order and re-rank it
        // dynamically: words whose glyphs are currently well supported at their crossings are the
        // least constraining live choices, so trying them first limits the elimination cascades
        // that lead to backtracks. The Preferred tier keeps priority over Standard so the
        // re-ranking cannot starve preferred words; ties keep the static ranking (the sort is
        // stable). This only reorders the candidates we consider, never restricts which words
        // remain available.
        let mut word_candidates: Vec<(usize, WordId, u8, f32)> = config.slot_options[slot_id]
            .iter()
            .enumerate()
            .skip(starting_word_idx)
            .filter(|&(_, &word_id)| slots[slot_id].eliminations[word_id] == LIVE_WORD)
            .take(DYNAMIC_ORDERING_POOL_SIZE)
            .map(|(idx, &word_id)| {
                (
                    idx,
                    word_id,
                    u8::from(!slots[slot_id].preferred_by_word[word_id]),
                    live_crossing_support(config, &slots, slot_id, word_id),
                )
            })
            .collect();

        assert!(
            !word_candidates.is_empty(),
            "Unable to find option for slot {:?}",
            slots[slot_id]
        );

        // Record our position so we can pick up where we left off if needed: the first pool
        // member's index in the static ordering (captured before re-ranking), so that a later
        // retry of this slot reconsiders every word at or after it without skipping any words.
        let first_static_idx = word_candidates[0].0;

        word_candidates.sort_by(|a, b| a.2.cmp(&b.2).then_with(|| b.3.total_cmp(&a.3)));

        // Choose one of the best-ranked candidates at (weighted) random.
        let word_id = word_candidates[word_dist.sample(&mut rng).min(word_candidates.len() - 1)].1;

        last_slot_id = Some(slot_id);
        last_starting_word_idx = Some(first_static_idx);

        let choice = Choice { slot_id, word_id };

        // Try to propagate the implications of making this choice to the rest of the grid.
        if maintain_arc_consistency(
            config,
            &mut slots,
            crossing_weights,
            &slot_weights,
            &ArcConsistencyMode::Choice(choice.clone()),
            &mut statistics.choice_arc_consistency_time,
            elimination_sets,
            options.minimum_preferred_words,
        ) {
            // If we successfully propagated constraints for this choice, we can record it and
            // move on to the next slot.
            choices.push(choice);
            continue;
        }

        // Otherwise, we can rule this option out. If we can successfully propagate the implications
        // of that elimination, we can move on to the next slot; otherwise, we need to keep
        // backtracking until we find a choice we can successfully propagate the reversal of.
        let mut undoing_choice = choice;
        loop {
            statistics.backtracks += 1;

            if maintain_arc_consistency(
                config,
                &mut slots,
                crossing_weights,
                &slot_weights,
                &ArcConsistencyMode::Elimination(
                    undoing_choice.clone(),
                    choices.last().map(|choice| choice.slot_id),
                ),
                &mut statistics.elimination_arc_consistency_time,
                elimination_sets,
                options.minimum_preferred_words,
            ) {
                // If we successfully propagated constraints for this elimination, we're done
                // backtracking and can return to the top-level loop.
                break;
            }

            // If we didn't, it means the previous choice is also no longer viable, because we've
            // now proven that given all previous choices, neither `slot_id = word_id`
            // nor `slot_id != word_id` are possible. We should undo the impact of that
            // choice and then continue the backtracking loop to see if it's possible to propagate
            // the opposite of the choice.
            let Some(last_choice) = choices.pop() else {
                // If there are no previous choices, we've now proven that the whole grid is
                // unsolvable.
                return Err(FillFailure::HardFailure);
            };
            undoing_choice = last_choice;

            slots[undoing_choice.slot_id].clear_choice();

            for slot in &mut slots {
                if slot.id != undoing_choice.slot_id && slot.fixed_word_id.is_none() {
                    slot.clear_eliminations(config, undoing_choice.slot_id);
                }
            }

            // If we've exceeded our backtrack limit, restart the fill process with a new seed.
            if statistics.backtracks > max_backtracks {
                return Err(FillFailure::ExceededBacktrackLimit(statistics.backtracks));
            }

            // Our cached position in the last slot's option list is now invalid.
            last_slot_id = None;
            last_starting_word_idx = None;
        }
    }
}

/// Search with the legacy defaults: no preferred-word minimum and no additional cancellation flag.
#[allow(dead_code)]
pub fn find_fill(
    config: &GridConfig,
    timeout: Option<Duration>,
    elimination_sets: Option<&mut [EliminationSet]>,
) -> Result<FillSuccess, FillFailure> {
    find_fill_with_options(config, timeout, elimination_sets, FillOptions::default())
}

/// Search for a valid fill while applying per-attempt preferred-word and cancellation controls.
#[allow(dead_code)]
pub fn find_fill_with_options(
    config: &GridConfig,
    timeout: Option<Duration>,
    elimination_sets: Option<&mut [EliminationSet]>,
    options: FillOptions,
) -> Result<FillSuccess, FillFailure> {
    let start = Instant::now();
    let deadline = timeout.map(|timeout| start + timeout);
    let prepared = PreparedSearch::new(config)?;
    find_fill_from_prepared(
        config,
        &prepared,
        start,
        deadline,
        elimination_sets,
        options,
    )
}

/// Search from an initial arc-consistent state that may be shared with sibling workers.
pub(crate) fn find_fill_from_prepared(
    config: &GridConfig,
    prepared: &PreparedSearch,
    start: Instant,
    deadline: Option<Instant>,
    elimination_sets: Option<&mut [EliminationSet]>,
    options: FillOptions,
) -> Result<FillSuccess, FillFailure> {
    if !prepared
        .root
        .can_satisfy_target(options.minimum_preferred_words)
    {
        return Err(FillFailure::HardFailure);
    }

    let mut state = prepared.root.fork(config);
    let elimination_sets = elimination_sets.unwrap_or(&mut state.elimination_sets);
    let mut max_backtracks: usize = 500;

    for retry_num in 0.. {
        match find_fill_for_seed_with_options(
            config,
            &state.slots,
            deadline,
            max_backtracks,
            retry_num,
            &mut state.crossing_weights,
            elimination_sets,
            options,
        ) {
            Ok(mut result) => {
                result.statistics.retries = retry_num as usize;
                result.statistics.try_time = result.statistics.total_time;
                result.statistics.total_time = start.elapsed();
                result.statistics.initial_arc_consistency_time =
                    prepared.initial_arc_consistency_time;
                return Ok(result);
            }
            Err(FillFailure::ExceededBacktrackLimit(_backtrack_count)) => {
                max_backtracks = (max_backtracks + 1)
                    .max((max_backtracks as f32 * RETRY_GROWTH_FACTOR) as usize);
            }
            other_error => return other_error,
        }
    }

    unreachable!();
}

#[cfg(test)]
mod tests {
    use crate::backtracking_search::{find_fill, FillFailure};
    use crate::grid_config::{
        generate_grid_config_from_template_string, render_grid, OwnedGridConfig,
    };
    use crate::test_support::{dictionary_path, word_list_source_config};
    use crate::types::GlobalWordId;
    use crate::word_list::{WordList, WordListSourceConfig, WordListSourceConfigProvider};
    use indoc::indoc;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::time::{Duration, Instant};

    fn load_word_list(max_length: usize) -> WordList {
        let word_list = WordList::new(word_list_source_config(), None, Some(max_length), Some(5));
        let word_list_errors = word_list.get_source_errors().get("0").unwrap().clone();
        assert!(
            word_list_errors.is_empty(),
            "load_word_list: failed to load: {word_list_errors:?}"
        );
        word_list
    }

    fn generate_config_with_min_score<'a>(
        word_list: &'a mut WordList,
        template: &str,
        min_score: u16,
    ) -> OwnedGridConfig<'a> {
        let template = template.trim();
        let mut config = generate_grid_config_from_template_string(word_list, template, min_score)
            .expect("test template is valid");
        config.abort = Some(Arc::new(AtomicBool::new(false)));
        config
    }

    fn generate_config<'a>(word_list: &'a mut WordList, template: &str) -> OwnedGridConfig<'a> {
        generate_config_with_min_score(word_list, template, 40)
    }

    #[test]
    fn test_find_fill_for_3x3_square() {
        let mut word_list = load_word_list(3);
        let grid_config = generate_config(
            &mut word_list,
            "
            ...
            ...
            ...
            ",
        );

        let result =
            find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

        println!("{:?}", result.statistics);
        println!(
            "{}",
            render_grid(&grid_config.to_config_ref(), &result.choices)
        );
    }

    #[test]
    fn test_find_fill_for_5x5_square() {
        let mut word_list = load_word_list(5);
        let grid_config = generate_config(
            &mut word_list,
            "
            .....
            .....
            .....
            .....
            .....
            ",
        );

        let result =
            find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

        println!("{:?}", result.statistics);
        println!(
            "{}",
            render_grid(&grid_config.to_config_ref(), &result.choices)
        );
    }

    #[test]
    fn test_find_fill_for_6x6_square() {
        let mut word_list = load_word_list(6);
        let grid_config = generate_config(
            &mut word_list,
            "
            ......
            ......
            ......
            ......
            ......
            ......
            ",
        );

        let result =
            find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

        println!("{:?}", result.statistics);
        println!(
            "{}",
            render_grid(&grid_config.to_config_ref(), &result.choices)
        );
    }

    #[test]
    fn test_find_fill_for_empty_7x7_template() {
        let mut word_list = load_word_list(7);
        let grid_config = generate_config(
            &mut word_list,
            "
            #...###
            #....##
            .......
            .......
            .......
            ##....#
            ###...#
            ",
        );

        let result =
            find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

        println!("{:?}", result.statistics);
        println!(
            "{}",
            render_grid(&grid_config.to_config_ref(), &result.choices)
        );
    }

    #[test]
    fn test_find_fill_for_partially_populated_7x7_template() {
        let mut word_list = load_word_list(7);
        let grid_config = generate_config(
            &mut word_list,
            "
            #..s###
            #..i.##
            ...m...
            .......
            .......
            ##....#
            ###...#
            ",
        );

        let result =
            find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

        println!("{:?}", result.statistics);
        println!(
            "{}",
            render_grid(&grid_config.to_config_ref(), &result.choices)
        );
    }

    #[test]
    fn test_dupe_prevention_doesnt_affect_prefilled_entries() {
        let mut word_list = load_word_list(7);
        let grid_config = generate_config(
            &mut word_list,
            "
            #..p###
            #..a.##
            ...r...
            partiii
            ...i...
            ##.e..#
            ###s..#
            ",
        );

        let result =
            find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

        println!("{:?}", result.statistics);
    }

    #[test]
    fn test_fill_fails_gracefully() {
        let mut word_list = load_word_list(7);
        let grid_config = generate_config(
            &mut word_list,
            "
            #..x###
            #....##
            ......x
            ......x
            ......x
            ##....#
            ###..x#
            ",
        );

        find_fill(&grid_config.to_config_ref(), None, None)
            .expect_err("Found an impossible fill??");
    }

    #[test]
    fn test_find_fill_for_empty_15x15_themed_template() {
        let mut word_list = load_word_list(15);
        let grid_config = generate_config(
            &mut word_list,
            "
            ....#.....#....
            ....#.....#....
            ...............
            ......##.......
            ###.....#......
            ............###
            .....#.....#...
            ....#.....#....
            ...#.....#.....
            ###............
            ......#.....###
            .......##......
            ...............
            ....#.....#....
            ....#.....#....
            ",
        );

        let result =
            find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

        println!("{:?}", result.statistics);
        println!(
            "{}",
            render_grid(&grid_config.to_config_ref(), &result.choices)
        );
    }

    #[test]
    fn test_find_fill_for_empty_15x15_cryptic_template() {
        let mut word_list = load_word_list(15);
        let grid_config = generate_config(
            &mut word_list,
            "
            ....#....#....#
            .#.#.#.#.#.#.#.
            ...............
            .#.#.#.#.#.#.#.
            ...............
            ##.#.#.#.###.#.
            ...............
            .###.#####.###.
            ...............
            .#.###.#.#.#.##
            ...............
            .#.#.#.#.#.#.#.
            ...............
            .#.#.#.#.#.#.#.
            #....#....#....
            ",
        );

        let result =
            find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

        println!("{:?}", result.statistics);
        println!(
            "{}",
            render_grid(&grid_config.to_config_ref(), &result.choices)
        );
    }

    #[test]
    fn test_find_fill_for_empty_15x15_themeless_template() {
        let mut word_list = load_word_list(15);
        let grid_config = generate_config(
            &mut word_list,
            "
            ..........#....
            ..........#....
            ..........#....
            ...#...#.......
            ....###........
            .........#.....
            ###.......#....
            ...#.......#...
            ....#.......###
            .....#.........
            ........###....
            .......#...#...
            ....#..........
            ....#..........
            ....#..........
            ",
        );

        let result =
            find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

        println!("{:?}", result.statistics);
        println!(
            "{}",
            render_grid(&grid_config.to_config_ref(), &result.choices)
        );
    }

    #[test]
    fn test_find_fill_for_partially_populated_15x15_themeless_template() {
        let mut word_list = load_word_list(15);
        let grid_config = generate_config(
            &mut word_list,
            "
            .......##......
            admirers#......
            .......t.......
            .....#.i...#...
            ....#..c..#....
            ...#...k.#.....
            ###....y......#
            ##.....f.....##
            #......i....###
            .....#.n...#...
            ....#..g..#....
            ...#...e.#.....
            .......r.......
            ......#s.......
            ......##.......
            ",
        );

        let result =
            find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

        println!("{:?}", result.statistics);
        println!(
            "{}",
            render_grid(&grid_config.to_config_ref(), &result.choices)
        );
    }

    #[test]
    fn test_abort_fill_attempt() {
        let mut word_list = load_word_list(15);
        let grid_config = generate_config_with_min_score(
            &mut word_list,
            "
            .......##......
            .......s#......
            .......t.......
            .....#.i...#...
            ....#..c..#....
            ...#...k.#.....
            ###....y......#
            ##.....f.....##
            #......i....###
            .....#.n...#...
            ....#..g..#....
            ...#...e.#.....
            .......r.......
            ......#s.......
            ......##.......
            ",
            50,
        );

        let abort = grid_config.abort.clone().unwrap();
        let start = Instant::now();

        let result = std::thread::scope(|scope| {
            let handle = scope.spawn(|| find_fill(&grid_config.to_config_ref(), None, None));
            std::thread::sleep(Duration::from_secs(1));
            abort.store(true, Ordering::Relaxed);
            handle.join().unwrap()
        });
        let result = result.unwrap_err();
        let time = start.elapsed();

        assert!(matches!(result, FillFailure::Abort));
        println!("Aborted in {time:?}");
    }

    #[test]
    fn test_add_extra_dupe_rules() {
        let mut word_list = load_word_list(7);
        let rendered_1;

        {
            let grid_config = generate_config(
                &mut word_list,
                "
                #..s###
                #..i.##
                ...m...
                .......
                .......
                ##....#
                ###...#
                ",
            );

            let result_1 =
                find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

            // Obviously we'll have to rewrite this test if the algorithm changes in
            // a way that affects the output, but w/e.
            // (Rewritten for the dynamic value ordering, which changed the fill trajectory.)
            rendered_1 = render_grid(&grid_config.to_config_ref(), &result_1.choices);
            assert_eq!(
                rendered_1,
                indoc! {"
                .ass...
                .ilia..
                imamess
                retiree
                adelina
                ..reed.
                ...sss.
                "}
                .trim()
            );
        }

        let get_id = |word_list: &WordList, word_str: &str| -> GlobalWordId {
            (
                word_str.len(),
                *word_list.word_id_by_string.get(word_str).unwrap(),
            )
        };

        let retiree_id = get_id(&word_list, "retiree");
        let sss_id = get_id(&word_list, "sss");

        word_list
            .dupe_index
            .as_mut()
            .add_dupe_pair(retiree_id, sss_id);

        {
            let grid_config = generate_config(
                &mut word_list,
                "
                #..s###
                #..i.##
                ...m...
                .......
                .......
                ##....#
                ###...#
                ",
            );

            let result_2 =
                find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

            // The extra dupe rule must actually bind: the new fill cannot contain both paired
            // words, so it differs from `result_1` (which used both "retiree" and "sss").
            // (Expectation rewritten for the dynamic value ordering's changed trajectory.)
            let rendered_2 = render_grid(&grid_config.to_config_ref(), &result_2.choices);
            assert_ne!(rendered_1, rendered_2);
            assert!(!(rendered_2.contains("retiree") && rendered_2.contains("sss")));
            assert_eq!(
                rendered_2,
                indoc! {"
                .ass...
                .slit..
                inamist
                patinae
                apelike
                ..deee.
                ...sss.
                "}
                .trim()
            );
        }
    }

    #[test]
    fn test_unusual_characters() {
        let template = "
            #...###
            #....##
            ......â
            .......
            .......
            ##....#
            ###...#
            "
        .trim();

        let mut word_list = WordList::new(
            vec![
                WordListSourceConfig {
                    id: "0".into(),
                    enabled: true,
                    provider: WordListSourceConfigProvider::Memory {
                        words: vec![("monsutâ".into(), 50), ("âbc".into(), 50)],
                    },
                    normalization: None,
                },
                WordListSourceConfig {
                    id: "1".into(),
                    enabled: true,
                    provider: WordListSourceConfigProvider::File {
                        path: dictionary_path().into(),
                    },
                    normalization: None,
                },
            ],
            None,
            Some(7),
            None,
        );

        let grid_config = generate_grid_config_from_template_string(&mut word_list, template, 40)
            .expect("test template is valid");

        let result =
            find_fill(&grid_config.to_config_ref(), None, None).expect("Failed to find a fill");

        println!("{:?}", result.statistics);
        println!(
            "{}",
            render_grid(&grid_config.to_config_ref(), &result.choices)
        );
    }
}
