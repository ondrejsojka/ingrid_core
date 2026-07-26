//! Adaptive multi-core search for fills that maximize preferred-tier words.

use crate::backtracking_search::{find_fill_with_options, FillFailure, FillOptions, FillSuccess};
use crate::grid_config::{Choice, GridConfig};
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
    pub preferred_word_count: usize,
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

/// Search on the requested number of CPU cores and return the fill with the largest provably
/// attainable number of preferred-tier words.
///
/// One worker starts at zero to establish a baseline fill quickly; the rest start at evenly
/// distributed preferred-word minima. A success at `N` cancels every worker whose minimum is at
/// most the success's actual preferred count, while harder workers keep running. A hard failure at
/// `N` symmetrically cancels minima at least `N`. Freed cores bisect the remaining target gaps; once
/// every distinct target is represented, extra cores run independent RNG streams for the
/// still-viable targets.
#[allow(clippy::too_many_lines)]
pub fn find_best_fill(
    config: &GridConfig,
    timeout: Option<Duration>,
    worker_count: Option<usize>,
) -> Result<PreferredFillSuccess, FillFailure> {
    let worker_count = worker_count
        .unwrap_or_else(|| thread::available_parallelism().map_or(1, usize::from))
        .max(1);
    let maximum = maximum_preferred_words(config);
    let deadline = timeout.map(|duration| Instant::now() + duration);

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
                terminal_failure = Some(FillFailure::Abort);
                break;
            }
            if deadline.is_some_and(|deadline| Instant::now() >= deadline) {
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
                let worker_timeout =
                    deadline.map(|deadline| deadline.saturating_duration_since(Instant::now()));
                scope.spawn(move || {
                    let result = find_fill_with_options(
                        config,
                        worker_timeout,
                        None,
                        FillOptions {
                            minimum_preferred_words: target,
                            abort: Some(abort.as_ref()),
                            rng_seed_offset: id.wrapping_mul(0x9E37_79B9_7F4A_7C15),
                        },
                    );
                    let _ = sender.send(WorkerResult {
                        id,
                        minimum_preferred_words: target,
                        result,
                    });
                });
            }

            if active.is_empty() {
                terminal_failure = Some(FillFailure::HardFailure);
                break;
            }

            match receiver.recv_timeout(SCHEDULER_POLL_INTERVAL) {
                Ok(worker_result) => {
                    active.remove(&worker_result.id);
                    match worker_result.result {
                        Ok(fill) => {
                            let preferred_word_count = count_preferred_words(config, &fill.choices);
                            if best.as_ref().is_none_or(|current| {
                                preferred_word_count > current.preferred_word_count
                            }) {
                                best = Some(PreferredFillSuccess {
                                    fill,
                                    preferred_word_count,
                                });
                            }
                            cancel_matching(&active, |target| target <= preferred_word_count);
                        }
                        Err(FillFailure::HardFailure) => {
                            if best.as_ref().is_none_or(|success| {
                                worker_result.minimum_preferred_words > success.preferred_word_count
                            }) {
                                impossible_from =
                                    impossible_from.min(worker_result.minimum_preferred_words);
                                cancel_matching(&active, |target| {
                                    target >= worker_result.minimum_preferred_words
                                });
                            }
                        }
                        Err(FillFailure::Timeout) => {
                            terminal_failure = Some(FillFailure::Timeout);
                            break;
                        }
                        Err(FillFailure::Abort) => {}
                        Err(FillFailure::ExceededBacktrackLimit(_)) => unreachable!(
                            "find_fill_with_options handles retry backtrack limits internally"
                        ),
                    }
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    terminal_failure = Some(FillFailure::HardFailure);
                    break;
                }
            }
        }

        cancel_matching(&active, |_| true);

        match terminal_failure {
            Some(FillFailure::Abort) => Err(FillFailure::Abort),
            Some(FillFailure::Timeout) => best.ok_or(FillFailure::Timeout),
            Some(failure) => best.ok_or(failure),
            None => best.ok_or(FillFailure::HardFailure),
        }
    })
}

#[cfg(test)]
mod tests {
    use super::{find_best_fill, initial_targets};
    use crate::backtracking_search::{find_fill_with_options, FillFailure, FillOptions};
    use crate::grid_config::generate_grid_config_from_template_string;
    use crate::word_list::{WordList, WordListSourceConfig, WordListSourceConfigProvider};
    use std::collections::HashSet;

    fn tiered_single_slot_config() -> crate::grid_config::OwnedGridConfig {
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
        generate_grid_config_from_template_string(word_list, "...\n", 0)
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
        let result = find_best_fill(&config.to_config_ref(), None, Some(2)).unwrap();
        assert_eq!(result.preferred_word_count, 1);
    }
}
