use rand::prelude::{SeedableRng, SmallRng};
use rand::RngExt;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc;
use std::thread;
use std::time::Instant;

use crate::grid_config::{Choice, GridConfig};
use crate::live_state::LiveSearchState;
use crate::types::WordId;

const RESULT_BATCH_SIZE: usize = 256;

#[derive(Debug)]
pub(super) enum WalkOutcome {
    Accepted {
        log2_weight: f64,
        fill: Box<[WordId]>,
    },
    Rejected,
}

struct IndexedOutcome {
    index: usize,
    outcome: WalkOutcome,
}

#[derive(Debug, Clone, Copy)]
struct Interrupted;

type Interruptible<T> = Result<T, Interrupted>;

/// Precomputed cumulative rank weights for all option counts in one estimator run.
pub(super) struct RankProposal {
    cumulative: Vec<f64>,
    guide_probability: f64,
}

impl RankProposal {
    pub(super) fn new(maximum_option_count: usize, guide_probability: f64) -> Self {
        let mut total = 0.0;
        let cumulative = (1..=maximum_option_count)
            .map(|rank| {
                total += 1.0 / (rank as f64).powi(2);
                total
            })
            .collect();
        Self {
            cumulative,
            guide_probability,
        }
    }

    fn sample(
        &self,
        option_count: usize,
        incumbent_index: Option<usize>,
        rng: &mut SmallRng,
    ) -> (usize, f64) {
        let rank_total = self.cumulative[option_count - 1];
        let use_guide = incumbent_index.is_some() && rng.random::<f64>() < self.guide_probability;
        let selected = if use_guide {
            incumbent_index.unwrap()
        } else {
            let target = rng.random::<f64>() * rank_total;
            self.cumulative[..option_count]
                .partition_point(|&cumulative| cumulative <= target)
                .min(option_count - 1)
        };
        let rank_probability = (1.0 / ((selected + 1) as f64).powi(2)) / rank_total;
        let probability = if incumbent_index.is_some() {
            (1.0 - self.guide_probability).mul_add(
                rank_probability,
                if incumbent_index == Some(selected) {
                    self.guide_probability
                } else {
                    0.0
                },
            )
        } else {
            rank_probability
        };
        (selected, probability)
    }
}

#[allow(clippy::too_many_arguments)]
pub(super) fn collect_walks(
    config: &GridConfig,
    root: &LiveSearchState,
    minimum_preferred_words: usize,
    incumbent_words: &[WordId],
    proposal: &RankProposal,
    worker_count: usize,
    walk_limit: usize,
    rng_seed: u64,
    seed_namespace: u64,
    deadline: Option<Instant>,
) -> Vec<WalkOutcome> {
    if walk_limit == 0 {
        return Vec::new();
    }
    let worker_count = worker_count.min(walk_limit);
    let next_index = AtomicUsize::new(0);
    let mut outcomes = thread::scope(|scope| {
        let (sender, receiver) = mpsc::channel::<Vec<IndexedOutcome>>();
        for _ in 0..worker_count {
            let sender = sender.clone();
            let next_index = &next_index;
            scope.spawn(move || {
                let mut state = root.fork(config);
                let mut batch = Vec::with_capacity(RESULT_BATCH_SIZE);
                loop {
                    let index = next_index.fetch_add(1, Ordering::Relaxed);
                    if index >= walk_limit
                        || deadline.is_some_and(|deadline| Instant::now() >= deadline)
                    {
                        break;
                    }
                    let mut rng = SmallRng::seed_from_u64(splitmix64(
                        rng_seed ^ seed_namespace ^ index as u64,
                    ));
                    match run_walk(
                        config,
                        state,
                        minimum_preferred_words,
                        incumbent_words,
                        proposal,
                        deadline,
                        &mut rng,
                    ) {
                        Ok((outcome, reusable_state)) => {
                            state = reusable_state;
                            batch.push(IndexedOutcome { index, outcome });
                            if batch.len() == RESULT_BATCH_SIZE
                                && sender.send(std::mem::take(&mut batch)).is_err()
                            {
                                return;
                            }
                        }
                        Err(Interrupted) => break,
                    }
                }
                if !batch.is_empty() {
                    let _ = sender.send(batch);
                }
            });
        }
        drop(sender);
        let mut outcomes = Vec::new();
        for mut batch in receiver {
            outcomes.append(&mut batch);
        }
        // Restore walk-index order so aggregation is deterministic and seed reproducibility
        // holds across worker counts.
        outcomes.sort_unstable_by_key(|outcome| outcome.index);
        outcomes
    });
    outcomes.drain(..).map(|indexed| indexed.outcome).collect()
}

