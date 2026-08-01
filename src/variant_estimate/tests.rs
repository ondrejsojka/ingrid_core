use std::collections::HashSet;
use std::time::{Duration, Instant};

use super::walk::{collect_walks, RankProposal, WalkOutcome};
use super::{
    estimate_variants, estimator_budget, select_cohort_size, InconclusiveReason,
    VariantEstimateOptions, VariantEstimateOutcome,
};
use crate::backtracking_search::{FillSuccess, Statistics};
use crate::fill_set::DistinctFillSet;
use crate::grid_config::{
    generate_grid_config_from_template_string, Choice, GridConfig, OwnedGridConfig,
};
use crate::parallel_search::{canonical_fill_key, prepare_search, PreferredFillSuccess};
use crate::types::WordId;
use crate::word_list::{WordList, WordListSourceConfig, WordListSourceConfigProvider};

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

fn word_list_with_shared_limit(
    preferred: &[&str],
    standard: &[&str],
    max_shared_substring: Option<usize>,
) -> WordList {
    let max_length = preferred
        .iter()
        .chain(standard)
        .map(|word| word.chars().count())
        .max();
    let mut list = WordList::new(
        vec![source("preferred", preferred), source("standard", standard)],
        None,
        max_length,
        max_shared_substring,
    );
    list.set_preferred_source_ids(HashSet::from(["preferred".into()]));
    list
}

fn word_list(preferred: &[&str], standard: &[&str]) -> WordList {
    word_list_with_shared_limit(preferred, standard, None)
}

fn word_id(config: &OwnedGridConfig, word: &str) -> usize {
    *config
        .word_list
        .word_id_by_string
        .get(word)
        .expect("fixture word should exist")
}

fn incumbent(choices: Vec<Choice>, preferred_word_count: usize) -> PreferredFillSuccess {
    let mut canonical = choices
        .iter()
        .map(|choice| (choice.slot_id, choice.word_id))
        .collect::<Vec<_>>();
    canonical.sort_unstable_by_key(|&(slot_id, _)| slot_id);
    let fill_key = canonical
        .into_iter()
        .map(|(_, word_id)| word_id)
        .collect::<Vec<_>>()
        .into_boxed_slice();
    PreferredFillSuccess {
        fill: FillSuccess {
            statistics: Statistics::default(),
            choices,
        },
        preferred_word_count,
        fixed_preferred_word_count: 0,
        certified_fills: DistinctFillSet::with_fill(fill_key),
    }
}

fn options(walk_count: usize, seed: u64) -> VariantEstimateOptions {
    VariantEstimateOptions {
        runtime_ratio: 0.5,
        worker_count: Some(2),
        walk_count,
        rng_seed: seed,
        maximum_duration: Some(Duration::from_secs(2)),
        guide_probability: 0.8,
    }
}

/// Collect one logical cohort as the given sequence of waves, numbering walks globally the way the
/// estimator's wave loop does.
fn cohort_waves(
    config: &GridConfig,
    prepared: &crate::parallel_search::PreparedSearch,
    incumbent: &PreferredFillSuccess,
    waves: &[usize],
    seed: u64,
    worker_count: usize,
) -> Vec<WalkOutcome> {
    let incumbent_words =
        canonical_fill_key(config, &incumbent.fill.choices).expect("complete incumbent");
    let proposal = RankProposal::new(
        config.slot_options.iter().map(Vec::len).max().unwrap_or(0),
        0.8,
    );
    let mut batch = Vec::new();
    for &wave in waves {
        batch.extend(collect_walks(
            config,
            &prepared.root,
            incumbent.preferred_word_count,
            &incumbent_words,
            &proposal,
            worker_count,
            wave,
            batch.len(),
            seed,
            super::COHORT_SEED_NAMESPACE,
            None,
        ));
    }
    assert_eq!(batch.len(), waves.iter().sum::<usize>());
    batch
}

fn fixed_cohort(
    config: &GridConfig,
    prepared: &crate::parallel_search::PreparedSearch,
    incumbent: &PreferredFillSuccess,
    walk_count: usize,
    seed: u64,
    worker_count: usize,
) -> Vec<WalkOutcome> {
    cohort_waves(
        config,
        prepared,
        incumbent,
        &[walk_count],
        seed,
        worker_count,
    )
}

/// Per-walk identity, so two cohorts drawn from the same seed stream can be compared walk by walk.
fn walk_keys(batch: &[WalkOutcome]) -> Vec<Option<(u64, Box<[WordId]>)>> {
    batch
        .iter()
        .map(|outcome| match outcome {
            WalkOutcome::Accepted { log2_weight, fill } => {
                Some((log2_weight.to_bits(), fill.clone()))
            }
            WalkOutcome::Rejected => None,
        })
        .collect()
}

