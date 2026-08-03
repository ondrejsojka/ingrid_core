# Investigation Report Template

Reports go in the project's investigation reports directory (e.g., `docs/perf_investigations/<slug>.md`).

If the project has existing reports, match their style. Otherwise, follow this template:

```markdown
# Investigation: <Title>

## Summary

<One sentence: what was done, result percentage, workload.>

**Baseline:** <median> (median, all values: <all 10 comma-separated>)
**After:** <median> (median, all values: <all 10 comma-separated>)
**Improvement:** ~<X>% (same-session comparison)
**Mann-Whitney U:** <U>/<total_pairs> (<p-bracket>)
**Regression:** <None observed on <workloads> OR <describe regression>>

## Problem

<What was slow and why. Include profiling data: function names, percentages of total time, call counts. Be specific about the mechanism causing slowness.>

## Solution

<What was changed. Key design decisions with rationale. Include code patterns if they clarify the approach.>

### Key design decisions

<Numbered list of important choices and why they were made. Each decision should explain the alternative considered and why it was rejected.>

## Files changed

<Bulleted list of files with brief description of what changed in each.>

## Why X% instead of Y%

<Explain the gap between theoretical maximum improvement and actual measured improvement. This section demonstrates understanding of where the remaining overhead is.>

## Remaining opportunities

<Bulleted list of follow-up investigations suggested by this work. Include estimated ROI if possible.>
```

## Guidelines

- **Be specific**: "function_x = 71.52% of total time" not "function_x is the bottleneck"
- **Include raw numbers**: Absolute timings, not just percentages
- **Explain gaps**: If profiling suggested 10% but you got 5%, explain why
- **Document failures too**: If an approach was tried and regressed, document that in a report. Failed experiments are valuable data.
- **Link to related reports**: Use relative links from the project root
- **All timing values**: Include every individual measurement, not just the median
