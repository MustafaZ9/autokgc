pub mod kingshot_net;
pub mod mastery;

use crate::logging::log;
use anyhow::Result;
use reqwest::Client;
use std::collections::HashSet;

/// Trait for gift code sources.
#[async_trait::async_trait]
pub trait GiftCodeSource: Send + Sync {
    /// Fetch all currently active gift codes from this source.
    async fn fetch_codes(&self, client: &Client) -> Result<Vec<String>>;
    /// Human-readable name of this source.
    fn name(&self) -> &str;
}

/// Randomized User-Agent for scraping requests.
pub fn random_user_agent() -> String {
    use rand::Rng;
    let mut rng = rand::thread_rng();
    let version: u32 = rng.gen_range(124..136);
    let platforms = [
        "Windows NT 10.0; Win64; x64",
        "Macintosh; Intel Mac OS X 10_15_7",
        "X11; Linux x86_64",
    ];
    let platform = platforms[rng.gen_range(0..platforms.len())];
    format!(
        "Mozilla/5.0 ({}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{}.0.0.0 Safari/537.36",
        platform, version
    )
}

/// Extract codes from HTML using multiple CSS selectors, returning the first match.
pub fn extract_codes_from_html(
    html: &str,
    selectors: &[&str],
    source_name: &str,
) -> Vec<String> {
    use scraper::{Html, Selector};
    let document = Html::parse_document(html);

    for selector_str in selectors {
        if let Ok(selector) = Selector::parse(selector_str) {
            let codes: Vec<String> = document
                .select(&selector)
                .map(|el| el.text().collect::<String>().trim().to_string())
                .filter(|text| {
                    !text.is_empty()
                        && text.len() >= 3
                        && text.len() <= 30
                        && text.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
                })
                .collect();

            if !codes.is_empty() {
                log(&format!(
                    "  [{}] Found {} codes using selector: {}",
                    source_name,
                    codes.len(),
                    selector_str
                ));
                return codes;
            }
        }
    }

    log(&format!(
        "  [{}] WARNING: No codes found with any known selector — site HTML may have changed!",
        source_name
    ));
    vec![]
}

/// Fetch codes from all sources, merge and deduplicate.
pub async fn fetch_all_codes(client: &Client) -> Vec<String> {
    let sources: Vec<Box<dyn GiftCodeSource>> = vec![
        Box::new(kingshot_net::KingshotNet),
        Box::new(mastery::KingshotMastery),
    ];

    let mut all_codes = HashSet::new();

    for source in &sources {
        match source.fetch_codes(client).await {
            Ok(codes) => {
                if codes.is_empty() {
                    log(&format!(
                        "  [{}] returned 0 codes — selector may be broken!",
                        source.name()
                    ));
                } else {
                    log(&format!(
                        "  [{}] contributed {} codes",
                        source.name(),
                        codes.len()
                    ));
                }
                all_codes.extend(codes);
            }
            Err(e) => log(&format!("  [{}] failed — {}", source.name(), e)),
        }
    }

    if all_codes.is_empty() {
        log("WARNING: ALL sources returned 0 codes. Scraping may be completely broken!");
    }

    let mut codes: Vec<String> = all_codes.into_iter().collect();
    codes.sort();
    codes
}
