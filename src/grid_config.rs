//! This module implements code for configuring a crossword-filling operation, independent of the
//! specific fill algorithm.

use fancy_regex::Regex;
use std::collections::{HashMap, HashSet};
use std::fmt::Debug;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, OnceLock};

#[cfg(feature = "serde")]
use serde_derive::{Deserialize, Serialize};

#[cfg(feature = "serde")]
use serde::{Deserialize, Deserializer, Serialize, Serializer};

use crate::types::{GlyphId, WordId};
use crate::util::build_glyph_counts_by_cell;
use crate::word_list::{normalize_grid_letter, WordList};

/// An identifier for the intersection between two slots; these correspond one-to-one with checked
/// squares in the grid and are used to track weights (i.e., how often each square is involved in
/// a domain wipeout).
pub type CrossingId = usize;

/// An identifier for a given slot, based on its index in the `GridConfig`'s `slot_configs` field.
pub type SlotId = usize;

/// Zero-indexed x and y coords for a cell in the grid, where y = 0 in the top row.
pub type GridCoord = (usize, usize);

/// The direction that a slot is facing.
#[derive(Debug, Clone, Copy, Eq, PartialEq, Hash, PartialOrd, Ord)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "lowercase"))]
#[allow(dead_code)]
pub enum Direction {
    Across,
    Down,
}

/// A struct representing a crossing between one slot and another, referencing the other slot's id
/// and the location of the intersection within the other slot.
#[derive(Debug, Clone)]
pub struct Crossing {
    pub other_slot_id: SlotId,
    pub other_slot_cell: usize,
    pub crossing_id: CrossingId,
}

/// A struct representing the aspects of a slot in the grid that are static during filling.
#[derive(Debug, Clone)]
pub struct SlotConfig {
    pub id: SlotId,
    pub start_cell: GridCoord,
    pub direction: Direction,
    pub length: usize,
    pub crossings: Vec<Option<Crossing>>,
    pub min_score_override: Option<u16>,
    pub filter_pattern: Option<Regex>,
}

impl SlotConfig {
    /// Generate the coords for each cell of this slot.
    #[must_use]
    pub fn cell_coords(&self) -> Vec<GridCoord> {
        (0..self.length)
            .map(|cell_idx| match self.direction {
                Direction::Across => (self.start_cell.0 + cell_idx, self.start_cell.1),
                Direction::Down => (self.start_cell.0, self.start_cell.1 + cell_idx),
            })
            .collect()
    }

    /// Generate the indices of this slot's cells in a flat fill array like `GridConfig.fill`.
    #[must_use]
    pub fn cell_fill_indices(&self, grid_width: usize) -> Vec<usize> {
        self.cell_coords()
            .iter()
            .map(|loc| loc.0 + loc.1 * grid_width)
            .collect()
    }

    /// Get the values of this slot's cells in a flat fill array like `GridConfig.fill`.
    #[must_use]
    pub fn fill(&self, fill: &[Option<GlyphId>], grid_width: usize) -> Vec<Option<GlyphId>> {
        self.cell_fill_indices(grid_width)
            .iter()
            .map(|&idx| fill[idx])
            .collect()
    }

    /// Get this slot's `fill` if and only if all of its cells are populated.
    #[must_use]
    pub fn complete_fill(
        &self,
        fill: &[Option<GlyphId>],
        grid_width: usize,
    ) -> Option<Vec<GlyphId>> {
        self.fill(fill, grid_width).into_iter().collect()
    }

    /// Generate a `SlotSpec` identifying this slot.
    #[must_use]
    pub fn slot_spec(&self) -> SlotSpec {
        SlotSpec {
            start_cell: self.start_cell,
            direction: self.direction,
            length: self.length,
        }
    }

    /// Generate a string key identifying this slot.
    #[must_use]
    pub fn slot_key(&self) -> String {
        self.slot_spec().to_key()
    }
}

