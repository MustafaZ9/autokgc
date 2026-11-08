use super::{extract_codes_from_html, random_user_agent, GiftCodeSource};
use anyhow::Result;
use reqwest::Client;

const URL: &str = "https://kingshotmastery.com/gift-codes";

/// CSS selectors to try for kingshotmastery.com.
const SELECTORS: &[&str] = &[
    "code.font-bold.tracking-wide",                     // active code cards
    "code.font-num.font-bold",                          // variant
    "td.font-mono:not(:has(s))",                        // table cells without strikethrough (active)
    "span.code-string",                                 // possible future format
    ".code-card code",                                  // card-based layout
];

pub struct KingshotMastery;

#[async_trait::async_trait]
impl GiftCodeSource for KingshotMastery {
    fn name(&self) -> &str {
        "kingshotmastery.com"
    }

    async fn fetch_codes(&self, client: &Client) -> Result<Vec<String>> {
        crate::logging::log(&format!("  [{}] Fetching {}...", self.name(), URL));

        let resp = client
            .get(URL)
            .header("User-Agent", random_user_agent())
            .header(
                "Accept",
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            )
            .header("Accept-Language", "en-US,en;q=0.5")
            .send()
            .await?;

        let status = resp.status();
        if !status.is_success() {
            anyhow::bail!("HTTP {}", status);
        }

        let html = resp.text().await?;
        Ok(extract_codes_from_html(&html, SELECTORS, self.name()))
    }
}
