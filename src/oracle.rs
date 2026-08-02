//! A persistent fillability oracle: load the dictionary once, answer many template questions.
//!
//! The one-shot CLI spends essentially all of its time building the `WordList` and almost none
//! deciding fillability, so a constructor that asks "is this grid still fillable?" ten thousand
//! times per restart cannot afford to go through it. An [`Oracle`] holds the loaded dictionary and
//! rebuilds only the per-template parts — slot topology, candidate lists, and the initial
//! arc-consistent root — on every probe. There is deliberately no caching of propagation state
//! between probes: the dictionary is the expensive thing, and 225 cells are not.
//!
//! Probes answer with three distinguishable states, never a boolean; see [`Verdict`].

use std::fmt::{self, Display, Formatter};
use std::time::{Duration, Instant};

use crate::backtracking_search::{find_fill_from_prepared, FillFailure, FillOptions};
use crate::grid_config::{
    generate_grid_config_from_parsed, render_grid, CandidateOrder, OwnedGridConfig, ParsedTemplate,
    TemplateError,
};
use crate::live_state::PreparedSearch;
use crate::word_list::{DiacriticPolicyConflict, WordList};

/// Campaign-fixed probe policy. Word lists, scores, dupe rules and diacritic handling are all
/// properties of the loaded `WordList` and are therefore fixed for the lifetime of the oracle by
/// construction; what remains here is everything else a probe needs. If a campaign needs two
/// policies, run two oracles.
#[derive(Debug, Clone)]
pub struct OracleOptions {
    /// Minimum allowable word score, applied to every slot of every probe.
    pub min_score: u16,

    /// Default search budget spent per probe *after* initial arc consistency. Zero means
    /// "arc consistency only", which is the cheap and most decisive setting: it can prove
    /// `Unfillable` but never proves `Fillable`.
    pub default_probe_time: Duration,

    /// Base RNG seed for probe searches. Probes are otherwise deterministic given a template.
    pub seed: u64,
}

impl Default for OracleOptions {
    fn default() -> Self {
        OracleOptions {
            min_score: 50,
            default_probe_time: Duration::ZERO,
            seed: 0,
        }
    }
}

/// Per-probe overrides.
#[derive(Debug, Clone, Copy, Default)]
pub struct ProbeOptions {
    /// Overrides [`OracleOptions::default_probe_time`] when present. `Some(Duration::ZERO)`
    /// requests arc consistency only even when the campaign default is larger.
    pub probe_time: Option<Duration>,

    /// Render and return the fill when one is found. Costs one grid render, not one search.
    pub want_fill: bool,
}

/// The three states a probe can end in. Collapsing [`Verdict::Unknown`] into
/// [`Verdict::Unfillable`] is how a constructor comes to report a perfectly good grid as
/// saturated, so they are distinct here and there is no `bool` conversion.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    /// Proven: no assignment of dictionary words satisfies this grid, honoring the dupe index and
    /// the shared-substring constraint. Either initial arc consistency wiped out a domain, or the
    /// search exhausted the whole tree within the budget. Safe to prune.
    Unfillable,

    /// A complete fill was found.
    Fillable,

    /// The budget ran out with neither a fill nor a proof. **Not** a rejection.
    Unknown,
}

impl Verdict {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Verdict::Unfillable => "unfillable",
            Verdict::Fillable => "fillable",
            Verdict::Unknown => "unknown",
        }
    }
}

impl Display for Verdict {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// The answer to one probe, plus the diagnostics that are free to collect.
#[derive(Debug, Clone)]
pub struct Probe {
    pub verdict: Verdict,

    /// Number of slots in the template.
    pub slot_count: usize,

    /// Smallest surviving candidate count over all slots after initial arc consistency, counting a
    /// slot whose fill is fully specified as one. Zero exactly when arc consistency proved the
    /// grid unfillable. This is the honest version of a "every slot keeps at least N candidates"
    /// screen: it is measured after propagation rather than from the induced pattern alone.
    pub min_domain: usize,

    /// Time spent turning the template into slot topology and candidate lists, before propagation.
    pub setup_time: Duration,

    /// Time spent establishing initial arc consistency.
    pub arc_consistency_time: Duration,

    /// Total probe time: setup, arc consistency, and any search.
    pub elapsed: Duration,

    /// The fill as a template string (blocks as `#`), when one was found and requested.
    pub fill: Option<String>,
}

/// A probe that could not be attempted. These are input problems, not verdicts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProbeError {
    /// The template did not parse; see [`TemplateError`].
    Template(TemplateError),