/// A struct holding references to all of the information needed as input to a crossword filling
/// operation.
#[allow(dead_code)]
#[derive(Clone)]
pub struct GridConfig<'a> {
    /// The word list used to fill the grid; see `word_list.rs`.
    pub word_list: &'a WordList,

    /// A flat array of letters filled into the grid, in order of row and then column. `None` can
    /// represent a block or an unfilled cell.
    pub fill: &'a [Option<GlyphId>],

    /// Config representing all of the slots in the grid and their crossings.
    pub slot_configs: &'a [SlotConfig],

    /// An array of available words for each (respective) slot, based on both the word list config
    /// and the existing letters filled into the grid.
    pub slot_options: &'a [Vec<WordId>],

    /// Lazily built index from (slot, cell, glyph) to the slot's options carrying that glyph in
    /// that cell, used by arc consistency to touch only the options affected by a glyph losing
    /// support. Shared with the owning `OwnedGridConfig`, so the first arc-consistency pass for a
    /// grid builds it once and every subsequent pass (on any thread) reuses it.
    pub support_index: &'a OnceLock<SupportIndex>,

    /// The width and height of the grid.
    pub width: usize,
    pub height: usize,

    /// The number of distinct crossings represented in all of the `slot_configs`.
    pub crossing_count: usize,

    /// An optional atomic flag that can be set to signal that the fill operation should be canceled.
    pub abort: Option<&'a AtomicBool>,
}

/// A struct that owns every piece of per-template setup a `GridConfig` needs, and borrows the
/// corpus those `WordId`s index into.
///
/// The corpus is campaign state that outlives any single grid, so it is not owned here — a
/// long-lived caller configures thousands of grids against one word list. It is *borrowed* rather
/// than passed in later so that the borrow checker enforces what a comment cannot: the word list
/// cannot be mutated, rewound, or swapped for a different one while a config that indexes into it
/// is still alive.
pub struct OwnedGridConfig<'a> {
    pub word_list: &'a WordList,
    pub fill: Vec<Option<GlyphId>>,
    pub slot_configs: Vec<SlotConfig>,
    pub slot_options: Vec<Vec<WordId>>,
    pub width: usize,
    pub height: usize,
    pub crossing_count: usize,
    pub abort: Option<Arc<AtomicBool>>,
    /// Lazily built arc-consistency support index; see `GridConfig::support_index`.
    pub support_index: OnceLock<SupportIndex>,
}

impl OwnedGridConfig<'_> {
    #[allow(dead_code)]
    #[must_use]
    pub fn to_config_ref(&self) -> GridConfig<'_> {
        GridConfig {
            word_list: self.word_list,
            fill: &self.fill,
            slot_configs: &self.slot_configs,
            slot_options: &self.slot_options,
            support_index: &self.support_index,
            width: self.width,
            height: self.height,
            crossing_count: self.crossing_count,
            abort: self.abort.as_deref(),
        }
    }
}

/// Given a configured grid, reorder the options for each slot so that the "best" choices are at the
/// front. This is a balance between fillability (the most important factor, since our odds of being
/// able to find a fill in a reasonable amount of time depend on how many tries it takes us to find
/// a usable word for each slot) and quality metrics like word score and letter score.
#[allow(clippy::cast_lossless)]
pub fn sort_slot_options(
    word_list: &WordList,
    slot_configs: &[SlotConfig],
    slot_options: &mut [Vec<WordId>],
) {
    // To calculate the fillability score for each word, we need statistics about which letters are
    // most likely to appear in each position for each slot.
    let glyph_counts_by_cell_by_slot: Vec<_> = slot_configs
        .iter()
        .map(|slot_config| {
            build_glyph_counts_by_cell(word_list, slot_config.length, &slot_options[slot_config.id])
        })
        .collect();

    // Now we can actually sort the options.
    for slot_idx in 0..slot_configs.len() {
        let slot_config = &slot_configs[slot_idx];
        let slot_options = &mut slot_options[slot_idx];

        slot_options.sort_by_cached_key(|&option| {
            let word = &word_list.words[slot_config.length][option];

            // To calculate the fill score for a word, average the logarithms of the number of
            // crossing options that are compatible with each letter (based on the grid geometry).
            // This is kind of arbitrary, but it seems like it makes sense because we care a lot
            // more about the difference between 1 option and 5 options or 5 options and 20 options
            // than 100 options and 500 options.
            let fill_score = slot_config
                .crossings
                .iter()
                .zip(&word.glyphs)
                .map(|(crossing, &glyph)| match crossing {
                    Some(crossing) => {
                        let crossing_counts_by_cell =
                            &glyph_counts_by_cell_by_slot[crossing.other_slot_id];

                        (crossing_counts_by_cell[crossing.other_slot_cell][glyph] as f32).log10()
                    }
                    None => 0.0,
                })
                .fold(0.0, |a, b| a + b)
                / (slot_config.length as f32);

            // This is arbitrary, based on visual inspection of the ranges for each value. Generally
            // increasing the weight of `fill_score` relative to the other two will reduce fill
            // time.
            (
                word_list.word_tier((slot_config.length, option))
                    == crate::word_list::WordTier::Standard,
                -((fill_score * 900.0) as i64
                    + ((word.letter_score as f32) * 5.0) as i64
                    + ((word.score as f32) * 5.0) as i64),
            )
        });
    }
}

