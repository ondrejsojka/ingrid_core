//! Reusable root and reversible path state shared by search and variant estimation.
//!
//! Constraint propagation and slot domains are shared with the ordinary backtracker. Variant
//! estimation deliberately uses a deterministic minimum-priority slot order rather than the
//! backtracker's adaptive randomized ordering so that each fill is one leaf in a stable tree.

use float_ord::FloatOrd;
use std::time::Duration;

use crate::arc_consistency::EliminationSet;
use crate::backtracking_search::{
    calculate_slot_priority, calculate_slot_weights, can_satisfy_minimum_preferred_words,
    maintain_arc_consistency, ArcConsistencyMode, FillFailure, Slot,
};
use crate::grid_config::{Choice, GridConfig, SlotId};
use crate::types::WordId;
use crate::util::build_glyph_counts_by_cell;
use crate::word_list::WordTier;

/// Initial arc-consistent state prepared once for all search workers and later estimation.
pub struct PreparedSearch {
    pub(crate) root: LiveSearchState,
    pub(crate) initial_arc_consistency_time: Duration,
}

impl PreparedSearch {
    pub(crate) fn new(config: &GridConfig) -> Result<Self, FillFailure> {
        let (root, initial_arc_consistency_time) = LiveSearchState::new(config)?;
        Ok(Self {
            root,
            initial_arc_consistency_time,
        })
    }

    /// Time spent establishing initial arc consistency.
    pub(crate) fn initial_arc_consistency_time(&self) -> Duration {
        self.initial_arc_consistency_time
    }

    /// Smallest number of candidates any slot still has after initial arc consistency, counting a
    /// fully specified slot as one. Zero is unreachable here: a wiped-out domain fails
    /// construction instead.
    pub(crate) fn min_remaining_options(&self) -> usize {
        self.root
            .slots
            .iter()
            .map(|slot| {
                if slot.fixed_word_id.is_some() {
                    1
                } else {
                    slot.remaining_option_count
                }
            })
            .min()
            .unwrap_or(0)
    }
}

/// Reversible solver state for algorithms following one assignment path at a time.
pub(crate) struct LiveSearchState {
    pub(crate) slots: Vec<Slot>,
    pub(crate) crossing_weights: Vec<f32>,
    pub(crate) elimination_sets: Vec<EliminationSet>,
    root_crossing_weights: Vec<f32>,
}

impl LiveSearchState {
    fn new(config: &GridConfig) -> Result<(Self, Duration), FillFailure> {
        let mut slots = build_slots(config);
        let mut crossing_weights = vec![1.0; config.crossing_count];
        let mut elimination_sets = EliminationSet::build_all(config.slot_configs, config.word_list);
        let slot_weights = calculate_slot_weights(config, &slots, &crossing_weights);
        let mut initial_arc_consistency_time = Duration::default();
        // Build target-neutral domains once: the Preferred bound rejects states but never
        // eliminates values, so each worker can safely apply its own target to this shared root.
        if !maintain_arc_consistency(
            config,
            &mut slots,
            &mut crossing_weights,
            &slot_weights,
            &ArcConsistencyMode::Initial,
            &mut initial_arc_consistency_time,
            &mut elimination_sets,
            0,
        ) {
            return Err(FillFailure::HardFailure);
        }
        let root_crossing_weights = crossing_weights.clone();
        Ok((
            Self {
                slots,
                crossing_weights,
                elimination_sets,
                root_crossing_weights,
            },
            initial_arc_consistency_time,
        ))
    }

    #[must_use]
    pub(crate) fn fork(&self, config: &GridConfig) -> Self {
        Self {
            slots: self.slots.clone(),
            crossing_weights: self.crossing_weights.clone(),
            elimination_sets: EliminationSet::build_all(config.slot_configs, config.word_list),
            root_crossing_weights: self.crossing_weights.clone(),
        }
    }

    #[must_use]
    pub(crate) fn can_satisfy_target(
        &self,
        config: &GridConfig,
        minimum_preferred_words: usize,
    ) -> bool {
        can_satisfy_minimum_preferred_words(config, &self.slots, minimum_preferred_words)
    }

