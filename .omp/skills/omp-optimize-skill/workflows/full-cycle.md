# Full Optimization Cycle

You are the orchestrator for an autonomous performance optimization loop. You generate candidates, dispatch parallel workers to isolated worktrees, collect results, merge winners, clean up code quality, discard losers, and loop.

## Prerequisites

- Working directory is a git repository root
- Current branch is clean (no uncommitted changes to source files)
- Project discovery (SKILL.md Step 0) has been completed

## The Loop

Repeat until the user interrupts (Ctrl+C):

### Step 1: SURVEY

Read the current state of the project:

1. Read performance investigation backlog (if one exists — e.g., `PERFORMANCE_INVESTIGATIONS.md`)
2. Read recent investigation reports (if a reports directory exists) — note "Remaining opportunities" sections
3. Check for existing flamegraph SVGs or profiling data
4. Read source files in known hotspot areas (identified via profiling or past reports)

If this is the first round and no profiling data exists, generate a profile:
- For Rust: use `perf record` + `inferno-flamegraph`, or `cargo flamegraph`, or whatever profiling infrastructure the project has
- For other languages: use the appropriate profiling tool
- If no profiling tool is available, rely on code structure analysis and speculation

### Step 2: GENERATE CANDIDATES

Generate a ranked list of 3-5 optimization candidates from four sources:

**Source 1 — Profiling hotspots:** Read profiling data and identify the top functions by inclusive time. Bottom-up analysis: "function X is Y% of runtime, what can we do about it?"

**Source 2 — Code structure analysis:** Read source files and identify:
- Allocation-heavy patterns (allocations in hot loops, unnecessary clones/copies)
- Cache-unfriendly data access patterns (pointer chasing, poor locality)
- Lock contention or synchronization overhead
- Redundant computation (same work done multiple times)
- Data structure inefficiencies (wrong container type, oversized enums, etc.)

**Source 3 — Backlog + past reports:** Pull from the performance backlog's open items and "Remaining opportunities" in recent investigation reports.

**Source 4 — Speculative redesigns:** Brainstorm architectural changes that could yield large improvements:
- Data structure replacements
- Algorithm redesigns
- Elimination of entire subsystems or passes
- These are explicitly encouraged because worktree isolation makes them safe to attempt

**Source 5 — Simplification and consolidation:** Large refactors that greatly simplify the codebase are **high-priority candidates in every round** — not just periodic cleanup. A refactor that eliminates hundreds of lines of duplication, unifies parallel code paths, or removes an unnecessary abstraction layer is more valuable than a micro-optimization because it reduces the surface area for all future work. **When you identify a large simplifying refactor during survey, include it as a candidate alongside performance candidates and rank it by code-reduction impact.** Additionally, every ~5th round should include at least one consolidation candidate to catch accumulated cruft. **Simplification workers should be multi-target, not single-focus.** A single simplification worker can tackle multiple unrelated cleanup targets across different files in one investigation — dead code removal, duplication elimination, API consolidation, etc. The only acceptance criterion is no performance regression (U > 27). There is no reason to limit a simplification worker to one focused area when multiple targets exist. Consolidation targets include:
- Redundant code paths that can be unified (e.g., multiple functions doing the same thing with slight variations)
- Dead code accumulated from prior optimization rounds
- Abstractions that have become unnecessarily complex through incremental changes
- Duplicated logic that should be factored into shared helpers
- Consolidation candidates use a relaxed acceptance threshold: they are KEPT as long as they do not cause a statistically significant regression (U > 27 on all workloads). They do NOT need U >= 73. Neutral performance (27 < U < 73) is perfectly acceptable for consolidation — the value is in code simplicity, not speed.

For each candidate, provide:
- **Slug**: snake_case identifier (e.g., `lockfree_intern`)
- **Title**: one-line description
- **Hypothesis**: expected mechanism of improvement
- **Estimated ROI**: rough percentage based on profiling data
- **Risk**: low/medium/high (high = architectural rewrite)
- **Files**: which source files will be modified
- **Brief**: 2-3 paragraph investigation brief for the worker agent

