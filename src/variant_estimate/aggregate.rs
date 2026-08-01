use super::walk::WalkOutcome;
use super::SamplingDiagnostics;
use crate::fill_set::DistinctFillSet;

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
        let count = 2.0_f64.powf(raw_slack_bits).max(known_lower_bound as f64);
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
        // Floor the interval at the certified evidence rather than at zero. When the
        // relative standard error exceeds 1/1.96 the normal-approximation lower end goes
        // negative; clamping that to `f64::MIN_POSITIVE` used to print a lower bound of
        // -1022 bits, which is the log2 of the smallest subnormal double leaking into the
        // report and reads as a broken number. The honest floor is the number of distinct
        // fills we actually enumerated: those exist, so no interval may sit below them.
        let certified = (known_lower_bound.max(1)) as f64;
        let lower = (count * (1.0 - 1.96 * relative_standard_error)).max(certified);
        let upper = (count * (1.0 + 1.96 * relative_standard_error)).max(lower);
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

pub(super) fn retain_known_fills(outcomes: &[WalkOutcome], known_fills: &mut DistinctFillSet) {
    for outcome in outcomes {
        let WalkOutcome::Accepted { fill, .. } = outcome else {
            continue;
        };
        known_fills.insert(fill.clone());
    }
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

    #[test]
    fn certified_point_estimate_never_falls_below_known_fills() {
        let outcomes = vec![
            WalkOutcome::Accepted {
                log2_weight: 2.0,
                fill: vec![0].into_boxed_slice(),
            },
            WalkOutcome::Rejected,
        ];
        let estimate = summarize(&outcomes).estimate(1, 1.0).unwrap();
        assert!((estimate.count - 2.0).abs() < f64::EPSILON);
        assert!((estimate.slack_bits - 1.0).abs() < f64::EPSILON);
        let clamped = summarize(&outcomes).estimate(100, 1.0).unwrap();
        assert!((clamped.count - 100.0).abs() < f64::EPSILON);
        assert!(clamped.interval_bits.0 <= clamped.slack_bits);
        assert!(clamped.slack_bits <= clamped.interval_bits.1);
    }

    #[test]
    fn wild_weight_spread_floors_the_interval_at_the_certified_bound() {
        // Two accepted walks 30 bits apart: the relative standard error blows past
        // 1/1.96, so the normal-approximation lower end is negative. It must clamp to the
        // certified count, never to a subnormal that prints as -1022 bits.
        let outcomes = vec![
            WalkOutcome::Accepted {
                log2_weight: 0.0,
                fill: vec![0].into_boxed_slice(),
            },
            WalkOutcome::Accepted {
                log2_weight: 30.0,
                fill: vec![1].into_boxed_slice(),
            },
        ];
        let estimate = summarize(&outcomes).estimate(2, 1.0).unwrap();
        assert!(
            estimate.interval_bits.0 >= 1.0,
            "lower end {} fell below log2(2 certified fills)",
            estimate.interval_bits.0
        );
        assert!(estimate.interval_bits.0 <= estimate.slack_bits);
        assert!(estimate.slack_bits <= estimate.interval_bits.1);
    }
}
