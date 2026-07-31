use std::collections::BTreeSet;

use crate::types::WordId;

use super::walk::WalkOutcome;
use super::SamplingDiagnostics;

pub(super) struct Summary {
    pub(super) count: f64,
    pub(super) slack_bits: f64,
    pub(super) interval_bits: (f64, f64),
}

pub(super) struct CohortStatistics {
    walk_count: usize,
    accepted_walk_count: usize,
    effective_sample_size: f64,
    log2_weight_sum: f64,
    log2_squared_weight_sum: f64,
}

impl CohortStatistics {
    pub(super) fn diagnostics(&self) -> SamplingDiagnostics {
        SamplingDiagnostics {
            walk_count: self.walk_count,
            accepted_walk_count: self.accepted_walk_count,
            effective_sample_size: self.effective_sample_size,
        }
    }

    pub(super) fn estimate(
        &self,
        known_lower_bound: usize,
        minimum_effective_samples: f64,
    ) -> Option<Summary> {
        if self.walk_count == 0
            || self.accepted_walk_count == 0
            || self.effective_sample_size < minimum_effective_samples
        {
            return None;
        }
        let raw_slack_bits = self.log2_weight_sum - (self.walk_count as f64).log2();
        let raw_count = 2.0_f64.powf(raw_slack_bits);
        let known_lower_bound = known_lower_bound as f64;
        let count = raw_count.max(known_lower_bound);
        let slack_bits = count.log2();
        let relative_variance = (self.walk_count as f64
            * 2.0_f64.powf(self.log2_squared_weight_sum - 2.0 * self.log2_weight_sum)
            - 1.0)
            .max(0.0);
        let relative_standard_error = if self.walk_count > 1 {
            (relative_variance / (self.walk_count - 1) as f64).sqrt()
        } else {
            0.0
        };
        let lower = (raw_count * (1.0 - 1.96 * relative_standard_error))
            .max(known_lower_bound)
            .max(f64::MIN_POSITIVE);
        let upper = (raw_count * (1.0 + 1.96 * relative_standard_error)).max(lower);
        Some(Summary {
            count,
            slack_bits,
            interval_bits: (lower.log2(), upper.log2()),
        })
    }
}

pub(super) fn summarize(outcomes: &[WalkOutcome]) -> CohortStatistics {
    let walk_count = outcomes.len();
    let accepted_walk_count = outcomes
        .iter()
        .filter(|outcome| matches!(outcome, WalkOutcome::Accepted { .. }))
        .count();
    if accepted_walk_count == 0 {
        return CohortStatistics {
            walk_count,
            accepted_walk_count,
            effective_sample_size: 0.0,
            log2_weight_sum: f64::NEG_INFINITY,
            log2_squared_weight_sum: f64::NEG_INFINITY,
        };
    }
    let log2_weight_sum = log2_sum_exp_scaled(outcomes, 1.0);
    let log2_squared_weight_sum = log2_sum_exp_scaled(outcomes, 2.0);
    let effective_sample_size = 2.0_f64
        .powf(2.0 * log2_weight_sum - log2_squared_weight_sum)
        .min(walk_count as f64);
    CohortStatistics {
        walk_count,
        accepted_walk_count,
        effective_sample_size,
        log2_weight_sum,
        log2_squared_weight_sum,
    }
}

pub(super) fn retain_known_fills(
    outcomes: &[WalkOutcome],
    known_fills: &mut BTreeSet<Box<[WordId]>>,
    maximum_known_fills: usize,
) -> bool {
    let mut capped = false;
    for outcome in outcomes {
        let WalkOutcome::Accepted { fill, .. } = outcome else {
            continue;
        };
        if known_fills.len() < maximum_known_fills {
            known_fills.insert(fill.clone());
        } else if !known_fills.contains(fill) {
            capped = true;
        }
    }
    capped
}

fn log2_sum_exp_scaled(outcomes: &[WalkOutcome], scale: f64) -> f64 {
    let maximum = outcomes
        .iter()
        .filter_map(|outcome| match outcome {
            WalkOutcome::Accepted { log2_weight, .. } => Some(scale * log2_weight),
            WalkOutcome::Rejected => None,
        })
        .fold(f64::NEG_INFINITY, f64::max);
    maximum
        + outcomes
            .iter()
            .filter_map(|outcome| match outcome {
                WalkOutcome::Accepted { log2_weight, .. } => {
                    Some(2.0_f64.powf(scale.mul_add(*log2_weight, -maximum)))
                }
                WalkOutcome::Rejected => None,
            })
            .sum::<f64>()
            .log2()
}

#[cfg(test)]
mod tests {
    use super::{summarize, WalkOutcome};

    #[test]
    fn summary_counts_rejections_as_zero_weight_samples() {
        let outcomes = vec![
            WalkOutcome::Accepted {
                log2_weight: 1.0,
                fill: vec![0].into_boxed_slice(),
            },
            WalkOutcome::Rejected,
        ];
        let statistics = summarize(&outcomes);
        let estimate = statistics.estimate(1, 1.0).unwrap();
        assert!((estimate.count - 1.0).abs() < f64::EPSILON);
    }
}