**Ranking: Breadth first, then ambition.** Before ranking, check: how many Major Proposals or backlog categories remain completely uninvestigated? If there are uninvestigated proposals, at least one candidate MUST come from a fresh, never-tried proposal — not a follow-up to a previous investigation. **When an approach fails (DISCARD), do NOT generate a variation of the same approach as the next candidate.** Move to a completely different proposal that attacks the problem from a different angle. Each Major Proposal is a distinct tree of investigation; when one tree stops bearing fruit, move to the next tree.

Within that constraint, rank by estimated ROI. Architectural redesigns, algorithmic improvements, and speculative rewrites are ALWAYS preferred over safe micro-optimizations. Failed attempts in throwaway sandboxes cost nothing. Only deprioritize a high-ROI candidate if it is fundamentally unmeasurable or would take an impractical amount of worker time.

**NEVER leave a worker slot empty because you've "run out of ideas" on the current approach.** If you can't think of a candidate, you are looking too narrowly. Read the full backlog, read uninvestigated Major Proposals, brainstorm from a completely different angle. A speculative long-shot from an untried proposal is always better than an empty slot.

Select the top 2 candidates for dispatch.

### Step 3: BUILD BASELINE

Build a release/optimized binary from the current state and copy it to `/tmp/` for A/B comparison:

```bash
# Discover and run the project's release build command
# Copy the benchmark-relevant binary to /tmp/<project>_baseline_<binary-name>
```

The specific build command depends on the project (discovered in Step 0). Ensure deterministic build flags where possible (e.g., `CARGO_INCREMENTAL=0` for Rust).

Verify the baseline binary produces expected benchmark output.

### Step 4: SANDBOXES

No manual worktrees. Each worker spawn runs in its own omp-isolated workspace — an on-demand copy of the repo rooted at the worker's cwd — via the eval kernel's `agent()` with `isolated=True, apply=False`:

- `isolated=True` — the worker builds and benchmarks inside its own workspace; concurrent workers never see each other, and the main checkout is never touched while a run is in flight.
- `apply=False` — when the worker completes, its changes are captured as a patch file (`<label>.patch`; the completion notice reports the absolute path) and are NOT applied to the main checkout. The verdict arrives first; only a KEEP patch is ever applied, by you, in Step 7. The workspace itself is torn down automatically at completion.

This is what gives the KEEP/DISCARD gate its integrity: the main checkout is pristine at every instant, a user interrupt can never strand worker changes in the tree, and a DISCARD has zero cleanup.

**Why not `task`-tool spawns with `isolated: true`?** Task-level isolation AUTO-APPLIES the worker's patch to the main checkout at completion, before the result is delivered (measured: the change is present in the main checkout the moment the job settles; the agent ending up idle/parked afterwards is irrelevant — the merge is part of completion teardown, and the `apply`/`merge` guards only exist on eval's `agent()`). Auto-apply would land DISCARD candidates in your tree and would corrupt an interruptible loop. The workflowz eval engine is therefore the default dispatch engine for this loop.

### Step 5: DISPATCH AND COLLECT (Round-Based Fan-Out)

Workers are **eval subagents** spawned through `agent()` inside an eval cell — `parallel()` runs a round of candidates concurrently, and each worker returns a compact result object validated against the JSON schema you pass. There is no team to create, no shared task board, and no per-worker lifecycle — you assign each candidate directly in the worker's prompt.

**Round width 2 — both slots always filled:**

**THIS IS NON-NEGOTIABLE: every round dispatches 2 workers** (a 1-wide round only when a single unstarted candidate remains). When a round settles, process every result immediately — report, backlog, apply KEEP patches — then immediately generate and dispatch the next round, until the session ends or the user interrupts. The only acceptable reasons for a gap between rounds are: (a) the session just started, (b) you are generating the next round's candidates (this should take minutes, not idle time), or (c) the user interrupted. **A DISCARD result is not a reason to slow down — it is normal and expected. Most investigations will be discarded. That is the point of sandbox isolation.** Never pause, hesitate, or "reflect" before dispatching the next round. Process the results, apply KEEPs, dispatch, move on.

**Orchestrator loop (one eval cell):**

