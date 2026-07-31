use clap::Parser;
use ingrid_core::backtracking_search::FillFailure;
use ingrid_core::grid_config::{generate_grid_config_from_template_string, render_grid};
use ingrid_core::parallel_search::{
    find_best_fill_prepared, find_best_fill_prepared_with_observer, prepare_search, SearchEvent,
    SearchEventKind, SearchEventResult,
};
use ingrid_core::variant_estimate::{
    estimate_variants, InconclusiveReason, SamplingDiagnostics, VariantEstimate,
    VariantEstimateOptions, VariantEstimateOutcome,
};
use ingrid_core::word_list::{
    normalize_word, NormalizationSettings, WordList, WordListSourceConfig,
    WordListSourceConfigProvider,
};
use std::collections::HashSet;
use std::fmt::{Debug, Display, Formatter};
use std::fs;
use std::io::{self, Write};
use std::num::NonZeroUsize;
use std::time::{Duration, Instant};

const STWL_RAW: &str = include_str!("../resources/spreadthewordlist.dict");

const SEARCH_LOG_HEADER: &str = "elapsed_ms,event,worker_id,target,active_workers,incumbent_preferred_words,impossible_from,fixed_preferred_words,discovered_preferred_words,states,backtracks,retries,result\n";

struct CsvOption<T>(Option<T>);

impl<T: Display> Display for CsvOption<T> {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        if let Some(value) = &self.0 {
            value.fmt(formatter)?;
        }
        Ok(())
    }
}

fn search_event_kind_csv(kind: SearchEventKind) -> &'static str {
    match kind {
        SearchEventKind::WorkerStart => "worker_start",
        SearchEventKind::Success => "success",
        SearchEventKind::HardFailure => "hard_failure",
        SearchEventKind::Abort => "abort",
        SearchEventKind::Timeout => "timeout",
        SearchEventKind::IncumbentImprovement => "incumbent_improvement",
        SearchEventKind::FinalReturn => "final_return",
    }
}

fn search_event_result_csv(result: SearchEventResult) -> &'static str {
    match result {
        SearchEventResult::Success => "success",
        SearchEventResult::HardFailure => "hard_failure",
        SearchEventResult::Abort => "abort",
        SearchEventResult::Timeout => "timeout",
    }
}

struct SearchCsvLog {
    file: fs::File,
}

impl SearchCsvLog {
    fn open(path: &str) -> io::Result<Self> {
        let mut file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?;
        if file.metadata()?.len() == 0 {
            file.write_all(SEARCH_LOG_HEADER.as_bytes())?;
        }
        Ok(Self { file })
    }

    fn write_event(&mut self, event: SearchEvent) -> io::Result<()> {
        let row = format!(
            "{},{},{},{},{},{},{},{},{},{},{},{},{}\n",
            event.elapsed.as_millis(),
            search_event_kind_csv(event.kind),
            CsvOption(event.worker_id),
            CsvOption(event.target),
            event.active_worker_count,
            CsvOption(event.incumbent_preferred_word_count),
            event.impossible_from,
            event.fixed_preferred_word_count,
            CsvOption(event.discovered_preferred_word_count),
            CsvOption(event.states),
            CsvOption(event.backtracks),
            CsvOption(event.retries),
            CsvOption(event.result.map(search_event_result_csv)),
        );
        self.file.write_all(row.as_bytes())
    }
}

/// ingrid_core: Command-line crossword generation tool
#[derive(Parser, Debug)]
#[command(author, version, about, long_about = None)]
struct Args {
    /// Path to the grid file, as ASCII with # representing blocks and . representing empty squares
    grid_path: String,

    /// Path to the standard-tier scored wordlist [default: embedded Spread the Wordlist]
    #[arg(long)]
    wordlist: Option<String>,

    /// Path to a preferred-tier scored wordlist
    #[arg(long)]
    preferred_wordlist: Option<String>,

    /// Path to a blocklist of words to exclude from every tier, one per line; `#` starts a comment
    #[arg(long)]
    blocklist: Option<String>,

    /// Minimum allowable word score
    #[arg(long, default_value_t = 50)]
    min_score: u16,

    /// Maximum shared substring length between entries [default: none]
    #[arg(long)]
    max_shared_substring: Option<usize>,

    /// Convert accented letters to their unaccented forms in the grid and word lists
    #[arg(long, default_value_t = false)]
    ignore_diacritics: bool,

    /// Number of CPU cores to use [default: all available cores]
    #[arg(long)]
    cores: Option<NonZeroUsize>,

    /// Maximum search time in seconds; 0 waits for a proven optimum
    #[arg(long, default_value_t = 60)]
    timeout: u64,