/// A static index from each (slot, cell, glyph) to the slot's options that carry that glyph in
/// that cell. Arc consistency uses this to revise a crossing in time proportional to the options
/// actually affected by a glyph losing support, rather than rescanning the crossing slot's whole
/// option list for every queued cell.
pub struct SupportIndex {
    /// `by_slot[slot_id][cell_idx]` is populated iff the cell participates in a crossing; cells
    /// without crossings are never queued for propagation and need no index.
    by_slot: Vec<Vec<Option<CellSupport>>>,
}

/// The option buckets for one (slot, cell) pair, laid out as contiguous ranges of `words`.
pub struct CellSupport {
    /// Bucket boundaries by `GlyphId`: bucket `g` spans `words[offsets[g]..offsets[g + 1]]`.
    offsets: Vec<u32>,
    /// All of the slot's options, grouped by the glyph they carry in the indexed cell.
    words: Vec<WordId>,
}

impl SupportIndex {
    /// Access the support buckets for a cell that participates in a crossing.
    pub(crate) fn cell_support(&self, slot_id: SlotId, cell_idx: usize) -> &CellSupport {
        self.by_slot[slot_id][cell_idx]
            .as_ref()
            .expect("support index covers every cell that has a crossing")
    }
}

impl CellSupport {
    /// The options carrying `glyph_id` in the indexed cell.
    pub(crate) fn words_for_glyph(&self, glyph_id: GlyphId) -> &[WordId] {
        &self.words[self.offsets[glyph_id] as usize..self.offsets[glyph_id + 1] as usize]
    }
}

/// Build the [`SupportIndex`] for a grid config: a counting sort of each slot's options by the
/// glyph they carry in each crossing cell.
#[must_use]
pub fn build_support_index(config: &GridConfig<'_>) -> SupportIndex {
    let glyph_count = config.word_list.glyphs.len();
    let by_slot = config
        .slot_configs
        .iter()
        .map(|slot_config| {
            let slot_options = &config.slot_options[slot_config.id];
            slot_config
                .crossings
                .iter()
                .enumerate()
                .map(|(cell_idx, crossing)| {
                    crossing.as_ref().map(|_| {
                        let words_by_length = &config.word_list.words[slot_config.length];

                        let mut offsets = vec![0u32; glyph_count + 1];
                        for &word_id in slot_options {
                            offsets[words_by_length[word_id].glyphs[cell_idx] + 1] += 1;
                        }
                        for idx in 1..=glyph_count {
                            offsets[idx] += offsets[idx - 1];
                        }

                        let mut words = vec![0; slot_options.len()];
                        let mut write_positions = offsets.clone();
                        for &word_id in slot_options {
                            let glyph = words_by_length[word_id].glyphs[cell_idx];
                            words[write_positions[glyph] as usize] = word_id;
                            write_positions[glyph] += 1;
                        }

                        CellSupport { offsets, words }
                    })
                })
                .collect()
        })
        .collect();
    SupportIndex { by_slot }
}

/// A struct identifying a specific slot in the grid.
#[derive(Debug, PartialEq, Eq, Hash, Clone)]
pub struct SlotSpec {
    pub start_cell: GridCoord,
    pub direction: Direction,
    pub length: usize,
}

impl SlotSpec {
    /// Parse a string like "1,2,down,5" into a `SlotSpec` struct.
    pub fn from_key(key: &str) -> Result<SlotSpec, String> {
        let key_parts: Vec<&str> = key.split(',').collect();
        if key_parts.len() != 4 {
            return Err(format!("invalid slot key: {key}"));
        }

        let x: Result<usize, _> = key_parts[0].parse();
        let y: Result<usize, _> = key_parts[1].parse();
        let direction: Option<Direction> = match key_parts[2] {
            "across" => Some(Direction::Across),
            "down" => Some(Direction::Down),
            _ => None,
        };
        let length: Result<usize, _> = key_parts[3].parse();

        if let (Ok(x), Ok(y), Some(direction), Ok(length)) = (x, y, direction, length) {
            Ok(SlotSpec {
                start_cell: (x, y),
                direction,
                length,
            })
        } else {
            Err(format!("invalid slot key: {key:?}"))
        }
    }

