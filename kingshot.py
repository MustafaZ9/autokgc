#!/usr/bin/env python3
"""
Kingshot Gift Code Scraper & Redeemer — Unified Script v2.0
Scrapes https://kingshot.net/gift-codes for new codes, then redeems them
for all player IDs in a CSV file via the Kingshot API.

Usage:
    # Auto mode: scrape for new codes and redeem them
    python kingshot.py --csv lod.csv

    # Scrape-only: just check for new codes, don't redeem
    python kingshot.py --scrape-only

    # Manual: redeem a specific code (skip scraping)
    python kingshot.py --csv lod.csv --code SOMECODE
"""

import os
import sys
import csv
import json
import time
import random
import hashlib
import argparse
import requests
from glob import glob
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# ─────────────────────────── Configuration ────────────────────────────
SCRAPE_URL = "https://kingshot.net/gift-codes"
BASE_URL = "https://kingshot-giftcode.centurygame.com"
REDEEM_URL = BASE_URL + "/api/gift_code"
ORIGIN = BASE_URL
KS_ENCRYPT_KEY = "mN4!pQs6JrYwV9"

DELAY = 1.0                 # seconds between player IDs
RETRY_DELAY = 2             # seconds between transport retries
MAX_RETRIES = 3             # transport retries per request
MAX_FID_ATTEMPTS = 3        # redemption attempts per player before giving up
TOO_FREQUENT_SLEEP = 60     # per-FID cooldown after a TOO FREQUENT (40019)
MAX_COOLDOWNS = 3           # cooldowns a single player may take before being given up on
MAX_CONSECUTIVE_FAILURES = 10
MAX_KINGDOM_ID = 999999

TRANSPORT_FAILURE = "Redemption request failed"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWN_CODES_FILE = os.path.join(SCRIPT_DIR, "known_codes.txt")
LOG_FILE = os.path.join(SCRIPT_DIR, "redeemed_codes.txt")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

RESULT_MESSAGES = {
    "SUCCESS": "Successfully redeemed",
    "RECEIVED": "Already redeemed",
    "SAME TYPE EXCHANGE": "Successfully redeemed (same type)",
    "TIME ERROR": "Code has expired",
    "CDK NOT FOUND": "Code not found or incorrect",
    "USED": "Claim limit reached, unable to claim",
    "TIMEOUT RETRY": "Server requested retry",
    "TOO FREQUENT": "Rate limited on this player ID",
    "USER INFO ERROR": "Wrong kingdom for this player ID",
    "ROLE NOT EXIST": "No such player",
    "STOVE_LV ERROR": "Town Center level too low for this code",
    "RECHARGE_MONEY ERROR": "Requires in-game purchase",
    "RECHARGE_MONEY_VIP ERROR": "VIP level too low for this code",
    "SIGN ERROR": "Sign error (request encoding issue)",
    "NOT LOGIN": "Session rejected by the server",
}

FATAL_STATUSES = ("TIME ERROR", "CDK NOT FOUND", "USED")
SUCCESS_STATUSES = ("SUCCESS", "SAME TYPE EXCHANGE")

BROWSER_PROFILES = [
    ('Chrome', list(range(124, 136))),
    ('Brave', list(range(132, 146))),
    ('Edge', list(range(124, 136))),
]

PLATFORMS = [
    ('Windows NT 10.0; Win64; x64', '"Windows"'),
    ('Windows NT 11.0; Win64; x64', '"Windows"'),
    ('Macintosh; Intel Mac OS X 10_15_7', '"macOS"'),
    ('X11; Linux x86_64', '"Linux"'),
]

