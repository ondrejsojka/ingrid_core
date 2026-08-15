//! Shared fixtures for the crate's own tests: the memory-source word-list builders,
//! corpus fingerprints and word-id lookups every module's tests would otherwise copy.
//! Compiled only under `cfg(test)`; never part of the library surface.

use std::collections::HashSet;
use std::path::{self, PathBuf};

use crate::types::WordId;
use crate::word_list::{WordList, WordListSourceConfig, WordListSourceConfigProvider};

/// Path of the bundled dictionary used by the disk-backed fixtures, derived from this
/// file's location so it survives any working directory.
#[must_use]
pub fn dictionary_path() -> PathBuf {
    let mut path = path::PathBuf::from(file!());
    path.pop();
    path.pop();
    path.push("resources");
    path.push("spreadthewordlist.dict");
    path
}

/// One source reading the bundled dictionary, the disk-backed fixture's `WordList::new`
/// input.
#[must_use]
pub fn word_list_source_config() -> Vec<WordListSourceConfig> {
    vec![WordListSourceConfig {
        id: "0".into(),
        enabled: true,
        provider: WordListSourceConfigProvider::File {
            path: dictionary_path().into(),
        },
        normalization: None,
    }]
}

/// One in-memory source of `word;score` pairs under a stable id.
#[must_use]
pub fn memory_source(id: &str, words: &[(&str, u16)]) -> WordListSourceConfig {
    WordListSourceConfig {
        id: id.into(),
        enabled: true,
        provider: WordListSourceConfigProvider::Memory {
            words: words
                .iter()
                .map(|&(word, score)| (word.to_string(), score))
                .collect(),
        },
        normalization: None,
    }
}

/// [`memory_source`] with every word scored 50: fixtures rarely care about scores.
#[must_use]
pub fn uniform_source(id: &str, words: &[&str]) -> WordListSourceConfig {
    memory_source(
        id,
        &words.iter().map(|&word| (word, 50)).collect::<Vec<_>>(),
    )
}

/// A single `standard` memory source with slot length capped at 5: the shape the oracle
/// fixtures and the rewind tests build on.
#[must_use]
pub fn memory_word_list(words: &[(&str, u16)], max_shared_substring: Option<usize>) -> WordList {
    WordList::new(
        vec![memory_source("standard", words)],
        None,
        Some(5),
        max_shared_substring,
    )
}

/// Preferred and standard sources at score 50 with the preferred tier registered; the
/// maximum slot length follows the longest fixture word.
#[must_use]
pub fn tiered_word_list(preferred: &[&str], standard: &[&str]) -> WordList {
    tiered_word_list_with_limit(preferred, standard, None)
}

/// [`tiered_word_list`] with an explicit `max_shared_substring`.
#[must_use]
pub fn tiered_word_list_with_limit(
    preferred: &[&str],
    standard: &[&str],
    max_shared_substring: Option<usize>,
) -> WordList {
    let max_length = preferred
        .iter()
        .chain(standard)
        .map(|word| word.chars().count())
        .max();
    let mut list = WordList::new(
        vec![
            uniform_source("preferred", preferred),
            uniform_source("standard", standard),
        ],
        None,
        max_length,
        max_shared_substring,
    );
    list.set_preferred_source_ids(HashSet::from(["preferred".into()]));
    list
}

/// Everything a probe could leave behind in the corpus: bucket sizes, the string index,
/// the glyph table and both dupe-index counters.
#[must_use]
pub fn fingerprint(word_list: &WordList) -> (Vec<usize>, usize, usize, usize, usize) {
    (
        word_list.words.iter().map(Vec::len).collect(),
        word_list.word_id_by_string.len(),
        word_list.glyphs.len(),
        word_list.dupe_index.group_count(),
        word_list.dupe_index.indexed_word_count(),
    )
}

/// Id of a fixture word; the lookup's success is the assertion that it exists.
#[must_use]
pub fn word_id(word_list: &WordList, word: &str) -> WordId {
    *word_list
        .word_id_by_string
        .get(word)
        .expect("fixture word should exist")
}
