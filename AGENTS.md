# Repository Guidelines

## Project Overview

`ingrid_core` is a Rust 2021 crossword-fill engine exposed as both a library and a CLI. It reads rectangular ASCII templates (`#` block, `.` empty, letters fixed), loads scored standard and optional preferred word lists, searches for a valid fill, and renders the completed grid. The repository also contains Python utilities for preparing Czech dictionaries and estimating template fillability.

## Architecture & Data Flow

The main CLI flow in `src/bin.rs` is:

1. Parse CLI options and a grid template.
2. Build a normalized, tiered `WordList`; optionally hide exact normalized blocklist entries.
3. Convert the template into slot/crossing topology and ranked candidates with `grid_config`.
4. Run `parallel_search`, whose workers invoke the AC-3-backed randomized backtracking solver.
5. Select the best preferred-word result and render its `Choice` values into the grid.

Core modules:

- `word_list`: ingestion, normalization, glyph encoding, source priority, preferred tiers, hidden words, and duplicate indexing.
- `grid_config`: template parsing, slot/crossing construction, candidate filtering/ranking, and rendering. `OwnedGridConfig` owns setup data; `GridConfig<'a>` is the borrowed solver view.
- `arc_consistency`: generic crossword-specific AC-3 propagation through `ArcConsistencyAdapter`.
- `backtracking_search`: reversible per-slot state, dom/wdeg-style selection, randomized retries, deadlines, and abort handling.
- `parallel_search`: scoped worker threads, `std::sync::mpsc`, preferred-word target scheduling, cancellation, and optional scalar telemetry.
- `dupe_index`, `types`, `util`: shared-substring constraints, compact ID aliases, and glyph-count domain summaries.

State is explicit and mutable rather than global: `WordList` owns dictionary state; solver attempts own reversible `Slot`/elimination state. Concurrency uses threads, channels, and `Arc<AtomicBool>`, not an async runtime.

## Key Directories

- `src/`: Rust library, CLI, solver, and inline unit tests.
- `resources/`: tracked dictionaries/blocklists, including `spreadthewordlist.dict` used by the CLI fallback and tests.
- `scripts/`: Python dictionary preparation, source inspection, blocklist application, and fill-margin tooling.
- `calibration/`: committed fillability calibration data and generated grid shapes.
- `examples/`: currently empty; do not assume an example harness exists.
- `local/`: ignored generated corpora, experiments, reports, and sweep outputs. Do not treat it as committed source.

## Development Commands

Use Cargo directly from the repository root:

```sh
cargo check
cargo build
cargo build --release
cargo test --all-features
cargo run --release -- [CLI options] GRID_FILE
```

Useful conventional checks (not repository-configured policies):

```sh
cargo fmt -- --check
cargo clippy --all-targets --all-features -- -D warnings
```

Typical fill invocation:

```sh
cargo run --release -- \
  --preferred-wordlist preferred.dict \
  --wordlist standard.dict \
  --blocklist resources/blocklist_cs.txt \
  --min-score 30 --max-shared-substring 5 \
  --timeout 900 --search-log local/search.csv grid.txt
```

`--timeout 0` searches until optimality is proved; the default is 60 seconds. Consult `README.md` or `cargo run -- --help` before composing less common CLI options.

## Code Conventions & Common Patterns

- Follow standard Rust naming: `snake_case` functions/modules, `PascalCase` structs/enums, and explicit aliases such as `GlyphId`, `WordId`, and `GlobalWordId` in `src/types.rs`.
- Prefer existing data-oriented APIs and borrowed views. Do not introduce a second ownership model beside `OwnedGridConfig`/`GridConfig<'a>`.
- Return `Result` and existing domain errors (`FillFailure`, `ArcConsistencyFailure`, `WordListError`) for expected failures. `panic!`/`expect` are reserved for internal topology or invariant violations.
- Preserve reversible search updates: every provisional choice/elimination must be rollback-safe. Avoid cloning large candidate/domain structures in hot search paths.
- Inject solver state through borrowed configuration or traits such as `ArcConsistencyAdapter`; do not add global mutable state.
- Use the existing thread/channel/atomic model for solver concurrency. Do not add Tokio or async APIs without a concrete cross-cutting requirement.
- Keep telemetry cheap: `SearchEvent` is scalar and intended to avoid allocation in the scheduler path.
- Word handling is normalization-sensitive. Apply the same normalization policy to grids, dictionaries, and blocklists; `--ignore-diacritics` affects all three and output.
- Feature-gated behavior belongs behind existing Cargo features (`serde`, `check_invariants`) where applicable.
- Do not preserve backwards compatibility.

Python scripts generally emit deterministic `word;score` dictionaries and optional CSV/JSON audit artifacts. Preserve exact-normalized denylist precedence and cache fingerprint checks when changing those pipelines. `scripts/fill_margin.py` is a calibrated pre-search heuristic, not a solution-count estimator; `fillability-slack.md` describes proposals, including commands that may not exist.

## Important Files

- `Cargo.toml`: single-package manifest, lib/bin targets, features, dependencies, and optimized dev/test/release profiles.
- `src/lib.rs`: public module surface and `MAX_SLOT_LENGTH`.
- `src/bin.rs`: CLI entry point, argument definitions, error presentation, and search telemetry output.
- `src/word_list.rs`: dictionary model and update/normalization logic.
- `src/grid_config.rs`: public grid construction and rendering APIs.
- `src/backtracking_search.rs`: single-worker solver.
- `src/parallel_search.rs`: multi-core optimizer and observer events.
- `README.md`: canonical CLI and Czech word-source workflows.
- `fill-margin.md`: operational interpretation of the fillability screen.
- `.gitignore`: excludes `target/`, `Cargo.lock`, `local/`, and Python caches.

## Runtime/Tooling Preferences

- `Cargo.lock` may exist locally but is intentionally ignored; do not commit it unless repository policy changes.
- Optional Python workflows have script-specific external requirements, notably `curl`, `pdftotext`, NumPy, and MorphoDiTa/model files. Read each script's `--help`; generated data normally belongs under ignored `local/`. Prefer to use `uv`.
- Build profiles, including tests, use `opt-level = 3`, LTO, and one codegen unit, so first builds/tests may be slower than a default Cargo project.
- The scout subagent is smarter than you think; feel free to use it.

## Testing & QA

Tests use Rust's built-in harness and live in inline `#[cfg(test)]` modules under `src/`; there is currently no top-level `tests/`, coverage tool, or coverage threshold. Shared fixtures are mostly in-memory templates/word lists, plus `resources/spreadthewordlist.dict`; temporary filesystem cases use `tempfile`.

Use `cargo test --all-features` for the complete existing suite. For behavioral changes, prefer light, relevant evidence: smoke the CLI path, add integration/end-to-end coverage where boundaries matter, and add a regression test for a reproduced bug. Do not pursue line-coverage targets or delete existing tests merely to simplify the suite. Keep tests deterministic and preserve focused cases for normalization, duplicate constraints, abort/timeout behavior, preferred-word optimality, serialization, and telemetry.
