use crate::logging::log;
use crate::players::Player;
use md5::{Digest, Md5};
use rand::Rng;
use reqwest::Client;
use std::collections::{BTreeMap, HashMap};
use std::time::{Duration, Instant};

// ─── Configuration constants ───
const BASE_URL: &str = "https://kingshot-giftcode.centurygame.com";
const REDEEM_ENDPOINT: &str = "/api/gift_code";
const KS_ENCRYPT_KEY: &str = "mN4!pQs6JrYwV9";

const DELAY_SECS: f64 = 1.0;
const RETRY_DELAY_SECS: u64 = 2;
const MAX_RETRIES: u32 = 3;
const MAX_FID_ATTEMPTS: u32 = 3;
const TOO_FREQUENT_SLEEP_SECS: u64 = 60;
const MAX_COOLDOWNS: u32 = 3;
const MAX_CONSECUTIVE_FAILURES: u32 = 10;

const TRANSPORT_FAILURE: &str = "Redemption request failed";

// ─── Status classification ───
const FATAL_STATUSES: &[&str] = &["TIME ERROR", "CDK NOT FOUND", "USED"];
const SUCCESS_STATUSES: &[&str] = &["SUCCESS", "SAME TYPE EXCHANGE"];

fn friendly_message(status: &str) -> &str {
    match status {
        "SUCCESS" => "Successfully redeemed",
        "RECEIVED" => "Already redeemed",
        "SAME TYPE EXCHANGE" => "Successfully redeemed (same type)",
        "TIME ERROR" => "Code has expired",
        "CDK NOT FOUND" => "Code not found or incorrect",
        "USED" => "Claim limit reached, unable to claim",
        "TIMEOUT RETRY" => "Server requested retry",
        "TOO FREQUENT" => "Rate limited on this player ID",
        "USER INFO ERROR" => "Wrong kingdom for this player ID",
        "ROLE NOT EXIST" => "No such player",
        "STOVE_LV ERROR" => "Town Center level too low for this code",
        "RECHARGE_MONEY ERROR" => "Requires in-game purchase",
        "RECHARGE_MONEY_VIP ERROR" => "VIP level too low for this code",
        "SIGN ERROR" => "Sign error (request encoding issue)",
        "NOT LOGIN" => "Session rejected by the server",
        _ => status,
    }
}

// ─── Browser header randomization ───
struct BrowserProfile {
    name: &'static str,
    versions: std::ops::Range<u32>,
}

const BROWSER_PROFILES: &[BrowserProfile] = &[
    BrowserProfile { name: "Chrome", versions: 124..136 },
    BrowserProfile { name: "Brave", versions: 132..146 },
    BrowserProfile { name: "Edge", versions: 124..136 },
];

const PLATFORMS: &[(&str, &str)] = &[
    ("Windows NT 10.0; Win64; x64", "\"Windows\""),
    ("Windows NT 11.0; Win64; x64", "\"Windows\""),
    ("Macintosh; Intel Mac OS X 10_15_7", "\"macOS\""),
    ("X11; Linux x86_64", "\"Linux\""),
];

fn random_headers() -> Vec<(String, String)> {
    let mut rng = rand::thread_rng();
    let profile = &BROWSER_PROFILES[rng.gen_range(0..BROWSER_PROFILES.len())];
    let version = rng.gen_range(profile.versions.clone());
    let (platform, sec_platform) = PLATFORMS[rng.gen_range(0..PLATFORMS.len())];

    let sec_ch_ua = if profile.name == "Edge" {
        format!(
            "\"Not A(B)rand\";v=\"8\", \"Chromium\";v=\"{}\", \"Microsoft Edge\";v=\"{}\"",
            version, version
        )
    } else {
        format!(
            "\"Not:A-Brand\";v=\"99\", \"{}\";v=\"{}\", \"Chromium\";v=\"{}\"",
            profile.name, version, version
        )
    };

    vec![
        ("accept".into(), "application/json, text/plain, */*".into()),
        ("accept-encoding".into(), "gzip, deflate".into()),
        ("accept-language".into(), "en-US,en;q=0.9".into()),
        ("content-type".into(), "application/x-www-form-urlencoded".into()),
        (
            "user-agent".into(),
            format!(
                "Mozilla/5.0 ({}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{}.0.0.0 Safari/537.36",
                platform, version
            ),
        ),
        ("origin".into(), BASE_URL.into()),
        ("referer".into(), format!("{}/", BASE_URL)),
        ("sec-ch-ua".into(), sec_ch_ua),
        ("sec-ch-ua-mobile".into(), "?0".into()),
        ("sec-ch-ua-platform".into(), sec_platform.to_string()),
        ("sec-fetch-dest".into(), "empty".into()),
        ("sec-fetch-mode".into(), "cors".into()),
        ("sec-fetch-site".into(), "same-origin".into()),
    ]
}

