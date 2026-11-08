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

/// Check if a string looks like a valid gift code.
fn is_gift_code(s: &str) -> bool {
    let len = s.len();
    len >= 3
        && len <= 30
        && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
        // Must contain at least one uppercase letter (avoids matching random words)
        && s.chars().any(|c| c.is_ascii_uppercase())
}

/// Extract codes from JSON-LD structured data (schema.org ItemList).
fn extract_codes_from_jsonld(html: &str, source_name: &str) -> Vec<String> {
    use scraper::{Html, Selector};
    let document = Html::parse_document(html);

    if let Ok(sel) = Selector::parse("script[type=\"application/ld+json\"]") {
        for el in document.select(&sel) {
            let json_text = el.text().collect::<String>();
            if let Ok(val) = serde_json::from_str::<serde_json::Value>(&json_text) {
                let codes = extract_codes_from_jsonld_value(&val);
                if !codes.is_empty() {
                    log(&format!(
                        "  [{}] Found {} codes from JSON-LD structured data",
                        source_name,
                        codes.len()
                    ));
                    return codes;
                }
            }
        }
    }

    // Some Next.js sites embed JSON-LD in __next_f.push RSC payloads
    // Search for the pattern in raw HTML
    for cap in html.split("\"@type\":\"ItemList\"") {
        // Look for "name":"CODE" patterns after ItemList
        let chunk = &cap[..cap.len().min(5000)];
        let mut codes = Vec::new();
        for part in chunk.split("\"name\":\"") {
            if let Some(end) = part.find('"') {
                let name = &part[..end];
                if is_gift_code(name) {
                    codes.push(name.to_string());
                }
            }
        }
        if !codes.is_empty() {
            log(&format!(
                "  [{}] Found {} codes from inline JSON-LD/RSC payload",
                source_name,
                codes.len()
            ));
            return codes;
        }
    }

    vec![]
}

/// Recursively extract code names from a JSON-LD value.
fn extract_codes_from_jsonld_value(val: &serde_json::Value) -> Vec<String> {
    let mut codes = Vec::new();

    if let Some(obj) = val.as_object() {
        // Look for ItemList with itemListElement
        let is_item_list = obj
            .get("@type")
            .and_then(|v| v.as_str())
            .map_or(false, |t| t == "ItemList");

        if is_item_list {
            if let Some(items) = obj.get("itemListElement").and_then(|v| v.as_array()) {
                for item in items {
                    if let Some(name) = item
                        .get("item")
                        .and_then(|i| i.get("name"))
                        .and_then(|n| n.as_str())
                    {
                        if is_gift_code(name) {
                            codes.push(name.to_string());
                        }
                    }
                    // Some structures put name directly
                    if let Some(name) = item.get("name").and_then(|n| n.as_str()) {
                        if is_gift_code(name) && !codes.contains(&name.to_string()) {
                            codes.push(name.to_string());
                        }
                    }
                }
            }
        }

        // Recurse into all values
        for v in obj.values() {
            codes.extend(extract_codes_from_jsonld_value(v));
        }
    } else if let Some(arr) = val.as_array() {
        for v in arr {
            codes.extend(extract_codes_from_jsonld_value(v));
        }
    }

    codes
}

/// Extract codes from meta description tag (both sites list active codes there).
fn extract_codes_from_meta(html: &str, source_name: &str) -> Vec<String> {
    use scraper::{Html, Selector};
    let document = Html::parse_document(html);

    // Try meta description and og:description
    let meta_selectors = [
        "meta[name=\"description\"]",
        "meta[property=\"og:description\"]",
    ];

    for sel_str in &meta_selectors {
        if let Ok(sel) = Selector::parse(sel_str) {
            for el in document.select(&sel) {
                if let Some(content) = el.value().attr("content") {
                    // Look for code-like strings: uppercase+digits, 3-30 chars
                    let codes: Vec<String> = content
                        .split(|c: char| c == ',' || c == ' ' || c == '.' || c == ':')
                        .map(|s| s.trim())
                        .filter(|s| is_gift_code(s))
                        .map(|s| s.to_string())
                        .collect();

                    if !codes.is_empty() {
                        log(&format!(
                            "  [{}] Found {} codes from meta description fallback",
                            source_name,
                            codes.len()
                        ));
                        return codes;
                    }
                }
            }
        }
    }
    vec![]
}

/// Extract codes from HTML using multiple strategies:
/// 1. CSS selectors (site-specific)
/// 2. JSON-LD structured data
/// 3. Meta description fallback
pub fn extract_codes_from_html(
    html: &str,
    selectors: &[&str],
    source_name: &str,
) -> Vec<String> {
    use scraper::{Html, Selector};
    let document = Html::parse_document(html);

    // Strategy 1: CSS selectors
    for selector_str in selectors {
        if let Ok(selector) = Selector::parse(selector_str) {
            let codes: Vec<String> = document
                .select(&selector)
                .map(|el| el.text().collect::<String>().trim().to_string())
                .filter(|text| is_gift_code(text))
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

    // Strategy 2: JSON-LD structured data
    let jsonld_codes = extract_codes_from_jsonld(html, source_name);
    if !jsonld_codes.is_empty() {
        return jsonld_codes;
    }

    // Strategy 3: Meta description
    let meta_codes = extract_codes_from_meta(html, source_name);
    if !meta_codes.is_empty() {
        return meta_codes;
    }

    log(&format!(
        "  [{}] WARNING: No codes found with any strategy — site HTML may have changed!",
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


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_kingshot_net_extraction() {
        let html = match std::fs::read_to_string("/tmp/kingshot.html") {
            Ok(h) => h,
            Err(_) => {
                eprintln!("Skipping test: /tmp/kingshot.html not found");
                return;
            }
        };
        let selectors: &[&str] = &[
            "code.font-num.font-bold.text-cyan",
            "code.font-num.font-bold",
            "code.font-bold.tracking-wide",
            "p.font-mono.text-xl.font-bold.tracking-wider",
            "p.font-mono.font-bold",
            "code.text-cyan",
        ];
        let codes = extract_codes_from_html(&html, selectors, "kingshot.net");
        println!("kingshot.net codes: {:?}", codes);
        assert!(!codes.is_empty(), "Should find codes from kingshot.net");
    }

    #[test]
    fn test_mastery_extraction() {
        let html = match std::fs::read_to_string("/tmp/mastery.html") {
            Ok(h) => h,
            Err(_) => {
                eprintln!("Skipping test: /tmp/mastery.html not found");
                return;
            }
        };
        let selectors: &[&str] = &[
            "code.font-bold.tracking-wide",
            "code.font-num.font-bold",
            "td.font-mono:not(:has(s))",
            "span.code-string",
            ".code-card code",
        ];
        let codes = extract_codes_from_html(&html, selectors, "mastery");
        println!("mastery codes: {:?}", codes);
        assert!(!codes.is_empty(), "Should find codes from mastery");
    }

    #[test]
    fn test_is_gift_code() {
        assert!(is_gift_code("FORTHEKINGDOM"));
        assert!(is_gift_code("VIP777"));
        assert!(is_gift_code("11OFFICIALSTORE08"));
        assert!(is_gift_code("KS1104"));
        assert!(!is_gift_code("ab"));       // too short
        assert!(!is_gift_code(""));         // empty
        assert!(!is_gift_code("hello"));    // no uppercase
        assert!(!is_gift_code("A B"));      // space
    }
}