fn assert_identical_estimates(first: &VariantEstimateOutcome, second: &VariantEstimateOutcome) {
    match (first, second) {
        (
            VariantEstimateOutcome::Estimated {
                count: first_count,
                slack_bits: first_slack_bits,
                interval_bits: first_interval_bits,
                sampling: first_sampling,
            },
            VariantEstimateOutcome::Estimated {
                count: second_count,
                slack_bits: second_slack_bits,
                interval_bits: second_interval_bits,
                sampling: second_sampling,
            },
        ) => {
            assert_eq!(first_count, second_count);
            assert_eq!(first_slack_bits, second_slack_bits);
            assert_eq!(first_interval_bits, second_interval_bits);
            assert_eq!(first_sampling.walk_count, second_sampling.walk_count);
            assert_eq!(
                first_sampling.accepted_walk_count,
                second_sampling.accepted_walk_count
            );
            assert_eq!(
                first_sampling.effective_sample_size,
                second_sampling.effective_sample_size
            );
        }
        _ => panic!("both cohorts should produce estimates"),
    }
}

#[test]
fn fully_fixed_grid_has_exactly_one_fill() {
    let config =
        generate_grid_config_from_template_string(word_list(&["cat"], &["dog"]), "cat\n", 0);
    let config_ref = config.to_config_ref();
    let prepared = prepare_search(&config_ref).unwrap();
    let result = incumbent(
        vec![Choice {
            slot_id: 0,
            word_id: word_id(&config, "cat"),
        }],
        1,
    );
    let estimate = estimate_variants(
        &config_ref,
        &prepared,
        &result,
        Duration::from_secs(10),
        &options(64, 1),
    );
    assert!(matches!(
        estimate.outcome,
        VariantEstimateOutcome::Exact { count: 1 }
    ));
}

#[test]
fn impossible_preferred_threshold_has_exactly_zero_fills() {
    let config =
        generate_grid_config_from_template_string(word_list(&["cat"], &["dog"]), "...\n", 0);
    let config_ref = config.to_config_ref();
    let prepared = prepare_search(&config_ref).unwrap();
    let result = incumbent(
        vec![Choice {
            slot_id: 0,
            word_id: word_id(&config, "cat"),
        }],
        2,
    );
    let estimate = estimate_variants(
        &config_ref,
        &prepared,
        &result,
        Duration::from_secs(10),
        &options(64, 2),
    );
    assert!(matches!(
        estimate.outcome,
        VariantEstimateOutcome::Exact { count: 0 }
    ));
}

#[test]
fn disconnected_slots_estimate_duplicate_constrained_count() {
    let config =
        generate_grid_config_from_template_string(word_list(&["cat"], &["dog"]), "...#...\n", 0);
    let config_ref = config.to_config_ref();
    let prepared = prepare_search(&config_ref).unwrap();
    let result = incumbent(
        vec![
            Choice {
                slot_id: 0,
                word_id: word_id(&config, "cat"),
            },
            Choice {
                slot_id: 1,
                word_id: word_id(&config, "dog"),
            },
        ],
        0,
    );
    let batch = fixed_cohort(&config_ref, &prepared, &result, 4_096, 3, 2);
    let VariantEstimateOutcome::Estimated { count, .. } = super::cohort_outcome(&batch, 1) else {
        panic!("expected a numerical estimate");
    };
    assert!((count - 2.0).abs() < 0.1, "count was {count}");
}

#[test]
fn shared_substring_rejections_never_contribute_as_leaves() {
    let config = generate_grid_config_from_template_string(
        word_list_with_shared_limit(&[], &["stone", "stony", "spear"], Some(3)),
        ".....#.....\n",
        0,
    );
    let config_ref = config.to_config_ref();
    let prepared = prepare_search(&config_ref).unwrap();
    let result = incumbent(
        vec![
            Choice {
                slot_id: 0,
                word_id: word_id(&config, "stone"),
            },
            Choice {
                slot_id: 1,
                word_id: word_id(&config, "spear"),
            },
        ],
        0,
    );
    let batch = fixed_cohort(&config_ref, &prepared, &result, 4_096, 31, 2);
    let VariantEstimateOutcome::Estimated {
        count, sampling, ..
    } = super::cohort_outcome(&batch, 1)
    else {
        panic!("expected a numerical estimate");
    };
    assert!(
        (count - 4.0).abs() < 0.2,
        "count was {count}; sampling {sampling:#?}"
    );
}

