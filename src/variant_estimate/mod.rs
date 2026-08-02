//! Runtime-bounded estimation of fills at least as Preferred-heavy as an incumbent.
//!
//! Samples traverse the solver's real constraint state with deterministic variable ordering. Value
//! proposals mix a strong incumbent guide with a rank-weighted distribution over every live value;
//! inverse-probability weighting keeps the resulting count estimator asymptotically unbiased.

mod aggregate;
mod walk;

use std::thread;
use std::time::{Duration, Instant};

use crate::fill_set::DistinctFillSet;
use crate::grid_config::GridConfig;
use crate::live_state::LiveSearchState;
use crate::parallel_search::{canonical_fill_key, PreferredFillSuccess, PreparedSearch};
use crate::types::WordId;

use self::aggregate::{retain_known_fills, summarize, Summary};
use self::walk::{collect_walks, RankProposal, WalkOutcome};

const DEFAULT_WALK_COUNT: usize = 16;
const MAX_WALK_COUNT: usize = 100_000;
const MINIMUM_EFFECTIVE_SAMPLES: f64 = 1.0;
const MINIMUM_USEFUL_BUDGET: Duration = Duration::from_millis(20);
const DEADLINE_FRACTION: f64 = 0.9;
/// Fraction of the time left before the deadline that one wave may plan to consume; whatever the
/// wave's real cost turns out to be, the next wave re-measures and resizes from the remainder.
const COHORT_SAFETY_FACTOR: f64 = 0.5;
const CALIBRATION_SEED_NAMESPACE: u64 = 0x4341_4c49_4252_4154; // "CALIBRAT"
const COHORT_SEED_NAMESPACE: u64 = 0x434f_484f_5254_0000; // "COHORT"

/// Controls for post-search variant estimation.
#[derive(Debug, Clone)]
pub struct VariantEstimateOptions {
    /// Maximum estimator/search wall-time ratio. Values above 1.0 are clamped to 1.0.
    pub runtime_ratio: f32,
    /// Number of sampling workers. `None` uses all available CPU cores.
    pub worker_count: Option<usize>,
    /// Maximum total cohort walks across every wave. Measured throughput sizes each wave.
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

/// Diagnostics over the union of every cohort wave of sequential-importance walks.
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
    /// The incumbent's choices are not one complete slot-indexed assignment.
    InvalidIncumbent,
    InsufficientBudget,
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
        /// Nominal 95% normal-approximation spread in log2 space; unreliable at very low ESS.
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
    /// Certified lower bound from search workers, the incumbent, and retained cohort fills.
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
    let mut known_fills: DistinctFillSet = incumbent
        .certified_fills
        .iter()
        .filter(|fill| fill.len() == config.slot_configs.len())
        .cloned()
        .collect();
    // The rebuilt set only learns about insertions past the cap; the search's own marker
    // would otherwise be silently dropped.
    if incumbent.certified_fills.capped() {
        known_fills.mark_capped();
    }

    // Every branch — invalid inputs, exact answers, budget shortfalls, and the sampling path —
    // computes only its outcome; the shared report tail runs exactly once below.
    let outcome = (|| {
        let Some(incumbent_words) = canonical_fill_key(config, &incumbent.fill.choices) else {
            return VariantEstimateOutcome::Inconclusive {
                reason: InconclusiveReason::InvalidIncumbent,
                sampling: None,
            };
        };

        if !valid_options(options) {
            return VariantEstimateOutcome::Inconclusive {
                reason: InconclusiveReason::InvalidOptions,
                sampling: None,
            };
        }
        let allowed = estimator_budget(search_elapsed, options);
        if allowed < MINIMUM_USEFUL_BUDGET || options.walk_count == 0 {
            return VariantEstimateOutcome::Inconclusive {
                reason: InconclusiveReason::InsufficientBudget,
                sampling: None,
            };
        }
        let deadline = start + allowed.mul_f64(DEADLINE_FRACTION);

        if let Some(count) = exact_root_count(config, prepared, minimum_preferred_words) {
            return VariantEstimateOutcome::Exact { count };
        }
        if Instant::now() >= deadline {
            return VariantEstimateOutcome::Inconclusive {
                reason: InconclusiveReason::InsufficientBudget,
                sampling: None,
            };
        }

        let maximum_option_count = config.slot_options.iter().map(Vec::len).max().unwrap_or(0);
        let proposal = RankProposal::new(maximum_option_count, options.guide_probability);
        let calibration_proposal = RankProposal::new(maximum_option_count, 1.0);
        let context = WalkContext {
            config,
            root: &prepared.root,
            minimum_preferred_words,
            incumbent_words: &incumbent_words,
            worker_count: resolve_worker_count(options.worker_count),
            rng_seed: options.rng_seed,
        };
        let calibration_start = Instant::now();
        let calibration = context.collect(
            &calibration_proposal,
            1,
            0,
            CALIBRATION_SEED_NAMESPACE,
            Some(deadline),
        );
        let first_wave = select_cohort_size(
            calibration.len(),
            calibration_start.elapsed(),
            deadline.saturating_duration_since(Instant::now()),
            options.walk_count,
        );
        if first_wave == 0 {
            return VariantEstimateOutcome::Inconclusive {
                reason: InconclusiveReason::InsufficientBudget,
                sampling: None,
            };
        }

        let batch = collect_cohort_waves(
            &context,
            &proposal,
            first_wave,
            options.walk_count,
            deadline,
        );
        retain_known_fills(&batch, &mut known_fills);
        cohort_outcome(&batch, known_fills.len())
    })();

