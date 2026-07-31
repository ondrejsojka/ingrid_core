use std::collections::HashSet;
use std::time::Duration;

use super::{
    estimate_variants, estimator_budget, InconclusiveReason, VariantEstimateOptions,
    VariantEstimateOutcome, MAX_WALK_COUNT,
};
use crate::backtracking_search::{FillSuccess, Statistics};
use crate::grid_config::{generate_grid_config_from_template_string, Choice, OwnedGridConfig};
use crate::parallel_search::{prepare_search, PreferredFillSuccess};
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

fn word_list(preferred: &[&str], standard: &[&str]) -> WordList {
    let max_length = preferred
        .iter()
        .chain(standard)
        .map(|word| word.chars().count())
        .max();
    let mut list = WordList::new(
        vec![source("preferred", preferred), source("standard", standard)],
        None,
        max_length,
        None,
    );
    list.set_preferred_source_ids(HashSet::from(["preferred".into()]));
    list
}

fn word_id(config: &OwnedGridConfig, word: &str) -> usize {
    *config
        .word_list
        .word_id_by_string
        .get(word)
        .expect("fixture word should exist")
}

fn incumbent(choices: Vec<Choice>, preferred_word_count: usize) -> PreferredFillSuccess {
    PreferredFillSuccess {
        fill: FillSuccess {
            statistics: Statistics::default(),
            choices,
        },
        preferred_word_count,
        fixed_preferred_word_count: 0,
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
    let estimate = estimate_variants(
        &config_ref,
        &prepared,
        &result,
        Duration::from_secs(10),
        &options(4_096, 3),
    );
    let VariantEstimateOutcome::Estimated { count, .. } = &estimate.outcome else {
        panic!("expected a numerical estimate: {estimate:#?}");
    };
    assert!((*count - 2.0).abs() < 0.1, "count was {count}");
    assert_eq!(estimate.known_distinct_fills, 2);
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
    let mut single = options(1_024, 99);
    single.worker_count = Some(1);
    let mut parallel = single.clone();
    parallel.worker_count = Some(4);
    let first = estimate_variants(
        &config_ref,
        &prepared,
        &result,
        Duration::from_secs(10),
        &single,
    );
    let second = estimate_variants(
        &config_ref,
        &prepared,
        &result,
        Duration::from_secs(10),
        &parallel,
    );
    match (first.outcome, second.outcome) {
        (
            VariantEstimateOutcome::Estimated {
                count: first_count,
                sampling: first_sampling,
                ..
            },
            VariantEstimateOutcome::Estimated {
                count: second_count,
                sampling: second_sampling,
                ..
            },
        ) => {
            assert!((first_count - second_count).abs() < f64::EPSILON);
            assert_eq!(first_sampling.walk_count, second_sampling.walk_count);
            assert_eq!(
                first_sampling.accepted_walk_count,
                second_sampling.accepted_walk_count
            );
            assert!(
                (first_sampling.effective_sample_size - second_sampling.effective_sample_size)
                    .abs()
                    < f64::EPSILON
            );
        }
        _ => panic!("both fixed cohorts should produce estimates"),
    }
}

#[test]
fn interrupted_cohort_retains_certified_sampled_fills() {
    let config = generate_grid_config_from_template_string(
        word_list(&["cat", "cap"], &["dog", "dig"]),
        "...#...\n",
        0,
    );
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
    let mut configured = options(MAX_WALK_COUNT, 7);
    configured.worker_count = Some(1);
    configured.maximum_duration = Some(Duration::from_millis(20));
    configured.guide_probability = 0.0;
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
            reason: InconclusiveReason::Interrupted,
            ..
        }
    ));
    assert!(estimate.known_distinct_fills > 1);
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
        Duration::from_secs(5)
    );
    configured.runtime_ratio = 0.45;
    configured.maximum_duration = Some(Duration::from_secs(2));
    assert_eq!(
        estimator_budget(Duration::from_secs(10), &configured),
        Duration::from_secs(2)
    );
}