fn run_walk(
    config: &GridConfig,
    mut state: LiveSearchState,
    minimum_preferred_words: usize,
    incumbent_words: &[WordId],
    proposal: &RankProposal,
    deadline: Option<Instant>,
    rng: &mut SmallRng,
) -> Interruptible<(WalkOutcome, LiveSearchState)> {
    let mut explicit_choices = Vec::new();
    let mut live_options = Vec::new();
    let mut log2_probability: f64 = 0.0;

    let outcome = loop {
        if deadline.is_some_and(|deadline| Instant::now() >= deadline) {
            return Err(Interrupted);
        }
        let Some(slot_id) = state.best_slot_by_priority(config) else {
            let Some(choices) = state.complete_choices(config) else {
                break WalkOutcome::Rejected;
            };
            if !LiveSearchState::validate_complete_choices(
                config,
                &choices,
                minimum_preferred_words,
            ) {
                break WalkOutcome::Rejected;
            }
            let log2_weight = -log2_probability;
            break if log2_weight.is_finite() {
                WalkOutcome::Accepted {
                    log2_weight,
                    fill: choices
                        .iter()
                        .map(|choice| choice.word_id)
                        .collect::<Vec<_>>()
                        .into_boxed_slice(),
                }
            } else {
                WalkOutcome::Rejected
            };
        };

        state.live_options(config, slot_id, &mut live_options);
        if live_options.is_empty() {
            break WalkOutcome::Rejected;
        }
        let incumbent_index = incumbent_words
            .get(slot_id)
            .and_then(|incumbent| live_options.iter().position(|word_id| word_id == incumbent));
        let (option_index, probability) = proposal.sample(live_options.len(), incumbent_index, rng);
        log2_probability += probability.log2();
        let choice = Choice {
            slot_id,
            word_id: live_options[option_index],
        };
        if state.apply_choice(config, &choice, minimum_preferred_words) {
            explicit_choices.push(choice);
        } else {
            break WalkOutcome::Rejected;
        }
    };

    state.rollback_choices(config, &mut explicit_choices);
    Ok((outcome, state))
}

fn splitmix64(mut value: u64) -> u64 {
    value = value.wrapping_add(0x9e37_79b9_7f4a_7c15);
    value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    value ^ (value >> 31)
}

#[cfg(test)]
mod tests {
    use super::RankProposal;
    use rand::prelude::{SeedableRng, SmallRng};

    #[test]
    fn rank_proposal_probabilities_match_sampling_components() {
        let proposal = RankProposal::new(3, 0.8);
        let rank_total = 1.0 + 0.25 + 1.0 / 9.0;
        let mut rng = SmallRng::seed_from_u64(7);
        for _ in 0..100 {
            let (selected, probability) = proposal.sample(3, Some(1), &mut rng);
            let rank_probability = (1.0 / ((selected + 1) as f64).powi(2)) / rank_total;
            let expected = 0.2_f64.mul_add(rank_probability, if selected == 1 { 0.8 } else { 0.0 });
            assert!((probability - expected).abs() < f64::EPSILON);

            let (selected, probability) = proposal.sample(3, None, &mut rng);
            let expected = (1.0 / ((selected + 1) as f64).powi(2)) / rank_total;
            assert!((probability - expected).abs() < f64::EPSILON);
        }
    }
}
