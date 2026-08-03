# Pick Optimization Target

Generate and rank optimization candidates without dispatching workers. Use this when you want to review candidates before committing to implementation.

## Workflow

### Step 1: Discover Project

Complete project discovery (SKILL.md Step 0) if not already done.

### Step 1b (Optional): Fan Out Candidate Generation

Candidate generation MAY be fanned out as a single `task` batch of up to 3 read-only `scout` subagents, one per angle: profiling-analysis, code-structure, and backlog+speculation. Each scout returns ranked candidates in the Step 6 format; the orchestrator dedupes and ranks them. The inline Steps 2–5 below remain the canonical flow — scouts are an accelerator, not a replacement.

### Step 2: Profile

Check for recent profiling data (flamegraphs, perf recordings, profile outputs).

If none exists, generate a profile using whatever profiling infrastructure the project has:
- Rust: `perf record` + flamegraph, `cargo flamegraph`, project-specific profiling scripts
- Python: `py-spy`, `cProfile`
- Go: `pprof`
- JS/TS: Node.js profiler, Chrome DevTools
- Other: whatever is appropriate

Read the profile output. Identify the top 10 functions by inclusive time percentage.

### Step 3: Read Backlog

If the project has a performance backlog (e.g., `PERFORMANCE_INVESTIGATIONS.md`), read it. Note:
- Open items (no annotation) — these are candidates
- Partially addressed items — check if follow-up is worthwhile
- "Remaining opportunities" sections in recent investigation reports

### Step 4: Analyze Source

Read source files for likely hotspots (informed by profiling or general knowledge of the codebase). Look for:
- Allocation patterns in hot loops
- Redundant computation
- Cache-unfriendly access patterns
- Data structure inefficiencies
- Lock or synchronization overhead
- Opportunities for algorithmic improvement

### Step 5: Speculate

Brainstorm 2-3 architectural changes that go beyond what profiling suggests:
- "What if we replaced X data structure with Y?"
- "What if we eliminated this entire pass by fusing it with that one?"
- "What if we precomputed this at parse time instead of runtime?"
- "What if we changed the memory layout of this core structure?"

These are high-risk/high-reward candidates. Worktree isolation makes them safe to attempt.

### Step 6: Rank and Report

For each candidate (aim for 5-8 total), report:

```
## Candidate: <slug>

**Title:** <one-line description>
**Source:** profiling | code-analysis | backlog | speculation
**Hypothesis:** <expected mechanism of improvement>
**Estimated ROI:** <X%> based on <evidence>
**Risk:** low | medium | high
**Files:** <which source files>
**Confidence:** <how sure are you this will work>

### Brief
<2-3 paragraphs: what to implement, key design decisions, potential pitfalls>
```

Sort by: estimated_ROI × confidence. Present the top candidates as recommended for the next `/optimize` run.