    /// Represent this slot as a string like "1,2,down,5".
    #[must_use]
    pub fn to_key(&self) -> String {
        let direction = match self.direction {
            Direction::Across => "across",
            Direction::Down => "down",
        };
        format!(
            "{},{},{},{}",
            self.start_cell.0, self.start_cell.1, direction, self.length,
        )
    }

    /// Does this spec match the given slot config?
    #[must_use]
    pub fn matches_slot(&self, slot: &SlotConfig) -> bool {
        self.start_cell == slot.start_cell
            && self.direction == slot.direction
            && self.length == slot.length
    }

    /// Generate the coords for each cell of this entry.
    #[must_use]
    pub fn cell_coords(&self) -> Vec<GridCoord> {
        (0..self.length)
            .map(|cell_idx| match self.direction {
                Direction::Across => (self.start_cell.0 + cell_idx, self.start_cell.1),
                Direction::Down => (self.start_cell.0, self.start_cell.1 + cell_idx),
            })
            .collect()
    }
}

/// Serialize a `SlotSpec` into a string key.
#[cfg(feature = "serde")]
impl Serialize for SlotSpec {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(&self.to_key())
    }
}

/// Deserialize a `SlotSpec` from a string key.
#[cfg(feature = "serde")]
impl<'de> Deserialize<'de> for SlotSpec {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let raw_string = String::deserialize(deserializer)?;
        SlotSpec::from_key(&raw_string).map_err(serde::de::Error::custom)
    }
}

/// Given `GridEntry` structs specifying the positions of the slots in a grid, generate
/// `SlotConfig`s containing derived information about crossings, etc.
#[must_use]
pub fn generate_slot_configs(entries: &[SlotSpec]) -> (Vec<SlotConfig>, usize) {
    #[derive(Debug)]
    struct GridCell {
        entries: Vec<(usize, usize)>, // (entry index, cell index within entry)
        number: Option<u32>,
    }

    let mut slot_configs: Vec<SlotConfig> = vec![];

    // Build a map from cell location to entries involved, which we can then use to calculate
    // crossings.
    let mut cell_by_loc: HashMap<GridCoord, GridCell> = HashMap::new();

    for (entry_idx, entry) in entries.iter().enumerate() {
        for (cell_idx, &loc) in entry.cell_coords().iter().enumerate() {
            let grid_cell = cell_by_loc.entry(loc).or_insert_with(|| GridCell {
                entries: vec![],
                number: None,
            });
            grid_cell.entries.push((entry_idx, cell_idx));
        }
    }

    let mut ordered_coords: Vec<_> = cell_by_loc.keys().copied().collect();
    ordered_coords.sort_by_key(|&(x, y)| (y, x));
    let mut current_number = 1;
    for coord in ordered_coords {
        if cell_by_loc[&coord]
            .entries
            .iter()
            .any(|&(_, cell_idx)| cell_idx == 0)
        {
            cell_by_loc.get_mut(&coord).unwrap().number = Some(current_number);
            current_number += 1;
        }
    }

    // This is slightly tricky. When we're generating a Crossing, if
    // `(current_slot_id, crossing_slot_id)` is in this list, use its index; if not, use
    // `constraint_id_cache.len()` as the id and push `(crossing_slot_id, current_id)` into the list
    // so we can reuse it when we see the crossing from the other side. This wouldn't work if the
    // grid topology weren't 2D, so that each crossing is guaranteed to be seen by exactly two slots.
    let mut constraint_id_cache: Vec<(SlotId, SlotId)> = vec![];

    // Now we can build the actual slot configs.
    for (entry_idx, entry) in entries.iter().enumerate() {
        let crossings: Vec<Option<Crossing>> = entry
            .cell_coords()
            .iter()
            .map(|&loc| {
                let crossing_idxs: Vec<_> = cell_by_loc[&loc]
                    .entries
                    .iter()
                    .filter(|&&(e, _)| e != entry_idx)
                    .collect();

                if crossing_idxs.is_empty() {
                    None
                } else if crossing_idxs.len() > 1 {
                    panic!("More than two entries crossing in cell?");
                } else {
                    let &(other_slot_id, other_slot_cell) = crossing_idxs[0];

                    let crossing_id = if let Some(found_constraint_id) = constraint_id_cache
                        .iter()
                        .enumerate()
                        .find(|&(_, &id_pair)| id_pair == (entry_idx, other_slot_id))
                        .map(|(crossing_id, _)| crossing_id)
                    {
                        found_constraint_id
                    } else {
                        constraint_id_cache.push((other_slot_id, entry_idx));
                        constraint_id_cache.len() - 1
                    };

                    Some(Crossing {
                        other_slot_id,
                        other_slot_cell,
                        crossing_id,
                    })
                }
            })
            .collect();

        slot_configs.push(SlotConfig {
            id: entry_idx,
            start_cell: entry.start_cell,
            direction: entry.direction,
            length: entry.length,
            crossings,
            min_score_override: None,
            filter_pattern: None,
        });
    }

    (slot_configs, constraint_id_cache.len())
}

