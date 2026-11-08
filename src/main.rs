mod config;
mod logging;
mod players;
mod redeemer;
mod scraper;
mod state;

use clap::Parser;
use logging::log;
use std::path::PathBuf;

#[derive(Parser)]
#[command(
    name = "autokgc",
    about = "Kingshot Gift Code Scraper & Redeemer v2 (Rust)",
    long_about = "Scrapes gift code websites for new codes, then redeems them\n\
                  for all player IDs in a CSV file via the Kingshot API."
)]
struct Cli {
    /// Path to the CSV file with player IDs
    #[arg(long)]
    csv: Option<String>,

    /// Redeem a specific code (skip scraping)
    #[arg(long)]
    code: Option<String>,

    /// Default kingdom for players whose CSV row doesn't carry one
    #[arg(long, default_value = "1266")]
    kingdom: u32,

    /// Only scrape for new codes, don't redeem
    #[arg(long)]
    scrape_only: bool,
}

fn script_dir() -> PathBuf {
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    let default_kingdom = cli.kingdom.to_string();

    let known_codes_path = script_dir().join("known_codes.txt");
    let config_path = script_dir().join("config.json");

    // Build HTTP client (shared across scraping + redemption)
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(60))
        .gzip(true)
        .deflate(true)
        .build()?;

    // ── Mode 1: Scrape-only ──
    if cli.scrape_only {
        log("Starting scrape-only mode...");
        let mut known_codes = state::load_known_codes(&known_codes_path);

        let fetched = scraper::fetch_all_codes(&client).await;
        let new_codes: Vec<String> = fetched
            .into_iter()
            .filter(|c| !known_codes.contains(c))
            .collect();

        if new_codes.is_empty() {
            log("No new gift codes found. All codes have been seen previously.");
        } else {
            log(&format!(
                "\n🎉 NEW GIFT CODES FOUND: {}",
                new_codes.join(", ")
            ));
            for code in &new_codes {
                known_codes.insert(code.clone());
            }
            state::save_known_codes(&known_codes_path, &known_codes);
            log(&format!(
                "Saved {} new code(s) to {}",
                new_codes.len(),
                known_codes_path.display()
            ));
        }
        return Ok(());
    }

    // Resolve CSV path
    let csv_path = cli.csv.unwrap_or_else(|| {
        let cfg = config::Config::load(&config_path);
        cfg.default_csv
    });

    // ── Mode 2: Manual code redemption ──
    if let Some(code) = cli.code {
        log(&format!("Starting manual redemption for code: {}", code));
        let players = players::load_players(&csv_path, &default_kingdom)?;
        if players.is_empty() {
            log("Error: No valid player IDs loaded. Exiting.");
            std::process::exit(1);
        }
        redeemer::redeem_code_for_all_players(&client, &code, &players).await;
        return Ok(());
    }

    // ── Mode 3: Auto — scrape then redeem ──
    log("Starting auto gift code redeemer...");

    let mut known_codes = state::load_known_codes(&known_codes_path);
    log(&format!(
        "Checking multiple sources for new gift codes..."
    ));

    let fetched = scraper::fetch_all_codes(&client).await;
    log(&format!("Fetched {} total codes from all sources.", fetched.len()));

    let new_codes: Vec<String> = fetched
        .into_iter()
        .filter(|c| !known_codes.contains(c))
        .collect();

    log(&format!("Found {} new codes.", new_codes.len()));

    if new_codes.is_empty() {
        log("Nothing to redeem.");
        return Ok(());
    }

    log(&format!(
        "\n🎉 NEW GIFT CODES FOUND: {}",
        new_codes.join(", ")
    ));

    let players = players::load_players(&csv_path, &default_kingdom)?;
    if players.is_empty() {
        log("Error: No valid player IDs loaded from any CSV file. Skipping.");
        return Ok(());
    }

    for code in &new_codes {
        redeemer::redeem_code_for_all_players(&client, code, &players).await;
        known_codes.insert(code.clone());
        state::save_known_codes(&known_codes_path, &known_codes);
        log(&format!("Marked '{}' as known.\n", code));
    }

    Ok(())
}
