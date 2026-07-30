//! Runtime-bounded estimation of distinct fills at least as Preferred-heavy as an incumbent.
//!
//! Each sample is a sequential-importance walk through the solver's real MAC search state. A walk
//! contributes the inverse of its exact proposal probability at a valid leaf and zero at a
//! contradiction. The arithmetic mean is therefore an estimate of the number of valid leaves in
//! the deterministic variable-ordering tree.

use crate::backtracking_search::{FillFailure, LiveSearchState, MaintainResult};
use crate::grid_config::{Choice, GridConfig};
use crate::parallel_search::PreferredFillSuccess;
use crate::types::WordId;
use rand::prelude::{SeedableRng, SmallRng};
use rand::RngExt;
use std::collections::BTreeSet;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

const DEFAULT_MAX_KNOWN_FILLS: usize = 100_000;
const MAX_FIXED_COHORT: usize = 100_000;
const MINIMUM_USEFUL_BUDGET: Duration = Duration::from_millis(20);
const DEADLINE_FRACTION: f64 = 0.9;
const RESULT_BATCH_SIZE: usize = 256;

/// Controls for the post-search variant estimator.
#[derive(Debug, Clone)]
pub struct VariantEstimateOptions {
    /// Maximum estimator/search wall-time ratio. Values above 0.5 are clamped to 0.5.
    pub runtime_ratio: f32,
    /// Number of independent walk workers. `None` uses all available CPU cores.
    pub worker_count: Option<usize>,
    /// Minimum completed walks required before a numerical estimate is reported.
    pub minimum_walks: usize,
    /// Seed from which deterministic per-walk random streams are derived.
    pub rng_seed: u64,
    /// Optional absolute cap, applied in addition to `runtime_ratio`.
    pub maximum_duration: Option<Duration>,
    /// Optional exact walk cap. Primarily useful for reproducible experiments.
    pub maximum_walks: Option<usize>,
    /// Probability of sampling uniformly rather than from the rank-biased proposal.
    pub uniform_proposal_fraction: f64,
    /// Particles per SMC replicate when ordinary walks have sparse acceptance.
    pub smc_particle_count: usize,
    /// Maximum number of distinct sampled fills retained as a certified lower bound.
    pub maximum_known_fills: usize,
}

impl Default for VariantEstimateOptions {
    fn default() -> Self {
        Self {
            runtime_ratio: 0.45,
            worker_count: None,
            minimum_walks: 8,
            rng_seed: 0,
            maximum_duration: None,
            maximum_walks: None,
            uniform_proposal_fraction: 0.1,
            smc_particle_count: 8,
            maximum_known_fills: DEFAULT_MAX_KNOWN_FILLS,
        }
    }
}

/// Sampling process used for the reported diagnostics.
#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum VariantEstimateMethod {
    Exact,
    ImportanceWalks,
    SequentialMonteCarlo,
}

/// Why a variant-estimation run did or did not produce a numerical estimate.
#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum VariantEstimateStatus {
    Estimated,
    ExactOne,
    ExactZero,
    InsufficientBudget,
    InsufficientEvidence,
    InvalidOptions,
}

/// Estimated multiplicity of fills satisfying a Preferred-word threshold.
#[derive(Debug, Clone)]
pub struct VariantEstimate {
    pub status: VariantEstimateStatus,
    pub method: VariantEstimateMethod,
    pub minimum_preferred_words: usize,
    /// Certified lower bound consisting of the incumbent and retained distinct sampled fills.
    pub known_distinct_fills: usize,
    /// True if `known_distinct_fills` stopped growing at `maximum_known_fills`.
    pub known_distinct_fills_capped: bool,
    pub estimated_fill_count: Option<f64>,
    pub estimated_slack_bits: Option<f64>,
    pub interval_slack_bits: Option<(f64, f64)>,
    pub walk_count: usize,
    pub accepted_walk_count: usize,
    pub effective_sample_size: f64,
    pub elapsed: Duration,
    pub search_runtime_ratio: f32,
}

#[derive(Debug)]
struct WalkOutcome {
    walk_id: usize,
    log2_weight: Option<f64>,
    fill: Option<Box<[WordId]>>,
}

struct SampleBatch {
    outcomes: Vec<WalkOutcome>,
    complete: bool,
}

struct SmcParticle {
    state: LiveSearchState,
    explicit_choices: Vec<Choice>,
    complete: bool,
}

enum WalkResult {
    Complete {
        log2_weight: f64,
        fill: Box<[WordId]>,
    },
    Contradiction,
    Interrupted,
}

/// Estimate distinct fills containing at least as many Preferred entries as `incumbent`.
#[must_use]
pub fn estimate_variants(
    config: &GridConfig,
    incumbent: &PreferredFillSuccess,
    search_elapsed: Duration,
    options: VariantEstimateOptions,
) -> VariantEstimate {
    estimate_for_target(
        config,
        incumbent.preferred_word_count,
        Some(&incumbent.fill.choices),
        search_elapsed,
        options,
    )
}