/// Given a single slot's fill, minimum score, and optional filter pattern, generate the possible
/// options for that slot by starting with the complete word list and then removing words that
/// contradict the criteria. If `allowed_word_ids` is provided, the given words will be included in
/// the options as long as they don't contradict the fill, regardless of whether they match the min
/// score and filter pattern.
pub fn generate_slot_options(
    word_list: &mut WordList,
    entry_fill: &[Option<GlyphId>],
    min_score: u16,
    filter_pattern: Option<&Regex>,
    allowed_word_ids: Option<&HashSet<WordId>>,
) -> Vec<WordId> {
    let length = entry_fill.len();

    // If the slot is fully specified, we need to either use an existing word or create a new
    // (hidden) one.
    let complete_fill: Option<Vec<GlyphId>> = entry_fill.iter().copied().collect();

    if let Some(complete_fill) = complete_fill {
        let word_string: String = complete_fill
            .iter()
            .map(|&glyph_id| word_list.glyphs[glyph_id])
            .collect();

        let (_word_length, word_id) = word_list.get_word_id_or_add_hidden(&word_string);

        vec![word_id]
    } else {
        let options: Vec<WordId> = (0..word_list.words[length].len())
            .filter(|&word_id| {
                let word = &word_list.words[length][word_id];
                let enforce_criteria = allowed_word_ids.map_or(true, |allowed_word_ids| {
                    !allowed_word_ids.contains(&word_id)
                });

                if enforce_criteria {
                    if word.hidden || word.score < min_score {
                        return false;
                    }

                    if let Some(filter_pattern) = filter_pattern.as_ref() {
                        if !filter_pattern
                            .is_match(&word.normalized_string)
                            .unwrap_or(false)
                        {
                            return false;
                        }
                    }
                }

                entry_fill.iter().enumerate().all(|(cell_idx, cell_fill)| {
                    cell_fill
                        .map(|g| g == word.glyphs[cell_idx])
                        .unwrap_or(true)
                })
            })
            .collect();

        options
    }
}

/// Given an input fill and an array of slot configs, generate the possible options for each slot
/// by starting with the complete word list and then removing words that contradict any fill that's
/// already present in the grid or violate criteria like minimum score or filter pattern.
pub fn generate_all_slot_options(
    word_list: &mut WordList,
    fill: &[Option<GlyphId>],
    slot_configs: &[SlotConfig],
    grid_width: usize,
    global_min_score: u16,
) -> Vec<Vec<WordId>> {
    slot_configs
        .iter()
        .map(|slot| {
            generate_slot_options(
                word_list,
                &slot.fill(fill, grid_width),
                slot.min_score_override.unwrap_or(global_min_score),
                slot.filter_pattern.as_ref(),
                None,
            )
        })
        .collect()
}

/// Whether a generated config's per-slot options should be ranked by [`sort_slot_options`].
///
/// Arc consistency computes the same closure whatever order the options are in, so a caller that
/// only needs propagation — the fillability oracle answering from arc consistency alone — can skip
/// the ranking pass, which is the more expensive half of building a config. Any caller that
/// actually searches wants `Ranked`: the order is how the solver's value heuristic is expressed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CandidateOrder {
    Ranked,
    Unranked,
}

/// A validated crossword template: fixed letters, dimensions, slot topology, and the longest run
/// of non-block cells.
///
/// This is the only template parser. The one-shot CLI and the persistent oracle both go through it,
/// so validation and slot detection cannot drift apart, and a caller that has a `ParsedTemplate`
/// knows the rows were rectangular and non-empty rather than having been quietly reshaped.
#[derive(Debug, Clone)]
pub struct ParsedTemplate {
    pub width: usize,
    pub height: usize,

