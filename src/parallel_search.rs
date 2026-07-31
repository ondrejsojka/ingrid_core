//! Adaptive multi-core search for fills that maximize preferred-tier words.

use crate::backtracking_search::{find_fill_from_prepared, FillFailure, FillOptions, FillSuccess};
use crate::fill_set::DistinctFillSet;
use crate::grid_config::{Choice, GridConfig};
pub use crate::live_state::PreparedSearch;
use crate::types::WordId;
use crate::word_list::WordTier;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::{Duration, Instant};

const SCHEDULER_POLL_INTERVAL: Duration = Duration::from_millis(10);

/// A fill together with the preferred-word objective value it achieved.
#[derive(Debug)]
pub struct PreferredFillSuccess {
    pub fill: FillSuccess,
    /// Total preferred words in the completed fill.
    pub preferred_word_count: usize,
    /// Preferred words already fixed in the input grid.
    pub fixed_preferred_word_count: usize,
    /// Distinct solver-produced fills at this result's Preferred threshold, including canceled
    /// workers' races. These are certified lower-bound evidence, not probability-weighted
    /// estimator samples. Keys are slot-indexed assignments tied to the [`GridConfig`] used here.
    pub certified_fills: DistinctFillSet,
}

/// The scheduler transition represented by a [`SearchEvent`].
#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum SearchEventKind {
    WorkerStart,
    Success,
    HardFailure,
    Abort,
    Timeout,
    IncumbentImprovement,
    FinalReturn,
}

/// The outcome of a completed worker or the final scheduler return.
#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum SearchEventResult {
    Success,
    HardFailure,
    Abort,
    Timeout,
}

/// Allocation-free telemetry emitted by the parallel-search scheduler.
///
/// Worker and statistics fields are absent when they do not apply. Failed workers have no
/// statistics because the underlying fill API only returns statistics with a successful fill.
#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub struct SearchEvent {
    pub elapsed: Duration,
    pub kind: SearchEventKind,
    pub worker_id: Option<u64>,
    pub target: Option<usize>,
    pub active_worker_count: usize,
    pub incumbent_preferred_word_count: Option<usize>,
    pub impossible_from: usize,
    pub fixed_preferred_word_count: usize,
    pub discovered_preferred_word_count: Option<usize>,
    pub states: Option<usize>,
    pub backtracks: Option<usize>,
    pub retries: Option<usize>,
    pub result: Option<SearchEventResult>,
}

#[derive(Clone, Copy, Default)]
struct EventDetails {
    worker: Option<(u64, usize)>,
    statistics: Option<(usize, usize, usize)>,
    result: Option<SearchEventResult>,
}