```python
empty_rounds = 0
while empty_rounds < 10:                     # Stopping Conditions
    candidates = survey_and_rank()            # Steps 1-2; top unstarted candidates
    build_baseline_if_tree_changed()          # Step 3

    def run_candidate(c):
        try:
            return agent(worker_prompt(c),    # construction below
                         schema=RESULT_SCHEMA,
                         isolated=True, apply=False,
                         handle=True,         # text + structured output + id
                         label=f"opt-{c['slug']}")
        except Exception:
            return None                       # crashed worker: Error Recovery

    batch = candidates[:2]                    # round width 2 — never 1 while 2 exist
    results = parallel([lambda c=c: run_candidate(c) for c in batch])
    # barrier: the round settles together; each worker's patch waited,
    # unapplied, in its completion notice

    for h in results:
        if h is None:                         # crashed: salvage history://<id>,
            continue                          # report as DISCARD, nothing to clean
        result = h["output"]                  # already validated against RESULT_SCHEMA
        patch = patch_path_from(h["text"])    # "changes captured at `<path>`"
        write_report_and_update_backlog(result)     # Step 8 — immediately
        if result["verdict"] == "KEEP":
            apply_and_merge(patch)                  # Step 7: git apply → cleanup → tests → commit
        # DISCARD: nothing applied, nothing to clean; patch path noted in report
    empty_rounds = 0 if any KEEP in this round else empty_rounds + 1
```

The orchestrator should never be idle waiting for all workers to finish before acting. Each result is processed the moment it arrives, and the freed slot is immediately filled. **There is no "pause between rounds" — when the last candidate of round N finishes, round N+1 candidates should already be dispatched or dispatching.**

**Spawning a worker (per candidate):**

- `label`: `opt-<slug>` — names the agent and its captured patch (`opt-<slug>.patch`).
- `isolated=True, apply=False`: the sandbox mechanics (Step 4). NEVER spawn a worker without `apply=False` — without it the patch lands in the main checkout at completion and the DISCARD gate is gone.
- `schema`: the canonical worker result contract below. The worker's final output must conform; the validated object IS the round's result.
- `handle=True`: you need both the validated output object and the completion notice text (which names the patch path).
- The prompt (first `agent()` argument): a pointer to the worker spec plus the assignment block. The worker's cwd IS the isolated workspace root — there is no WORKTREE_PATH:

```
Read skill://omp-optimize-skill/agents/optimizer-worker.md for your full protocol. Follow it exactly.

You are running inside an isolated workspace copy of the repository — your cwd is the repo root of that copy. Build, test, and benchmark here. The workspace is discarded when you finish and your changes are captured as a patch for the orchestrator.

## Your Assignment

SLUG: <candidate slug>
BASELINE_BIN: <absolute path to baseline binary in /tmp/>
PROJECT_ROOT: <absolute path to main repo — read-only reference, never write>
BUILD_CMD: <the release build command for this project>
BENCH_CMD: <command to run the primary benchmark, producing timing output>
SECONDARY_BENCH_CMDS: <commands for secondary workload regression checks>
TEST_CMD: <command to run the test suite>
LINT_CMD: <command to run linting/clippy>
FMT_CMD: <command to run formatting>

## Investigation Brief

<paste the candidate brief>
```

**Worker Prompt Construction:**

1. Point the worker at `skill://omp-optimize-skill/agents/optimizer-worker.md` — do NOT paste the spec inline
2. Add the assignment block shown above (from Step 0 discovery + Step 3/4 artifacts)
3. Add the investigation brief for this candidate

Keep prompts compact; never paste file contents or the worker spec inline.

**Canonical worker result contract** (pass as `schema` on every `agent()` call):