#[test]
fn fixed_seed_is_reproducible_across_worker_counts() {
    let config =
        generate_grid_config_from_template_string(word_list(&["cat"], &["dog"]), "...#...\n", 0);
    let config_ref = config.to_config_ref();
    let prepared = prepare_search(&config_ref).unwrap();
    let result = incumbent(
        vec![
            Choice {
                slot_id: 0,
                word_id: word_id(&config, "cat"),
            },
            Choice {
                slot_id: 1,
                word_id: word_id(&config, "dog"),
            },
        ],
        0,
    );
    let first = super::cohort_outcome(
        &fixed_cohort(&config_ref, &prepared, &result, 1_024, 99, 1),
        1,
    );
    let second = super::cohort_outcome(
        &fixed_cohort(&config_ref, &prepared, &result, 1_024, 99, 4),
        1,
    );
    assert_identical_estimates(&first, &second);
}

#[test]
fn wave_split_cohorts_match_the_single_wave_cohort() {
    let config =
        generate_grid_config_from_template_string(word_list(&["cat"], &["dog"]), "...#...\n", 0);
    let config_ref = config.to_config_ref();
    let prepared = prepare_search(&config_ref).unwrap();
    let result = incumbent(
        vec![
            Choice {
                slot_id: 0,
                word_id: word_id(&config, "cat"),
            },
            Choice {
                slot_id: 1,
                word_id: word_id(&config, "dog"),
            },
        ],
        0,
    );

    // Explicit wave boundaries: walk i keeps its own stream, so both cohorts agree walk by walk.
    let single = fixed_cohort(&config_ref, &prepared, &result, 900, 17, 2);
    let split = cohort_waves(&config_ref, &prepared, &result, &[16, 284, 600], 17, 2);
    assert_eq!(walk_keys(&single), walk_keys(&split));
    assert_identical_estimates(
        &super::cohort_outcome(&single, 1),
        &super::cohort_outcome(&split, 1),
    );

    // The estimator's own wave loop: whatever split the clock produces, the union is still the
    // cohort a single call of that size would have drawn.
    let incumbent_words =
        canonical_fill_key(&config_ref, &result.fill.choices).expect("complete incumbent");
    let proposal = RankProposal::new(
        config_ref.slot_options.iter().map(Vec::len).max().unwrap_or(0),
        0.8,
    );
    let context = super::WalkContext {
        config: &config_ref,
        root: &prepared.root,
        minimum_preferred_words: result.preferred_word_count,
        incumbent_words: &incumbent_words,
        worker_count: 2,
        rng_seed: 17,
    };
    let waved = super::collect_cohort_waves(
        &context,
        &proposal,
        8,
        4_096,
        Instant::now() + Duration::from_millis(500),
    );
    assert!(
        waved.len() > 8,
        "the remaining budget should have funded another wave, got {} walks",
        waved.len()
    );
    let refilled = fixed_cohort(&config_ref, &prepared, &result, waved.len(), 17, 2);
    assert_eq!(walk_keys(&waved), walk_keys(&refilled));
    assert_identical_estimates(
        &super::cohort_outcome(&waved, 1),
        &super::cohort_outcome(&refilled, 1),
    );
}

#[test]
fn canonical_search_evidence_survives_without_sampling() {
    let config =
        generate_grid_config_from_template_string(word_list(&["cat"], &["dog"]), "...#...\n", 0);
    let config_ref = config.to_config_ref();
    let reversed_choices = vec![
        Choice {
            slot_id: 1,
            word_id: word_id(&config, "dog"),
        },
        Choice {
            slot_id: 0,
            word_id: word_id(&config, "cat"),
        },
    ];
    let mut result = incumbent(reversed_choices.clone(), 0);
    assert_eq!(
        canonical_fill_key(&config_ref, &reversed_choices),
        Some(vec![word_id(&config, "cat"), word_id(&config, "dog")].into_boxed_slice())
    );
    result
        .certified_fills
        .insert(vec![word_id(&config, "dog"), word_id(&config, "cat")].into_boxed_slice());
    assert_eq!(result.certified_fills.len(), 2);
    let mut configured = options(16, 4);
    configured.runtime_ratio = 0.0;
    let estimate = estimate_variants(
        &config_ref,
        &prepare_search(&config_ref).unwrap(),
        &result,
        Duration::from_secs(1),
        &configured,
    );
    assert_eq!(estimate.known_distinct_fills, 2);
    assert!(matches!(
        estimate.outcome,
        VariantEstimateOutcome::Inconclusive {
            reason: InconclusiveReason::InsufficientBudget,
            ..
        }
    ));
}