    /// Select the strict minimum-priority slot. Unlike ordinary search, this has no randomized
    /// top-k choice or adaptive stickiness; that makes the sampled tree deterministic.
    #[must_use]
    pub(crate) fn best_slot_by_priority(&self, config: &GridConfig) -> Option<SlotId> {
        let slot_weights = calculate_slot_weights(config, &self.slots, &self.crossing_weights);
        (0..self.slots.len())
            .filter(|&slot_id| {
                self.slots[slot_id].fixed_word_id.is_none()
                    && self.slots[slot_id].remaining_option_count > 1
            })
            .min_by_key(|&slot_id| {
                (
                    FloatOrd(calculate_slot_priority(&self.slots, &slot_weights, slot_id)),
                    slot_id,
                )
            })
    }

    pub(crate) fn live_options(
        &self,
        config: &GridConfig,
        slot_id: SlotId,
        destination: &mut Vec<WordId>,
    ) {
        destination.clear();
        destination.extend(
            config.slot_options[slot_id]
                .iter()
                .copied()
                .filter(|&word_id| self.slots[slot_id].eliminations[word_id].is_none()),
        );
    }

    pub(crate) fn apply_choice(
        &mut self,
        config: &GridConfig,
        choice: &Choice,
        minimum_preferred_words: usize,
    ) -> bool {
        let slot_weights = calculate_slot_weights(config, &self.slots, &self.crossing_weights);
        let mut elapsed = Duration::default();
        maintain_arc_consistency(
            config,
            &mut self.slots,
            &mut self.crossing_weights,
            &slot_weights,
            &ArcConsistencyMode::Choice(choice.clone()),
            &mut elapsed,
            &mut self.elimination_sets,
            minimum_preferred_words,
        )
    }

    pub(crate) fn rollback_choices(&mut self, config: &GridConfig, choices: &mut Vec<Choice>) {
        while let Some(choice) = choices.pop() {
            self.slots[choice.slot_id].clear_choice();
            for slot in &mut self.slots {
                if slot.id != choice.slot_id && slot.fixed_word_id.is_none() {
                    slot.clear_eliminations(config, choice.slot_id);
                }
            }
        }
        self.crossing_weights
            .clone_from(&self.root_crossing_weights);
    }

    #[must_use]
    pub(crate) fn complete_choices(&self, config: &GridConfig) -> Option<Vec<Choice>> {
        self.slots
            .iter()
            .map(|slot| slot.get_choice(config))
            .collect()
    }

    #[must_use]
    pub(crate) fn validate_complete_choices(
        config: &GridConfig,
        choices: &[Choice],
        minimum_preferred_words: usize,
    ) -> bool {
        let preferred_count = choices
            .iter()
            .filter(|choice| {
                let slot = &config.slot_configs[choice.slot_id];
                config.word_list.word_tier((slot.length, choice.word_id)) == WordTier::Preferred
            })
            .count();
        if preferred_count < minimum_preferred_words {
            return false;
        }

        for (index, choice) in choices.iter().enumerate() {
            let slot = &config.slot_configs[choice.slot_id];
            let dupes = config
                .word_list
                .dupe_index
                .get_dupes_by_length(
                    (slot.length, choice.word_id),
                    config.word_list.exempt_preferred_dupes,
                    &|global_word_id| {
                        config.word_list.word_tier(global_word_id) == WordTier::Preferred
                    },
                );
            for other_choice in &choices[index + 1..] {
                let other_slot = &config.slot_configs[other_choice.slot_id];
                if dupes
                    .get(&other_slot.length)
                    .is_some_and(|word_ids| word_ids.contains(&other_choice.word_id))
                {
                    return false;
                }
            }
        }
        true
    }
}

fn build_slots(config: &GridConfig) -> Vec<Slot> {
    config
        .slot_configs
        .iter()
        .map(|slot_config| {
            let glyph_counts_by_cell = build_glyph_counts_by_cell(
                config.word_list,
                slot_config.length,
                &config.slot_options[slot_config.id],
            );
            let is_fixed = slot_config
                .complete_fill(config.fill, config.width)
                .is_some();
            Slot {
                id: slot_config.id,
                length: slot_config.length,
                eliminations: vec![None; config.word_list.words[slot_config.length].len()],
                remaining_option_count: config.slot_options[slot_config.id].len(),
                fixed_word_id: is_fixed.then(|| {
                    assert_eq!(config.slot_options[slot_config.id].len(), 1);
                    config.slot_options[slot_config.id][0]
                }),
                fixed_glyph_counts_by_cell: is_fixed.then_some(glyph_counts_by_cell.clone()),
                glyph_counts_by_cell,
            }
        })
        .collect()
}