impl SearchEvent {
    fn new(
        elapsed: Duration,
        kind: SearchEventKind,
        active_worker_count: usize,
        incumbent_preferred_word_count: Option<usize>,
        impossible_from: usize,
        fixed_preferred_word_count: usize,
        details: EventDetails,
    ) -> Self {
        let (worker_id, target) = details
            .worker
            .map_or((None, None), |(id, target)| (Some(id), Some(target)));
        let (states, backtracks, retries) = details
            .statistics
            .map_or((None, None, None), |(states, backtracks, retries)| {
                (Some(states), Some(backtracks), Some(retries))
            });
        let discovered_preferred_word_count =
            incumbent_preferred_word_count.map(|preferred_word_count| {
                preferred_word_count
                    .checked_sub(fixed_preferred_word_count)
                    .expect("fixed preferred count cannot exceed the incumbent")
            });

        Self {
            elapsed,
            kind,
            worker_id,
            target,
            active_worker_count,
            incumbent_preferred_word_count,
            impossible_from,
            fixed_preferred_word_count,
            discovered_preferred_word_count,
            states,
            backtracks,
            retries,
            result: details.result,
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn emit_search_event(
    observer: &mut Option<&mut dyn FnMut(SearchEvent)>,
    search_start: Instant,
    kind: SearchEventKind,
    active_worker_count: usize,
    incumbent_preferred_word_count: Option<usize>,
    impossible_from: usize,
    fixed_preferred_word_count: usize,
    details: EventDetails,
) {
    if let Some(observer) = observer.as_deref_mut() {
        observer(SearchEvent::new(
            search_start.elapsed(),
            kind,
            active_worker_count,
            incumbent_preferred_word_count,
            impossible_from,
            fixed_preferred_word_count,
            details,
        ));
    }
}

struct ActiveWorker {
    minimum_preferred_words: usize,
    abort: Arc<AtomicBool>,
}

struct WorkerResult {
    id: u64,
    minimum_preferred_words: usize,
    result: Result<FillSuccess, FillFailure>,
}

/// Count preferred-tier slot choices in a completed fill.
#[must_use]
pub fn count_preferred_words(config: &GridConfig, choices: &[Choice]) -> usize {
    choices
        .iter()
        .filter(|choice| {
            let slot = &config.slot_configs[choice.slot_id];
            config.word_list.word_tier((slot.length, choice.word_id)) == WordTier::Preferred
        })
        .count()
}

pub(crate) fn canonical_fill_key(config: &GridConfig, choices: &[Choice]) -> Option<Box<[WordId]>> {
    if choices.len() != config.slot_configs.len() {
        return None;
    }
    let mut by_slot = vec![None; choices.len()];
    for choice in choices {
        let destination = by_slot.get_mut(choice.slot_id)?;
        if destination.replace(choice.word_id).is_some() {
            return None;
        }
    }
    by_slot
        .into_iter()
        .collect::<Option<Vec<_>>>()
        .map(Vec::into_boxed_slice)
}

fn fixed_preferred_word_count(config: &GridConfig) -> usize {
    config
        .slot_configs
        .iter()
        .filter(|slot| slot.complete_fill(config.fill, config.width).is_some())
        .filter(|slot| {
            config.slot_options[slot.id].iter().any(|&word_id| {
                config.word_list.word_tier((slot.length, word_id)) == WordTier::Preferred
            })
        })
        .count()
}

/// Compute a safe upper bound on the number of preferred words in any fill.
#[must_use]
pub fn maximum_preferred_words(config: &GridConfig) -> usize {
    config
        .slot_configs
        .iter()
        .filter(|slot| {
            config.slot_options[slot.id].iter().any(|&word_id| {
                config.word_list.word_tier((slot.length, word_id)) == WordTier::Preferred
            })
        })
        .count()
}

fn initial_targets(maximum: usize, worker_count: usize) -> VecDeque<usize> {
    let mut seen = HashSet::new();
    (0..worker_count.saturating_sub(1))
        .map(|index| {
            let numerator = (maximum as u128) * ((worker_count - index) as u128);
            numerator.div_ceil(worker_count as u128) as usize
        })
        .chain(std::iter::once(0))
        .filter(|&target| seen.insert(target))
        .collect()
}

fn viable_bounds(
    best: Option<&PreferredFillSuccess>,
    impossible_from: usize,
) -> Option<(usize, usize)> {
    let lower = best.map_or(0, |success| success.preferred_word_count + 1);
    let upper = impossible_from.checked_sub(1)?;
    (lower <= upper).then_some((lower, upper))
}

fn next_unrepresented_target(
    lower: usize,
    upper: usize,
    represented: &BTreeSet<usize>,
) -> Option<usize> {
    let mut intervals = Vec::new();
    let mut interval_start = lower;

    for &target in represented.range(lower..=upper) {
        if interval_start < target {
            intervals.push((interval_start, target - 1));
        }
        interval_start = target.saturating_add(1);
    }
    if interval_start <= upper {
        intervals.push((interval_start, upper));
    }

    intervals
        .into_iter()
        .max_by_key(|&(start, end)| (end - start, end))
        .map(|(start, end)| start + (end - start).div_ceil(2))
}

fn duplicate_target(
    lower: usize,
    upper: usize,
    active: &HashMap<u64, ActiveWorker>,
) -> Option<usize> {
    let mut counts = BTreeMap::<usize, usize>::new();
    for worker in active.values() {
        if (lower..=upper).contains(&worker.minimum_preferred_words)
            && !worker.abort.load(Ordering::Relaxed)
        {
            *counts.entry(worker.minimum_preferred_words).or_default() += 1;
        }
    }
    counts
        .into_iter()
        .min_by_key(|&(target, count)| (count, usize::MAX - target))
        .map(|(target, _)| target)
}

fn cancel_matching(active: &HashMap<u64, ActiveWorker>, predicate: impl Fn(usize) -> bool) {
    for worker in active.values() {
        if predicate(worker.minimum_preferred_words) {
            worker.abort.store(true, Ordering::Relaxed);
        }
    }
}

/// Establish initial arc consistency once for search workers and optional post-search analysis.
pub fn prepare_search(config: &GridConfig) -> Result<PreparedSearch, FillFailure> {
    PreparedSearch::new(config)
}

/// Search on the requested number of CPU cores for the fill with the largest provably attainable
/// number of preferred-tier words, starting from a prepared root created by [`prepare_search`].
///
/// One worker starts at zero to establish a baseline fill quickly; the rest start at evenly
/// distributed preferred-word minima. A success at `N` cancels every worker whose minimum is at
/// most the success's actual preferred count, while harder workers keep running. A hard failure at
/// `N` symmetrically cancels minima at least `N`. A freed core first targets one more than the
/// incumbent to guarantee incremental anytime progress; remaining cores bisect unexplored target
/// gaps and, once every distinct target is represented, run independent RNG streams.
pub fn find_best_fill(
    config: &GridConfig,
    prepared: &PreparedSearch,
    timeout: Option<Duration>,
    worker_count: Option<usize>,
    rng_seed: u64,
) -> Result<PreferredFillSuccess, FillFailure> {
    find_best_fill_internal(config, prepared, timeout, worker_count, None, rng_seed)
}

/// Search from a prepared root while synchronously reporting scheduler transitions to `observer`.
///
/// Failures discovered during [`prepare_search`] occur before any scheduler exists and emit no
/// events; call `prepare_search` separately if they matter.
pub fn find_best_fill_with_observer(
    config: &GridConfig,
    prepared: &PreparedSearch,
    timeout: Option<Duration>,
    worker_count: Option<usize>,
    rng_seed: u64,
    mut observer: impl FnMut(SearchEvent),
) -> Result<PreferredFillSuccess, FillFailure> {
    find_best_fill_internal(
        config,
        prepared,
        timeout,
        worker_count,
        Some(&mut observer),
        rng_seed,
    )
}

#[allow(clippy::too_many_lines)]
fn find_best_fill_internal(
    config: &GridConfig,
    prepared: &PreparedSearch,
    timeout: Option<Duration>,
    worker_count: Option<usize>,
    mut observer: Option<&mut dyn FnMut(SearchEvent)>,
    rng_seed: u64,
) -> Result<PreferredFillSuccess, FillFailure> {
    let worker_count = worker_count
        .unwrap_or_else(|| thread::available_parallelism().map_or(1, usize::from))
        .max(1);
    let maximum = maximum_preferred_words(config);
    let fixed_preferred_word_count = fixed_preferred_word_count(config);
    let search_start = Instant::now();
    let deadline = timeout.map(|duration| search_start + duration);

    let (result, final_incumbent, final_impossible_from, final_active_worker_count) =
        thread::scope(|scope| {
            let (sender, receiver) = mpsc::channel::<WorkerResult>();
            let mut initial_targets = initial_targets(maximum, worker_count);
            let mut active = HashMap::<u64, ActiveWorker>::new();
            let mut next_worker_id = 0_u64;
            let mut best: Option<PreferredFillSuccess> = None;
            let mut impossible_from = maximum + 1;
            let mut terminal_failure: Option<FillFailure> = None;

            loop {
                if config
                    .abort
                    .is_some_and(|abort| abort.load(Ordering::Relaxed))
                {
                    emit_search_event(
                        &mut observer,
                        search_start,
                        SearchEventKind::Abort,
                        active.len(),
                        best.as_ref().map(|success| success.preferred_word_count),
                        impossible_from,
                        fixed_preferred_word_count,
                        EventDetails {
                            result: Some(SearchEventResult::Abort),
                            ..EventDetails::default()
                        },
                    );
                    terminal_failure = Some(FillFailure::Abort);
                    break;
                }
                if deadline.is_some_and(|deadline| Instant::now() >= deadline) {
                    emit_search_event(
                        &mut observer,
                        search_start,
                        SearchEventKind::Timeout,
                        active.len(),
                        best.as_ref().map(|success| success.preferred_word_count),
                        impossible_from,
                        fixed_preferred_word_count,
                        EventDetails {
                            result: Some(SearchEventResult::Timeout),
                            ..EventDetails::default()
                        },
                    );
                    terminal_failure = Some(FillFailure::Timeout);
                    break;
                }
                if best
                    .as_ref()
                    .is_some_and(|success| success.preferred_word_count + 1 >= impossible_from)
                {
                    break;
                }
                if best.is_none() && impossible_from == 0 {
                    terminal_failure = Some(FillFailure::HardFailure);
                    break;
                }

                while active.len() < worker_count {
                    let Some((lower, upper)) = viable_bounds(best.as_ref(), impossible_from) else {
                        break;
                    };
                    let represented = active
                        .values()
                        .filter(|worker| !worker.abort.load(Ordering::Relaxed))
                        .map(|worker| worker.minimum_preferred_words)
                        .collect::<BTreeSet<_>>();

                    let queued_target = loop {
                        let Some(target) = initial_targets.pop_front() else {
                            break None;
                        };
                        if (lower..=upper).contains(&target) && !represented.contains(&target) {
                            break Some(target);
                        }
                    };
                    let target = queued_target
                        // Always keep one worker on the smallest count that would improve the
                        // incumbent. This produces steady anytime progress while the remaining
                        // workers continue probing harder, distributed targets.
                        .or_else(|| (!represented.contains(&lower)).then_some(lower))
                        .or_else(|| next_unrepresented_target(lower, upper, &represented))
                        .or_else(|| duplicate_target(lower, upper, &active));
                    let Some(target) = target else {
                        break;
                    };

                    let id = next_worker_id;
                    next_worker_id = next_worker_id.wrapping_add(1);
                    let abort = Arc::new(AtomicBool::new(false));
                    active.insert(
                        id,
                        ActiveWorker {
                            minimum_preferred_words: target,
                            abort: Arc::clone(&abort),
                        },
                    );

                    let sender = sender.clone();
                    scope.spawn(move || {
                        let worker_start = Instant::now();
                        let result = find_fill_from_prepared(
                            config,
                            prepared,
                            worker_start,
                            deadline,
                            None,
                            FillOptions {
                                minimum_preferred_words: target,
                                abort: Some(abort.as_ref()),
                                rng_seed_offset: rng_seed
                                    .wrapping_add(id.wrapping_mul(0x9E37_79B9_7F4A_7C15)),
                            },
                        );
                        let _ = sender.send(WorkerResult {
                            id,
                            minimum_preferred_words: target,
                            result,
                        });
                    });
                    emit_search_event(
                        &mut observer,
                        search_start,
                        SearchEventKind::WorkerStart,
                        active.len(),
                        best.as_ref().map(|success| success.preferred_word_count),
                        impossible_from,
                        fixed_preferred_word_count,
                        EventDetails {
                            worker: Some((id, target)),
                            ..EventDetails::default()
                        },
                    );
                }

                if active.is_empty() {
                    emit_search_event(
                        &mut observer,
                        search_start,
                        SearchEventKind::HardFailure,
                        0,
                        best.as_ref().map(|success| success.preferred_word_count),
                        impossible_from,
                        fixed_preferred_word_count,
                        EventDetails {
                            result: Some(SearchEventResult::HardFailure),
                            ..EventDetails::default()
                        },
                    );
                    terminal_failure = Some(FillFailure::HardFailure);
                    break;
                }

                match receiver.recv_timeout(SCHEDULER_POLL_INTERVAL) {
                    Ok(worker_result) => {
                        let WorkerResult {
                            id,
                            minimum_preferred_words: target,
                            result,
                        } = worker_result;
                        active.remove(&id);
                        match result {
                            Ok(fill) => {
                                let preferred_word_count =
                                    count_preferred_words(config, &fill.choices);
                                let statistics = (
                                    fill.statistics.states,
                                    fill.statistics.backtracks,
                                    fill.statistics.retries,
                                );
                                let previous_incumbent =
                                    best.as_ref().map(|success| success.preferred_word_count);
                                let incumbent_improved = previous_incumbent
                                    .is_none_or(|current| preferred_word_count > current);
                                if incumbent_improved {
                                    let fill_key = canonical_fill_key(config, &fill.choices)
                                        .expect("solver fills contain one choice per slot");
                                    best = Some(PreferredFillSuccess {
                                        fill,
                                        preferred_word_count,
                                        fixed_preferred_word_count,
                                        certified_fills: DistinctFillSet::with_fill(fill_key),
                                    });
                                } else if previous_incumbent == Some(preferred_word_count) {
                                    let fill_key = canonical_fill_key(config, &fill.choices)
                                        .expect("solver fills contain one choice per slot");
                                    let best = best.as_mut().expect("equal incumbent exists");
                                    best.certified_fills.insert(fill_key);
                                }
                                cancel_matching(&active, |target| target <= preferred_word_count);

                                emit_search_event(
                                    &mut observer,
                                    search_start,
                                    SearchEventKind::Success,
                                    active.len(),
                                    previous_incumbent,
                                    impossible_from,
                                    fixed_preferred_word_count,
                                    EventDetails {
                                        worker: Some((id, target)),
                                        statistics: Some(statistics),
                                        result: Some(SearchEventResult::Success),
                                    },
                                );
                                if incumbent_improved {
                                    emit_search_event(
                                        &mut observer,
                                        search_start,
                                        SearchEventKind::IncumbentImprovement,
                                        active.len(),
                                        Some(preferred_word_count),
                                        impossible_from,
                                        fixed_preferred_word_count,
                                        EventDetails {
                                            worker: Some((id, target)),
                                            ..EventDetails::default()
                                        },
                                    );
                                }
                            }
                            Err(FillFailure::HardFailure) => {
                                if best
                                    .as_ref()
                                    .is_none_or(|success| target > success.preferred_word_count)
                                {
                                    impossible_from = impossible_from.min(target);
                                    cancel_matching(&active, |active_target| {
                                        active_target >= target
                                    });
                                }
                                emit_search_event(
                                    &mut observer,
                                    search_start,
                                    SearchEventKind::HardFailure,
                                    active.len(),
                                    best.as_ref().map(|success| success.preferred_word_count),
                                    impossible_from,
                                    fixed_preferred_word_count,
                                    EventDetails {
                                        worker: Some((id, target)),
                                        result: Some(SearchEventResult::HardFailure),
                                        ..EventDetails::default()
                                    },
                                );
                            }
                            Err(FillFailure::Timeout) => {
                                emit_search_event(
                                    &mut observer,
                                    search_start,
                                    SearchEventKind::Timeout,
                                    active.len(),
                                    best.as_ref().map(|success| success.preferred_word_count),
                                    impossible_from,
                                    fixed_preferred_word_count,
                                    EventDetails {
                                        worker: Some((id, target)),
                                        result: Some(SearchEventResult::Timeout),
                                        ..EventDetails::default()
                                    },
                                );
                                terminal_failure = Some(FillFailure::Timeout);
                                break;
                            }
                            Err(FillFailure::Abort) => {
                                emit_search_event(
                                    &mut observer,
                                    search_start,
                                    SearchEventKind::Abort,
                                    active.len(),
                                    best.as_ref().map(|success| success.preferred_word_count),
                                    impossible_from,
                                    fixed_preferred_word_count,
                                    EventDetails {
                                        worker: Some((id, target)),
                                        result: Some(SearchEventResult::Abort),
                                        ..EventDetails::default()
                                    },
                                );
                            }
                            Err(FillFailure::ExceededBacktrackLimit(_)) => unreachable!(
                                "find_fill_with_options handles retry backtrack limits internally"
                            ),
                        }
                    }
                    Err(mpsc::RecvTimeoutError::Timeout) => {}
                    Err(mpsc::RecvTimeoutError::Disconnected) => {
                        emit_search_event(
                            &mut observer,
                            search_start,
                            SearchEventKind::HardFailure,
                            active.len(),
                            best.as_ref().map(|success| success.preferred_word_count),
                            impossible_from,
                            fixed_preferred_word_count,
                            EventDetails {
                                result: Some(SearchEventResult::HardFailure),
                                ..EventDetails::default()
                            },
                        );
                        terminal_failure = Some(FillFailure::HardFailure);
                        break;
                    }
                }
            }

            cancel_matching(&active, |_| true);

            // Consume cancellation races on both observed and unobserved paths. Late successful
            // fills remain useful as certified evidence even though they cannot affect scheduling.
            drop(sender);
            while !active.is_empty() {
                let Ok(WorkerResult {
                    id,
                    minimum_preferred_words: target,
                    result,
                }) = receiver.recv()
                else {
                    break;
                };
                active.remove(&id);

                let (kind, details) = match result {
                    Ok(fill) => {
                        let statistics = (
                            fill.statistics.states,
                            fill.statistics.backtracks,
                            fill.statistics.retries,
                        );
                        if let Some(best) = best.as_mut() {
                            let preferred_word_count = count_preferred_words(config, &fill.choices);
                            if preferred_word_count >= best.preferred_word_count {
                                let fill_key = canonical_fill_key(config, &fill.choices)
                                    .expect("solver fills contain one choice per slot");
                                best.certified_fills.insert(fill_key);
                            }
                        }
                        (
                            SearchEventKind::Success,
                            EventDetails {
                                worker: Some((id, target)),
                                statistics: Some(statistics),
                                result: Some(SearchEventResult::Success),
                            },
                        )
                    }
                    Err(FillFailure::HardFailure) => (
                        SearchEventKind::HardFailure,
                        EventDetails {
                            worker: Some((id, target)),
                            result: Some(SearchEventResult::HardFailure),
                            ..EventDetails::default()
                        },
                    ),
                    Err(FillFailure::Timeout) => (
                        SearchEventKind::Timeout,
                        EventDetails {
                            worker: Some((id, target)),
                            result: Some(SearchEventResult::Timeout),
                            ..EventDetails::default()
                        },
                    ),
                    Err(FillFailure::Abort) => (
                        SearchEventKind::Abort,
                        EventDetails {
                            worker: Some((id, target)),
                            result: Some(SearchEventResult::Abort),
                            ..EventDetails::default()
                        },
                    ),
                    Err(FillFailure::ExceededBacktrackLimit(_)) => unreachable!(
                        "find_fill_with_options handles retry backtrack limits internally"
                    ),
                };
                emit_search_event(
                    &mut observer,
                    search_start,
                    kind,
                    active.len(),
                    best.as_ref().map(|success| success.preferred_word_count),
                    impossible_from,
                    fixed_preferred_word_count,
                    details,
                );
            }

            let final_incumbent = best.as_ref().map(|success| success.preferred_word_count);
            let result = match terminal_failure {
                Some(FillFailure::Abort) => Err(FillFailure::Abort),
                Some(FillFailure::Timeout) => best.ok_or(FillFailure::Timeout),
                Some(failure) => best.ok_or(failure),
                None => best.ok_or(FillFailure::HardFailure),
            };
            (result, final_incumbent, impossible_from, active.len())
        });

    let final_result = match &result {
        Ok(_) => SearchEventResult::Success,
        Err(FillFailure::HardFailure) => SearchEventResult::HardFailure,
        Err(FillFailure::Timeout) => SearchEventResult::Timeout,
        Err(FillFailure::Abort) => SearchEventResult::Abort,
        Err(FillFailure::ExceededBacktrackLimit(_)) => {
            unreachable!("find_fill_with_options handles retry backtrack limits internally")
        }
    };
    emit_search_event(
        &mut observer,
        search_start,
        SearchEventKind::FinalReturn,
        final_active_worker_count,
        final_incumbent,
        final_impossible_from,
        fixed_preferred_word_count,
        EventDetails {
            result: Some(final_result),
            ..EventDetails::default()
        },
    );

    result
}

#[cfg(test)]
mod tests {
    use super::{
        find_best_fill, find_best_fill_with_observer, initial_targets, prepare_search,
        SearchEventKind, SearchEventResult,
    };
    use crate::backtracking_search::{find_fill_with_options, FillFailure, FillOptions};
    use crate::grid_config::generate_grid_config_from_template_string;
    use crate::word_list::{WordList, WordListSourceConfig, WordListSourceConfigProvider};
    use std::collections::HashSet;
    use std::sync::atomic::AtomicBool;
    use std::sync::Arc;
    use std::time::Duration;

    fn tiered_word_list() -> WordList {
        let source = |id: &str, word: &str| WordListSourceConfig {
            id: id.into(),
            enabled: true,
            provider: WordListSourceConfigProvider::Memory {
                words: vec![(word.into(), 50)],
            },
            normalization: None,
        };
        let mut word_list = WordList::new(
            vec![source("preferred", "cat"), source("standard", "dog")],
            None,
            Some(3),
            None,
        );
        word_list.set_preferred_source_ids(HashSet::from(["preferred".into()]));
        word_list
    }

    fn tiered_single_slot_config() -> crate::grid_config::OwnedGridConfig {
        generate_grid_config_from_template_string(tiered_word_list(), "...\n", 0)
    }

    #[test]
    fn distributes_initial_targets_across_the_full_range() {
        assert_eq!(
            initial_targets(1_000, 10).into_iter().collect::<Vec<_>>(),
            vec![1_000, 900, 800, 700, 600, 500, 400, 300, 200, 0]
        );
        assert_eq!(
            initial_targets(1_000, 1).into_iter().collect::<Vec<_>>(),
            vec![0]
        );
    }

    #[test]
    fn preferred_minimum_is_a_hard_global_constraint() {
        let config = tiered_single_slot_config();
        let result = find_fill_with_options(
            &config.to_config_ref(),
            None,
            None,
            FillOptions {
                minimum_preferred_words: 2,
                ..FillOptions::default()
            },
        );
        assert!(matches!(result, Err(FillFailure::HardFailure)));
    }

    #[test]
    fn parallel_search_finds_the_optimal_preferred_count() {
        let config = tiered_single_slot_config();
        let config_ref = config.to_config_ref();
        let prepared = prepare_search(&config_ref).unwrap();
        let result = find_best_fill(&config_ref, &prepared, None, Some(2), 0).unwrap();
        assert_eq!(result.preferred_word_count, 1);
    }

    #[test]
    fn observer_reports_successful_convergence_in_order() {
        let config = tiered_single_slot_config();
        let config_ref = config.to_config_ref();
        let prepared = prepare_search(&config_ref).unwrap();
        let mut events = Vec::new();
        let result =
            find_best_fill_with_observer(&config_ref, &prepared, None, Some(1), 0, |event| {
                events.push(event);
            })
            .unwrap();

        assert_eq!(result.preferred_word_count, 1);
        assert_eq!(result.fixed_preferred_word_count, 0);
        assert_eq!(
            events.iter().map(|event| event.kind).collect::<Vec<_>>(),
            vec![
                SearchEventKind::WorkerStart,
                SearchEventKind::Success,
                SearchEventKind::IncumbentImprovement,
                SearchEventKind::FinalReturn,
            ]
        );
        assert!(events
            .windows(2)
            .all(|pair| pair[0].elapsed <= pair[1].elapsed));

        let started = events[0];
        assert_eq!(started.worker_id, Some(0));
        assert_eq!(started.target, Some(0));
        assert_eq!(started.active_worker_count, 1);
        assert_eq!(started.incumbent_preferred_word_count, None);
        assert_eq!(started.impossible_from, 2);
        assert_eq!(started.result, None);

        let succeeded = events[1];
        assert_eq!(succeeded.worker_id, Some(0));
        assert_eq!(succeeded.active_worker_count, 0);
        assert_eq!(succeeded.incumbent_preferred_word_count, None);
        assert!(succeeded.states.is_some());
        assert!(succeeded.backtracks.is_some());
        assert!(succeeded.retries.is_some());
        assert_eq!(succeeded.result, Some(SearchEventResult::Success));

        let improved = events[2];
        assert_eq!(improved.incumbent_preferred_word_count, Some(1));
        assert_eq!(improved.fixed_preferred_word_count, 0);
        assert_eq!(improved.discovered_preferred_word_count, Some(1));

        let returned = events[3];
        assert_eq!(returned.worker_id, None);
        assert_eq!(returned.target, None);
        assert_eq!(returned.active_worker_count, 0);
        assert_eq!(returned.incumbent_preferred_word_count, Some(1));
        assert_eq!(returned.result, Some(SearchEventResult::Success));
    }

    #[test]
    fn prepare_search_reports_unsatisfiable_root_without_events() {
        // Root arc consistency now rejects this unsatisfiable 3x3 grid during
        // `prepare_search`, so no observer events are emitted: there is no
        // scheduler yet when preparation fails. The hard-failure contract moved
        // from the observer event stream to the `prepare_search` return value.
        let config =
            generate_grid_config_from_template_string(tiered_word_list(), "...\n.#.\n...\n", 0);
        let config_ref = config.to_config_ref();
        let result = prepare_search(&config_ref);

        assert!(matches!(result, Err(FillFailure::HardFailure)));
    }

    #[test]
    fn observer_reports_scheduler_timeout_without_starting_workers() {
        let config = tiered_single_slot_config();
        let config_ref = config.to_config_ref();
        let prepared = prepare_search(&config_ref).unwrap();
        let mut events = Vec::new();
        let result = find_best_fill_with_observer(
            &config_ref,
            &prepared,
            Some(Duration::ZERO),
            Some(1),
            0,
            |event| events.push(event),
        );

        assert!(matches!(result, Err(FillFailure::Timeout)));
        assert_eq!(
            events.iter().map(|event| event.kind).collect::<Vec<_>>(),
            vec![SearchEventKind::Timeout, SearchEventKind::FinalReturn]
        );
        assert_eq!(events[0].worker_id, None);
        assert_eq!(events[0].active_worker_count, 0);
        assert_eq!(events[0].result, Some(SearchEventResult::Timeout));
        assert_eq!(events[1].result, Some(SearchEventResult::Timeout));
    }

    #[test]
    fn observer_reports_scheduler_abort_without_starting_workers() {
        let mut config = tiered_single_slot_config();
        config.abort = Some(Arc::new(AtomicBool::new(true)));
        let config_ref = config.to_config_ref();
        let prepared = prepare_search(&config_ref).unwrap();
        let mut events = Vec::new();
        let result =
            find_best_fill_with_observer(&config_ref, &prepared, None, Some(1), 0, |event| {
                events.push(event);
            });

        assert!(matches!(result, Err(FillFailure::Abort)));
        assert_eq!(
            events.iter().map(|event| event.kind).collect::<Vec<_>>(),
            vec![SearchEventKind::Abort, SearchEventKind::FinalReturn]
        );
        assert_eq!(events[0].worker_id, None);
        assert_eq!(events[0].result, Some(SearchEventResult::Abort));
        assert_eq!(events[1].result, Some(SearchEventResult::Abort));
    }

    #[test]
    fn fixed_preferred_words_are_separate_from_discovered_words() {
        let config = generate_grid_config_from_template_string(tiered_word_list(), "cat\n", 0);
        let config_ref = config.to_config_ref();
        let prepared = prepare_search(&config_ref).unwrap();
        let mut events = Vec::new();
        let result =
            find_best_fill_with_observer(&config_ref, &prepared, None, Some(1), 0, |event| {
                events.push(event);
            })
            .unwrap();

        assert_eq!(result.preferred_word_count, 1);
        assert_eq!(result.fixed_preferred_word_count, 1);
        let returned = events.last().unwrap();
        assert_eq!(returned.kind, SearchEventKind::FinalReturn);
        assert_eq!(returned.incumbent_preferred_word_count, Some(1));
        assert_eq!(returned.fixed_preferred_word_count, 1);
        assert_eq!(returned.discovered_preferred_word_count, Some(0));
    }
}