```json
{
  "type": "object",
  "required": ["verdict", "slug", "summary", "tests_passed", "files_changed"],
  "properties": {
    "verdict": {"type": "string", "enum": ["KEEP", "DISCARD"]},
    "slug": {"type": "string"},
    "summary": {"type": "string", "description": "2-4 sentences: what was tried, measured outcome, why it worked or failed"},
    "primary": {"type": "object", "properties": {
      "u": {"type": "number"}, "n_pairs": {"type": "number"},
      "baseline_median": {"type": "number"}, "optimized_median": {"type": "number"},
      "improvement_pct": {"type": "number"},
      "baseline_timings": {"type": "array", "items": {"type": "number"}},
      "optimized_timings": {"type": "array", "items": {"type": "number"}}
    }},
    "secondary": {"type": "array", "items": {"type": "object",
      "properties": {"workload": {"type": "string"}, "u": {"type": "number"},
        "median_ratio": {"type": "number"}, "regression": {"type": "boolean"}},
      "required": ["workload", "u", "regression"]}},
    "tests_passed": {"type": "boolean"},
    "files_changed": {"type": "array", "items": {"type": "string"}},
    "insights": {"type": "array", "items": {"type": "string"}},
    "failure_details": {"type": "string"}
  }
}
```

Read from each worker's structured output: verdict (KEEP/DISCARD), primary workload U statistic and improvement percentage, secondary workload results (if applicable), files changed, insights discovered, and failure details (if DISCARD). You receive only this compact schema-validated object plus the completion notice (patch path) — the full transcript stays out of band at `history://<worker-id>` when you need it.

**Continuous variant (task + hub):** if first-finisher replacement (no round barrier) or live mid-run steering is genuinely needed, spawn workers as named `task` background jobs and collect via `hub` op "wait", which races job deliveries against worker DMs. In that engine `isolated: true` is FORBIDDEN (it auto-applies at completion — see Step 4), so the variant falls back to manual sandboxes: `git worktree add ../<project>-opt-<slug> -b opt/<slug>`, pass WORKTREE_PATH in the assignment, and merge (`git merge opt/<slug>`) or remove (`git worktree remove` + `git branch -D`) those worktrees in Step 7. Finished task workers park and stay DM-revivable for follow-up questions — that is the variant's one real advantage.

### Step 6: REVIEW AND LEARN

For each worker result, evaluate:

1. **Did the worker follow the protocol correctly?** (tests, lint, measurement, report format)
2. **Were there recurring problems?** (workers struggling with a specific API, test framework issues, measurement problems)
3. **Did any worker discover something surprising?** (unexpected hotspot, architectural insight)
4. **Code quality issues in KEEP results?** (dead code introduced, redundant paths, missing error handling)

**Self-improvement**: If you identify a pattern of worker failures or protocol gaps:
- Update `skill://omp-optimize-skill/agents/optimizer-worker.md` with clarifications or additional guidance
- Update workflow files (`skill://omp-optimize-skill/workflows/`) if the measurement or dispatch process needs refinement
- Update reference docs (`skill://omp-optimize-skill/references/`) if conventions need correction
- Log what you changed and why in the investigation report

**CRITICAL: When updating skill/agent/reference files, NEVER write project-specific information.** These files are shared across all projects. No project names, binary names, case IDs, specific file paths, or project-specific commands. Only generic protocol improvements, better phrasing, and additional discovery patterns. See SKILL.md "Skill Files Must Remain Project-Agnostic" section.

### Step 7: MERGE, CLEAN, OR DISCARD

For each worker:

**If KEEP (U >= 73, tests pass, no regression) OR consolidation candidate (U > 27, tests pass, no regression):**

1. **Apply the worker's captured patch** in the main checkout (path from the worker's completion notice):
```bash
cd <main-repo>
git apply <patch_path>
```
If the patch does not apply cleanly (an earlier KEEP in this round touched the same lines): try `git apply --3way`; if still non-trivial, discard instead and note it in the report.

2. **Post-merge cleanup** — the merged code must be cleaner than what the worker produced:
```bash
# Run formatter (project-specific: cargo fmt, prettier, black, gofmt, etc.)
<FMT_CMD>

# Run linter with auto-fix where possible
<LINT_FIX_CMD>  # e.g., cargo clippy --fix --allow-dirty --all-targets

# Fix ALL remaining warnings manually — including pre-existing ones
<LINT_CMD>  # Review output, fix everything

# Remove dead code introduced by the optimization
# Review git diff for unused imports, unreachable branches, commented-out code
```

