//! Runtime-bounded estimation of fills at least as Preferred-heavy as an incumbent.
//!
//! Samples traverse the solver's real constraint state with deterministic variable ordering. Value
//! proposals mix a strong incumbent guide with a rank-weighted distribution over every live value;
//! inverse-probability weighting keeps the resulting count estimator asymptotically unbiased.

mod aggregate;
mod walk;

use std::collections::BTreeSet;
use std::thread;
use std::time::{Duration, Instant};

use crate::grid_config::GridConfig;
use crate::live_state::LiveSearchState;
use crate::parallel_search::{PreferredFillSuccess, PreparedSearch};
use crate::types::WordId;

use self::aggregate::{retain_known_fills, summarize, Summary};
use self::walk::{collect_walks, RankProposal, SampleBatch};

const DEFAULT_WALK_COUNT: usize = 16;
const MAX_WALK_COUNT: usize = 100_000;
const MAX_KNOWN_FILLS: usize = 100_000;
const MINIMUM_EFFECTIVE_SAMPLES: f64 = 1.0;
const MINIMUM_USEFUL_BUDGET: Duration = Duration::from_millis(20);
const DEADLINE_FRACTION: f64 = 0.9;

/// Controls for post-search variant estimation.
#[derive(Debug, Clone)]
pub struct VariantEstimateOptions {
    /// Maximum estimator/search wall-time ratio. Values above 0.5 are clamped to 0.5.
    pub runtime_ratio: f32,
    /// Number of sampling workers. `None` uses all available CPU cores.
    pub worker_count: Option<usize>,
    /// Fixed cohort size. A deadline-truncated cohort is never used for a numerical estimate.
    pub walk_count: usize,
    /// Seed from which deterministic per-walk random streams are derived.
    pub rng_seed: u64,
    /// Optional absolute cap, applied in addition to `runtime_ratio`.
    pub maximum_duration: Option<Duration>,
    /// Per-step probability of selecting the incumbent value when it remains live.
    pub guide_probability: f64,
}

impl Default for VariantEstimateOptions {
    fn default() -> Self {
        Self {
            runtime_ratio: 0.45,
            worker_count: None,
            walk_count: DEFAULT_WALK_COUNT,
            rng_seed: 0,
            maximum_duration: None,
            guide_probability: 0.98,
        }
    }
}

/// Diagnostics for one fixed cohort of sequential-importance walks.
#[derive(Debug, Clone)]
pub struct SamplingDiagnostics {
    pub walk_count: usize,
    pub accepted_walk_count: usize,
    pub effective_sample_size: f64,
}

/// Why no numerical estimate is available.
#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum InconclusiveReason {
    InvalidOptions,
    InsufficientBudget,
    Interrupted,
    InsufficientEvidence,
}

/// The estimator's mutually exclusive result payload.
#[derive(Debug, Clone)]
pub enum VariantEstimateOutcome {
    Exact {
        count: u64,
    },
    Estimated {
        count: f64,
        slack_bits: f64,
        interval_bits: (f64, f64),
        sampling: SamplingDiagnostics,
    },
    Inconclusive {
        reason: InconclusiveReason,
        sampling: Option<SamplingDiagnostics>,
    },
}

/// Estimated multiplicity and diagnostics for one Preferred-word threshold.
#[derive(Debug, Clone)]
pub struct VariantEstimate {
    pub minimum_preferred_words: usize,
    /// Certified lower bound consisting of the incumbent and retained distinct sampled fills.
    pub known_distinct_fills: usize,
    /// True if retention stopped at the internal distinct-fill memory cap.
    pub known_distinct_fills_capped: bool,
    pub elapsed: Duration,
    pub search_runtime_ratio: f32,
    pub outcome: VariantEstimateOutcome,
}

