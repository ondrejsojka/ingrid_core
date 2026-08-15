#![warn(clippy::pedantic)]
#![allow(clippy::cast_possible_truncation)]
#![allow(clippy::cast_precision_loss)]
#![allow(clippy::cast_sign_loss)]
#![allow(clippy::comparison_chain)]
#![allow(clippy::implicit_hasher)]
#![allow(clippy::missing_errors_doc)]
#![allow(clippy::missing_panics_doc)]
#![allow(clippy::module_name_repetitions)]
#![allow(clippy::similar_names)]
// Test code only: exact float equality is routinely the property under test, and table
// tests run long.
#![cfg_attr(test, allow(clippy::float_cmp))]
#![cfg_attr(test, allow(clippy::too_many_lines))]

pub mod arc_consistency;
pub mod backtracking_search;
pub mod dupe_index;
pub mod fill_set;
pub mod grid_config;
mod live_state;
pub mod oracle;
pub mod parallel_search;
#[cfg(test)]
pub(crate) mod test_support;
pub mod types;
pub mod util;
pub mod variant_estimate;
pub mod word_list;

/// The expected maximum length for a single slot.
pub const MAX_SLOT_LENGTH: usize = 21;