fn estimate_for_target(
    config: &GridConfig,
    minimum_preferred_words: usize,
    incumbent_choices: Option<&[Choice]>,
    search_elapsed: Duration,
    options: VariantEstimateOptions,
) -> VariantEstimate {
    let estimator_start = Instant::now();
    let mut known_fills = BTreeSet::new();
    if let Some(choices) = incumbent_choices {
        known_fills.insert(choice_word_ids(choices));
    }
    if !options.runtime_ratio.is_finite()
        || options.runtime_ratio < 0.0
        || !options.uniform_proposal_fraction.is_finite()
        || options.minimum_walks > MAX_FIXED_COHORT
        || options
            .maximum_walks
            .is_some_and(|walks| walks > MAX_FIXED_COHORT)
    {
        return empty_estimate(
            VariantEstimateStatus::InvalidOptions,
            minimum_preferred_words,
            known_fills.len(),
            estimator_start.elapsed(),
            search_elapsed,
        );
    }
    let allowed = estimator_budget(search_elapsed, &options);
    let deadline = estimator_start + allowed.mul_f64(DEADLINE_FRACTION);

    if allowed < MINIMUM_USEFUL_BUDGET || options.maximum_walks == Some(0) {
        return empty_estimate(
            VariantEstimateStatus::InsufficientBudget,
            minimum_preferred_words,
            known_fills.len(),
            estimator_start.elapsed(),
            search_elapsed,
        );
    }

    let root = match LiveSearchState::new(config, minimum_preferred_words, deadline) {
        Ok(root) => root,
        Err(FillFailure::HardFailure) => {
            return exact_estimate(
                VariantEstimateStatus::ExactZero,
                minimum_preferred_words,
                0,
                0.0,
                f64::NEG_INFINITY,
                estimator_start.elapsed(),
                search_elapsed,
            );
        }
        Err(_) => {
            return empty_estimate(
                VariantEstimateStatus::InsufficientBudget,
                minimum_preferred_words,
                known_fills.len(),
                estimator_start.elapsed(),
                search_elapsed,
            );
        }
    };

    let Ok(root_slot) = root.choose_next_slot(config) else {
        return empty_estimate(
            VariantEstimateStatus::InsufficientBudget,
            minimum_preferred_words,
            known_fills.len(),
            estimator_start.elapsed(),
            search_elapsed,
        );
    };
    if root_slot.is_none() {
        let choices = match root.complete_choices(config) {
            Ok(Some(choices)) => choices,
            Ok(None) => {
                return exact_estimate(
                    VariantEstimateStatus::ExactZero,
                    minimum_preferred_words,
                    0,
                    0.0,
                    f64::NEG_INFINITY,
                    estimator_start.elapsed(),
                    search_elapsed,
                );
            }
            Err(()) => {
                return empty_estimate(
                    VariantEstimateStatus::InsufficientBudget,
                    minimum_preferred_words,
                    known_fills.len(),
                    estimator_start.elapsed(),
                    search_elapsed,
                );
            }
        };
        match root.validate_complete_choices(config, &choices, minimum_preferred_words) {
            Some(true) => {
                return exact_estimate(
                    VariantEstimateStatus::ExactOne,
                    minimum_preferred_words,
                    1,
                    1.0,
                    0.0,
                    estimator_start.elapsed(),
                    search_elapsed,
                );
            }
            Some(false) => {
                return exact_estimate(
                    VariantEstimateStatus::ExactZero,
                    minimum_preferred_words,
                    0,
                    0.0,
                    f64::NEG_INFINITY,
                    estimator_start.elapsed(),
                    search_elapsed,
                );
            }
            None => {
                return empty_estimate(
                    VariantEstimateStatus::InsufficientBudget,
                    minimum_preferred_words,
                    known_fills.len(),
                    estimator_start.elapsed(),
                    search_elapsed,
                );
            }
        }
    }

    if Instant::now() >= deadline {
        return empty_estimate(
            VariantEstimateStatus::InsufficientBudget,
            minimum_preferred_words,
            known_fills.len(),
            estimator_start.elapsed(),
            search_elapsed,
        );
    }

    let worker_count = resolve_worker_count(options.worker_count);
    let uniform_fraction = options.uniform_proposal_fraction.clamp(0.0, 1.0);
    let mut known_distinct_fills_capped = false;

    let (mut batch, method) = if let Some(walk_limit) = options.maximum_walks {
        (
            run_independent_walks(
                config,
                &root,
                minimum_preferred_words,
                worker_count,
                walk_limit,
                uniform_fraction,
                options.rng_seed,
                0,
                deadline,
            ),
            VariantEstimateMethod::ImportanceWalks,
        )
    } else {
        // Timed samples are a throughput pilot only: path runtime is outcome-dependent, so using
        // the deadline-truncated cohort in the arithmetic mean would bias the count. The reported
        // estimate comes from a second, fixed-size cohort that must finish in its entirety.
        let pilot_start = Instant::now();
        let pilot = run_independent_walks(
            config,
            &root,
            minimum_preferred_words,
            worker_count,
            usize::MAX,
            uniform_fraction,
            options.rng_seed,
            0,
            estimator_start + allowed.mul_f64(0.25),
        );
        known_distinct_fills_capped |= retain_known_fills(
            &pilot.outcomes,
            &mut known_fills,
            options.maximum_known_fills,
        );
        let use_smc =
            outcome_effective_sample_size(&pilot.outcomes) < 4.0 && options.smc_particle_count > 1;
        if use_smc {
            let particle_count = options.smc_particle_count.clamp(2, 32);
            let smc_worker_count = worker_count.min((32 / particle_count).max(1));
            (
                run_smc_replicates(
                    config,
                    &root,
                    minimum_preferred_words,
                    smc_worker_count,
                    particle_count,
                    options.minimum_walks.max(4),
                    uniform_fraction,
                    options.rng_seed,
                    deadline,
                ),
                VariantEstimateMethod::SequentialMonteCarlo,
            )
        } else {
            let pilot_seconds = pilot_start.elapsed().as_secs_f64().max(f64::MIN_POSITIVE);
            let remaining_seconds = deadline
                .saturating_duration_since(Instant::now())
                .as_secs_f64();
            let conservative_throughput =
                (pilot.outcomes.len() as f64 / pilot_seconds) * remaining_seconds * 0.5;
            let walk_limit = (conservative_throughput as usize)
                .max(options.minimum_walks)
                .min(MAX_FIXED_COHORT);
            (
                run_independent_walks(
                    config,
                    &root,
                    minimum_preferred_words,
                    worker_count,
                    walk_limit,
                    uniform_fraction,
                    options.rng_seed,
                    0x4d41_494e_0000_0000,
                    deadline,
                ),
                VariantEstimateMethod::ImportanceWalks,
            )
        }
    };

    if !batch.complete {
        batch.outcomes.clear();
        known_fills.clear();
        if let Some(choices) = incumbent_choices {
            known_fills.insert(choice_word_ids(choices));
        }
        known_distinct_fills_capped = false;
    }

    aggregate_outcomes(
        batch.outcomes,
        &mut known_fills,
        minimum_preferred_words,
        estimator_start,
        search_elapsed,
        &options,
        method,
        known_distinct_fills_capped,
        !batch.complete,
    )
}