    /// The template has a run longer than the longest word the corpus was loaded with. Refused
    /// rather than reported unfillable, because that emptiness would be an artifact of the
    /// load-time length filter rather than a proof.
    SlotTooLong { length: usize, maximum: usize },
}

impl From<TemplateError> for ProbeError {
    fn from(error: TemplateError) -> Self {
        ProbeError::Template(error)
    }
}

impl Display for ProbeError {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        match self {
            ProbeError::Template(error) => error.fmt(f),
            ProbeError::SlotTooLong { length, maximum } => write!(
                f,
                "template has a slot of length {length}, above the oracle's maximum of {maximum}"
            ),
        }
    }
}

/// A loaded dictionary plus a fixed probe policy. Construct once per campaign, probe many times.
///
/// The corpus is campaign state and stays byte-for-byte identical across probes: each probe brackets
/// its configuration work with [`WordList::snapshot`] and [`WordList::rewind`], so the hidden
/// entries a fully specified slot forces into the dictionary are local to the grid that needed them.
/// Without that, a service answering millions of questions would slowly answer them against a
/// dictionary shaped by whichever grids happened to come first.
pub struct Oracle {
    word_list: WordList,
    options: OracleOptions,
    /// Derived from the corpus, never configured separately: whether fixed letters in a template
    /// must be folded to match dictionary entries.
    convert_diacritics: bool,
    probe_count: u64,
}

impl Oracle {
    /// Take ownership of a fully configured word list. Everything that varies per campaign —
    /// sources, tiers, blocklist, `max_shared_substring`, `exempt_preferred_dupes`, and how
    /// accented letters were folded — is already baked into `word_list` and cannot be changed
    /// afterwards.
    ///
    /// Fails when the enabled sources disagree about diacritics. There is then no policy the
    /// oracle can apply to a template's fixed letters, and picking one would make it answer
    /// `Unfillable` — a verdict callers are told is a proof — about grids that fill.
    pub fn new(
        word_list: WordList,
        options: OracleOptions,
    ) -> Result<Self, DiacriticPolicyConflict> {
        let convert_diacritics = word_list.converts_diacritics()?;
        Ok(Oracle {
            word_list,
            options,
            convert_diacritics,
            probe_count: 0,
        })
    }

    /// Whether this oracle folds accented letters in templates, as its corpus does.
    #[must_use]
    pub fn converts_diacritics(&self) -> bool {
        self.convert_diacritics
    }

    /// The campaign corpus, exactly as it was when the oracle was constructed.
    #[cfg(test)]
    #[must_use]
    pub fn word_list(&self) -> &WordList {
        &self.word_list
    }

    /// Number of probes answered so far.
    #[cfg(test)]
    #[must_use]
    pub fn probe_count(&self) -> u64 {
        self.probe_count
    }

    /// The longest slot this oracle can answer about, i.e. the longest word bucket the dictionary
    /// was loaded with.
    #[must_use]
    pub fn max_slot_length(&self) -> usize {
        self.word_list.words.len().saturating_sub(1)
    }

    /// Number of visible (non-hidden) words available for filling.
    #[must_use]
    pub fn visible_word_count(&self) -> usize {
        self.word_list
            .words
            .iter()
            .flatten()
            .filter(|word| !word.hidden)
            .count()
    }

    /// Probe a template using the campaign's default budget.
    #[cfg(test)]
    pub fn probe(&mut self, template: &str) -> Result<Probe, ProbeError> {
        self.probe_with(template, &ProbeOptions::default())
    }

    /// Probe a template. `template` uses `#` for blocks, `.` for empty cells, and letters for
    /// fixed fill, exactly like a grid file.
    pub fn probe_with(
        &mut self,
        template: &str,
        probe_options: &ProbeOptions,
    ) -> Result<Probe, ProbeError> {
        let start = Instant::now();
        let parsed = self.parse_template(template)?;

        let budget = probe_options
            .probe_time
            .unwrap_or(self.options.default_probe_time);
        // Ranking the candidates is the expensive half of building a config and only matters as
        // the search's value heuristic, so an arc-consistency-only probe skips it. The verdict is
        // identical either way.
        let order = if budget.is_zero() {
            CandidateOrder::Unranked
        } else {
            CandidateOrder::Ranked
        };

        let corpus = self.word_list.snapshot();
        // The config borrows the corpus for as long as it lives, so the rewind below could not
        // even be written inside this block: the borrow checker, not a convention, is what keeps
        // a config from outliving the word ids it holds.
        let probe = {
            let owned = generate_grid_config_from_parsed(
                &mut self.word_list,
                &parsed,
                self.options.min_score,
                order,
            );
            let setup_time = start.elapsed();
            run_probe(
                &owned,
                budget,
                probe_options.want_fill,
                self.options.seed,
                start,
                setup_time,
            )
        };

        // Drop this grid's forced entries before answering, so the next probe sees the corpus the
        // campaign was configured with.
        self.word_list.rewind(&corpus);
        self.probe_count += 1;
        Ok(probe)
    }