3. **Review the diff** — read `git diff HEAD~1` and fix:
   - Dead code (unused functions, variables, imports)
   - Redundant code paths
   - Missing or incorrect comments
   - Style inconsistencies with the rest of the codebase
   - Any code that wouldn't belong in an ideal version of these files

4. **Run full test suite again** after cleanup to confirm nothing broke:
```bash
<TEST_CMD>
```
If tests fail after cleanup, the cleanup introduced a bug — fix it.

5. **Commit cleanup as a separate commit:**
```bash
git add -A
git commit -m "Post-merge cleanup: fmt, lint, dead code removal"
```

6. **Rebuild baseline** for next round:
```bash
<BUILD_CMD>
cp <binary> /tmp/<project>_baseline_<binary>
```

7. **No sandbox cleanup needed** — the isolated workspace was discarded automatically at completion. Reference the `.patch` path in the investigation report for provenance.

**If DISCARD:** nothing to clean up — the patch was never applied and the workspace is gone. Record the patch path in the report for the record and move on.

### Step 8: REPORT

For ALL results (kept and discarded):

1. Write investigation report following the template in `skill://omp-optimize-skill/references/investigation-template.md`
   - Place in the project's investigation reports directory (discovered in Step 0)
   - If no such directory exists, create one (e.g., `docs/perf_investigations/`)
2. Update the performance backlog (if one exists) following conventions in `skill://omp-optimize-skill/references/backlog-conventions.md`
3. Commit the reports

### Step 9: LOOP

Return to Step 1 with the updated baseline. The survey will see the new reports and updated backlog, informing the next round of candidate generation.

## Concurrency Limits

- **Round width 2** — every dispatch covers 2 candidates via `parallel()` (leaves headroom within omp's concurrent-spawn limit). A 1-wide round only when a single unstarted candidate remains.
- A round is a barrier: both workers settle before results are processed. This is deliberate — builds plus N=10 measurement dominate worker runtime at roughly fixed cost, so the barrier loses little, and it buys a pristine main checkout at every instant.
- Sequential merge: apply one winning patch at a time, running cleanup between applies; rebuild the baseline once per round after the last KEEP.
- A crashed worker raises out of `agent()` — treat per Error Recovery; the round continues with whatever settled.
- Mid-run steering exists only in the task+hub continuous variant (Step 5); in the round engine, workers surface blockers through `failure_details` and surprises through `insights`.

## Error Recovery

- **Worker crashes**: `agent()` raises inside the round — salvage anything useful from `history://<worker-id>` if the id is known, report as DISCARD, and make sure the next round has a fresh candidate in that slot. Nothing was applied and nothing needs removing
- **Merge conflict**: Attempt resolution. If complex, discard and note in report.
- **Baseline build failure**: Fix before continuing. Do not dispatch workers against a broken baseline.
- **All candidates in a round discarded**: **This is completely normal and expected.** Most optimization attempts fail — that is the entire point of sandbox isolation. A round where all candidates are discarded is not a signal to slow down, pause, or reconsider. It means you generate the next round's candidates and keep going. **Generate new candidates immediately and continue.**
- **Multiple consecutive rounds with no winners**: Still normal. Keep going. Performance optimization is a numbers game — you try many things and most fail. The successes compound.

## Stopping Conditions

**The ONLY conditions that stop the loop:**
- User interrupts (Ctrl+C)
- **10 consecutive ROUNDS** (not individual candidates) with zero winners across ALL candidates in those rounds. This means roughly 20+ consecutive DISCARD results before you even consider pausing. Until you hit that threshold, you keep generating candidates and dispatching workers without hesitation.
- A critical error that cannot be auto-recovered (build system broken, git corruption, etc.)

**To be absolutely clear:** 5 consecutive DISCARDs is not a reason to stop. 10 consecutive DISCARDs is not a reason to stop. 20 consecutive DISCARDs is not a reason to stop. You stop at 10 consecutive ROUNDS (each containing 2 candidates) with no winners — that's 20+ consecutive DISCARDs. Until then, the loop continues unconditionally. Every DISCARD teaches you something that informs the next round's candidates.