    /// Append scheduler convergence telemetry to this CSV path
    #[arg(long, value_name = "PATH")]
    search_log: Option<String>,

    /// Estimate how many distinct fills are at least as Preferred-heavy as the returned fill
    #[arg(long, default_value_t = false)]
    estimate_variants: bool,

    /// Maximum estimator/search runtime ratio; values above 0.5 are capped
    #[arg(long, default_value_t = 0.45)]
    estimate_runtime_ratio: f32,

    /// Absolute estimator time cap in seconds
    #[arg(long)]
    estimate_max_time: Option<u64>,

    /// Random seed for variant-estimation walks
    #[arg(long, default_value_t = 0)]
    estimate_seed: u64,

    /// Fixed number of variant-estimation walks
    #[arg(long, default_value_t = 16)]
    estimate_walks: usize,

    /// Probability of following the incumbent value at each sampled decision
    #[arg(long, default_value_t = 0.98)]
    estimate_guide_probability: f64,

    /// Print timing information along with the grid
    #[arg(short, long, default_value_t = false)]
    time: bool,
}

struct Error(String);

impl Debug for Error {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0) // Print error unquoted
    }
}

fn fill_failure_error(failure: FillFailure) -> Error {
    Error(match failure {
        FillFailure::HardFailure => "Unfillable grid".into(),
        FillFailure::Timeout => "No fill found before the search timeout".into(),
        FillFailure::Abort => "Fill canceled".into(),
        FillFailure::ExceededBacktrackLimit(limit) => {
            format!("Fill stopped after exceeding the {limit}-backtrack limit")
        }
    })
}

fn print_sampling(sampling: &SamplingDiagnostics) {
    eprintln!(
        "accepted walks: {} / {}",
        sampling.accepted_walk_count, sampling.walk_count
    );
    eprintln!("effective samples: {:.1}", sampling.effective_sample_size);
}

fn print_variant_estimate(estimate: &VariantEstimate) {
    eprintln!(
        "variant estimate for preferred >= {}:",
        estimate.minimum_preferred_words
    );
    let capped = if estimate.known_distinct_fills_capped {
        "+"
    } else {
        ""
    };
    eprintln!(
        "known distinct fills: {}{capped}",
        estimate.known_distinct_fills
    );
    match &estimate.outcome {
        VariantEstimateOutcome::Exact { count: 0 } => {
            eprintln!("estimated fills: 0 (exact)");
            eprintln!("estimated slack: -infinity");
        }
        VariantEstimateOutcome::Exact { count } => {
            eprintln!("estimated fills: {count} (exact)");
            eprintln!("estimated additional variants: {}", count.saturating_sub(1));
            eprintln!("estimated slack: {:.1} bits", (*count as f64).log2());
        }
        VariantEstimateOutcome::Estimated {
            count,
            slack_bits,
            interval_bits: (lower, upper),
            sampling,
        } => {
            eprintln!("estimated fills: ~{count:.3e}");
            eprintln!(
                "estimated additional variants: ~{:.3e}",
                (count - 1.0).max(0.0)
            );
            eprintln!("estimated slack: {slack_bits:.1} bits");
            eprintln!("interval: {lower:.1}-{upper:.1} bits");
            print_sampling(sampling);
        }
        VariantEstimateOutcome::Inconclusive { reason, sampling } => {
            let reason = match reason {
                InconclusiveReason::InvalidOptions => "invalid options",
                InconclusiveReason::InsufficientBudget => "insufficient budget",
                InconclusiveReason::Interrupted => "fixed cohort interrupted",
                InconclusiveReason::InsufficientEvidence => "insufficient evidence",
            };
            eprintln!("estimate: {reason}");
            if let Some(sampling) = sampling {
                print_sampling(sampling);
            }
        }
    }
    eprintln!(
        "estimator time: {:.3} s ({:.1}% of search time)",
        estimate.elapsed.as_secs_f64(),
        100.0 * estimate.search_runtime_ratio
    );
}

