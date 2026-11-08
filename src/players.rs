use crate::logging::log;
use anyhow::{Context, Result};
use std::path::Path;

const MAX_KINGDOM_ID: u32 = 999999;

/// A player with their fortress ID and kingdom ID.
#[derive(Debug, Clone)]
pub struct Player {
    pub fid: String,
    pub kid: String,
}

/// Parse a single CSV row into (fid, optional kid) pairs.
///
/// Handles formats:
///   fid,kid,name...  →  [(fid, Some(kid))]
///   fid,kid          →  [(fid, Some(kid))]
///   fid              →  [(fid, None)]
fn parse_csv_row(fields: &[&str]) -> Vec<(String, Option<String>)> {
    let fields: Vec<&str> = fields
        .iter()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .collect();

    if fields.is_empty() || fields[0].starts_with('#') {
        return vec![];
    }

    // 2+ columns: if first two are numeric and second looks like a kingdom ID
    if fields.len() >= 2
        && fields[0].chars().all(|c| c.is_ascii_digit())
        && fields[1].chars().all(|c| c.is_ascii_digit())
    {
        if let Ok(kid) = fields[1].parse::<u32>() {
            if kid <= MAX_KINGDOM_ID {
                return vec![(fields[0].to_string(), Some(fields[1].to_string()))];
            }
        }
    }

    // Bare FID(s) on a line
    fields
        .iter()
        .filter(|f| f.chars().all(|c| c.is_ascii_digit()) && !f.is_empty())
        .map(|f| (f.to_string(), None))
        .collect()
}

/// Read player IDs from a CSV file.
fn read_players_from_csv(file_path: &Path) -> Result<Vec<(String, Option<String>)>> {
    let content = std::fs::read_to_string(file_path)
        .with_context(|| format!("Could not read {}", file_path.display()))?;

    if content.trim().is_empty() {
        log(&format!(
            "Warning: '{}' appears to be empty.",
            file_path.file_name().unwrap_or_default().to_string_lossy()
        ));
        return Ok(vec![]);
    }

    let mut players = Vec::new();
    let mut ignored = 0;

    for line in content.lines() {
        let fields: Vec<&str> = line.split(',').collect();
        let parsed = parse_csv_row(&fields);
        if parsed.is_empty()
            && !line.trim().is_empty()
            && !line.trim().starts_with('#')
        {
            ignored += 1;
        }
        players.extend(parsed);
    }

    let with_kingdom = players.iter().filter(|(_, k)| k.is_some()).count();
    log(&format!(
        "Read {} player IDs from {} ({} with a kingdom)",
        players.len(),
        file_path.file_name().unwrap_or_default().to_string_lossy(),
        with_kingdom
    ));
    if ignored > 0 {
        log(&format!(
            "Ignored {} non-numeric or malformed rows.",
            ignored
        ));
    }

    Ok(players)
}

/// Load players from CSV file(s), deduplicate, and assign default kingdom where missing.
pub fn load_players(csv_path: &str, default_kingdom: &str) -> Result<Vec<Player>> {
    let path = Path::new(csv_path);
    let csv_files: Vec<std::path::PathBuf> = if path.is_dir() {
        std::fs::read_dir(path)?
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.extension().map_or(false, |ext| ext == "csv"))
            .collect()
    } else {
        vec![path.to_path_buf()]
    };

    if csv_files.is_empty() {
        anyhow::bail!("No CSV files found.");
    }

    let mut raw: Vec<(String, Option<String>)> = Vec::new();
    for csv_file in &csv_files {
        match read_players_from_csv(csv_file) {
            Ok(entries) => raw.extend(entries),
            Err(e) => log(&format!("Error processing {}: {}", csv_file.display(), e)),
        }
    }

    // Deduplicate: keep the first kingdom seen for each fid
    let mut kingdoms = std::collections::HashMap::new();
    for (fid, kid) in &raw {
        if kid.is_some() || !kingdoms.contains_key(fid) {
            kingdoms.insert(
                fid.clone(),
                kid.clone().or_else(|| kingdoms.get(fid).cloned().flatten()),
            );
        }
    }

    let missing: Vec<_> = kingdoms
        .iter()
        .filter(|(_, k)| k.is_none())
        .map(|(fid, _)| fid.clone())
        .collect();

    if !missing.is_empty() {
        log(&format!(
            "{} player ID(s) had no kingdom in the CSV; using default kingdom {}.",
            missing.len(),
            default_kingdom
        ));
    }

    let mut players: Vec<Player> = kingdoms
        .into_iter()
        .map(|(fid, kid)| Player {
            fid,
            kid: kid.unwrap_or_else(|| default_kingdom.to_string()),
        })
        .collect();

    // Sort by FID numerically
    players.sort_by(|a, b| {
        a.fid
            .parse::<u64>()
            .unwrap_or(0)
            .cmp(&b.fid.parse::<u64>().unwrap_or(0))
    });

    let duplicates = raw.len() as i64 - players.len() as i64;
    if duplicates > 0 {
        log(&format!("Removed {} duplicate entries.", duplicates));
    }

    log(&format!("Total unique player IDs to process: {}", players.len()));
    Ok(players)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_fid_kid_name() {
        let row = vec!["183695044", "1266", "FredericSELLIER"];
        let result = parse_csv_row(&row);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, "183695044");
        assert_eq!(result[0].1, Some("1266".to_string()));
    }

    #[test]
    fn test_parse_fid_kid() {
        let row = vec!["183695044", "1266"];
        let result = parse_csv_row(&row);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, "183695044");
        assert_eq!(result[0].1, Some("1266".to_string()));
    }

    #[test]
    fn test_parse_bare_fid() {
        let row = vec!["183695044"];
        let result = parse_csv_row(&row);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, "183695044");
        assert_eq!(result[0].1, None);
    }

    #[test]
    fn test_parse_comment() {
        let row = vec!["# this is a comment"];
        let result = parse_csv_row(&row);
        assert!(result.is_empty());
    }

    #[test]
    fn test_parse_empty_name() {
        let row = vec!["181340930", "1266", ""];
        let result = parse_csv_row(&row);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, "181340930");
        assert_eq!(result[0].1, Some("1266".to_string()));
    }

    #[test]
    fn test_kingdom_not_treated_as_player() {
        // This was the bug: 3-column rows treated kingdom as a player ID
        let row = vec!["183695044", "1266", "SomeName"];
        let result = parse_csv_row(&row);
        assert_eq!(result.len(), 1, "Should NOT create a phantom player for kingdom 1266");
        assert_eq!(result[0].0, "183695044");
    }
}
