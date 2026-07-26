use clap::Parser;
use ingrid_core::backtracking_search::FillFailure;
use ingrid_core::grid_config::{generate_grid_config_from_template_string, render_grid};
use ingrid_core::parallel_search::find_best_fill;
use ingrid_core::word_list::{
    normalize_word, NormalizationSettings, WordList, WordListSourceConfig,
    WordListSourceConfigProvider,
};
use std::collections::HashSet;
use std::fmt::{Debug, Formatter};
use std::fs;
use std::num::NonZeroUsize;
use std::time::{Duration, Instant};

const STWL_RAW: &str = include_str!("../resources/spreadthewordlist.dict");

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

fn main() -> Result<(), Error> {
    let args = Args::parse();

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

    let grid_config =
        generate_grid_config_from_template_string(word_list, &raw_grid_content, args.min_score);

    let timeout = (args.timeout != 0).then(|| Duration::from_secs(args.timeout));
    let result = find_best_fill(
        &grid_config.to_config_ref(),
        timeout,
        args.cores.map(NonZeroUsize::get),
    )
    .map_err(fill_failure_error)?;

    let fill_time = start.elapsed() - word_list_time;

    println!(
        "{}",
        render_grid(&grid_config.to_config_ref(), &result.fill.choices).replace('.', "#")
    );

    if args.time {
        eprintln!(
            "{word_list_time:?} loading word lists, {fill_time:?} finding fill, {} preferred words",
            result.preferred_word_count
        );
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{fill_failure_error, Args};
    use clap::Parser;
    use ingrid_core::backtracking_search::FillFailure;

    #[test]
    fn cli_search_timeout_defaults_to_one_minute() {
        let args = Args::try_parse_from(["ingrid_core", "grid.txt"]).unwrap();
        assert_eq!(args.timeout, 60);
    }

    #[test]
    fn cli_accepts_diacritic_insensitive_mode() {
        let args =
            Args::try_parse_from(["ingrid_core", "--ignore-diacritics", "grid.txt"]).unwrap();
        assert!(args.ignore_diacritics);
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
}