// ─── Payload signing ───
fn sign_payload(data: &BTreeMap<&str, String>) -> String {
    let encoded: String = data
        .iter()
        .map(|(k, v)| format!("{}={}", k, v))
        .collect::<Vec<_>>()
        .join("&");
    let to_hash = format!("{}{}", encoded, KS_ENCRYPT_KEY);
    let mut hasher = Md5::new();
    hasher.update(to_hash.as_bytes());
    format!("{:x}", hasher.finalize())
}

fn build_payload(fid: &str, kid: &str, cdk: &str) -> Vec<(String, String)> {
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs()
        .to_string();

    let mut data = BTreeMap::new();
    data.insert("cdk", cdk.to_string());
    data.insert("fid", fid.to_string());
    data.insert("kid", kid.to_string());
    data.insert("time", timestamp.clone());

    let sign = sign_payload(&data);

    vec![
        ("cdk".into(), cdk.to_string()),
        ("fid".into(), fid.to_string()),
        ("kid".into(), kid.to_string()),
        ("time".into(), timestamp),
        ("sign".into(), sign),
    ]
}

// ─── API response classification ───
fn classify(data: &serde_json::Value) -> String {
    let msg = data
        .get("msg")
        .and_then(|v| v.as_str())
        .unwrap_or("Unknown error")
        .trim_end_matches('.');
    let err_code = data.get("err_code").and_then(|v| v.as_i64());

    match (msg, err_code) {
        ("SUCCESS", _) => "SUCCESS".into(),
        ("RECEIVED", Some(40008)) => "RECEIVED".into(),
        ("SAME TYPE EXCHANGE", Some(40011)) => "SAME TYPE EXCHANGE".into(),
        ("TIME ERROR", Some(40007)) => "TIME ERROR".into(),
        ("CDK NOT FOUND", Some(40014)) => "CDK NOT FOUND".into(),
        ("USED", Some(40005)) => "USED".into(),
        ("TIMEOUT RETRY", Some(40004)) => "TIMEOUT RETRY".into(),
        ("TOO FREQUENT", Some(40019)) => "TOO FREQUENT".into(),
        ("USER INFO ERROR", Some(40020)) => "USER INFO ERROR".into(),
        (m, Some(40001)) if m.to_lowercase().contains("not exist") => "ROLE NOT EXIST".into(),
        ("STOVE_LV ERROR", Some(40006)) => "STOVE_LV ERROR".into(),
        ("RECHARGE_MONEY ERROR", Some(40017)) => "RECHARGE_MONEY ERROR".into(),
        ("RECHARGE_MONEY_VIP ERROR", Some(40018)) => "RECHARGE_MONEY_VIP ERROR".into(),
        (m, _) if m.to_lowercase().contains("sign error") => "SIGN ERROR".into(),
        ("NOT LOGIN", _) => "NOT LOGIN".into(),
        _ => msg.to_string(),
    }
}