    /// Row-major fixed letters, lowercased. `None` for a block or an empty cell, which is exactly
    /// what `GridConfig::fill` means.
    pub fill: Vec<Option<char>>,

    /// Every maximal run of two or more non-block cells, across and then down.
    pub slots: Vec<SlotSpec>,

    /// Length of the longest run of non-block cells in either direction, including runs of one.
    /// A caller with a bounded dictionary compares this against its longest loaded word.
    pub longest_run: usize,
}

/// Why a template string could not be turned into a [`ParsedTemplate`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TemplateError {
    NoRows,
    EmptyRow {
        row: usize,
    },
    RaggedRows {
        row: usize,
        expected: usize,
        found: usize,
    },
    /// A fixed letter folded away to nothing, which no legitimate grid cell does.
    UnfoldableLetter {
        row: usize,
        column: usize,
        letter: char,
    },
}

impl std::fmt::Display for TemplateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TemplateError::NoRows => write!(f, "template has no rows"),
            TemplateError::EmptyRow { row } => write!(f, "row {row} is empty"),
            TemplateError::RaggedRows {
                row,
                expected,
                found,
            } => write!(
                f,
                "rows must all be the same length: row {row} has {found}, expected {expected}"
            ),
            TemplateError::UnfoldableLetter {
                row,
                column,
                letter,
            } => write!(
                f,
                "the letter {letter:?} at row {row}, column {column} is not a fillable character"
            ),
        }
    }
}

impl ParsedTemplate {
    /// Parse a template with `#` for blocks, `.` for empty cells, and letters for fixed fill.
    ///
    /// Surrounding blank lines and per-row surrounding whitespace are ignored; an *interior* blank
    /// row is an error rather than something to skip, because in a framed request (rows joined by
    /// `/`) it is a genuinely malformed grid and skipping it would answer a different question.
    pub fn parse(template: &str) -> Result<ParsedTemplate, TemplateError> {
        let rows: Vec<&str> = template.trim().lines().map(str::trim).collect();
        if rows.is_empty() {
            return Err(TemplateError::NoRows);
        }

        let width = rows[0].chars().count();
        if width == 0 {
            return Err(TemplateError::EmptyRow { row: 0 });
        }
        for (index, row) in rows.iter().enumerate().skip(1) {
            let found = row.chars().count();
            if found == 0 {
                return Err(TemplateError::EmptyRow { row: index });
            }
            if found != width {
                return Err(TemplateError::RaggedRows {
                    row: index,
                    expected: width,
                    found,
                });
            }
        }

        let height = rows.len();
        let mut blocks = Vec::with_capacity(width * height);
        let mut fill = Vec::with_capacity(width * height);
        for row in &rows {
            for cell in row.chars() {
                blocks.push(cell == '#');
                fill.push(match cell {
                    '#' | '.' => None,
                    letter => Some(
                        letter
                            .to_lowercase()
                            .next()
                            .expect("char::to_lowercase always yields at least one char"),
                    ),
                });
            }
        }

        let mut slots = Vec::new();
        let mut longest_run = 0;
        let mut collect = |run: &[GridCoord], longest_run: &mut usize| {
            *longest_run = (*longest_run).max(run.len());
            if run.len() > 1 {
                slots.push(SlotSpec {
                    start_cell: run[0],
                    length: run.len(),
                    direction: if run[0].1 == run[1].1 {
                        Direction::Across
                    } else {
                        Direction::Down
                    },
                });
            }
        };

        let mut run: Vec<GridCoord> = Vec::with_capacity(width.max(height));
        for y in 0..height {
            run.clear();
            for x in 0..width {
                if blocks[y * width + x] {
                    collect(&run, &mut longest_run);
                    run.clear();
                } else {
                    run.push((x, y));
                }
            }
            collect(&run, &mut longest_run);
        }
        for x in 0..width {
            run.clear();
            for y in 0..height {
                if blocks[y * width + x] {
                    collect(&run, &mut longest_run);
                    run.clear();
                } else {
                    run.push((x, y));
                }
            }
            collect(&run, &mut longest_run);
        }

        Ok(ParsedTemplate {
            width,
            height,
            fill,
            slots,
            longest_run,
        })
    }

