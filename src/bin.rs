use clap::Parser;
use ingrid_core::backtracking_search::FillFailure;
use ingrid_core::grid_config::{
    generate_grid_config_from_parsed, render_grid, CandidateOrder, ParsedTemplate,
};
use ingrid_core::oracle::{Oracle, OracleOptions, ProbeOptions};
use ingrid_core::parallel_search::{
    distinct_incumbent_fills, find_best_fill, find_best_fill_with_observer, prepare_search,
    SearchEvent, SearchEventKind, SearchEventResult,
};
use ingrid_core::variant_estimate::{
    estimate_variants, InconclusiveReason, SamplingDiagnostics, VariantEstimate,
    VariantEstimateOptions, VariantEstimateOutcome,
};
use ingrid_core::word_list::{
    normalize_word, NormalizationSettings, WordList, WordListSourceConfig,
    WordListSourceConfigProvider,
};
use ingrid_core::MAX_SLOT_LENGTH;
use std::collections::HashSet;
use std::fmt::{Debug, Display, Formatter};
use std::fs;
use std::io::{self, BufRead, Write};
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
    /// Path to the grid file, as ASCII with # representing blocks and . representing empty squares.
    /// Omitted with --serve, which reads templates from stdin instead.
    grid_path: Option<String>,

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

    /// Ignore shared-substring duplicates when both entries are preferred-tier; pairs involving a
    /// standard-only entry and whole-word duplicates are still enforced
    #[arg(long, default_value_t = false)]
    dupe_exempt_preferred: bool,

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

    /// Emit up to N distinct certified fills at the best Preferred count found, incumbent first;
    /// separated by a blank line on stdout, or one file per grid under --grids-dir
    #[arg(long, default_value_t = 1, value_parser = clap::value_parser!(u64).range(1..=1000))]
    grids: u64,

    /// Write the emitted grids as one file per grid under PATH (grid-1.txt, ...) instead of stdout
    #[arg(long, value_name = "PATH")]
    grids_dir: Option<String>,

    /// Estimate how many distinct fills are at least as Preferred-heavy as the returned fill
    #[arg(long, default_value_t = false)]
    estimate_variants: bool,

    /// Maximum estimator/search runtime ratio; values above 1.0 are capped
    #[arg(long, default_value_t = 0.45)]
    estimate_runtime_ratio: f32,

    /// Absolute estimator time cap in seconds
    #[arg(long)]
    estimate_max_time: Option<u64>,

    /// Random seed for search workers and variant-estimation walks
    #[arg(long, default_value_t = 0)]
    seed: u64,

    /// Maximum variant-estimation walks (1-100000); measured throughput sizes each wave of the cohort
    #[arg(long, default_value_t = 16, value_parser = clap::value_parser!(u64).range(1..=100_000))]
    estimate_walks: u64,

    /// Probability of following the incumbent value at each sampled decision; must be below 1
    #[arg(long, default_value_t = 0.98)]
    estimate_guide_probability: f64,

    /// Print timing information along with the grid
    #[arg(short, long, default_value_t = false)]
    time: bool,

    /// Load the word lists once and then answer fillability questions from stdin forever, one
    /// template per line with rows joined by `/`; see `oracle.md` for the protocol
    #[arg(long, default_value_t = false)]
    serve: bool,

    /// Default per-probe search budget in milliseconds after arc consistency (--serve only); 0
    /// answers from arc consistency alone, which can prove `unfillable` but never `fillable`
    #[arg(long, default_value_t = 0)]
    probe_time: u64,

    /// Longest slot the oracle will be asked about (--serve only); shorter values load fewer words
    #[arg(long)]
    max_length: Option<usize>,
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
            eprintln!("nominal 95% spread: {lower:.1}-{upper:.1} bits");
            print_sampling(sampling);
            if sampling.effective_sample_size < 2.0 {
                eprintln!(
                    "estimate note: weights are dominated by one effective sample; rely on the \
                     certified lower bound and nominal spread"
                );
            }
        }
        VariantEstimateOutcome::Inconclusive { reason, sampling } => {
            let reason = match reason {
                InconclusiveReason::InvalidOptions => "invalid options",
                InconclusiveReason::InvalidIncumbent => "invalid incumbent",
                InconclusiveReason::InsufficientBudget => "insufficient budget",
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

/// Build the campaign's word list: preferred tier first so it wins ties, then the standard tier,
/// then the blocklist. Returns the list and the number of words the blocklist hid.
fn build_word_list(
    args: &Args,
    normalization: &Option<NormalizationSettings>,
    max_length: usize,
) -> Result<(WordList, Option<usize>), Error> {
    let mut source_configs = Vec::with_capacity(2);
    if let Some(preferred_wordlist_path) = args.preferred_wordlist.as_deref() {
        source_configs.push(WordListSourceConfig {
            id: "preferred".into(),
            enabled: true,
            provider: WordListSourceConfigProvider::File {
                path: preferred_wordlist_path.into(),
            },
            normalization: *normalization,
        });
    }
    source_configs.push(match args.wordlist.as_deref() {
        Some(wordlist_path) => WordListSourceConfig {
            id: "standard".into(),
            enabled: true,
            provider: WordListSourceConfigProvider::File {
                path: wordlist_path.into(),
            },
            normalization: *normalization,
        },
        None => WordListSourceConfig {
            id: "standard".into(),
            enabled: true,
            provider: WordListSourceConfigProvider::FileContents { contents: STWL_RAW },
            normalization: *normalization,
        },
    });

    let mut word_list = WordList::new(
        source_configs,
        None,
        Some(max_length),
        args.max_shared_substring,
    );
    word_list.exempt_preferred_dupes = args.dupe_exempt_preferred;
    if args.preferred_wordlist.is_some() {
        word_list.set_preferred_source_ids(HashSet::from(["preferred".into()]));
    }

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
                    (!word.is_empty()).then(|| normalize_word(word, normalization))
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

    Ok((word_list, blocked_word_count))
}

fn main() -> Result<(), Error> {
    let args = Args::parse();
    if !args.estimate_runtime_ratio.is_finite() || args.estimate_runtime_ratio < 0.0 {
        return Err(Error(
            "--estimate-runtime-ratio must be a finite nonnegative number".into(),
        ));
    }
    if !(0.0..1.0).contains(&args.estimate_guide_probability) {
        return Err(Error(
            "--estimate-guide-probability must be in [0, 1); 1.0 would never leave the incumbent path"
                .into(),
        ));
    }
    if !args
        .max_shared_substring
        .is_none_or(|mss| (3..=10).contains(&mss))
    {
        return Err(Error(
            "If given, max shared substring must be between 3 and 10".into(),
        ));
    }
    let normalization = args.ignore_diacritics.then_some(NormalizationSettings {
        strip_punctuation: false,
        convert_diacritics: true,
    });

    if args.serve {
        serve(&args, normalization)
    } else {
        fill_once(&args, normalization)
    }
}

/// Write one file per emitted grid: grid-1.txt .. grid-<K>.txt, zero-padded so lexical order
/// matches emission order (incumbent first).
fn write_grid_files(dir: &str, renders: &[String]) -> Result<(), Error> {
    fs::create_dir_all(dir)
        .map_err(|error| Error(format!("Couldn't create grids directory '{dir}': {error}")))?;
    let width = renders.len().to_string().len();
    for (index, render) in renders.iter().enumerate() {
        let path = std::path::Path::new(dir).join(format!("grid-{:0width$}.txt", index + 1));
        fs::write(&path, format!("{render}\n")).map_err(|error| {
            Error(format!(
                "Couldn't write grid to '{}': {error}",
                path.display()
            ))
        })?;
    }
    eprintln!("Wrote {} grids to {dir}", renders.len());
    Ok(())
}

fn fill_once(args: &Args, normalization: Option<NormalizationSettings>) -> Result<(), Error> {
    let Some(grid_path) = args.grid_path.as_deref() else {
        return Err(Error(
            "A grid file is required unless --serve is given".into(),
        ));
    };
    if args.max_length.is_some() {
        return Err(Error("--max-length only applies to --serve".into()));
    }
    let raw_grid_content = fs::read_to_string(grid_path)
        .map_err(|_| Error(format!("Couldn't read file '{grid_path}'")))?;

    // Parse the grid syntax first, then fold only its fixed letters. Running the dictionary
    // normalizer over whole rows would put `#` and `.` through a sanitiser built for word content.
    let mut template = ParsedTemplate::parse(&raw_grid_content)
        .map_err(|error| Error(format!("Invalid grid: {error}")))?;
    if args.ignore_diacritics {
        template
            .fold_diacritics()
            .map_err(|error| Error(format!("Invalid grid: {error}")))?;
    }
    let max_side = template.width.max(template.height);

    let start = Instant::now();
    let (mut word_list, blocked_word_count) = build_word_list(args, &normalization, max_side)?;
    let word_list_time = start.elapsed();

    let grid_config = generate_grid_config_from_parsed(
        &mut word_list,
        &template,
        args.min_score,
        CandidateOrder::Ranked,
    );

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
        let result = find_best_fill_with_observer(
            &config_ref,
            &prepared,
            remaining_timeout,
            worker_count,
            args.seed,
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
        find_best_fill(
            &config_ref,
            &prepared,
            remaining_timeout,
            worker_count,
            args.seed,
        )
    }
    .map_err(fill_failure_error)?;

    let search_elapsed = search_start.elapsed();
    let fill_time = start.elapsed() - word_list_time;

    let fills = distinct_incumbent_fills(&config_ref, &result, args.grids as usize);
    let renders: Vec<String> = fills
        .iter()
        .map(|choices| render_grid(&config_ref, choices).replace('.', "#"))
        .collect();
    if renders.len() < args.grids as usize {
        eprintln!(
            "Requested {} grids; only {} distinct fill(s) were certified at the optimum ({} preferred words)",
            args.grids,
            renders.len(),
            result.preferred_word_count,
        );
    }
    if let Some(grids_dir) = args.grids_dir.as_deref() {
        write_grid_files(grids_dir, &renders)?;
    } else {
        println!("{}", renders.join("\n\n"));
    }

    if args.estimate_variants {
        let estimate_options = VariantEstimateOptions {
            runtime_ratio: args.estimate_runtime_ratio,
            worker_count,
            walk_count: args.estimate_walks as usize,
            rng_seed: args.seed,
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

/// One parsed request line: a template plus its per-probe overrides.
#[derive(Debug)]
struct Request {
    template: String,
    options: ProbeOptions,
}

/// Parse a request line: the template first, with rows joined by `/`, then `key=value` overrides.
fn parse_request(line: &str) -> Result<Request, String> {
    let mut tokens = line.split_whitespace();
    let template = tokens
        .next()
        .ok_or_else(|| "empty request".to_string())?
        .replace('/', "\n");
    let mut options = ProbeOptions::default();
    for token in tokens {
        let (key, value) = token
            .split_once('=')
            .ok_or_else(|| format!("option '{token}' is not key=value"))?;
        match key {
            "ms" => {
                let ms = value
                    .parse::<u64>()
                    .map_err(|_| format!("ms must be a nonnegative integer, got '{value}'"))?;
                options.probe_time = Some(Duration::from_millis(ms));
            }
            "fill" => {
                options.want_fill = match value {
                    "0" => false,
                    "1" => true,
                    other => return Err(format!("fill must be 0 or 1, got '{other}'")),
                };
            }
            other => return Err(format!("unknown option '{other}'")),
        }
    }
    Ok(Request { template, options })
}

/// Load the word lists once, then answer one probe per stdin line until stdin closes or the client
/// says `quit`. Every response is exactly one line, so a client can read them back lockstep.
fn serve(args: &Args, normalization: Option<NormalizationSettings>) -> Result<(), Error> {
    if args.grid_path.is_some() {
        return Err(Error(
            "--serve reads templates from stdin; don't also pass a grid file".into(),
        ));
    }
    if args.search_log.is_some() {
        return Err(Error("--search-log doesn't apply to --serve".into()));
    }
    if args.estimate_variants {
        return Err(Error("--estimate-variants doesn't apply to --serve".into()));
    }
    if args.grids != 1 {
        return Err(Error("--grids doesn't apply to --serve".into()));
    }
    if args.grids_dir.is_some() {
        return Err(Error("--grids-dir doesn't apply to --serve".into()));
    }
    let max_length = args.max_length.unwrap_or(MAX_SLOT_LENGTH);
    if !(2..=MAX_SLOT_LENGTH).contains(&max_length) {
        return Err(Error(format!(
            "--max-length must be between 2 and {MAX_SLOT_LENGTH}"
        )));
    }

    let start = Instant::now();
    let (word_list, blocked_word_count) = build_word_list(args, &normalization, max_length)?;
    let mut oracle = Oracle::new(
        word_list,
        OracleOptions {
            min_score: args.min_score,
            default_probe_time: Duration::from_millis(args.probe_time),
            seed: args.seed,
        },
    )
    .map_err(|conflict| Error(conflict.to_string()))?;
    let load_time = start.elapsed();

    let stdout = io::stdout();
    let mut out = stdout.lock();
    writeln!(
        out,
        "ready words={} max_length={} min_score={} probe_ms={} blocked={} diacritics={} load_ms={}",
        oracle.visible_word_count(),
        oracle.max_slot_length(),
        args.min_score,
        args.probe_time,
        blocked_word_count.unwrap_or(0),
        u8::from(oracle.converts_diacritics()),
        load_time.as_millis(),
    )
    .map_err(serve_io_error)?;
    out.flush().map_err(serve_io_error)?;

    for line in io::stdin().lock().lines() {
        let line = line.map_err(serve_io_error)?;
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if line == "quit" {
            break;
        }
        match parse_request(line) {
            Ok(request) => match oracle.probe_with(&request.template, &request.options) {
                Ok(probe) => {
                    write!(
                        out,
                        "{} slots={} min_domain={} setup_us={} ac_us={} us={}",
                        probe.verdict,
                        probe.slot_count,
                        probe.min_domain,
                        probe.setup_time.as_micros(),
                        probe.arc_consistency_time.as_micros(),
                        probe.elapsed.as_micros(),
                    )
                    .map_err(serve_io_error)?;
                    if let Some(fill) = probe.fill {
                        write!(out, " fill={}", fill.replace('\n', "/")).map_err(serve_io_error)?;
                    }
                    writeln!(out).map_err(serve_io_error)?;
                }
                Err(error) => writeln!(out, "error {error}").map_err(serve_io_error)?,
            },
            Err(message) => writeln!(out, "error {message}").map_err(serve_io_error)?,
        }
        out.flush().map_err(serve_io_error)?;
    }

    Ok(())
}

fn serve_io_error(error: io::Error) -> Error {
    Error(format!("Oracle I/O failed: {error}"))
}

#[cfg(test)]
mod tests {
    use super::{fill_failure_error, parse_request, Args, SearchCsvLog, SEARCH_LOG_HEADER};
    use clap::Parser;
    use ingrid_core::backtracking_search::FillFailure;
    use ingrid_core::parallel_search::{SearchEvent, SearchEventKind, SearchEventResult};
    use std::fs;
    use std::time::Duration;

    /// One smoke over argv wiring: the default invocation, the documented flags, and
    /// `--serve`, which takes no grid file.
    #[test]
    fn cli_parses_the_documented_invocations() {
        let plain = Args::try_parse_from(["ingrid_core", "grid.txt"]).unwrap();
        assert_eq!(plain.timeout, 60);
        assert!(plain.search_log.is_none());

        let ignore_diacritics =
            Args::try_parse_from(["ingrid_core", "--ignore-diacritics", "grid.txt"]).unwrap();
        assert!(ignore_diacritics.ignore_diacritics);

        let logged =
            Args::try_parse_from(["ingrid_core", "--search-log", "telemetry.csv", "grid.txt"])
                .unwrap();
        assert_eq!(logged.search_log.as_deref(), Some("telemetry.csv"));

        let serving = Args::try_parse_from(["ingrid_core", "--serve"]).unwrap();
        assert!(serving.serve);
        assert!(serving.grid_path.is_none());
        assert_eq!(serving.probe_time, 0);
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

    #[test]
    fn a_request_line_joins_rows_and_carries_its_options() {
        let request = parse_request("..#/#..").unwrap();
        assert_eq!(request.template, "..#\n#..");
        assert!(request.options.probe_time.is_none());
        assert!(!request.options.want_fill);

        let rich = parse_request("../.. ms=250 fill=1").unwrap();
        assert_eq!(rich.options.probe_time, Some(Duration::from_millis(250)));
        assert!(rich.options.want_fill);
        // Zero is meaningful: it forces arc consistency only despite a nonzero campaign default.
        assert_eq!(
            parse_request("../.. ms=0").unwrap().options.probe_time,
            Some(Duration::ZERO)
        );
    }

    #[test]
    fn a_malformed_request_line_names_the_problem() {
        assert_eq!(
            parse_request("../.. ms").unwrap_err(),
            "option 'ms' is not key=value"
        );
        assert_eq!(
            parse_request("../.. ms=soon").unwrap_err(),
            "ms must be a nonnegative integer, got 'soon'"
        );
        assert_eq!(
            parse_request("../.. fill=maybe").unwrap_err(),
            "fill must be 0 or 1, got 'maybe'"
        );
        assert_eq!(
            parse_request("../.. depth=3").unwrap_err(),
            "unknown option 'depth'"
        );
        assert_eq!(parse_request("   ").unwrap_err(), "empty request");
    }
}