// ─── HTTP request with retries ───
async fn make_request(
    client: &Client,
    payload: &[(String, String)],
) -> Option<serde_json::Value> {
    let url = format!("{}{}", BASE_URL, REDEEM_ENDPOINT);

    for attempt in 0..MAX_RETRIES {
        let headers = random_headers();
        let mut req = client.post(&url);
        for (k, v) in &headers {
            req = req.header(k.as_str(), v.as_str());
        }
        req = req.form(payload);

        match req.timeout(Duration::from_secs(30)).send().await {
            Ok(resp) => {
                let status = resp.status().as_u16();
                if status == 200 {
                    match resp.json::<serde_json::Value>().await {
                        Ok(json) => return Some(json),
                        Err(e) => {
                            log(&format!(
                                "Attempt {} to gift_code: invalid JSON — {}",
                                attempt + 1,
                                e
                            ));
                        }
                    }
                } else if status == 429 {
                    log(&format!(
                        "Attempt {} to gift_code: HTTP 429 (rate limited). Backing off...",
                        attempt + 1
                    ));
                    tokio::time::sleep(Duration::from_secs(
                        RETRY_DELAY_SECS * (attempt as u64 + 1) * 2,
                    ))
                    .await;
                    continue;
                } else if [502, 503, 504].contains(&status) {
                    log(&format!(
                        "Attempt {} to gift_code: HTTP {} (server busy). Retrying...",
                        attempt + 1,
                        status
                    ));
                    tokio::time::sleep(Duration::from_millis(
                        RETRY_DELAY_SECS * 1000 * (attempt as u64 + 1) * 3 / 2,
                    ))
                    .await;
                    continue;
                } else {
                    let body = resp.text().await.unwrap_or_default();
                    log(&format!(
                        "Attempt {} to gift_code: HTTP {}, {}",
                        attempt + 1,
                        status,
                        &body[..body.len().min(150)]
                    ));
                }
            }
            Err(e) => {
                log(&format!(
                    "Attempt {} to gift_code failed: {}",
                    attempt + 1,
                    e
                ));
            }
        }

        if attempt < MAX_RETRIES - 1 {
            tokio::time::sleep(Duration::from_secs(RETRY_DELAY_SECS * (attempt as u64 + 1))).await;
        }
    }

    None
}

// ─── Single redemption attempt ───
async fn redeem_once(client: &Client, fid: &str, kid: &str, cdk: &str) -> String {
    let payload = build_payload(fid, kid, cdk);
    match make_request(client, &payload).await {
        Some(json) => classify(&json),
        None => TRANSPORT_FAILURE.to_string(),
    }
}

// ─── Counters for summary ───
#[derive(Default)]
struct Counters {
    success: u32,
    already_redeemed: u32,
    wrong_kingdom: u32,
    errors: u32,
    rate_limited: u32,
}