# ─────────────────────────── Logging ──────────────────────────────────
def log(message):
    """Log a message to the console and to the log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp} - {message}"

    try:
        print(log_entry)
    except UnicodeEncodeError:
        cleaned = log_entry.encode("utf-8", errors="replace").decode("ascii", errors="replace")
        print(cleaned)

    try:
        with open(LOG_FILE, "a", encoding="utf-8-sig", newline='') as f:
            f.write(log_entry + "\n")
    except Exception as e:
        print(f"{timestamp} - LOGGING ERROR: Could not write to {LOG_FILE}. Error: {e}")

# ─────────────────────────── Scraping ─────────────────────────────────
def load_known_codes():
    """Load the set of previously-seen codes from disk."""
    if not os.path.exists(KNOWN_CODES_FILE):
        return set()
    with open(KNOWN_CODES_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())


def save_known_codes(codes):
    """Persist the full set of known codes to disk."""
    with open(KNOWN_CODES_FILE, "w") as f:
        for code in sorted(codes):
            f.write(code + "\n")


def scrape_gift_codes():
    """Fetch all gift codes currently listed on kingshot.net."""
    response = None

    # Try curl_cffi with multiple browser targets (best Cloudflare bypass)
    try:
        from curl_cffi import requests as cffi_requests
        log("Using curl_cffi for Cloudflare bypass...")

        for browser in ["chrome124", "chrome120", "chrome110", "safari17_0"]:
            try:
                resp = cffi_requests.get(SCRAPE_URL, impersonate=browser)
                if resp.status_code == 200:
                    log(f"Success with {browser} impersonation")
                    response = resp
                    break
                log(f"  {browser}: HTTP {resp.status_code}, trying next target...")
            except Exception as e:
                log(f"  {browser} failed: {e}")
                continue

    except ImportError:
        log("WARNING: curl_cffi is not installed! "
            "Install it with: pip install 'curl-cffi>=0.7.3,<0.14'")

    # Fallback to plain requests
    if response is None or response.status_code != 200:
        log("Falling back to plain requests (may be blocked by Cloudflare)...")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;'
                      'q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        response = requests.get(SCRAPE_URL, headers=headers)

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    code_elements = soup.find_all("p", class_="font-mono text-xl font-bold tracking-wider")
    return [el.text.strip() for el in code_elements]


def find_new_codes():
    """Scrape the website and compare against known codes."""
    log(f"Checking {SCRAPE_URL} for new gift codes...")

    current_codes = scrape_gift_codes()
    known_codes = load_known_codes()
    new_codes = [c for c in current_codes if c not in known_codes]

    if new_codes:
        log(f"\n🎉 NEW GIFT CODES FOUND: {', '.join(new_codes)}")
    else:
        log("No new gift codes found. All codes have been seen previously.")

    return new_codes, known_codes

# ─────────────────────────── v2 Redeem Engine ─────────────────────────
def get_headers():
    """Randomized browser-like headers, rotated per request."""
    browser, versions = random.choice(BROWSER_PROFILES)
    version = random.choice(versions)
    platform, sec_platform = random.choice(PLATFORMS)

    if browser == 'Edge':
        sec_ch_ua = f'"Not A(B)rand";v="8", "Chromium";v="{version}", "Microsoft Edge";v="{version}"'
    else:
        sec_ch_ua = f'"Not:A-Brand";v="99", "{browser}";v="{version}", "Chromium";v="{version}"'

    return {
        'accept': 'application/json, text/plain, */*',
        'accept-encoding': 'gzip, deflate',
        'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/x-www-form-urlencoded',
        'user-agent': (f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) "
                       f"Chrome/{version}.0.0.0 Safari/537.36"),
        'origin': ORIGIN,
        'referer': ORIGIN + '/',
        'sec-ch-ua': sec_ch_ua,
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': sec_platform,
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
    }


def encode_data(data):
    """Sign the payload with the MD5 hash the API expects."""
    sorted_keys = sorted(data.keys())
    encoded = "&".join(f"{key}={data[key]}" for key in sorted_keys)
    return {"sign": hashlib.md5(f"{encoded}{KS_ENCRYPT_KEY}".encode()).hexdigest(), **data}


SESSION = requests.Session()


def make_request(url, payload):
    """POST with transport-level retries; returns the response or None."""
    endpoint = url.split('/')[-1]
    for attempt in range(MAX_RETRIES):
        try:
            response = SESSION.post(url, data=payload, timeout=(10, 30))

            if response.status_code == 200:
                return response
            if response.status_code == 429:
                log(f"Attempt {attempt+1} to {endpoint}: HTTP 429 (rate limited). Backing off...")
                time.sleep(RETRY_DELAY * (attempt + 1) * 2)
                continue
            if response.status_code in (502, 503, 504):
                log(f"Attempt {attempt+1} to {endpoint}: HTTP {response.status_code} (server busy). Retrying...")
                time.sleep(RETRY_DELAY * (attempt + 1) * 1.5)
                continue
            log(f"Attempt {attempt+1} to {endpoint}: HTTP {response.status_code}, {response.text[:150]}")

        except requests.exceptions.Timeout:
            log(f"Attempt {attempt+1} to {endpoint} timed out. Retrying...")
        except requests.exceptions.RequestException as e:
            log(f"Attempt {attempt+1} to {endpoint} failed: {e.__class__.__name__} - {e}")

        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY * (attempt + 1))

    return None


def classify(data):
    """Map an API response to one of the RESULT_MESSAGES keys."""
    msg = str(data.get("msg", "Unknown error")).strip('.')
    err_code = data.get("err_code")

    if msg == "SUCCESS":
        return "SUCCESS"
    if msg == "RECEIVED" and err_code == 40008:
        return "RECEIVED"
    if msg == "SAME TYPE EXCHANGE" and err_code == 40011:
        return "SAME TYPE EXCHANGE"
    if msg == "TIME ERROR" and err_code == 40007:
        return "TIME ERROR"
    if msg == "CDK NOT FOUND" and err_code == 40014:
        return "CDK NOT FOUND"
    if msg == "USED" and err_code == 40005:
        return "USED"
    if msg == "TIMEOUT RETRY" and err_code == 40004:
        return "TIMEOUT RETRY"
    if msg == "TOO FREQUENT" and err_code == 40019:
        return "TOO FREQUENT"
    if msg == "USER INFO ERROR" and err_code == 40020:
        return "USER INFO ERROR"
    if err_code == 40001 and "not exist" in msg.lower():
        return "ROLE NOT EXIST"
    if msg == "STOVE_LV ERROR" and err_code == 40006:
        return "STOVE_LV ERROR"
    if msg == "RECHARGE_MONEY ERROR" and err_code == 40017:
        return "RECHARGE_MONEY ERROR"
    if msg == "RECHARGE_MONEY_VIP ERROR" and err_code == 40018:
        return "RECHARGE_MONEY_VIP ERROR"
    if "sign error" in msg.lower():
        return "SIGN ERROR"
    if msg == "NOT LOGIN":
        return "NOT LOGIN"
    return msg


def redeem_once(fid, kid, cdk):
    """One signed redemption POST for fid in kingdom kid."""
    payload = encode_data({
        "fid": fid,
        "cdk": cdk,
        "kid": str(kid),
        "time": str(int(time.time())),
    })
    response = make_request(REDEEM_URL, payload)
    if response is None:
        return TRANSPORT_FAILURE

    try:
        return classify(response.json())
    except ValueError:
        log(f"{fid} - Redemption response was not valid JSON: {response.text[:200]}")
        return "Redemption response invalid JSON"


def redeem_gift_code(fid, kid, cdk, position, total, retry_queue, counters, cooldowns):
    """Redeem for one player, retrying on transient statuses."""
    SESSION.headers.update(get_headers())
    log(f"Processing K{kid}-{fid} [{position}/{total}] for code: {cdk}")

    status = "Processing error"
    for attempt in range(MAX_FID_ATTEMPTS):
        status = redeem_once(fid, kid, cdk)

        if status == "TOO FREQUENT":
            counters["rate_limited"] += 1
            cooldowns[fid] = cooldowns.get(fid, 0) + 1
            if cooldowns[fid] <= MAX_COOLDOWNS:
                retry_queue[fid] = time.time() + TOO_FREQUENT_SLEEP
                log(f"{fid} - Rate limited, retrying in ~{TOO_FREQUENT_SLEEP}s")
                return status, retry_queue
            log(f"{fid} - Still rate limited after {MAX_COOLDOWNS} cooldowns. Giving up on this player.")

        if status == "TIMEOUT RETRY":
            if attempt < MAX_FID_ATTEMPTS - 1:
                log(f"{fid} - [Attempt {attempt+1}/{MAX_FID_ATTEMPTS}] {status}. Retrying...")
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            log(f"{fid} - Max attempts reached after {status}.")
        break

    friendly = RESULT_MESSAGES.get(status, status)
    if status in SUCCESS_STATUSES:
        level = 'success'
    elif status == "RECEIVED":
        level = 'dim'
    elif status in FATAL_STATUSES:
        level = 'warn'
    else:
        level = 'error'
    log(f"{fid} - Result: {friendly}")

    return status, retry_queue

# ─────────────────────────── CSV Parsing ──────────────────────────────
def parse_csv_row(row):
    """One CSV row -> [(fid, kid or None), ...]."""
    fields = [item.strip() for item in row if item.strip()]
    if not fields or fields[0].startswith("#"):
        return []

    if len(fields) == 2 and all(f.isdigit() for f in fields) and int(fields[1]) <= MAX_KINGDOM_ID:
        return [(fields[0], fields[1])]

    return [(f, None) for f in fields if f.isdigit()]


def read_players_from_csv(file_path):
    """Read (fid, kid) pairs from a CSV."""
    encodings_to_try = ['utf-8-sig', 'utf-8', 'latin-1', 'gbk']

    for encoding in encodings_to_try:
        try:
            with open(file_path, mode="r", newline="", encoding=encoding) as file:
                content = file.read()
        except FileNotFoundError:
            raise
        except UnicodeDecodeError:
            continue
        except Exception as e:
            log(f"Error reading {os.path.basename(file_path)} with encoding {encoding}: {e}")
            return []

        if not content.strip():
            log(f"Warning: '{os.path.basename(file_path)}' appears to be empty.")
            return []

        players = []
        ignored = 0
        for row in csv.reader(content.splitlines()):
            parsed = parse_csv_row(row)
            players.extend(parsed)
            if row and not parsed and row[0].strip() and not row[0].strip().startswith("#"):
                ignored += 1

        with_kingdom = sum(1 for _, kid in players if kid)
        log(f"Read {len(players)} player IDs from {os.path.basename(file_path)} "
            f"(encoding: {encoding}, {with_kingdom} with a kingdom)")
        if ignored:
            log(f"Ignored {ignored} non-numeric or malformed rows in {os.path.basename(file_path)}.")
        return players

    log(f"Error: could not decode {os.path.basename(file_path)} with any of: {encodings_to_try}")
    return []


def load_players(csv_files, default_kingdom):
    """Merge every CSV into a deduplicated, kingdom-resolved [(fid, kid), ...]."""
    raw = []
    for csv_file in csv_files:
        try:
            raw.extend(read_players_from_csv(csv_file))
        except FileNotFoundError:
            log(f"Error: CSV file '{os.path.basename(csv_file)}' not found.")
        except Exception as e:
            log(f"Error processing {os.path.basename(csv_file)}: {e}")

    kingdoms = {}
    for fid, kid in raw:
        if kid or fid not in kingdoms:
            kingdoms[fid] = kid or kingdoms.get(fid)

    missing = [fid for fid, kid in kingdoms.items() if not kid]
    if missing and not default_kingdom:
        log(f"Error: {len(missing)} player ID(s) have no kingdom and --kingdom was not given.")
        log("Fix this by passing --kingdom <id>, or by writing the CSV as 'fid,kid' per line.")
        sys.exit(1)

    players = [(fid, kingdoms[fid] or default_kingdom) for fid in sorted(kingdoms, key=int)]
    duplicates = len(raw) - len(players)
    if duplicates > 0:
        log(f"Removed {duplicates} duplicate entries.")
    if missing:
        log(f"{len(missing)} player ID(s) had no kingdom in the CSV; using --kingdom {default_kingdom}.")
    return players

# ─────────────────────── CSV Resolution & Config ───────────────────────
def get_csv_path(args_csv):
    """Determine the CSV path from args or config."""
    if args_csv:
        return args_csv

    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    config = loaded
        except Exception:
            pass

    if "default_csv" in config:
        return config["default_csv"]

    # In CI, just use lod.csv
    return "lod.csv"


def resolve_csv_files(csv_arg):
    """Resolve the --csv argument into a list of CSV file paths."""
    if csv_arg == "*.csv":
        csv_files = glob(os.path.join(SCRIPT_DIR, "*.csv"))
    elif os.path.isdir(csv_arg):
        csv_files = glob(os.path.join(csv_arg, "*.csv"))
    else:
        csv_files = [csv_arg]

    if not csv_files:
        log("Error: No CSV files found.")
        sys.exit(1)

    return csv_files

# ─────────────────────── Redeem All Players ───────────────────────────
def redeem_code_for_all_players(code, csv_files, default_kingdom):
    """Redeem a single gift code for every player using the v2 engine."""
    log(f"\n=== Starting redemption for gift code: {code} ===")

    players = load_players(csv_files, default_kingdom)
    if not players:
        log("Error: No valid player IDs loaded from any CSV file. Skipping.")
        return False

    log(f"Total unique player IDs to process: {len(players)}")

    counters = {
        "success": 0, "already_redeemed": 0, "wrong_kingdom": 0,
        "errors": 0, "rate_limited": 0, "requests": 0,
    }
    error_details = {}
    wrong_kingdom_fids = {}
    cooldowns = {}

    positions = {fid: i + 1 for i, (fid, _) in enumerate(players)}
    retry_queue = {}
    processed_fids = set()
    stop_processing = False
    consecutive_failures = 0
    script_start_time = time.time()

    try:
        while len(processed_fids) < len(players) and not stop_processing:
            now = time.time()
            ready = [(fid, kid) for fid, kid in players
                     if fid not in processed_fids and retry_queue.get(fid, 0) <= now]
            cooling = len(players) - len(processed_fids) - len(ready)

            if not ready:
                if not cooling:
                    break
                next_retry = min(retry_queue[fid] for fid, _ in players
                                 if fid not in processed_fids and fid in retry_queue)
                wait = max(1, min(30, next_retry - now + 1))
                log(f"{cooling} player ID(s) in cooldown. Waiting {int(wait)}s... "
                    f"Progress: {len(processed_fids)}/{len(players)}")
                time.sleep(wait)
                continue

            log(f"\n--- Starting processing cycle. Ready player IDs: {len(ready)} ---")
            for fid, kid in ready:
                status, retry_queue = redeem_gift_code(
                    fid, kid, code, positions[fid], len(players), retry_queue, counters, cooldowns)

                if retry_queue.get(fid, 0) > time.time():
                    continue

                processed_fids.add(fid)

                # Record result
                if status in SUCCESS_STATUSES:
                    counters["success"] += 1
                elif status == "RECEIVED":
                    counters["already_redeemed"] += 1
                elif status == "USER INFO ERROR":
                    counters["wrong_kingdom"] += 1
                    wrong_kingdom_fids[fid] = kid
                elif status not in FATAL_STATUSES:
                    counters["errors"] += 1
                    error_details[fid] = RESULT_MESSAGES.get(status, status)

                if status in FATAL_STATUSES:
                    log(f"\n *** {RESULT_MESSAGES[status]}! Stopping further processing. ***")
                    stop_processing = True
                    break

                consecutive_failures = consecutive_failures + 1 if status == TRANSPORT_FAILURE else 0
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log(f"\n *** {MAX_CONSECUTIVE_FAILURES} players in a row could not reach the API. Stopping. ***")
                    stop_processing = True
                    break

                time.sleep(DELAY + random.uniform(0, 0.5))

            log(f"--- Cycle finished. Total processed: {len(processed_fids)}/{len(players)} ---")
    except KeyboardInterrupt:
        log("\nInterrupted. Re-running is safe and will report them as already redeemed.")

    # Print summary
    execution_time = str(timedelta(seconds=int(time.time() - script_start_time)))
    log("\n" + "=" * 25 + " Redemption Summary " + "=" * 25)
    log(f"Code Redeemed: {code}")
    log(f"Total Unique Player IDs Found: {len(players)}")
    log(f"Total Player IDs Processed: {len(processed_fids)}")
    log(f"Successfully redeemed: {counters['success']}")
    log(f"Already redeemed: {counters['already_redeemed']}")
    log(f"Wrong kingdom: {counters['wrong_kingdom']}")
    log(f"Other Errors/Failures: {counters['errors']}")
    log(f"Rate Limit Events: {counters['rate_limited']}")
    log(f"Total execution time: {execution_time}")
    log("=" * 70)

    # Return False if a fatal status was hit (code is dead)
    return not stop_processing or not any(s in FATAL_STATUSES for s in [status])

# ─────────────────────── Main Entry Point ─────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Kingshot Gift Code Scraper & Redeemer v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python kingshot.py                         # Auto scrape + redeem using saved CSV\n"
            "  python kingshot.py --csv players.csv       # Scrape + redeem new codes\n"
            "  python kingshot.py --scrape-only           # Just check for new codes\n"
            "  python kingshot.py --code SOMECODE         # Redeem a specific code\n"
        ),
    )
    parser.add_argument("--csv", default=None, help="Path to the CSV file with player IDs")
    parser.add_argument("--code", default=None, help="Redeem a specific code instead of scraping")
    parser.add_argument("--kingdom", "--kid", dest="kingdom", type=int, default=1266,
                        help="Kingdom ID for players whose CSV row does not carry one (default: 1266)")
    parser.add_argument("--scrape-only", action="store_true", help="Only scrape for new codes, don't redeem")
    args = parser.parse_args()

    default_kingdom = str(args.kingdom)

    # ── Mode 1: Scrape-only ──
    if args.scrape_only:
        try:
            new_codes, known_codes = find_new_codes()
            if new_codes:
                known_codes.update(new_codes)
                save_known_codes(known_codes)
                log(f"Saved {len(new_codes)} new code(s) to {KNOWN_CODES_FILE}")
        except Exception as e:
            log(f"Error fetching the page: {e}")
            sys.exit(1)
        return

    # ── Mode 2: Manual code redemption ──
    if args.code:
        csv_path = get_csv_path(args.csv)
        csv_files = resolve_csv_files(csv_path)
        redeem_code_for_all_players(args.code, csv_files, default_kingdom)
        return

    # ── Mode 3: Auto — scrape then redeem ──
    csv_path = get_csv_path(args.csv)
    csv_files = resolve_csv_files(csv_path)

    try:
        new_codes, known_codes = find_new_codes()
    except Exception as e:
        log(f"Error fetching the page: {e}")
        sys.exit(1)

    if not new_codes:
        log("Nothing to redeem.")
        return

    for code in new_codes:
        redeem_code_for_all_players(code, csv_files, default_kingdom)
        known_codes.add(code)
        save_known_codes(known_codes)
        log(f"Marked '{code}' as known.\n")


if __name__ == "__main__":
    main()