    finish(
        minimum_preferred_words,
        &known_fills,
        start,
        search_elapsed,
        outcome,
    )
}

fn cohort_outcome(batch: &[WalkOutcome], known_lower_bound: usize) -> VariantEstimateOutcome {
    let summary = summarize(batch);
    let sampling = summary.diagnostics();
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
    if !prepared.root.can_satisfy_target(minimum_preferred_words) {
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

/// Immutable per-run inputs shared by calibration and every cohort wave.
struct WalkContext<'a> {
    config: &'a GridConfig<'a>,
    root: &'a LiveSearchState,
    minimum_preferred_words: usize,
    incumbent_words: &'a [WordId],
    worker_count: usize,
    rng_seed: u64,
}

impl WalkContext<'_> {
    fn collect(
        &self,
        proposal: &RankProposal,
        walk_limit: usize,
        start_index: usize,
        seed_namespace: u64,
        deadline: Option<Instant>,
    ) -> Vec<WalkOutcome> {
        collect_walks(
            self.config,
            self.root,
            self.minimum_preferred_words,
            self.incumbent_words,
            proposal,
            self.worker_count,
            walk_limit,
            start_index,
            self.rng_seed,
            seed_namespace,
            deadline,
        )
    }
}

/// Draw cohort waves until the walk cap or the deadline stops them, in global walk-index order.
///
/// Calibration only walks the incumbent path, which reaches a leaf and therefore costs far more
/// than the early-rejected walks that dominate a randomized cohort; sizing one fixed cohort from it
/// leaves most of the budget unspent. Each wave instead re-measures throughput from the walks it
/// actually completed and sizes its successor from the time still left. All waves draw from one
/// seed stream keyed by the global walk index, so the union is exactly the cohort a single call of
/// the same total size would have produced. Every started wave runs to completion, keeping
/// deadline truncation out of the sample; only the next wave's size reacts to elapsed time.
fn collect_cohort_waves(
    context: &WalkContext<'_>,
    proposal: &RankProposal,
    first_wave: usize,
    maximum_walk_count: usize,
    deadline: Instant,
) -> Vec<WalkOutcome> {
    let cohort_start = Instant::now();
    let mut walks = Vec::with_capacity(first_wave);
    let mut wave = first_wave;
    while wave > 0 {
        walks.extend(context.collect(proposal, wave, walks.len(), COHORT_SEED_NAMESPACE, None));
        wave = select_cohort_size(
            walks.len(),
            cohort_start.elapsed(),
            deadline.saturating_duration_since(Instant::now()),
            maximum_walk_count.saturating_sub(walks.len()),
        );
    }
    walks
}

/// Size the next wave of walks from a completed measurement of walk cost.
fn select_cohort_size(
    completed_walks: usize,
    measured_elapsed: Duration,
    remaining: Duration,
    maximum_walk_count: usize,
) -> usize {
    if completed_walks == 0
        || measured_elapsed.is_zero()
        || remaining.is_zero()
        || maximum_walk_count == 0
    {
        return 0;
    }
    let observed_throughput = completed_walks as f64 / measured_elapsed.as_secs_f64();
    let feasible = (observed_throughput * remaining.as_secs_f64() * COHORT_SAFETY_FACTOR)
        .floor()
        .min(maximum_walk_count as f64);
    feasible as usize
}

fn valid_options(options: &VariantEstimateOptions) -> bool {
    options.runtime_ratio.is_finite()
        && options.runtime_ratio >= 0.0
        && options.guide_probability.is_finite()
        && (0.0..1.0).contains(&options.guide_probability)
        && options.walk_count <= MAX_WALK_COUNT
}

fn estimator_budget(search_elapsed: Duration, options: &VariantEstimateOptions) -> Duration {
    let ratio_budget = search_elapsed.mul_f64(f64::from(options.runtime_ratio).clamp(0.0, 1.0));
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

fn finish(
    minimum_preferred_words: usize,
    known_fills: &DistinctFillSet,
    start: Instant,
    search_elapsed: Duration,
    outcome: VariantEstimateOutcome,
) -> VariantEstimate {
    let elapsed = start.elapsed();
    VariantEstimate {
        minimum_preferred_words,
        known_distinct_fills: known_fills.len(),
        known_distinct_fills_capped: known_fills.capped(),
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