/// Redeem a single gift code for all players.
pub async fn redeem_code_for_all_players(
    client: &Client,
    code: &str,
    players: &[Player],
) -> bool {
    log(&format!("\n=== Starting redemption for gift code: {} ===", code));
    log(&format!("Total unique player IDs to process: {}", players.len()));

    let mut counters = Counters::default();
    let mut error_details: HashMap<String, String> = HashMap::new();
    let mut wrong_kingdom_fids: HashMap<String, String> = HashMap::new();
    let mut cooldowns: HashMap<String, u32> = HashMap::new();
    let mut retry_queue: HashMap<String, Instant> = HashMap::new();
    let mut processed_fids: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut stop_processing = false;
    let mut consecutive_failures: u32 = 0;
    let script_start = Instant::now();

    while processed_fids.len() < players.len() && !stop_processing {
        let now = Instant::now();
        let ready: Vec<&Player> = players
            .iter()
            .filter(|p| {
                !processed_fids.contains(&p.fid)
                    && retry_queue.get(&p.fid).map_or(true, |t| now >= *t)
            })
            .collect();

        let cooling = players.len() - processed_fids.len() - ready.len();

        if ready.is_empty() {
            if cooling == 0 {
                break;
            }
            let next_retry = players
                .iter()
                .filter(|p| !processed_fids.contains(&p.fid) && retry_queue.contains_key(&p.fid))
                .filter_map(|p| retry_queue.get(&p.fid))
                .min()
                .copied();

            if let Some(next) = next_retry {
                let wait = next.saturating_duration_since(now) + Duration::from_secs(1);
                let wait_secs = wait.as_secs().min(30).max(1);
                log(&format!(
                    "{} player ID(s) in cooldown. Waiting {}s... Progress: {}/{}",
                    cooling,
                    wait_secs,
                    processed_fids.len(),
                    players.len()
                ));
                tokio::time::sleep(Duration::from_secs(wait_secs)).await;
            }
            continue;
        }

        log(&format!(
            "\n--- Starting processing cycle. Ready player IDs: {} ---",
            ready.len()
        ));

        for player in ready.iter() {
            let position = players.iter().position(|p| p.fid == player.fid).unwrap_or(0) + 1;

            log(&format!(
                "Processing K{}-{} [{}/{}] for code: {}",
                player.kid, player.fid, position, players.len(), code
            ));

            let mut status = "Processing error".to_string();

            for attempt in 0..MAX_FID_ATTEMPTS {
                status = redeem_once(client, &player.fid, &player.kid, code).await;

                if status == "TOO FREQUENT" {
                    counters.rate_limited += 1;
                    let cd = cooldowns.entry(player.fid.clone()).or_insert(0);
                    *cd += 1;
                    if *cd <= MAX_COOLDOWNS {
                        retry_queue.insert(
                            player.fid.clone(),
                            Instant::now() + Duration::from_secs(TOO_FREQUENT_SLEEP_SECS),
                        );
                        log(&format!(
                            "{} - Rate limited, retrying in ~{}s",
                            player.fid, TOO_FREQUENT_SLEEP_SECS
                        ));
                        break;
                    }
                    log(&format!(
                        "{} - Still rate limited after {} cooldowns. Giving up on this player.",
                        player.fid, MAX_COOLDOWNS
                    ));
                }

                if status == "TIMEOUT RETRY" {
                    if attempt < MAX_FID_ATTEMPTS - 1 {
                        log(&format!(
                            "{} - [Attempt {}/{}] {}. Retrying...",
                            player.fid,
                            attempt + 1,
                            MAX_FID_ATTEMPTS,
                            status
                        ));
                        tokio::time::sleep(Duration::from_secs(
                            RETRY_DELAY_SECS * (attempt as u64 + 1),
                        ))
                        .await;
                        continue;
                    }
                    log(&format!(
                        "{} - Max attempts reached after {}.",
                        player.fid, status
                    ));
                }
                break;
            }

            // Check if player is in cooldown retry queue
            if retry_queue
                .get(&player.fid)
                .map_or(false, |t| Instant::now() < *t)
            {
                continue;
            }

            processed_fids.insert(player.fid.clone());

            let friendly = friendly_message(&status);
            log(&format!("{} - Result: {}", player.fid, friendly));

            // Record result
            if SUCCESS_STATUSES.contains(&status.as_str()) {
                counters.success += 1;
            } else if status == "RECEIVED" {
                counters.already_redeemed += 1;
            } else if status == "USER INFO ERROR" {
                counters.wrong_kingdom += 1;
                wrong_kingdom_fids.insert(player.fid.clone(), player.kid.clone());
            } else if !FATAL_STATUSES.contains(&status.as_str()) {
                counters.errors += 1;
                error_details.insert(player.fid.clone(), friendly.to_string());
            }

            if FATAL_STATUSES.contains(&status.as_str()) {
                log(&format!(
                    "\n *** {}! Stopping further processing. ***",
                    friendly
                ));
                stop_processing = true;
                break;
            }

            consecutive_failures = if status == TRANSPORT_FAILURE {
                consecutive_failures + 1
            } else {
                0
            };

            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES {
                log(&format!(
                    "\n *** {} players in a row could not reach the API. Stopping. ***",
                    MAX_CONSECUTIVE_FAILURES
                ));
                stop_processing = true;
                break;
            }

            // Delay between players
            let jitter = rand::thread_rng().gen_range(0.0..0.5);
            tokio::time::sleep(Duration::from_secs_f64(DELAY_SECS + jitter)).await;
        }

        log(&format!(
            "--- Cycle finished. Total processed: {}/{} ---",
            processed_fids.len(),
            players.len()
        ));
    }

    // ─── Print summary ───
    let elapsed = script_start.elapsed();
    let hours = elapsed.as_secs() / 3600;
    let minutes = (elapsed.as_secs() % 3600) / 60;
    let seconds = elapsed.as_secs() % 60;

    log(&format!(
        "\n{}",
        "=".repeat(25).to_string() + " Redemption Summary " + &"=".repeat(25)
    ));
    log(&format!("Code Redeemed: {}", code));
    log(&format!("Total Unique Player IDs Found: {}", players.len()));
    log(&format!(
        "Total Player IDs Processed: {}",
        processed_fids.len()
    ));
    log(&format!("Successfully redeemed: {}", counters.success));
    log(&format!("Already redeemed: {}", counters.already_redeemed));
    log(&format!("Wrong kingdom: {}", counters.wrong_kingdom));
    log(&format!("Other Errors/Failures: {}", counters.errors));
    log(&format!("Rate Limit Events: {}", counters.rate_limited));
    log(&format!(
        "Total execution time: {:02}:{:02}:{:02}",
        hours, minutes, seconds
    ));
    log(&format!("{}", "=".repeat(70)));

    !stop_processing
}
