---
name: omp-optimize-skill
description: Autonomous performance optimization loop for any codebase — generates candidates, dispatches parallel benchmark workers in isolated git worktrees, KEEP/DISCARD via Mann-Whitney U test, merges winners, writes investigation reports. Use for /optimize-style requests: optimizing hot paths, running benchmark-driven experiments, or maintaining a performance investigation backlog.
---

# omp-optimize-skill — Autonomous Performance Optimization

Autonomous performance optimization system for any codebase. Generates optimization candidates, dispatches parallel workers to isolated git worktrees, measures via Mann-Whitney U test, merges winners, discards losers, cleans up code quality, and loops.

Works with any language/build system. Discovers project structure, benchmarks, and conventions at runtime.

## Invocation

- `/skill:omp-optimize-skill` — Run the full autonomous optimization loop (default)
- `/skill:omp-optimize-skill pick` — Generate and rank candidates only (no workers)
- `/skill:omp-optimize-skill measure` — A/B measure current working tree vs baseline
- `/skill:omp-optimize-skill report <slug>` — Write investigation report and update backlog

## Routing

Parse the argument after `/skill:omp-optimize-skill`:

- No argument or `full` → Load and follow `workflows/full-cycle.md`
- `pick` → Load and follow `workflows/pick-target.md`
- `measure` → Load and follow `workflows/measure-compare.md`
- `report` → Load and follow `workflows/write-report.md`

## Reference Material

Before starting any workflow, read the relevant reference docs:

- `skill://omp-optimize-skill/references/measurement-protocol.md` — Mann-Whitney U test details
- `skill://omp-optimize-skill/references/investigation-template.md` — Report format
- `skill://omp-optimize-skill/references/backlog-conventions.md` — Backlog annotation conventions

## Worker Agents and Coordination

Workers are **eval subagents**, one per optimization candidate, spawned through the eval kernel's `agent()` with `isolated=True, apply=False` — each worker builds and benchmarks inside its own isolated workspace copy, and on completion its changes are captured as a patch file that is NOT applied to the main checkout. (The `task` tool's `isolated` spawns auto-apply the patch at completion, before the result is delivered — that forfeits the DISCARD gate, which is why this skill does not use them.) A `parallel()` fan-out runs a round of 2 candidates concurrently. The orchestrator assigns the candidate directly in the worker's prompt; there is no shared task board. The worker agent specification lives inside the skill at:
```
skill://omp-optimize-skill/agents/optimizer-worker.md
```
Worker prompts stay compact: point at the spec (`Read skill://omp-optimize-skill/agents/optimizer-worker.md for your full protocol`) plus the assignment block (SLUG, BASELINE_BIN, PROJECT_ROOT, BUILD_CMD, BENCH_CMD, SECONDARY_BENCH_CMDS, TEST_CMD, LINT_CMD, FMT_CMD) and the investigation brief. The worker's cwd IS the isolated workspace root — no WORKTREE_PATH is needed.

**Result delivery:** the orchestrator passes the canonical worker result JSON Schema as `schema` on every `agent()` call, with `handle=True` so both the validated object and the completion notice come back. A worker ends its run by returning a single compact, schema-validated result object — verdict (KEEP/DISCARD), slug, summary, primary/secondary Mann-Whitney U measurements, tests_passed, files_changed, insights, failure details. The completion notice carries the captured patch path; only a KEEP patch is ever applied, by the orchestrator via `git apply` (full-cycle Step 7). Full transcripts stay out of band and are reachable at `history://<worker-id>` only when deliberately needed.

**Messaging:** the round engine has no mid-run steering — a `parallel()` round settles as a barrier, then results are processed. Workers surface blockers through `failure_details` and surprises through `insights`. If first-finisher replacement or live DM steering is genuinely needed, full-cycle Step 5 documents a task+hub continuous variant — but task-level `isolated` spawns auto-apply at completion, so that variant must fall back to manual `git worktree` sandboxes. Finished task workers park and stay DM-revivable for follow-up questions.