#[allow(clippy::too_many_arguments)]
fn run_independent_walks(
    config: &GridConfig,
    root: &LiveSearchState,
    minimum_preferred_words: usize,
    worker_count: usize,
    walk_limit: usize,
    uniform_fraction: f64,
    rng_seed: u64,
    seed_namespace: u64,
    deadline: Instant,
) -> SampleBatch {
    let next_walk = AtomicUsize::new(0);
    let (sender, receiver) = mpsc::channel::<Vec<WalkOutcome>>();

    thread::scope(|scope| {
        for _ in 0..worker_count {
            let sender = sender.clone();
            let next_walk = &next_walk;
            scope.spawn(move || {
                let Some(mut state) = root.fork(config, deadline) else {
                    return;
                };
                let mut batch = Vec::with_capacity(RESULT_BATCH_SIZE);
                loop {
                    if Instant::now() >= deadline {
                        break;
                    }
                    let walk_id = next_walk.fetch_add(1, Ordering::Relaxed);
                    if walk_id >= walk_limit {
                        break;
                    }
                    let seed = splitmix64(rng_seed ^ seed_namespace ^ walk_id as u64);
                    let mut rng = SmallRng::seed_from_u64(seed);
                    match run_walk(
                        config,
                        &mut state,
                        minimum_preferred_words,
                        uniform_fraction,
                        deadline,
                        &mut rng,
                    ) {
                        WalkResult::Complete { log2_weight, fill } => batch.push(WalkOutcome {
                            walk_id,
                            log2_weight: Some(log2_weight),
                            fill: Some(fill),
                        }),
                        WalkResult::Contradiction => batch.push(WalkOutcome {
                            walk_id,
                            log2_weight: None,
                            fill: None,
                        }),
                        WalkResult::Interrupted => break,
                    }
                    if batch.len() == RESULT_BATCH_SIZE
                        && sender.send(std::mem::take(&mut batch)).is_err()
                    {
                        return;
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
        outcomes.sort_unstable_by_key(|outcome| outcome.walk_id);
        let complete = outcomes.len() == walk_limit;
        SampleBatch { outcomes, complete }
    })
}

#[allow(clippy::too_many_arguments)]
fn run_smc_replicates(
    config: &GridConfig,
    root: &LiveSearchState,
    minimum_preferred_words: usize,
    worker_count: usize,
    particle_count: usize,
    replicate_limit: usize,
    uniform_fraction: f64,
    rng_seed: u64,
    deadline: Instant,
) -> SampleBatch {
    let next_replicate = AtomicUsize::new(0);
    let (sender, receiver) = mpsc::channel::<Vec<WalkOutcome>>();

    thread::scope(|scope| {
        for _ in 0..worker_count {
            let sender = sender.clone();
            let next_replicate = &next_replicate;
            scope.spawn(move || {
                let mut batch = Vec::with_capacity(RESULT_BATCH_SIZE);
                while Instant::now() < deadline {
                    let replicate_id = next_replicate.fetch_add(1, Ordering::Relaxed);
                    if replicate_id >= replicate_limit {
                        break;
                    }
                    let seed = splitmix64(rng_seed ^ 0x534d_4300_0000_0000 ^ replicate_id as u64);
                    let mut rng = SmallRng::seed_from_u64(seed);
                    match run_smc_replicate(
                        config,
                        root,
                        minimum_preferred_words,
                        particle_count,
                        uniform_fraction,
                        deadline,
                        &mut rng,
                    ) {
                        WalkResult::Complete { log2_weight, fill } => batch.push(WalkOutcome {
                            walk_id: replicate_id,
                            log2_weight: log2_weight.is_finite().then_some(log2_weight),
                            fill: Some(fill),
                        }),
                        WalkResult::Contradiction => batch.push(WalkOutcome {
                            walk_id: replicate_id,
                            log2_weight: None,
                            fill: None,
                        }),
                        WalkResult::Interrupted => break,
                    }
                    if batch.len() == RESULT_BATCH_SIZE
                        && sender.send(std::mem::take(&mut batch)).is_err()
                    {
                        return;
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
        outcomes.sort_unstable_by_key(|outcome| outcome.walk_id);
        let complete = outcomes.len() == replicate_limit;
        SampleBatch { outcomes, complete }
    })
}
fn run_smc_replicate(
    config: &GridConfig,
    root: &LiveSearchState,
    minimum_preferred_words: usize,
    particle_count: usize,
    uniform_fraction: f64,
    deadline: Instant,
    rng: &mut SmallRng,
) -> WalkResult {
    let mut particles = Vec::with_capacity(particle_count);
    for _ in 0..particle_count {
        let Some(state) = root.fork(config, deadline) else {
            return WalkResult::Interrupted;
        };
        particles.push(SmcParticle {
            state,
            explicit_choices: Vec::new(),
            complete: false,
        });
    }
    let mut log2_estimate = 0.0;
    let mut first_fill = None;
    let mut live_options = Vec::new();

    for _ in 0..=config.slot_configs.len() {
        if particles.iter().all(|particle| particle.complete) {
            let fill = first_fill;
            return fill.map_or(WalkResult::Contradiction, |fill| WalkResult::Complete {
                log2_weight: log2_estimate,
                fill,
            });
        }
        if Instant::now() >= deadline {
            return WalkResult::Interrupted;
        }

        let mut incremental_weights = Vec::with_capacity(particle_count);
        for particle in &mut particles {
            if particle.complete {
                incremental_weights.push(Some(0.0));
                continue;
            }
            if Instant::now() >= deadline {
                return WalkResult::Interrupted;
            }

            let slot_id = match particle.state.choose_next_slot(config) {
                Ok(Some(slot_id)) => slot_id,
                Err(()) => return WalkResult::Interrupted,
                Ok(None) => {
                    let choices = match particle.state.complete_choices(config) {
                        Ok(Some(choices)) => choices,
                        Ok(None) => {
                            incremental_weights.push(None);
                            continue;
                        }
                        Err(()) => return WalkResult::Interrupted,
                    };
                    match particle.state.validate_complete_choices(
                        config,
                        &choices,
                        minimum_preferred_words,
                    ) {
                        Some(true) => {
                            particle.complete = true;
                            first_fill.get_or_insert_with(|| choice_word_ids(&choices));
                            incremental_weights.push(Some(0.0));
                        }
                        Some(false) => incremental_weights.push(None),
                        None => return WalkResult::Interrupted,
                    }
                    continue;
                }
            };

            if !particle
                .state
                .live_options(config, slot_id, &mut live_options)
            {
                return WalkResult::Interrupted;
            }
            if live_options.is_empty() {
                incremental_weights.push(None);
                continue;
            }
            let Some((option_index, probability)) =
                sample_option(live_options.len(), uniform_fraction, rng, Some(deadline))
            else {
                return WalkResult::Interrupted;
            };
            let choice = Choice {
                slot_id,
                word_id: live_options[option_index],
            };
            match particle
                .state
                .apply_choice(config, &choice, minimum_preferred_words)
            {
                MaintainResult::Consistent => {
                    particle.explicit_choices.push(choice);
                    incremental_weights.push(Some(-probability.log2()));
                }
                MaintainResult::Contradiction => incremental_weights.push(None),
                MaintainResult::Abort => return WalkResult::Interrupted,
            }
        }

        let finite_weights: Vec<f64> = incremental_weights.iter().flatten().copied().collect();
        if finite_weights.is_empty() {
            return WalkResult::Contradiction;
        }
        let log2_weight_sum = log2_sum_exp(&finite_weights);
        log2_estimate += log2_weight_sum - (particle_count as f64).log2();

        let maximum = finite_weights
            .iter()
            .copied()
            .fold(f64::NEG_INFINITY, f64::max);
        let scaled_weights: Vec<f64> = incremental_weights
            .iter()
            .map(|weight| weight.map_or(0.0, |weight| 2.0_f64.powf(weight - maximum)))
            .collect();
        let scaled_total = scaled_weights.iter().sum::<f64>();
        let mut resampled = Vec::with_capacity(particle_count);
        for _ in 0..particle_count {
            let mut target = rng.random::<f64>() * scaled_total;
            let mut selected_index = particle_count - 1;
            for (index, weight) in scaled_weights.iter().enumerate() {
                target -= weight;
                if target <= 0.0 {
                    selected_index = index;
                    break;
                }
            }
            let selected = &particles[selected_index];
            let Some(state) = selected.state.fork(config, deadline) else {
                return WalkResult::Interrupted;
            };
            resampled.push(SmcParticle {
                state,
                explicit_choices: selected.explicit_choices.clone(),
                complete: selected.complete,
            });
        }
        particles = resampled;
    }

    if particles.iter().all(|particle| particle.complete) {
        if let Some(fill) = first_fill {
            return WalkResult::Complete {
                log2_weight: log2_estimate,
                fill,
            };
        }
    }
    WalkResult::Contradiction
}

fn retain_known_fills(
    outcomes: &[WalkOutcome],
    known_fills: &mut BTreeSet<Box<[WordId]>>,
    maximum_known_fills: usize,
) -> bool {
    let mut capped = false;
    for fill in outcomes.iter().filter_map(|outcome| outcome.fill.as_ref()) {
        if known_fills.len() < maximum_known_fills {
            known_fills.insert(fill.clone());
        } else if !known_fills.contains(fill) {
            capped = true;
        }
    }
    capped
}

fn run_walk(
    config: &GridConfig,
    state: &mut LiveSearchState,
    minimum_preferred_words: usize,
    uniform_fraction: f64,
    deadline: Instant,
    rng: &mut SmallRng,
) -> WalkResult {
    let mut explicit_choices = Vec::new();
    let mut live_options = Vec::new();
    let mut log2_probability = 0.0;

    let result = loop {
        if Instant::now() >= deadline {
            break WalkResult::Interrupted;
        }
        let slot_id = match state.choose_next_slot(config) {
            Ok(Some(slot_id)) => slot_id,
            Err(()) => break WalkResult::Interrupted,
            Ok(None) => {
                let choices = match state.complete_choices(config) {
                    Ok(Some(choices)) => choices,
                    Ok(None) => break WalkResult::Contradiction,
                    Err(()) => break WalkResult::Interrupted,
                };
                match state.validate_complete_choices(config, &choices, minimum_preferred_words) {
                    Some(true) => {}
                    Some(false) => break WalkResult::Contradiction,
                    None => break WalkResult::Interrupted,
                }
                break WalkResult::Complete {
                    log2_weight: -log2_probability,
                    fill: choice_word_ids(&choices),
                };
            }
        };

        if !state.live_options(config, slot_id, &mut live_options) {
            break WalkResult::Interrupted;
        }
        if live_options.is_empty() {
            break WalkResult::Contradiction;
        }
        let Some((option_index, probability)) =
            sample_option(live_options.len(), uniform_fraction, rng, Some(deadline))
        else {
            break WalkResult::Interrupted;
        };
        log2_probability += probability.log2();
        let choice = Choice {
            slot_id,
            word_id: live_options[option_index],
        };
        match state.apply_choice(config, &choice, minimum_preferred_words) {
            MaintainResult::Consistent => explicit_choices.push(choice),
            MaintainResult::Contradiction => break WalkResult::Contradiction,
            MaintainResult::Abort => break WalkResult::Interrupted,
        }
    };

    if matches!(&result, WalkResult::Interrupted) {
        return result;
    }
    if !state.rollback_choices(config, &mut explicit_choices) {
        return WalkResult::Interrupted;
    }
    result
}

fn sample_option(
    option_count: usize,
    uniform_fraction: f64,
    rng: &mut SmallRng,
    deadline: Option<Instant>,
) -> Option<(usize, f64)> {
    let uniform_probability = 1.0 / option_count as f64;
    let mut heuristic_total = 0.0;
    for rank in 1..=option_count {
        if rank % 256 == 0 && deadline.is_some_and(|deadline| Instant::now() >= deadline) {
            return None;
        }
        heuristic_total += 1.0 / (rank as f64).powi(2);
    }
    let use_uniform = rng.random::<f64>() < uniform_fraction;
    let selected = if use_uniform {
        rng.random_range(0..option_count)
    } else {
        let mut target = rng.random::<f64>() * heuristic_total;
        let mut selected = option_count - 1;
        for index in 0..option_count {
            if index % 256 == 0 && deadline.is_some_and(|deadline| Instant::now() >= deadline) {
                return None;
            }
            target -= 1.0 / ((index + 1) as f64).powi(2);
            if target <= 0.0 {
                selected = index;
                break;
            }
        }
        selected
    };
    let heuristic_probability = (1.0 / ((selected + 1) as f64).powi(2)) / heuristic_total;
    let probability = uniform_fraction.mul_add(
        uniform_probability,
        (1.0 - uniform_fraction) * heuristic_probability,
    );
    Some((selected, probability))
}

fn aggregate_outcomes(
    outcomes: Vec<WalkOutcome>,
    known_fills: &mut BTreeSet<Box<[WordId]>>,
    minimum_preferred_words: usize,
    estimator_start: Instant,
    search_elapsed: Duration,
    options: &VariantEstimateOptions,
    method: VariantEstimateMethod,
    mut known_distinct_fills_capped: bool,
    force_insufficient: bool,
) -> VariantEstimate {
    let walk_count = outcomes.len();
    let mut log2_weights = Vec::new();
    for outcome in outcomes {
        if let Some(log2_weight) = outcome.log2_weight {
            log2_weights.push(log2_weight);
            if let Some(fill) = outcome.fill {
                if known_fills.len() < options.maximum_known_fills {
                    known_fills.insert(fill);
                } else if !known_fills.contains(&fill) {
                    known_distinct_fills_capped = true;
                }
            }
        }
    }
    let accepted_walk_count = log2_weights.len();
    let elapsed = estimator_start.elapsed();

    if force_insufficient || walk_count < options.minimum_walks || accepted_walk_count == 0 {
        return VariantEstimate {
            status: VariantEstimateStatus::InsufficientEvidence,
            minimum_preferred_words,
            known_distinct_fills: known_fills.len(),
            known_distinct_fills_capped,
            estimated_fill_count: None,
            estimated_slack_bits: None,
            interval_slack_bits: None,
            walk_count,
            accepted_walk_count,
            effective_sample_size: 0.0,
            elapsed,
            method,
            search_runtime_ratio: duration_ratio(elapsed, search_elapsed),
        };
    }

    let log2_weight_sum = log2_sum_exp(&log2_weights);
    let doubled: Vec<f64> = log2_weights.iter().map(|weight| 2.0 * weight).collect();
    let log2_squared_weight_sum = log2_sum_exp(&doubled);
    let effective_sample_size = 2.0_f64
        .powf(2.0 * log2_weight_sum - log2_squared_weight_sum)
        .min(walk_count as f64);
    if effective_sample_size < 4.0 {
        return VariantEstimate {
            status: VariantEstimateStatus::InsufficientEvidence,
            method,
            minimum_preferred_words,
            known_distinct_fills: known_fills.len(),
            known_distinct_fills_capped,
            estimated_fill_count: None,
            estimated_slack_bits: None,
            interval_slack_bits: None,
            walk_count,
            accepted_walk_count,
            effective_sample_size,
            elapsed,
            search_runtime_ratio: duration_ratio(elapsed, search_elapsed),
        };
    }

    let estimate_bits = log2_weight_sum - (walk_count as f64).log2();
    let estimate = 2.0_f64.powf(estimate_bits);
    let relative_variance =
        (walk_count as f64 * 2.0_f64.powf(log2_squared_weight_sum - 2.0 * log2_weight_sum) - 1.0)
            .max(0.0);
    let relative_standard_error = if walk_count > 1 {
        (relative_variance / (walk_count - 1) as f64).sqrt()
    } else {
        0.0
    };
    let known_lower_bound = known_fills.len() as f64;
    if estimate < known_lower_bound {
        return VariantEstimate {
            status: VariantEstimateStatus::InsufficientEvidence,
            method,
            minimum_preferred_words,
            known_distinct_fills: known_fills.len(),
            known_distinct_fills_capped,
            estimated_fill_count: None,
            estimated_slack_bits: None,
            interval_slack_bits: None,
            walk_count,
            accepted_walk_count,
            effective_sample_size,
            elapsed,
            search_runtime_ratio: duration_ratio(elapsed, search_elapsed),
        };
    }
    let lower = (estimate * (1.0 - 1.96 * relative_standard_error))
        .max(known_lower_bound)
        .max(f64::MIN_POSITIVE);
    let upper = (estimate * (1.0 + 1.96 * relative_standard_error)).max(lower);

    VariantEstimate {
        status: VariantEstimateStatus::Estimated,
        method,
        minimum_preferred_words,
        known_distinct_fills: known_fills.len(),
        known_distinct_fills_capped,
        estimated_fill_count: Some(estimate),
        estimated_slack_bits: Some(estimate_bits),
        interval_slack_bits: Some((lower.log2(), upper.log2())),
        walk_count,
        accepted_walk_count,
        effective_sample_size,
        elapsed,
        search_runtime_ratio: duration_ratio(elapsed, search_elapsed),
    }
}

fn outcome_effective_sample_size(outcomes: &[WalkOutcome]) -> f64 {
    let log2_weights: Vec<f64> = outcomes
        .iter()
        .filter_map(|outcome| outcome.log2_weight)
        .collect();
    if log2_weights.is_empty() {
        return 0.0;
    }
    let log2_weight_sum = log2_sum_exp(&log2_weights);
    let doubled: Vec<f64> = log2_weights.iter().map(|weight| 2.0 * weight).collect();
    2.0_f64
        .powf(2.0 * log2_weight_sum - log2_sum_exp(&doubled))
        .min(outcomes.len() as f64)
}

fn estimator_budget(search_elapsed: Duration, options: &VariantEstimateOptions) -> Duration {
    let ratio = f64::from(options.runtime_ratio).clamp(0.0, 0.5);
    let ratio_budget = search_elapsed.mul_f64(ratio);
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

fn choice_word_ids(choices: &[Choice]) -> Box<[WordId]> {
    let mut by_slot = vec![0; choices.len()];
    for choice in choices {
        by_slot[choice.slot_id] = choice.word_id;
    }
    by_slot.into_boxed_slice()
}

fn log2_sum_exp(values: &[f64]) -> f64 {
    let maximum = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    maximum
        + values
            .iter()
            .map(|value| 2.0_f64.powf(value - maximum))
            .sum::<f64>()
            .log2()
}

fn splitmix64(mut value: u64) -> u64 {
    value = value.wrapping_add(0x9e37_79b9_7f4a_7c15);
    value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    value ^ (value >> 31)
}

fn duration_ratio(numerator: Duration, denominator: Duration) -> f32 {
    if denominator.is_zero() {
        f32::INFINITY
    } else {
        (numerator.as_secs_f64() / denominator.as_secs_f64()) as f32
    }
}

fn empty_estimate(
    status: VariantEstimateStatus,
    minimum_preferred_words: usize,
    known_distinct_fills: usize,
    elapsed: Duration,
    search_elapsed: Duration,
) -> VariantEstimate {
    VariantEstimate {
        status,
        method: VariantEstimateMethod::ImportanceWalks,
        minimum_preferred_words,
        known_distinct_fills,
        known_distinct_fills_capped: false,
        estimated_fill_count: None,
        estimated_slack_bits: None,
        interval_slack_bits: None,
        walk_count: 0,
        accepted_walk_count: 0,
        effective_sample_size: 0.0,
        elapsed,
        search_runtime_ratio: duration_ratio(elapsed, search_elapsed),
    }
}

fn exact_estimate(
    status: VariantEstimateStatus,
    minimum_preferred_words: usize,
    known_distinct_fills: usize,
    estimated_fill_count: f64,
    estimated_slack_bits: f64,
    elapsed: Duration,
    search_elapsed: Duration,
) -> VariantEstimate {
    VariantEstimate {
        status,
        method: VariantEstimateMethod::Exact,
        minimum_preferred_words,
        known_distinct_fills,
        known_distinct_fills_capped: false,
        estimated_fill_count: Some(estimated_fill_count),
        estimated_slack_bits: Some(estimated_slack_bits),
        interval_slack_bits: Some((estimated_slack_bits, estimated_slack_bits)),
        walk_count: 0,
        accepted_walk_count: usize::from(known_distinct_fills > 0),
        effective_sample_size: f64::from(known_distinct_fills > 0),
        elapsed,
        search_runtime_ratio: duration_ratio(elapsed, search_elapsed),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        estimate_for_target, estimator_budget, run_smc_replicate, sample_option, splitmix64,
        VariantEstimateOptions, VariantEstimateStatus, WalkResult, MAX_FIXED_COHORT,
    };
    use crate::backtracking_search::LiveSearchState;
    use crate::grid_config::generate_grid_config_from_template_string;
    use crate::word_list::{WordList, WordListSourceConfig, WordListSourceConfigProvider};
    use rand::prelude::{SeedableRng, SmallRng};
    use std::collections::HashSet;
    use std::time::{Duration, Instant};

    fn source(id: &str, words: &[&str]) -> WordListSourceConfig {
        WordListSourceConfig {
            id: id.into(),
            enabled: true,
            provider: WordListSourceConfigProvider::Memory {
                words: words.iter().map(|word| ((*word).into(), 50)).collect(),
            },
            normalization: None,
        }
    }

    fn word_list(max_shared_substring: Option<usize>) -> WordList {
        let mut list = WordList::new(
            vec![source("preferred", &["cat"]), source("standard", &["dog"])],
            None,
            Some(3),
            max_shared_substring,
        );
        list.set_preferred_source_ids(HashSet::from(["preferred".into()]));
        list
    }

    fn options(walks: usize, seed: u64) -> VariantEstimateOptions {
        VariantEstimateOptions {
            runtime_ratio: 0.5,
            worker_count: Some(2),
            minimum_walks: 1,
            rng_seed: seed,
            maximum_duration: Some(Duration::from_secs(1)),
            maximum_walks: Some(walks),
            uniform_proposal_fraction: 1.0,
            smc_particle_count: 8,
            maximum_known_fills: 100,
        }
    }

    #[test]
    fn fully_fixed_grid_has_exactly_one_fill() {
        let config = generate_grid_config_from_template_string(word_list(None), "cat\n", 0);
        let estimate = estimate_for_target(
            &config.to_config_ref(),
            1,
            None,
            Duration::from_secs(10),
            options(100, 1),
        );
        assert_eq!(estimate.status, VariantEstimateStatus::ExactOne);
        assert_eq!(estimate.estimated_fill_count, Some(1.0));
        assert_eq!(estimate.estimated_slack_bits, Some(0.0));
    }

    #[test]
    fn impossible_preferred_threshold_has_exactly_zero_fills() {
        let config = generate_grid_config_from_template_string(word_list(None), "...\n", 0);
        let estimate = estimate_for_target(
            &config.to_config_ref(),
            2,
            None,
            Duration::from_secs(10),
            options(100, 2),
        );
        assert_eq!(estimate.status, VariantEstimateStatus::ExactZero);
        assert_eq!(estimate.estimated_fill_count, Some(0.0));
        assert_eq!(estimate.estimated_slack_bits, Some(f64::NEG_INFINITY));
    }

    #[test]
    fn disconnected_slots_count_duplicate_constraint_exactly() {
        let config = generate_grid_config_from_template_string(word_list(None), "...#...\n", 0);
        let estimate = estimate_for_target(
            &config.to_config_ref(),
            0,
            None,
            Duration::from_secs(10),
            options(256, 3),
        );
        assert_eq!(estimate.status, VariantEstimateStatus::Estimated);
        assert_eq!(estimate.walk_count, 256);
        assert_eq!(estimate.accepted_walk_count, 256);
        assert_eq!(estimate.known_distinct_fills, 2);
        assert!((estimate.estimated_fill_count.unwrap() - 2.0).abs() < f64::EPSILON);
        assert!((estimate.estimated_slack_bits.unwrap() - 1.0).abs() < f64::EPSILON);
    }

    #[test]
    fn smc_resampling_preserves_the_exact_two_fill_count() {
        let config = generate_grid_config_from_template_string(word_list(None), "...#...\n", 0);
        let config_ref = config.to_config_ref();
        let root =
            LiveSearchState::new(&config_ref, 0, Instant::now() + Duration::from_secs(1)).unwrap();
        let mut rng = SmallRng::seed_from_u64(4);
        let result = run_smc_replicate(
            &config_ref,
            &root,
            0,
            8,
            1.0,
            Instant::now() + Duration::from_secs(1),
            &mut rng,
        );
        match result {
            WalkResult::Complete { log2_weight, .. } => {
                assert!((log2_weight - 1.0).abs() < f64::EPSILON);
            }
            _ => panic!("SMC replicate did not reach a valid leaf"),
        }
    }

    #[test]
    fn smc_returns_leaf_completed_on_its_final_pass() {
        let config = generate_grid_config_from_template_string(word_list(None), "...\n", 0);
        let config_ref = config.to_config_ref();
        let deadline = Instant::now() + Duration::from_secs(1);
        let root = LiveSearchState::new(&config_ref, 0, deadline).unwrap();
        let mut rng = SmallRng::seed_from_u64(7);
        let result = run_smc_replicate(&config_ref, &root, 0, 8, 1.0, deadline, &mut rng);
        match result {
            WalkResult::Complete { log2_weight, .. } => {
                assert!((log2_weight - 1.0).abs() < f64::EPSILON);
            }
            _ => panic!("SMC discarded leaves completed on the final pass"),
        }
    }

    #[test]
    fn nonfinite_public_options_are_rejected_without_panicking() {
        let config = generate_grid_config_from_template_string(word_list(None), "...\n", 0);
        let mut invalid_options = Vec::new();
        let mut nonfinite_ratio = options(10, 0);
        nonfinite_ratio.runtime_ratio = f32::NAN;
        invalid_options.push(nonfinite_ratio);
        let mut nonfinite_proposal = options(10, 0);
        nonfinite_proposal.uniform_proposal_fraction = f64::NAN;
        invalid_options.push(nonfinite_proposal);
        let mut oversized_cohort = options(10, 0);
        oversized_cohort.minimum_walks = MAX_FIXED_COHORT + 1;
        invalid_options.push(oversized_cohort);

        for configured in invalid_options {
            let estimate = estimate_for_target(
                &config.to_config_ref(),
                0,
                None,
                Duration::from_secs(10),
                configured,
            );
            assert_eq!(estimate.status, VariantEstimateStatus::InvalidOptions);
        }
    }
    #[test]
    fn preferred_threshold_estimate_converges_to_exact_count() {
        let config = generate_grid_config_from_template_string(word_list(None), "...\n", 0);
        let mut estimates = Vec::new();
        for seed in 0..16 {
            let mut configured = options(4_096, seed);
            configured.maximum_known_fills = 0;
            let estimate = estimate_for_target(
                &config.to_config_ref(),
                1,
                None,
                Duration::from_secs(10),
                configured,
            );
            estimates.push(estimate.estimated_fill_count.unwrap());
        }
        let mean = estimates.iter().sum::<f64>() / estimates.len() as f64;
        assert!((mean - 1.0).abs() < 0.02, "mean estimate was {mean}");
    }

    #[test]
    fn shared_substring_rejections_never_contribute_as_leaves() {
        let list = WordList::new(
            vec![source("standard", &["cater", "cates"])],
            None,
            Some(5),
            Some(3),
        );
        let config = generate_grid_config_from_template_string(list, ".....#.....\n", 0);
        let estimate = estimate_for_target(
            &config.to_config_ref(),
            0,
            None,
            Duration::from_secs(10),
            options(256, 4),
        );
        assert_eq!(estimate.status, VariantEstimateStatus::InsufficientEvidence);
        assert_eq!(estimate.accepted_walk_count, 0);
        assert_eq!(estimate.estimated_fill_count, None);
    }

    #[test]
    fn fixed_seed_and_walk_limit_are_reproducible_across_workers() {
        let config = generate_grid_config_from_template_string(word_list(None), "...\n", 0);
        let mut configured = options(4_096, 99);
        configured.worker_count = Some(4);
        let first = estimate_for_target(
            &config.to_config_ref(),
            1,
            None,
            Duration::from_secs(10),
            configured.clone(),
        );
        let second = estimate_for_target(
            &config.to_config_ref(),
            1,
            None,
            Duration::from_secs(10),
            configured,
        );
        assert_eq!(first.walk_count, second.walk_count);
        assert_eq!(first.accepted_walk_count, second.accepted_walk_count);
        assert_eq!(first.known_distinct_fills, second.known_distinct_fills);
        assert_eq!(first.estimated_fill_count, second.estimated_fill_count);
        assert_eq!(first.estimated_slack_bits, second.estimated_slack_bits);
        assert_eq!(first.interval_slack_bits, second.interval_slack_bits);
        assert_eq!(first.effective_sample_size, second.effective_sample_size);
    }

    #[test]
    fn zero_accepted_walks_are_insufficient_evidence_not_zero() {
        let config = generate_grid_config_from_template_string(word_list(None), "...\n", 0);
        let rejecting_seed = (0..10_000)
            .find(|seed| {
                let mut rng = SmallRng::seed_from_u64(splitmix64(*seed));
                sample_option(2, 1.0, &mut rng, None).unwrap().0 == 1
            })
            .unwrap();
        let estimate = estimate_for_target(
            &config.to_config_ref(),
            1,
            None,
            Duration::from_secs(10),
            options(1, rejecting_seed),
        );
        assert_eq!(estimate.status, VariantEstimateStatus::InsufficientEvidence);
        assert_eq!(estimate.walk_count, 1);
        assert_eq!(estimate.accepted_walk_count, 0);
        assert_eq!(estimate.estimated_fill_count, None);
    }

    #[test]
    fn interrupted_fixed_cohort_discards_partial_diagnostics() {
        let config = generate_grid_config_from_template_string(word_list(None), "...\n", 0);
        let mut configured = options(MAX_FIXED_COHORT, 0);
        configured.worker_count = Some(1);
        configured.maximum_duration = Some(Duration::from_millis(20));
        let estimate = estimate_for_target(
            &config.to_config_ref(),
            0,
            None,
            Duration::from_secs(1),
            configured,
        );
        assert_eq!(estimate.status, VariantEstimateStatus::InsufficientEvidence);
        assert_eq!(estimate.walk_count, 0);
        assert_eq!(estimate.accepted_walk_count, 0);
        assert_eq!(estimate.known_distinct_fills, 0);
    }

    #[test]
    fn runtime_budget_is_capped_by_ratio_and_absolute_limit() {
        let mut configured = options(100, 0);
        configured.runtime_ratio = 9.0;
        configured.maximum_duration = None;
        assert_eq!(
            estimator_budget(Duration::from_secs(10), &configured),
            Duration::from_secs(5)
        );
        configured.runtime_ratio = 0.45;
        configured.maximum_duration = Some(Duration::from_secs(2));
        assert_eq!(
            estimator_budget(Duration::from_secs(10), &configured),
            Duration::from_secs(2)
        );
    }
}
