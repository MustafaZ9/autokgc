use super::{extract_codes_from_html, random_user_agent, GiftCodeSource};
use anyhow::Result;
use reqwest::Client;

const URL: &str = "https://kingshot.net/gift-codes";

/// CSS selectors to try for kingshot.net, broadest last.
const SELECTORS: &[&str] = &[
    "p.font-mono.text-xl.font-bold.tracking-wider",   // original (pre-Nov 2026)
    "p.font-mono.font-bold",                            // broader
    "p.font-mono",                                      // broadest p
    "span.font-mono.font-bold",                         // alternate tag
    "code",                                             // semantic fallback
];

pub struct KingshotNet;

#[async_trait::async_trait]
impl GiftCodeSource for KingshotNet {
    fn name(&self) -> &str {
        "kingshot.net"
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
