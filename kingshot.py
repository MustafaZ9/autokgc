#!/usr/bin/env python3
"""
Kingshot Gift Code Scraper & Redeemer — Unified Script
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
import hashlib
import argparse
import requests
from glob import glob
from datetime import datetime
from bs4 import BeautifulSoup


# ─────────────────────────── Configuration ────────────────────────────

SCRAPE_URL = "https://kingshot.net/gift-codes"
LOGIN_URL = "https://kingshot-giftcode.centurygame.com/api/player"
REDEEM_URL = "https://kingshot-giftcode.centurygame.com/api/gift_code"
WOS_ENCRYPT_KEY = "mN4!pQs6JrYwV9"

DELAY = 1          # Seconds between each redemption
RETRY_DELAY = 2    # Seconds between retries
MAX_RETRIES = 3    # Max retry attempts per request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWN_CODES_FILE = os.path.join(SCRIPT_DIR, "known_codes.txt")
LOG_FILE = os.path.join(SCRIPT_DIR, "redeemed_codes.txt")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

RESULT_MESSAGES = {
    "SUCCESS": "Successfully redeemed",
    "RECEIVED": "Already redeemed",
    "SAME TYPE EXCHANGE": "Successfully redeemed (same type)",
    "TIME ERROR": "Code has expired",
    "TIMEOUT RETRY": "Server requested retry",
    "USED": "Claim limit reached, unable to claim",
}

counters = {
    "success": 0,
    "already_redeemed": 0,
    "errors": 0,
}


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
        with open(LOG_FILE, "a", encoding="utf-8-sig") as f:
            f.write(log_entry + "\n")
    except Exception as e:
        print(f"{timestamp} - LOGGING ERROR: Could not write to {LOG_FILE}. Error: {e}")


# ─────────────────────── Scraper Functions ────────────────────────────

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
    try:
        from curl_cffi import requests as cffi_requests
        # Impersonate chrome to bypass Cloudflare 403 blocks from datacenter IPs
        response = cffi_requests.get(SCRAPE_URL, impersonate="chrome120")
    except ImportError:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        response = requests.get(SCRAPE_URL, headers=headers)
        
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    code_elements = soup.find_all("p", class_="font-mono text-xl font-bold tracking-wider")
    return [el.text.strip() for el in code_elements]


def find_new_codes():
    """
    Scrape the website and compare against known codes.
    Returns (new_codes_list, updated_known_codes_set).
    """
    log(f"Checking {SCRAPE_URL} for new gift codes...")

    current_codes = scrape_gift_codes()
    known_codes = load_known_codes()
    new_codes = [c for c in current_codes if c not in known_codes]

    if new_codes:
        log(f"\n🎉 NEW GIFT CODES FOUND: {', '.join(new_codes)}")
    else:
        log("No new gift codes found. All codes have been seen previously.")

    # Return both so the caller can decide when to save
    return new_codes, known_codes


# ─────────────────────── Redeemer Functions ───────────────────────────

def encode_data(data):
    """Generate the signed payload (MD5 hash)."""
    sorted_keys = sorted(data.keys())
    encoded_data = "&".join(
        f"{key}={json.dumps(data[key]) if isinstance(data[key], dict) else data[key]}"
        for key in sorted_keys
    )
    sign = hashlib.md5(f"{encoded_data}{WOS_ENCRYPT_KEY}".encode()).hexdigest()
    return {"sign": sign, **data}


def make_request(url, payload):
    """Send a POST request with automatic retry logic."""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(url, json=payload)

            if response.status_code == 200:
                response_data = response.json()
                msg_content = response_data.get("msg", "")
                if isinstance(msg_content, str) and msg_content.strip(".") == "TIMEOUT RETRY":
                    if attempt < MAX_RETRIES - 1:
                        log(f"Attempt {attempt+1}: Server requested retry for FID: {payload.get('fid', 'N/A')}")
                        time.sleep(RETRY_DELAY)
                        continue
                    else:
                        log(f"Attempt {attempt+1}: Max retries reached for FID: {payload.get('fid', 'N/A')}")
                        return response
                return response

            log(f"Attempt {attempt+1} failed for FID {payload.get('fid', 'N/A')}: HTTP {response.status_code}")

        except requests.exceptions.RequestException as e:
            log(f"Attempt {attempt+1} failed for FID {payload.get('fid', 'N/A')}: {e}")
        except json.JSONDecodeError as e:
            log(f"Attempt {attempt+1} failed for FID {payload.get('fid', 'N/A')}: JSONDecodeError: {e}")

        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)

    log(f"All {MAX_RETRIES} attempts failed for FID {payload.get('fid', 'N/A')}.")
    return None


def redeem_gift_code(fid, cdk):
    """Login and redeem a single gift code for one player ID."""
    if not str(fid).strip().isdigit():
        log(f"Skipping invalid FID: '{fid}'")
        return {"msg": "Invalid FID format"}
    fid = str(fid).strip()

    try:
        # Login
        login_payload = encode_data({"fid": fid, "time": int(time.time() * 1000)})
        login_resp = make_request(LOGIN_URL, login_payload)

        if not login_resp:
            return {"msg": "Login request failed after retries"}

        try:
            login_data = login_resp.json()
            if login_data.get("code") != 0:
                login_msg = login_data.get("msg", "Unknown login error")
                log(f"Login failed for {fid}: {login_msg}")
                return {"msg": f"Login failed: {login_msg}"}

            nickname = login_data.get("data", {}).get("nickname")
            log(f"Processing {nickname or 'Unknown Player'} ({fid})")

        except json.JSONDecodeError:
            log(f"Login response for {fid} was not valid JSON: {login_resp.text[:200]}")
            return {"msg": "Login response invalid JSON"}

        # Redeem
        redeem_payload = encode_data({
            "fid": fid,
            "cdk": cdk,
            "time": int(time.time() * 1000),
        })
        redeem_resp = make_request(REDEEM_URL, redeem_payload)

        if not redeem_resp:
            return {"msg": "Redemption request failed after retries"}

        try:
            return redeem_resp.json()
        except json.JSONDecodeError:
            log(f"Redemption response for {fid} was not valid JSON: {redeem_resp.text[:200]}")
            return {"msg": "Redemption response invalid JSON"}

    except Exception as e:
        log(f"Unexpected error during redemption for {fid}: {e}")
        return {"msg": f"Unexpected Error: {e}"}


def read_player_ids_from_csv(file_path):
    """Read player IDs from a CSV file (one per line or comma-separated)."""
    player_ids = []
    try:
        with open(file_path, mode="r", newline="", encoding="utf-8-sig") as file:
            sample = "".join(file.readline() for _ in range(5))
            fmt = "comma-separated" if "," in sample else "newline"
            file.seek(0)

            log(f"Reading {file_path} (detected format: {fmt})")
            reader = csv.reader(file)
            for row in reader:
                for item in row:
                    fid = item.strip()
                    if fid:
                        player_ids.append(fid)
    except FileNotFoundError:
        raise
    except Exception as e:
        log(f"Error reading CSV file {file_path}: {e}")
        return []

    return player_ids


def redeem_code_for_all_players(code, csv_files):
    """Redeem a single gift code for every player in the given CSV files."""
    log(f"\n=== Starting redemption for gift code: {code} ===")

    for csv_file in csv_files:
        try:
            player_ids = read_player_ids_from_csv(csv_file)
            log(f"Loaded {len(player_ids)} player IDs from {csv_file}")

            for fid in player_ids:
                result = redeem_gift_code(fid, code)

                raw_msg = result.get("msg", "Unknown error").strip(".")
                friendly_msg = RESULT_MESSAGES.get(raw_msg, raw_msg)

                # Exit immediately if code is expired or claim limit reached
                if raw_msg == "TIME ERROR":
                    log("Code has expired! Stopping this code.")
                    return False
                elif raw_msg == "USED":
                    log("Claim limit reached! Stopping this code.")
                    return False

                # Update counters
                if raw_msg in ("SUCCESS", "SAME TYPE EXCHANGE"):
                    counters["success"] += 1
                elif raw_msg == "RECEIVED":
                    counters["already_redeemed"] += 1
                elif raw_msg != "TIMEOUT RETRY":
                    counters["errors"] += 1

                log(f"Result: {friendly_msg}")
                time.sleep(DELAY)

        except FileNotFoundError:
            log(f"Error: CSV file '{csv_file}' not found")
        except Exception as e:
            log(f"Error processing {csv_file}: {e}")

    return True


def print_summary():
    """Print the final redemption summary."""
    log("\n=== Redemption Complete ===")
    log(f"Successfully redeemed: {counters['success']}")
    log(f"Already redeemed: {counters['already_redeemed']}")
    log(f"Errors/Failures: {counters['errors']}")


# ─────────────────────── CSV Resolution & Config ───────────────────────────────

def get_csv_path(args_csv):
    """Determine the CSV path, prompting the user on the first run if necessary."""
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

    log("\n--- First Time Setup ---")
    log("Welcome! You haven't specified a CSV file containing your player IDs.")
    
    # Check if there are any CSV files in the current folder to suggest
    local_csvs = glob(os.path.join(SCRIPT_DIR, "*.csv"))
    if local_csvs:
        suggestions = ", ".join(os.path.basename(c) for c in local_csvs)
        log(f"Found these CSV files nearby: {suggestions}")

    csv_path = input("Please enter the path to your CSV file (e.g., lod.csv or *.csv): ").strip()
    
    while not csv_path:
        csv_path = input("Path cannot be empty. Please enter the path: ").strip()

    config["default_csv"] = csv_path
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=4)
        log(f"Saved '{csv_path}' as your default CSV file in config.json.\n")
    except Exception as e:
        log(f"Warning: Could not save config.json: {e}\n")

    return csv_path


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


# ─────────────────────── Main Entry Point ─────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Kingshot Gift Code Scraper & Redeemer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python kingshot.py                         # Auto scrape + redeem using saved CSV\n"
            "  python kingshot.py --csv players.csv       # Scrape + redeem new codes\n"
            "  python kingshot.py --scrape-only           # Just check for new codes\n"
            "  python kingshot.py --code SOMECODE         # Redeem a specific code\n"
        ),
    )
    parser.add_argument("--csv", default=None, help="Path to the CSV file with player IDs (if omitted, uses config or prompts)")
    parser.add_argument("--code", default=None, help="Redeem a specific code instead of scraping")
    parser.add_argument("--scrape-only", action="store_true", help="Only scrape for new codes, don't redeem")
    args = parser.parse_args()

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
        redeem_code_for_all_players(args.code, csv_files)
        print_summary()
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
        redeem_code_for_all_players(code, csv_files)
        # Mark this code as known after attempting redemption
        known_codes.add(code)
        save_known_codes(known_codes)
        log(f"Marked '{code}' as known.\n")

    print_summary()


if __name__ == "__main__":
    main()