#[test]
fn capped_certified_evidence_marks_the_estimate_capped() {
    // The certified-evidence marker must survive the estimator rebuilding its known-fill set,
    // even when the budget gate stops the estimator before any walk runs.
    let config =
        generate_grid_config_from_template_string(word_list(&["cat"], &["dog"]), "...#...\n", 0);
    let config_ref = config.to_config_ref();
    let mut result = incumbent(
        vec![
            Choice {
                slot_id: 0,
                word_id: word_id(&config, "cat"),
            },
            Choice {
                slot_id: 1,
                word_id: word_id(&config, "dog"),
            },
        ],
        0,
    );
    result.certified_fills.mark_capped();
    let mut configured = options(16, 4);
    configured.runtime_ratio = 0.0;
    let estimate = estimate_variants(
        &config_ref,
        &prepare_search(&config_ref).unwrap(),
        &result,
        Duration::from_secs(1),
        &configured,
    );
    assert!(matches!(
        estimate.outcome,
        VariantEstimateOutcome::Inconclusive {
            reason: InconclusiveReason::InsufficientBudget,
            ..
        }
    ));
    assert!(estimate.known_distinct_fills_capped);
}

#[test]
fn canonical_fill_key_rejects_duplicate_or_missing_slots() {
    let config =
        generate_grid_config_from_template_string(word_list(&["cat"], &["dog"]), "...#...\n", 0);
    let config_ref = config.to_config_ref();
    let cat = word_id(&config, "cat");
    let dog = word_id(&config, "dog");
    let shuffled = vec![
        Choice {
            slot_id: 1,
            word_id: dog,
        },
        Choice {
            slot_id: 0,
            word_id: cat,
        },
    ];
    assert_eq!(
        canonical_fill_key(&config_ref, &shuffled),
        Some(vec![cat, dog].into_boxed_slice())
    );
    assert_eq!(
        canonical_fill_key(
            &config_ref,
            &[
                Choice {
                    slot_id: 0,
                    word_id: cat,
                },
                Choice {
                    slot_id: 0,
                    word_id: dog,
                },
            ],
        ),
        None
    );
    assert_eq!(
        canonical_fill_key(
            &config_ref,
            &[Choice {
                slot_id: 0,
                word_id: cat,
            }],
        ),
        None
    );
}

#[test]
fn invalid_options_are_reported_without_sampling() {
    let config =
        generate_grid_config_from_template_string(word_list(&["cat"], &["dog"]), "...\n", 0);
    let config_ref = config.to_config_ref();
    let prepared = prepare_search(&config_ref).unwrap();
    let result = incumbent(
        vec![Choice {
            slot_id: 0,
            word_id: word_id(&config, "cat"),
        }],
        1,
    );
    let mut configured = options(64, 0);
    configured.guide_probability = f64::NAN;
    let estimate = estimate_variants(
        &config_ref,
        &prepared,
        &result,
        Duration::from_secs(1),
        &configured,
    );
    assert!(matches!(
        estimate.outcome,
        VariantEstimateOutcome::Inconclusive {
            reason: InconclusiveReason::InvalidOptions,
            sampling: None,
        }
    ));
    configured.guide_probability = 1.0;
    let estimate = estimate_variants(
        &config_ref,
        &prepared,
        &result,
        Duration::from_secs(1),
        &configured,
    );
    assert!(matches!(
        estimate.outcome,
        VariantEstimateOutcome::Inconclusive {
            reason: InconclusiveReason::InvalidOptions,
            ..
        }
    ));
}
#[test]
fn runtime_budget_is_capped_by_ratio_and_absolute_limit() {
    let mut configured = options(100, 0);
    configured.runtime_ratio = 9.0;
    configured.maximum_duration = None;
    assert_eq!(
        estimator_budget(Duration::from_secs(10), &configured),
        Duration::from_secs(10)
    );
    configured.runtime_ratio = 0.45;
    let default_ratio_budget = estimator_budget(Duration::from_secs(10), &configured);
    assert!(
        (default_ratio_budget.as_secs_f64() - 4.5).abs() < 1e-3,
        "default ratio budget was {default_ratio_budget:?}"
    );
    configured.maximum_duration = Some(Duration::from_secs(2));
    assert_eq!(
        estimator_budget(Duration::from_secs(10), &configured),
        Duration::from_secs(2)
    );
}

#[test]
fn cohort_size_uses_observed_throughput_and_safety_margin() {
    assert_eq!(
        select_cohort_size(4, Duration::from_millis(500), Duration::from_secs(1), 16,),
        // floor(8 walks/s * 1.0 s * 0.5) = 4
        4
    );
    assert_eq!(
        select_cohort_size(1, Duration::from_secs(1), Duration::from_millis(500), 16,),
        0
    );
    assert_eq!(
        select_cohort_size(4, Duration::from_millis(100), Duration::from_secs(1), 16,),
        // 40 walks/s * 1.0 s * 0.5 clamped to the requested maximum
        16
    );
}