/// Estimate distinct fills containing at least as many Preferred entries as `incumbent`.
///
/// `prepared` must be the same root state used for the search that produced `incumbent`; this avoids
/// rebuilding slot domains and rerunning initial arc consistency.
#[must_use]
pub fn estimate_variants(
    config: &GridConfig,
    prepared: &PreparedSearch,
    incumbent: &PreferredFillSuccess,
    search_elapsed: Duration,
    options: &VariantEstimateOptions,
) -> VariantEstimate {
    let start = Instant::now();
    let minimum_preferred_words = incumbent.preferred_word_count;
    let mut known_fills = BTreeSet::from([choice_word_ids(&incumbent.fill.choices)]);

    if !valid_options(options) {
        return finish(
            minimum_preferred_words,
            &known_fills,
            false,
            start,
            search_elapsed,
            VariantEstimateOutcome::Inconclusive {
                reason: InconclusiveReason::InvalidOptions,
                sampling: None,
            },
        );
    }

    let allowed = estimator_budget(search_elapsed, options);
    if allowed < MINIMUM_USEFUL_BUDGET || options.walk_count == 0 {
        return finish(
            minimum_preferred_words,
            &known_fills,
            false,
            start,
            search_elapsed,
            VariantEstimateOutcome::Inconclusive {
                reason: InconclusiveReason::InsufficientBudget,
                sampling: None,
            },
        );
    }
    let deadline = start + allowed.mul_f64(DEADLINE_FRACTION);

    if let Some(count) = exact_root_count(config, prepared, minimum_preferred_words) {
        return finish(
            minimum_preferred_words,
            &known_fills,
            false,
            start,
            search_elapsed,
            VariantEstimateOutcome::Exact { count },
        );
    }
    if Instant::now() >= deadline {
        return finish(
            minimum_preferred_words,
            &known_fills,
            false,
            start,
            search_elapsed,
            VariantEstimateOutcome::Inconclusive {
                reason: InconclusiveReason::InsufficientBudget,
                sampling: None,
            },
        );
    }

    let incumbent_words = choice_word_ids(&incumbent.fill.choices);
    let proposal = RankProposal::new(
        config.slot_options.iter().map(Vec::len).max().unwrap_or(0),
        options.guide_probability,
    );
    let batch = collect_walks(
        config,
        &prepared.root,
        minimum_preferred_words,
        &incumbent_words,
        &proposal,
        resolve_worker_count(options.worker_count),
        options.walk_count,
        options.rng_seed,
        deadline,
    );
    let known_distinct_fills_capped =
        retain_known_fills(&batch.outcomes, &mut known_fills, MAX_KNOWN_FILLS);
    let outcome = cohort_outcome(&batch, known_fills.len());

    finish(
        minimum_preferred_words,
        &known_fills,
        known_distinct_fills_capped,
        start,
        search_elapsed,
        outcome,
    )
}

fn cohort_outcome(batch: &SampleBatch, known_lower_bound: usize) -> VariantEstimateOutcome {
    let summary = summarize(&batch.outcomes);
    let sampling = summary.diagnostics();
    if !batch.complete {
        return VariantEstimateOutcome::Inconclusive {
            reason: InconclusiveReason::Interrupted,
            sampling: Some(sampling),
        };
    }
    match summary.estimate(known_lower_bound, MINIMUM_EFFECTIVE_SAMPLES) {
        Some(Summary {
            count,
            slack_bits,
            interval_bits,
        }) => VariantEstimateOutcome::Estimated {
            count,
            slack_bits,
            interval_bits,
            sampling,
        },
        None => VariantEstimateOutcome::Inconclusive {
            reason: InconclusiveReason::InsufficientEvidence,
            sampling: Some(sampling),
        },
    }
}

fn exact_root_count(
    config: &GridConfig,
    prepared: &PreparedSearch,
    minimum_preferred_words: usize,
) -> Option<u64> {
    if !prepared
        .root
        .can_satisfy_target(config, minimum_preferred_words)
    {
        return Some(0);
    }
    if prepared.root.best_slot_by_priority(config).is_some() {
        return None;
    }
    let Some(choices) = prepared.root.complete_choices(config) else {
        return Some(0);
    };
    Some(u64::from(LiveSearchState::validate_complete_choices(
        config,
        &choices,
        minimum_preferred_words,
    )))
}

fn valid_options(options: &VariantEstimateOptions) -> bool {
    options.runtime_ratio.is_finite()
        && options.runtime_ratio >= 0.0
        && options.guide_probability.is_finite()
        && (0.0..1.0).contains(&options.guide_probability)
        && options.walk_count <= MAX_WALK_COUNT
}

fn estimator_budget(search_elapsed: Duration, options: &VariantEstimateOptions) -> Duration {
    let ratio_budget = search_elapsed.mul_f64(f64::from(options.runtime_ratio).clamp(0.0, 0.5));
    options
        .maximum_duration
        .map_or(ratio_budget, |maximum| ratio_budget.min(maximum))
}

fn resolve_worker_count(requested: Option<usize>) -> usize {
    requested
        .filter(|&count| count > 0)
        .or_else(|| thread::available_parallelism().ok().map(usize::from))
        .unwrap_or(1)
}

fn choice_word_ids(choices: &[crate::grid_config::Choice]) -> Box<[WordId]> {
    choices
        .iter()
        .map(|choice| choice.word_id)
        .collect::<Vec<_>>()
        .into_boxed_slice()
}

fn finish(
    minimum_preferred_words: usize,
    known_fills: &BTreeSet<Box<[WordId]>>,
    known_distinct_fills_capped: bool,
    start: Instant,
    search_elapsed: Duration,
    outcome: VariantEstimateOutcome,
) -> VariantEstimate {
    let elapsed = start.elapsed();
    VariantEstimate {
        minimum_preferred_words,
        known_distinct_fills: known_fills.len(),
        known_distinct_fills_capped,
        elapsed,
        search_runtime_ratio: duration_ratio(elapsed, search_elapsed),
        outcome,
    }
}

fn duration_ratio(numerator: Duration, denominator: Duration) -> f32 {
    if denominator.is_zero() {
        f32::INFINITY
    } else {
        (numerator.as_secs_f64() / denominator.as_secs_f64()) as f32
    }
}

#[cfg(test)]
mod tests;