    /// Validate a template's syntax, then fold its fixed letters the way the corpus was folded.
    ///
    /// Parse first, fold second. The other order runs a dictionary normalizer across `#` and `.`,
    /// which at best is a no-op and at worst deletes the entire grid.
    fn parse_template(&self, template: &str) -> Result<ParsedTemplate, ProbeError> {
        let mut parsed = ParsedTemplate::parse(template)?;
        if self.convert_diacritics {
            parsed.fold_diacritics()?;
        }

        let maximum = self.max_slot_length();
        if parsed.longest_run > maximum {
            return Err(ProbeError::SlotTooLong {
                length: parsed.longest_run,
                maximum,
            });
        }
        Ok(parsed)
    }
}

/// Decide one already-configured template. Split out from [`Oracle::probe_with`] so that the
/// borrowed view lives no longer than the answer, which is what lets the caller rewind afterwards.
fn run_probe(
    owned: &OwnedGridConfig,
    budget: Duration,
    want_fill: bool,
    seed: u64,
    start: Instant,
    setup_time: Duration,
) -> Probe {
    let config = owned.to_config_ref();
    let slot_count = config.slot_configs.len();

    // Initial arc consistency has no deadline and the oracle never sets an abort flag, so a
    // wiped-out domain is the only way this fails, and it is a proof.
    let Ok(prepared) = PreparedSearch::new(&config) else {
        let elapsed = start.elapsed();
        return Probe {
            verdict: Verdict::Unfillable,
            slot_count,
            min_domain: 0,
            setup_time,
            arc_consistency_time: elapsed.saturating_sub(setup_time),
            elapsed,
            fill: None,
        };
    };

    let arc_consistency_time = prepared.initial_arc_consistency_time();
    let min_domain = prepared.min_remaining_options();

    if budget.is_zero() {
        return Probe {
            verdict: Verdict::Unknown,
            slot_count,
            min_domain,
            setup_time,
            arc_consistency_time,
            elapsed: start.elapsed(),
            fill: None,
        };
    }

    let search_start = Instant::now();
    let options = FillOptions {
        rng_seed_offset: seed,
        ..FillOptions::default()
    };
    let result = find_fill_from_prepared(
        &config,
        &prepared,
        search_start,
        Some(search_start + budget),
        None,
        options,
    );

    let (verdict, fill) = match result {
        Ok(success) => (
            Verdict::Fillable,
            want_fill.then(|| render_grid(&config, &success.choices).replace('.', "#")),
        ),
        // The solver only reports a hard failure after popping its last choice, which means it
        // refuted the entire tree; that is as much a proof as the initial propagation.
        Err(FillFailure::HardFailure) => (Verdict::Unfillable, None),
        Err(_) => (Verdict::Unknown, None),
    };

    Probe {
        verdict,
        slot_count,
        min_domain,
        setup_time,
        arc_consistency_time,
        elapsed: start.elapsed(),
        fill,
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;
    use std::time::Duration;

    use crate::grid_config::{
        generate_grid_config_from_parsed, CandidateOrder, ParsedTemplate, TemplateError,
    };
    use crate::live_state::PreparedSearch;
    use crate::oracle::{Oracle, OracleOptions, ProbeError, ProbeOptions, Verdict};
    use crate::word_list::tests::word_list_source_config;
    use crate::word_list::{
        NormalizationSettings, WordList, WordListSourceConfig, WordListSourceConfigProvider,
    };

    /// The engine forbids using the same word twice even with no `max_shared_substring`, so a
    /// fillable 2x2 needs four distinct words: `ab`/`cd` across, `ac`/`bd` down.
    const SQUARE: &[(&str, u16)] = &[("ab", 50), ("cd", 50), ("ac", 50), ("bd", 50)];

    fn word_list(words: &[(&str, u16)], max_shared_substring: Option<usize>) -> WordList {
        WordList::new(
            vec![WordListSourceConfig {
                id: "standard".into(),
                enabled: true,
                provider: WordListSourceConfigProvider::Memory {
                    words: words
                        .iter()
                        .map(|&(word, score)| (word.to_string(), score))
                        .collect(),
                },
                normalization: None,
            }],
            None,
            Some(5),
            max_shared_substring,
        )
    }

    fn oracle_with(words: &[(&str, u16)], options: OracleOptions) -> Oracle {
        Oracle::new(word_list(words, None), options).expect("sources agree about diacritics")
    }

    fn oracle(words: &[(&str, u16)]) -> Oracle {
        oracle_with(
            words,
            OracleOptions {
                min_score: 0,
                ..OracleOptions::default()
            },
        )
    }

    const FILL_PROBE: ProbeOptions = ProbeOptions {
        probe_time: Some(Duration::from_secs(5)),
        want_fill: true,
    };

    const AC_ONLY: ProbeOptions = ProbeOptions {
        probe_time: Some(Duration::ZERO),
        want_fill: false,
    };

    #[test]
    fn arc_consistency_only_never_claims_fillable() {
        let mut oracle = oracle(SQUARE);
        let probe = oracle.probe("..\n..").unwrap();
        assert_eq!(probe.verdict, Verdict::Unknown);
        assert_eq!(probe.slot_count, 4);
        assert!(probe.min_domain >= 1);
        assert!(probe.fill.is_none());
    }

    #[test]
    fn arc_consistency_proves_unfillable_without_searching() {
        // No word in the list contains `z`, so the slots through that cell have no candidate.
        let mut oracle = oracle(SQUARE);
        let probe = oracle.probe("z.\n..").unwrap();
        assert_eq!(probe.verdict, Verdict::Unfillable);
        assert_eq!(probe.min_domain, 0);
        // No search was needed, so the whole probe is arc consistency.
        assert!(probe.fill.is_none());
    }

    #[test]
    fn a_budget_turns_unknown_into_fillable_and_returns_the_fill() {
        let mut oracle = oracle(SQUARE);
        let probe = oracle.probe_with("..\n..", &FILL_PROBE).unwrap();
        assert_eq!(probe.verdict, Verdict::Fillable);
        assert_eq!(probe.fill.as_deref(), Some("ab\ncd"));
    }

    #[test]
    fn blocks_round_trip_through_a_returned_fill() {
        let mut oracle = oracle(SQUARE);
        let probe = oracle.probe_with("..#\n..#", &FILL_PROBE).unwrap();
        assert_eq!(probe.verdict, Verdict::Fillable);
        let fill = probe.fill.expect("fill was requested");
        assert_eq!(fill, "ab#\ncd#");
        // The rendered fill is itself a legal template, and probing it agrees.
        assert_eq!(
            oracle.probe_with(&fill, &FILL_PROBE).unwrap().verdict,
            Verdict::Fillable
        );
    }

    #[test]
    fn a_grid_that_survives_arc_consistency_can_still_be_refuted_by_search() {
        // Every option of every slot is compatible with some option of each crossing slot, so
        // arc consistency has nothing to eliminate and never reaches a singleton it could
        // propagate dupe rules from. But every candidate square reuses one of its words, so the
        // grid is unfillable. This is the necessary-versus-sufficient gap in miniature: the cheap
        // screen passes and the proof has to come from the search.
        let mut oracle = oracle(&[("ab", 50), ("ba", 50), ("cd", 50), ("dc", 50)]);
        let screen = oracle.probe_with("..\n..", &AC_ONLY).unwrap();
        assert_eq!(screen.verdict, Verdict::Unknown);
        assert_eq!(screen.min_domain, 4, "arc consistency eliminated nothing");
        assert_eq!(
            oracle.probe_with("..\n..", &FILL_PROBE).unwrap().verdict,
            Verdict::Unfillable
        );
    }

    #[test]
    fn the_shared_substring_constraint_participates_in_the_proof() {
        // Two independent five-letter slots and exactly two candidates, which share the four-letter
        // substring `abcd`. Forbidding that makes the grid unfillable — a constraint no per-slot
        // pattern screen can see, because each slot's domain stays non-empty.
        let words = &[("abcde", 50), ("abcdf", 50)];
        let template = ".....\n#####\n.....";
        let options = || OracleOptions {
            min_score: 0,
            ..OracleOptions::default()
        };

        let mut permissive =
            Oracle::new(word_list(words, None), options()).expect("sources agree about diacritics");
        assert_eq!(
            permissive
                .probe_with(template, &FILL_PROBE)
                .unwrap()
                .verdict,
            Verdict::Fillable
        );

        // `max_shared_substring` is the largest *allowed* overlap, so 3 forbids sharing four.
        let mut strict = Oracle::new(word_list(words, Some(3)), options())
            .expect("sources agree about diacritics");
        let probe = strict.probe_with(template, &FILL_PROBE).unwrap();
        assert_eq!(probe.verdict, Verdict::Unfillable);
        assert_eq!(
            probe.min_domain, 2,
            "both domains survive; the pair does not"
        );
    }

    #[test]
    fn min_score_is_campaign_fixed_and_prunes() {
        let low_scoring = &[("ab", 50), ("cd", 50), ("ac", 50), ("bd", 10)];
        assert_eq!(
            oracle(low_scoring)
                .probe_with("..\n..", &FILL_PROBE)
                .unwrap()
                .verdict,
            Verdict::Fillable
        );
        let mut strict = oracle_with(
            low_scoring,
            OracleOptions {
                min_score: 30,
                ..OracleOptions::default()
            },
        );
        let probe = strict.probe_with("..\n..", &FILL_PROBE).unwrap();
        assert_eq!(probe.verdict, Verdict::Unfillable);
        assert_eq!(probe.min_domain, 0);
    }

    #[test]
    fn probes_do_not_interfere_with_each_other() {
        let mut oracle = oracle(SQUARE);
        // A fully specified slot forces a hidden word into the dictionary and its dupe index;
        // repeated probes must not drift as that accumulates.
        for _ in 0..3 {
            assert_eq!(
                oracle
                    .probe_with("..\n..", &FILL_PROBE)
                    .unwrap()
                    .fill
                    .as_deref(),
                Some("ab\ncd")
            );
            assert_eq!(
                oracle.probe_with("zz\n..", &FILL_PROBE).unwrap().verdict,
                Verdict::Unfillable
            );
        }
        assert_eq!(oracle.probe_count(), 6);
    }

    #[test]
    fn a_blocklist_hides_words_for_every_later_probe() {
        let mut list = word_list(SQUARE, None);
        assert_eq!(list.hide_words(&HashSet::from(["bd".to_string()])), 1);
        let mut oracle = Oracle::new(
            list,
            OracleOptions {
                min_score: 0,
                ..OracleOptions::default()
            },
        )
        .expect("sources agree about diacritics");
        assert_eq!(
            oracle.probe_with("..\n..", &FILL_PROBE).unwrap().verdict,
            Verdict::Unfillable
        );
    }

    #[test]
    fn malformed_templates_are_errors_rather_than_verdicts() {
        let mut oracle = oracle(SQUARE);
        assert_eq!(
            oracle.probe("").unwrap_err(),
            ProbeError::Template(TemplateError::NoRows)
        );
        assert_eq!(
            oracle.probe("..\n...").unwrap_err(),
            ProbeError::Template(TemplateError::RaggedRows {
                row: 1,
                expected: 2,
                found: 3
            })
        );
        // An interior blank row is a malformed grid, not whitespace to be tidied away. Skipping it
        // would answer a question about a different, smaller template.
        assert_eq!(
            oracle.probe("..\n\n..").unwrap_err(),
            ProbeError::Template(TemplateError::EmptyRow { row: 1 })
        );
        assert_eq!(
            oracle.probe("......\n......").unwrap_err(),
            ProbeError::SlotTooLong {
                length: 6,
                maximum: 5
            }
        );
        // A refused probe leaves the oracle usable.
        assert_eq!(oracle.probe("..\n..").unwrap().verdict, Verdict::Unknown);
        assert_eq!(oracle.probe_count(), 1);
    }

    #[test]
    fn a_zero_override_forces_arc_consistency_only() {
        let mut oracle = oracle_with(
            SQUARE,
            OracleOptions {
                min_score: 0,
                default_probe_time: Duration::from_secs(5),
                ..OracleOptions::default()
            },
        );
        assert_eq!(oracle.probe("..\n..").unwrap().verdict, Verdict::Fillable);
        assert_eq!(
            oracle.probe_with("..\n..", &AC_ONLY).unwrap().verdict,
            Verdict::Unknown
        );
        // Even when the search is skipped, arc consistency still refutes.
        assert_eq!(
            oracle.probe_with("z.\n..", &AC_ONLY).unwrap().verdict,
            Verdict::Unfillable
        );
    }

    fn spread_the_wordlist(max_length: usize) -> WordList {
        let list = WordList::new(word_list_source_config(), None, Some(max_length), Some(5));
        assert!(
            list.get_source_errors().get("0").unwrap().is_empty(),
            "failed to load the bundled dictionary"
        );
        list
    }

    /// The oracle skips candidate ranking for arc-consistency-only probes on the grounds that
    /// propagation is order-invariant. That is load-bearing for correctness, not just speed.
    #[test]
    fn ranking_candidates_does_not_change_what_arc_consistency_proves() {
        for template in [
            "...#...\n...#...\n.......\n###....\n.......\n...#...\n...#...\n",
            "cat#...\n...#...\n.......\n###....\n.......\n...#...\n...#dog\n",
            "zzz#...\n...#...\n.......\n###....\n.......\n...#...\n...#...\n",
        ] {
            let parsed = ParsedTemplate::parse(template).expect("test template is valid");
            let mut ranked_list = spread_the_wordlist(7);
            let ranked = generate_grid_config_from_parsed(
                &mut ranked_list,
                &parsed,
                50,
                CandidateOrder::Ranked,
            );
            let mut unranked_list = spread_the_wordlist(7);
            let unranked = generate_grid_config_from_parsed(
                &mut unranked_list,
                &parsed,
                50,
                CandidateOrder::Unranked,
            );
            assert_eq!(
                ranked.slot_options.iter().map(Vec::len).collect::<Vec<_>>(),
                unranked
                    .slot_options
                    .iter()
                    .map(Vec::len)
                    .collect::<Vec<_>>(),
                "ranking changed a domain size for {template:?}"
            );
            match (
                PreparedSearch::new(&ranked.to_config_ref()),
                PreparedSearch::new(&unranked.to_config_ref()),
            ) {
                (Ok(ranked), Ok(unranked)) => assert_eq!(
                    ranked.min_remaining_options(),
                    unranked.min_remaining_options(),
                    "ranking changed the post-propagation minimum domain for {template:?}"
                ),
                (Err(_), Err(_)) => {}
                _ => panic!("ranking changed the arc consistency verdict for {template:?}"),
            }
        }
    }

    /// End to end on the bundled dictionary: one oracle answers a bare grid, a grid whose pinned
    /// entries are compatible, and a grid whose pinned entries are not — the loop the oracle exists
    /// for. Every question reuses the same loaded dictionary.
    #[test]
    fn one_oracle_answers_a_sequence_of_pinned_grids() {
        let mut oracle = Oracle::new(
            spread_the_wordlist(5),
            OracleOptions {
                min_score: 50,
                default_probe_time: Duration::from_secs(10),
                ..OracleOptions::default()
            },
        )
        .expect("sources agree about diacritics");
        let bare = oracle.probe(".....\n.....\n.....\n.....\n.....").unwrap();
        assert_eq!(bare.verdict, Verdict::Fillable);
        assert_eq!(bare.slot_count, 10);
        assert!(bare.min_domain > 100, "min_domain was {}", bare.min_domain);

        // `qqqqq` is not a word, so the pin cannot survive candidate generation at all.
        let refuted = oracle.probe("qqqqq\n.....\n.....\n.....\n.....").unwrap();
        assert_eq!(refuted.verdict, Verdict::Unfillable);
        assert_eq!(refuted.min_domain, 0);

        // A real entry in the top row leaves the rest of the grid to the solver.
        let pinned = oracle.probe("piano\n.....\n.....\n.....\n.....").unwrap();
        assert_eq!(pinned.verdict, Verdict::Fillable);
        let fill = oracle
            .probe_with(
                "piano\n.....\n.....\n.....\n.....",
                &ProbeOptions {
                    probe_time: Some(Duration::from_secs(10)),
                    want_fill: true,
                },
            )
            .unwrap()
            .fill
            .expect("fill was requested");
        assert!(fill.starts_with("piano\n"), "{fill}");
        assert_eq!(fill.lines().count(), 5);
        assert_eq!(oracle.probe_count(), 4);
    }

    /// Everything a probe could leave behind in the corpus.
    fn corpus_fingerprint(word_list: &WordList) -> (Vec<usize>, usize, usize, usize, usize) {
        (
            word_list.words.iter().map(Vec::len).collect(),
            word_list.word_id_by_string.len(),
            word_list.glyphs.len(),
            word_list.dupe_index.group_count(),
            word_list.dupe_index.indexed_word_count(),
        )
    }

    /// The blocker this design exists for: a fully specified slot forces a hidden entry into the
    /// dictionary and its dupe index, and a service answering millions of questions must not
    /// accumulate them. Probes that pin non-words, novel glyphs and repeated non-words all have to
    /// leave the corpus exactly as they found it.
    #[test]
    fn probes_leave_the_corpus_byte_for_byte_unchanged() {
        let mut oracle = Oracle::new(
            word_list(SQUARE, Some(3)),
            OracleOptions {
                min_score: 0,
                ..OracleOptions::default()
            },
        )
        .expect("sources agree about diacritics");
        let before = corpus_fingerprint(oracle.word_list());

        for _ in 0..3 {
            // `zz` is not in the list, and `z` is not even a known glyph.
            oracle.probe("zz\n..").unwrap();
            // Two fully specified slots, so two forced entries in one probe.
            oracle.probe("zz\nqq").unwrap();
            // A pin whose letters are long enough to enter the shared-substring index.
            oracle
                .probe_with("zz\n..", &ProbeOptions::default())
                .unwrap();
            assert_eq!(
                corpus_fingerprint(oracle.word_list()),
                before,
                "a probe left state behind in the corpus"
            );
        }
    }

    /// Same property with real five-letter pins against the bundled dictionary, where the forced
    /// entries are long enough to populate the shared-substring index.
    #[test]
    fn pinned_non_words_do_not_accumulate_in_the_dupe_index() {
        let mut oracle = Oracle::new(
            spread_the_wordlist(5),
            OracleOptions {
                min_score: 50,
                ..OracleOptions::default()
            },
        )
        .expect("sources agree about diacritics");
        let before = corpus_fingerprint(oracle.word_list());
        for pin in ["qqqqq", "xyzzy", "qqqqq", "vwxyz"] {
            let probe = oracle
                .probe(&format!("{pin}\n.....\n.....\n.....\n....."))
                .unwrap();
            // These probes run arc consistency only, so they can never claim a fill; the subject
            // of the test is what they leave behind, not which way they answer.
            assert_ne!(probe.verdict, Verdict::Fillable, "{pin}");
            assert_eq!(
                corpus_fingerprint(oracle.word_list()),
                before,
                "after {pin}"
            );
        }
        // And the corpus still answers the way it did before any of that.
        let bare = oracle
            .probe_with(".....\n.....\n.....\n.....\n.....", &FILL_PROBE)
            .unwrap();
        assert_eq!(bare.verdict, Verdict::Fillable);
    }

    /// A probe answered from a rewound corpus must still enforce the dupe rules that the forced
    /// entry participates in, so the rewind cannot be doing its job too early.
    #[test]
    fn a_forced_entry_still_constrains_the_grid_it_belongs_to() {
        // Two independent five-letter slots, one pinned to a non-word that shares `abcd` with the
        // only candidate for the other. With four-letter overlaps forbidden, the grid is refuted.
        let words = &[("abcde", 50)];
        let template = "abcdf\n#####\n.....";
        let options = || OracleOptions {
            min_score: 0,
            ..OracleOptions::default()
        };

        let mut permissive =
            Oracle::new(word_list(words, None), options()).expect("sources agree about diacritics");
        assert_eq!(
            permissive
                .probe_with(template, &FILL_PROBE)
                .unwrap()
                .verdict,
            Verdict::Fillable
        );

        let mut strict = Oracle::new(word_list(words, Some(3)), options())
            .expect("sources agree about diacritics");
        let before = corpus_fingerprint(strict.word_list());
        assert_eq!(
            strict.probe_with(template, &FILL_PROBE).unwrap().verdict,
            Verdict::Unfillable
        );
        // The entry did its job inside the probe and left nothing behind: the rewind removes it
        // from the substring index it had just joined, not merely from the word buckets.
        assert_eq!(corpus_fingerprint(strict.word_list()), before);
        assert_eq!(
            strict.probe_with(template, &FILL_PROBE).unwrap().verdict,
            Verdict::Unfillable
        );
    }

    fn accented_word_list(settings: Option<NormalizationSettings>) -> WordList {
        WordList::new(
            vec![WordListSourceConfig {
                id: "standard".into(),
                enabled: true,
                provider: WordListSourceConfigProvider::Memory {
                    words: SQUARE
                        .iter()
                        .map(|&(word, score)| (word.to_string(), score))
                        .collect(),
                },
                normalization: settings,
            }],
            None,
            Some(5),
            None,
        )
    }

    const FOLDING: NormalizationSettings = NormalizationSettings {
        strip_punctuation: false,
        convert_diacritics: true,
    };

    /// A diacritic-folding corpus stores `ab`, so a template pinning `á` has to be folded the same
    /// way or the oracle reports a *proof* of unfillability about a grid that fills. The policy is
    /// therefore taken from the corpus and cannot be set to something else.
    #[test]
    fn template_letters_are_folded_exactly_as_the_corpus_was() {
        let mut folding = Oracle::new(
            accented_word_list(Some(FOLDING)),
            OracleOptions {
                min_score: 0,
                ..OracleOptions::default()
            },
        )
        .expect("one source cannot disagree with itself");
        assert!(folding.converts_diacritics());
        assert_eq!(
            folding.probe_with("á.\n..", &FILL_PROBE).unwrap().verdict,
            Verdict::Fillable,
            "an accented pin must reach the folded entry it names"
        );
        assert_eq!(
            folding.probe_with("ab\ncd", &FILL_PROBE).unwrap().fill,
            Some("ab\ncd".to_string())
        );

        // The same letter against a corpus that keeps accents is genuinely unfillable, and saying
        // so is correct rather than an artifact of a mismatched second policy.
        let mut verbatim = oracle(SQUARE);
        assert!(!verbatim.converts_diacritics());
        assert_eq!(
            verbatim.probe_with("á.\n..", &FILL_PROBE).unwrap().verdict,
            Verdict::Unfillable
        );
    }

    /// Sources that disagree leave no single answer, and guessing one would put a wrong `á` in
    /// front of the dictionary on every probe.
    #[test]
    fn an_oracle_refuses_to_guess_between_conflicting_sources() {
        let word_list = WordList::new(
            vec![
                WordListSourceConfig {
                    id: "preferred".into(),
                    enabled: true,
                    provider: WordListSourceConfigProvider::Memory {
                        words: vec![("ab".into(), 50)],
                    },
                    normalization: Some(FOLDING),
                },
                WordListSourceConfig {
                    id: "standard".into(),
                    enabled: true,
                    provider: WordListSourceConfigProvider::Memory {
                        words: vec![("cd".into(), 50)],
                    },
                    normalization: None,
                },
            ],
            None,
            Some(5),
            None,
        );
        let Err(conflict) = Oracle::new(word_list, OracleOptions::default()) else {
            panic!("mismatched sources have no single policy");
        };
        assert_eq!(conflict.folding, vec!["preferred".to_string()]);
        assert_eq!(conflict.verbatim, vec!["standard".to_string()]);
    }

    /// Grid syntax is not word content: a source configured to strip punctuation from its entries
    /// must not have that stripping pointed at the `#` and `.` that carry the grid's structure.
    #[test]
    fn source_punctuation_stripping_never_reaches_the_template() {
        let stripping = NormalizationSettings {
            strip_punctuation: true,
            convert_diacritics: true,
        };
        let mut oracle = Oracle::new(
            accented_word_list(Some(stripping)),
            OracleOptions {
                min_score: 0,
                ..OracleOptions::default()
            },
        )
        .expect("one source cannot disagree with itself");
        assert_eq!(
            oracle.probe_with("..\n..", &FILL_PROBE).unwrap().verdict,
            Verdict::Fillable
        );
        assert_eq!(
            oracle.probe_with("..#\n..#", &FILL_PROBE).unwrap().fill,
            Some("ab#\ncd#".to_string())
        );
    }

    /// A cell holding a bare combining mark folds away to nothing; that is malformed input, not a
    /// silently empty cell.
    #[test]
    fn a_letter_that_folds_away_is_an_error() {
        let mut oracle = Oracle::new(
            accented_word_list(Some(FOLDING)),
            OracleOptions {
                min_score: 0,
                ..OracleOptions::default()
            },
        )
        .expect("one source cannot disagree with itself");
        assert_eq!(
            oracle.probe("\u{0301}.\n..").unwrap_err(),
            ProbeError::Template(TemplateError::UnfoldableLetter {
                row: 0,
                column: 0,
                letter: '\u{0301}',
            })
        );
    }
}