fn main() -> Result<(), Error> {
    let args = Args::parse();
    if !args.estimate_runtime_ratio.is_finite() || args.estimate_runtime_ratio < 0.0 {
        return Err(Error(
            "--estimate-runtime-ratio must be a finite nonnegative number".into(),
        ));
    }
    let normalization = args.ignore_diacritics.then_some(NormalizationSettings {
        strip_punctuation: false,
        convert_diacritics: true,
    });
    let raw_grid_content = fs::read_to_string(&args.grid_path)
        .map_err(|_| Error(format!("Couldn't read file '{}'", args.grid_path)))?
        .trim()
        .lines()
        .map(|line| normalize_word(line.trim(), &normalization))
        .collect::<Vec<_>>()
        .join("\n")
        + "\n";

    let height = raw_grid_content.lines().count();

    if height == 0 {
        return Err(Error("Grid must have at least one row".into()));
    }

    if raw_grid_content
        .lines()
        .map(|line| line.chars().count())
        .collect::<HashSet<_>>()
        .len()
        != 1
    {
        return Err(Error("Rows in grid must all be the same length".into()));
    }

    let width = raw_grid_content.lines().next().unwrap().chars().count();
    let max_side = width.max(height);

    if !args
        .max_shared_substring
        .map_or(true, |mss| (3..=10).contains(&mss))
    {
        return Err(Error(
            "If given, max shared substring must be between 3 and 10".into(),
        ));
    }

    let start = Instant::now();

    let blocklist_normalization = normalization.clone();
    let has_preferred_wordlist = args.preferred_wordlist.is_some();
    let mut source_configs = Vec::with_capacity(2);
    if let Some(preferred_wordlist_path) = args.preferred_wordlist {
        source_configs.push(WordListSourceConfig {
            id: "preferred".into(),
            enabled: true,
            provider: WordListSourceConfigProvider::File {
                path: preferred_wordlist_path.into(),
            },
            normalization: normalization.clone(),
        });
    }
    source_configs.push(match args.wordlist {
        Some(wordlist_path) => WordListSourceConfig {
            id: "standard".into(),
            enabled: true,
            provider: WordListSourceConfigProvider::File {
                path: wordlist_path.into(),
            },
            normalization: normalization.clone(),
        },
        None => WordListSourceConfig {
            id: "standard".into(),
            enabled: true,
            provider: WordListSourceConfigProvider::FileContents { contents: STWL_RAW },
            normalization,
        },
    });

    let mut word_list = WordList::new(
        source_configs,
        None,
        Some(max_side),
        args.max_shared_substring,
    );
    if has_preferred_wordlist {
        word_list.set_preferred_source_ids(HashSet::from(["preferred".into()]));
    }

    let word_list_time = start.elapsed();

    let source_errors = word_list
        .get_source_errors()
        .values()
        .flatten()
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    if source_errors.len() == 1 {
        return Err(Error(source_errors[0].clone()));
    } else if !source_errors.is_empty() {
        return Err(Error(format!("\n- {}", source_errors.join("\n- "))));
    }

    if word_list.word_id_by_string.is_empty() {
        return Err(Error("Word list is empty".into()));
    }

    let blocked_word_count = match args.blocklist.as_deref() {
        Some(blocklist_path) => {
            let contents = fs::read_to_string(blocklist_path)
                .map_err(|_| Error(format!("Couldn't read file '{blocklist_path}'")))?;
            let blocked: HashSet<String> = contents
                .lines()
                .filter_map(|line| {
                    let word = line.split('#').next().unwrap_or("").trim();
                    (!word.is_empty()).then(|| normalize_word(word, &blocklist_normalization))
                })
                .filter(|word| !word.is_empty())
                .collect();
            if blocked.is_empty() {
                return Err(Error(format!("Blocklist '{blocklist_path}' has no words")));
            }
            Some(word_list.hide_words(&blocked))
        }
        None => None,
    };

    let grid_config =
        generate_grid_config_from_template_string(word_list, &raw_grid_content, args.min_score);

    let timeout = (args.timeout != 0).then(|| Duration::from_secs(args.timeout));
    let worker_count = args.cores.map(NonZeroUsize::get);
    let config_ref = grid_config.to_config_ref();
    let search_start = Instant::now();
    let deadline = timeout.map(|timeout| search_start + timeout);
    let prepared = prepare_search(&config_ref).map_err(fill_failure_error)?;
    let remaining_timeout =
        deadline.map(|deadline| deadline.saturating_duration_since(Instant::now()));
    let result = if let Some(search_log_path) = args.search_log.as_deref() {
        let mut search_log = SearchCsvLog::open(search_log_path).map_err(|error| {
            Error(format!(
                "Couldn't open search log '{search_log_path}': {error}"
            ))
        })?;
        let mut search_log_error = None;
        let result = find_best_fill_prepared_with_observer(
            &config_ref,
            &prepared,
            remaining_timeout,
            worker_count,
            |event| {
                if search_log_error.is_none() {
                    if let Err(error) = search_log.write_event(event) {
                        search_log_error = Some(error);
                    }
                }
            },
        );
        if let Some(error) = search_log_error {
            return Err(Error(format!(
                "Couldn't write search log '{search_log_path}': {error}"
            )));
        }
        result
    } else {
        find_best_fill_prepared(&config_ref, &prepared, remaining_timeout, worker_count)
    }
    .map_err(fill_failure_error)?;

    let search_elapsed = search_start.elapsed();
    let fill_time = start.elapsed() - word_list_time;

    println!(
        "{}",
        render_grid(&config_ref, &result.fill.choices).replace('.', "#")
    );

    if args.estimate_variants {
        let estimate_options = VariantEstimateOptions {
            runtime_ratio: args.estimate_runtime_ratio.min(0.5),
            worker_count,
            walk_count: args.estimate_walks,
            rng_seed: args.estimate_seed,
            maximum_duration: args.estimate_max_time.map(Duration::from_secs),
            guide_probability: args.estimate_guide_probability,
        };
        let estimate = estimate_variants(
            &config_ref,
            &prepared,
            &result,
            search_elapsed,
            &estimate_options,
        );
        print_variant_estimate(&estimate);
    }

    if args.time {
        let discovered_preferred_word_count =
            result.preferred_word_count - result.fixed_preferred_word_count;
        let blocked = blocked_word_count
            .map(|count| format!("; blocked words: {count}"))
            .unwrap_or_default();
        eprintln!(
            "{word_list_time:?} loading word lists, {fill_time:?} finding fill; preferred words: {} total, {} fixed, {discovered_preferred_word_count} discovered{blocked}",
            result.preferred_word_count, result.fixed_preferred_word_count
        );
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{fill_failure_error, Args, SearchCsvLog, SEARCH_LOG_HEADER};
    use clap::Parser;
    use ingrid_core::backtracking_search::FillFailure;
    use ingrid_core::parallel_search::{SearchEvent, SearchEventKind, SearchEventResult};
    use std::fs;
    use std::time::Duration;

    #[test]
    fn cli_search_timeout_defaults_to_one_minute() {
        let args = Args::try_parse_from(["ingrid_core", "grid.txt"]).unwrap();
        assert_eq!(args.timeout, 60);
        assert!(args.search_log.is_none());
    }

    #[test]
    fn cli_accepts_diacritic_insensitive_mode() {
        let args =
            Args::try_parse_from(["ingrid_core", "--ignore-diacritics", "grid.txt"]).unwrap();
        assert!(args.ignore_diacritics);
    }

    #[test]
    fn cli_accepts_search_log_path() {
        let args =
            Args::try_parse_from(["ingrid_core", "--search-log", "telemetry.csv", "grid.txt"])
                .unwrap();
        assert_eq!(args.search_log.as_deref(), Some("telemetry.csv"));
    }

    #[test]
    fn cli_reports_solver_failures_accurately() {
        assert_eq!(
            format!("{:?}", fill_failure_error(FillFailure::HardFailure)),
            "Unfillable grid"
        );
        assert_eq!(
            format!("{:?}", fill_failure_error(FillFailure::Timeout)),
            "No fill found before the search timeout"
        );
        assert_eq!(
            format!("{:?}", fill_failure_error(FillFailure::Abort)),
            "Fill canceled"
        );
    }

    #[test]
    fn search_log_appends_one_header_and_complete_event_fields() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("search.csv");
        let returned = SearchEvent {
            elapsed: Duration::from_millis(1_234),
            kind: SearchEventKind::FinalReturn,
            worker_id: None,
            target: None,
            active_worker_count: 0,
            incumbent_preferred_word_count: Some(7),
            impossible_from: 8,
            fixed_preferred_word_count: 2,
            discovered_preferred_word_count: Some(5),
            states: None,
            backtracks: None,
            retries: None,
            result: Some(SearchEventResult::Success),
        };
        let aborted = SearchEvent {
            elapsed: Duration::from_millis(2_000),
            kind: SearchEventKind::Abort,
            worker_id: Some(9),
            target: Some(4),
            active_worker_count: 2,
            result: Some(SearchEventResult::Abort),
            ..returned
        };

        SearchCsvLog::open(path.to_str().unwrap())
            .and_then(|mut log| log.write_event(returned))
            .unwrap();
        SearchCsvLog::open(path.to_str().unwrap())
            .and_then(|mut log| log.write_event(aborted))
            .unwrap();

        let contents = fs::read_to_string(path).unwrap();
        assert_eq!(
            contents.lines().collect::<Vec<_>>(),
            vec![
                SEARCH_LOG_HEADER.trim_end(),
                "1234,final_return,,,0,7,8,2,5,,,,success",
                "2000,abort,9,4,2,7,8,2,5,,,,abort",
            ]
        );
    }
}
