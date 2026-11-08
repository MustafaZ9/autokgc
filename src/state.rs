use std::collections::BTreeSet;
use std::fs;
use std::path::Path;
use crate::logging::log;

/// Load the set of previously-seen codes from disk.
pub fn load_known_codes(path: &Path) -> BTreeSet<String> {
    if !path.exists() {
        return BTreeSet::new();
    }
    match fs::read_to_string(path) {
        Ok(content) => content
            .lines()
            .map(|l| l.trim().to_string())
            .filter(|l| !l.is_empty())
            .collect(),
        Err(e) => {
            log(&format!("Warning: could not read {}: {}", path.display(), e));
            BTreeSet::new()
        }
    }
}

/// Persist the full set of known codes to disk (sorted, one per line).
pub fn save_known_codes(path: &Path, codes: &BTreeSet<String>) {
    let content: String = codes.iter().map(|c| format!("{}\n", c)).collect();
    if let Err(e) = fs::write(path, content) {
        log(&format!("Error: could not write {}: {}", path.display(), e));
    }
}
