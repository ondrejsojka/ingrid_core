# Write Investigation Report

Write a performance investigation report and update the project's backlog. Use this after completing a measurement to document results.

## Arguments

- `<slug>`: Investigation slug (snake_case name for the report file)

## Workflow

### Step 1: Discover Project

Complete project discovery (SKILL.md Step 0) if not already done. Identify:
- Where investigation reports live (e.g., `docs/perf_investigations/`)
- Whether a performance backlog exists (e.g., `PERFORMANCE_INVESTIGATIONS.md`)
- Existing report format/conventions (read a recent report if available)

### Step 2: Gather Data

Collect all measurement data from the current session:
- A/B timing values (all individual runs, not just medians)
- Mann-Whitney U statistic and p-value bracket
- Files changed (from `git diff --stat`)
- Tests added/modified
- Profiling data if available (function percentages, call counts)

Read the investigation template:
```
skill://omp-optimize-skill/references/investigation-template.md
```

### Step 3: Write Report

Create `<reports-dir>/<slug>.md` following the template.

If the project has existing reports, match their style and conventions. If not, follow the template exactly.

Key sections:
- **Summary**: One sentence, baseline/after numbers, improvement %, U statistic
- **Problem**: What was slow, profiling data, mechanism of slowness
- **Solution**: What was changed, key design decisions with rationale
- **Files changed**: Bulleted list with descriptions
- **Why X% instead of Y%**: Explain the gap between expected and actual improvement
- **Remaining opportunities**: Follow-up candidates from this investigation

### Step 4: Update Backlog

If the project has a performance backlog, update it following conventions in `skill://omp-optimize-skill/references/backlog-conventions.md`:

1. Find the relevant backlog item(s)
2. Add status annotation (Implemented/Investigated/Partially addressed/Superseded)
3. Add strikethrough if fully resolved
4. Add link to the new report
5. If the investigation revealed new candidates, add them to the appropriate section

### Step 5: Commit

```bash
git add <report-file> <backlog-file>
git commit -m "<Title> investigation report"
```

### Step 6: Summary

Report to the user:
- Report file location
- Backlog items updated
- Any new candidates added to the backlog