**There is NO team lifecycle.** No team creation, no claiming from a board, no wind-down or teardown protocol, no deletion ceremony. Workers are spawned per round; when a round settles, the orchestrator processes every result and immediately dispatches the next round. `parallel()` is the collection primitive: it returns when the whole round has settled.

## Key Principles

1. **Sandbox isolation with deferred application**: Each worker runs in an omp-isolated workspace via `agent(..., isolated=True, apply=False)`: concurrent workers never see each other, the main checkout is never touched in flight, and the worker's changes return as a captured patch (see full-cycle.md Step 4). The verdict arrives before any application — only KEEP patches are applied (`git apply`), DISCARD patches are dropped with zero cleanup. The main checkout stays pristine at every instant, so even a user interrupt strands no worker changes.
2. **Statistical rigor**: Mann-Whitney U test with N=10 interleaved runs. U >= 73 (p < 0.05) required to KEEP.
3. **Regression checking**: Primary workload improvement AND no regression on secondary workloads.
4. **Ambitious changes over micro-optimizations**: Architectural redesigns, algorithmic changes, and speculative rewrites are ALWAYS preferred over safe data-layout tweaks, struct shrinking, or enum rearranging. **When the profile shows that structural overhead has been eliminated and the remaining time is dominated by actual computation, the next candidates MUST be algorithmic or architectural — not more micro-optimizations.** A risky candidate with 10% estimated ROI is more valuable than a safe candidate with 2% ROI. Throwaway sandboxes exist precisely to make risky changes cheap to attempt and discard. If you find yourself proposing only data-layout or allocation changes for 2+ consecutive rounds, STOP and force yourself to generate architectural candidates instead.
5. **Self-improvement**: After each round, review worker outcomes for protocol gaps or recurring problems. Update the worker agent spec, workflow docs, or reference files to prevent future issues.
6. **Post-merge cleanup**: After merging a winner, run fmt + lint fix + remove dead code before committing. The merged code must be cleaner than what the worker produced.
7. **Concurrent limits — round width 2, never 1**: every round dispatches exactly 2 workers (a 1-wide round only when a single unstarted candidate remains). The omp session cap is 3 concurrent subagents, so 2 leaves required headroom — it is the right number. When a round settles (KEEP or DISCARD), process every result immediately — report, backlog, apply KEEP patches — and dispatch the next round the moment processing is done. `parallel()` collects the round: it returns when both workers settle. A round of DISCARDs is not a reason to pause or slow down — it is the expected outcome for most investigations. The only idle time tolerated is during initial startup or the minutes spent generating the next round's candidates. **Never run a 1-wide round while 2 candidates exist, and never let a slot sit empty while you "think about" the next candidate. Generate candidates proactively so the next round is ready to dispatch the moment the current one settles.**
8. **Project-agnostic**: Discovers build commands, benchmark commands, test commands, and project conventions at runtime. Never hardcodes project-specific paths or tools.
9. **Simplification as a first-class optimization**: Large refactors that greatly simplify the code are high-priority candidates — not periodic afterthoughts. A refactor that eliminates 200 lines of duplication, unifies parallel code paths, or removes an entire abstraction layer is MORE valuable than a micro-optimization, because it reduces the surface area for future work and makes subsequent optimizations easier to implement and review. **When you identify a large simplifying refactor during survey or candidate generation, prioritize it alongside performance candidates — do not defer it to a "consolidation round."** Additionally, every ~5th round should still include at least one consolidation candidate to catch accumulated cruft. Consolidation candidates are accepted as long as they do not cause a statistically significant regression (U > 27 on all workloads). They do NOT need to demonstrate improvement (U >= 73) — neutral results are fine. The goal is codebase health, not speed. **Simplification workers should be multi-target, not single-focus.** A single simplification worker can tackle multiple unrelated cleanup targets across different files in one investigation — dead code removal here, duplication elimination there, API consolidation elsewhere. The only acceptance criterion is no performance regression (U > 27). There is no reason to limit a simplification worker to a single focused area when multiple targets exist.
10. **Breadth over depth — explore fresh directions, don't iterate on failures**: When generating candidates, prioritize UNINVESTIGATED proposals and approaches over follow-ups to things already tried. If an approach fails on a workload (e.g., "deeper indexing" fails after "root indexing" already partially worked), do NOT try yet another variation of indexing — move to a completely different proposal that attacks the problem from a different angle. **The backlog and Major Proposals list exists precisely for this: each proposal is a distinct tree of investigation. When one tree stops bearing fruit, move to the next tree — don't keep climbing the same one.** Concretely: before generating any follow-up candidate, check how many Major Proposals or backlog categories remain completely uninvestigated. If there are uninvestigated proposals, at least one candidate per round MUST come from a fresh proposal. Never convince yourself that "remaining targets are too hard" or "the design space is exhausted" — those phrases mean you've exhausted one approach, not all approaches. A worker slot should never sit empty because you've run out of ideas on the current approach.
11. **Context window hygiene**: Worker agents produce large transcripts (100K+ tokens). The eval `agent(..., schema=...)` machinery solves this: **Rules:**
    - **Workers end their run by returning the structured result contract** (passed as `schema` on every `agent()` call). The validated result object is all the orchestrator's context receives — verdict, measurements, summary — never the full transcript.
    - **Full transcripts stay out of band.** The orchestrator never ingests a worker transcript except deliberately, via `history://<worker-id>`, when the compact result genuinely lacks needed detail (e.g. salvaging a crashed job).
    - **Process results immediately**: When a worker result arrives, write the investigation report and update the backlog right away, then move on.
    - **Keep worker prompts compact**: Include only a pointer to the worker agent spec, the assignment block (SLUG, BASELINE_BIN, PROJECT_ROOT, BUILD_CMD, BENCH_CMD, SECONDARY_BENCH_CMDS, TEST_CMD, LINT_CMD, FMT_CMD), and the investigation brief. Do not paste large file contents — tell workers to read files themselves.