    /// Fold accented fixed letters into their unaccented forms, matching a word list loaded with
    /// `convert_diacritics`.
    ///
    /// This is the whole of the normalization a template needs, and it runs *after* parsing so
    /// that grid syntax is already consumed: `#` and `.` cannot reach it, and a dictionary
    /// sanitiser cannot be pointed at a template by accident. Callers derive the flag from the
    /// corpus (`WordList::converts_diacritics`) rather than configuring it separately, because a
    /// second copy of the policy is a way to prove a fillable grid unfillable.
    pub fn fold_diacritics(&mut self) -> Result<(), TemplateError> {
        for (index, cell) in self.fill.iter_mut().enumerate() {
            let Some(letter) = *cell else { continue };
            *cell = Some(normalize_grid_letter(letter, true).ok_or(
                TemplateError::UnfoldableLetter {
                    row: index / self.width,
                    column: index % self.width,
                    letter,
                },
            )?);
        }
        Ok(())
    }
}

/// Generate an `OwnedGridConfig` representing a grid with specified entries.
///
/// The word list goes in mutably because a fully specified slot needs a `WordId` for its letters
/// whether or not they spell a dictionary entry, and unfamiliar characters need glyph ids. It
/// comes back out as the config's immutable borrow, so a caller that brackets this with
/// [`WordList::snapshot`] and [`WordList::rewind`] to keep those additions template-local cannot
/// rewind while the config is still alive.
#[must_use]
pub fn generate_grid_config<'a>(
    word_list: &'a mut WordList,
    entries: &[SlotSpec],
    raw_fill: &[Option<char>],
    width: usize,
    height: usize,
    min_score: u16,
    order: CandidateOrder,
) -> OwnedGridConfig<'a> {
    let (slot_configs, crossing_count) = generate_slot_configs(entries);

    let fill: Vec<Option<GlyphId>> = raw_fill
        .iter()
        .map(|cell| cell.map(|cell| word_list.glyph_id_for_char(cell)))
        .collect();

    let mut slot_options =
        generate_all_slot_options(word_list, &fill, &slot_configs, width, min_score);

    if order == CandidateOrder::Ranked {
        sort_slot_options(word_list, &slot_configs, &mut slot_options);
    }

    OwnedGridConfig {
        // Downgrading the mutable borrow here is what closes the door: nothing can touch the
        // corpus again until this config is dropped.
        word_list,
        fill,
        slot_configs,
        slot_options,
        width,
        height,
        crossing_count,
        abort: None,
        support_index: OnceLock::new(),
    }
}

/// Generate an `OwnedGridConfig` from an already validated template.
#[must_use]
pub fn generate_grid_config_from_parsed<'a>(
    word_list: &'a mut WordList,
    template: &ParsedTemplate,
    min_score: u16,
    order: CandidateOrder,
) -> OwnedGridConfig<'a> {
    generate_grid_config(
        word_list,
        &template.slots,
        &template.fill,
        template.width,
        template.height,
        min_score,
        order,
    )
}

/// Parse a template string and generate an `OwnedGridConfig` with ranked candidates.
pub fn generate_grid_config_from_template_string<'a>(
    word_list: &'a mut WordList,
    template: &str,
    min_score: u16,
) -> Result<OwnedGridConfig<'a>, TemplateError> {
    let template = ParsedTemplate::parse(template)?;
    Ok(generate_grid_config_from_parsed(
        word_list,
        &template,
        min_score,
        CandidateOrder::Ranked,
    ))
}

/// A struct recording a slot assignment made during a fill process.
#[derive(Debug, Clone)]
pub struct Choice {
    pub slot_id: SlotId,
    pub word_id: WordId,
}

/// Turn the given grid config and fill choices into a rendered string.
#[allow(dead_code)]
#[must_use]
pub fn render_grid(config: &GridConfig, choices: &[Choice]) -> String {
    let mut grid: Vec<Option<char>> = config
        .fill
        .iter()
        .map(|&cell| cell.map(|glyph_id| config.word_list.glyphs[glyph_id as usize]))
        .collect();

    for &Choice { slot_id, word_id } in choices {
        let slot_config = &config.slot_configs[slot_id];
        let word = &config.word_list.words[slot_config.length][word_id];

        for (cell_idx, &glyph) in word.glyphs.iter().enumerate() {
            let (x, y) = match slot_config.direction {
                Direction::Across => (
                    slot_config.start_cell.0 + cell_idx,
                    slot_config.start_cell.1,
                ),
                Direction::Down => (
                    slot_config.start_cell.0,
                    slot_config.start_cell.1 + cell_idx,
                ),
            };

            grid[y * config.width + x] = Some(config.word_list.glyphs[glyph]);
        }
    }

    grid.chunks(config.width)
        .map(|line| {
            line.iter()
                .map(|cell| cell.unwrap_or('.').to_string())
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[cfg(test)]
mod template_tests {
    use crate::grid_config::{Direction, ParsedTemplate, SlotSpec, TemplateError};

    #[test]
    fn a_template_yields_across_and_down_slots_and_fixed_letters() {
        let parsed = ParsedTemplate::parse("AB#\n..#\n...\n").unwrap();
        assert_eq!((parsed.width, parsed.height), (3, 3));
        assert_eq!(parsed.fill[0], Some('a'), "letters are lowercased");
        assert_eq!(parsed.fill[1], Some('b'));
        assert_eq!(parsed.fill[2], None, "a block holds no letter");
        assert_eq!(parsed.fill[6], None, "an empty cell holds no letter");
        assert_eq!(
            parsed.slots,
            vec![
                SlotSpec {
                    start_cell: (0, 0),
                    length: 2,
                    direction: Direction::Across
                },
                SlotSpec {
                    start_cell: (0, 1),
                    length: 2,
                    direction: Direction::Across
                },
                SlotSpec {
                    start_cell: (0, 2),
                    length: 3,
                    direction: Direction::Across
                },
                SlotSpec {
                    start_cell: (0, 0),
                    length: 3,
                    direction: Direction::Down
                },
                SlotSpec {
                    start_cell: (1, 0),
                    length: 3,
                    direction: Direction::Down
                },
            ]
        );
        assert_eq!(parsed.longest_run, 3);
    }

    #[test]
    fn surrounding_whitespace_is_cosmetic_but_an_interior_blank_row_is_not() {
        let indented = ParsedTemplate::parse("\n  ...  \n  .#.  \n\n").unwrap();
        assert_eq!((indented.width, indented.height), (3, 2));

        // Rows arrive framed (joined by `/`) from the oracle protocol, so a blank row is a real
        // row of width zero, not stray formatting. Dropping it would answer a different question.
        assert_eq!(
            ParsedTemplate::parse("...\n\n...").unwrap_err(),
            TemplateError::EmptyRow { row: 1 }
        );
        assert_eq!(
            ParsedTemplate::parse("   ").unwrap_err(),
            TemplateError::NoRows
        );
    }

    #[test]
    fn ragged_rows_name_the_offending_row() {
        assert_eq!(
            ParsedTemplate::parse("...\n...\n....").unwrap_err(),
            TemplateError::RaggedRows {
                row: 2,
                expected: 3,
                found: 4
            }
        );
    }

    #[test]
    fn a_single_cell_run_is_measured_but_is_not_a_slot() {
        // The middle column is one cell tall between two blocks: too short to clue, but it still
        // has to be counted when a caller checks the template against its longest loaded word.
        let parsed = ParsedTemplate::parse("#.#\n...\n#.#").unwrap();
        assert_eq!(parsed.longest_run, 3);
        assert!(parsed.slots.iter().all(|slot| slot.length > 1));
        assert_eq!(
            ParsedTemplate::parse("#.#").unwrap().longest_run,
            1,
            "a lone cell still contributes its length"
        );
        assert!(ParsedTemplate::parse("#.#").unwrap().slots.is_empty());
    }

    #[test]
    fn blocks_break_runs_in_both_directions() {
        let parsed = ParsedTemplate::parse("....#....\n").unwrap();
        assert_eq!(parsed.longest_run, 4);
        let parsed = ParsedTemplate::parse(".\n.\n.\n.\n#\n.\n.\n").unwrap();
        assert_eq!(parsed.longest_run, 4);
    }
}

#[cfg(all(test, feature = "serde"))]
mod serde_tests {
    use crate::grid_config::{Direction, SlotSpec};

    #[test]
    fn test_slot_spec_serialization() {
        let slot_spec = SlotSpec {
            start_cell: (1, 2),
            direction: Direction::Across,
            length: 5,
        };

        let slot_key = serde_json::to_string(&slot_spec).unwrap();

        assert_eq!(slot_key, "\"1,2,across,5\"");
    }

    #[test]
    fn test_slot_spec_deserialization() {
        let slot_spec: SlotSpec = serde_json::from_str("\"3,4,down,12\"").unwrap();

        assert_eq!(
            slot_spec,
            SlotSpec {
                start_cell: (3, 4),
                direction: Direction::Down,
                length: 12,
            }
        );
    }
}