## CRITICAL: Skill Files Must Remain Project-Agnostic

**When updating skill files during self-improvement (principle 5), you MUST NEVER write project-specific information into any file under `.omp/skills/omp-optimize-skill/`, including `skill://omp-optimize-skill/agents/optimizer-worker.md`.** These files are shared across ALL projects.

Banned content in skill/agent/reference files:
- Project names, binary names, case IDs, benchmark identifiers
- Specific file paths (e.g., `src/kernel/compose.rs`)
- Project-specific script names (e.g., `pinned_env.sh`)
- Language-specific commands presented as the only option (always provide discovery patterns)
- Any information that would only be true for one project

**What IS allowed in self-improvement updates:**
- Generic protocol improvements (e.g., "always check for merge conflicts before committing cleanup")
- Better phrasing of instructions that workers misunderstood
- Additional decision criteria or edge case handling
- New discovery patterns (e.g., "also check for `benchmarks/` directory")
- Corrections to the Mann-Whitney U protocol or report template

## Project Discovery (Step 0)

Before any workflow begins, discover the project:

1. **Read CLAUDE.md or AGENTS.md** (project root) — contains project conventions, test commands, build instructions
2. **Detect language/build system** — Cargo.toml (Rust), package.json (JS/TS), Makefile, CMakeLists.txt, go.mod, etc.
3. **Find benchmarks** — look for `benches/`, `benchmarks/`, criterion config, custom benchmark scripts, perf binaries
4. **Find performance documentation** — look for files like `PERFORMANCE_INVESTIGATIONS.md`, `PERFORMANCE.md`, `docs/perf*/`, `docs/performance/`
5. **Find existing investigation reports** — `docs/perf_investigations/`, `docs/benchmarks/`, similar
6. **Identify primary and secondary benchmark workloads** — the heaviest/most representative benchmark case(s) for primary measurement, and 2-3 diverse secondary cases for regression checking
7. **Identify profiling tools available** — `perf`, flamegraph scripts, `cargo flamegraph`, `py-spy`, custom profiling infrastructure

Store all discovered information in a project context block that gets passed to workers.
