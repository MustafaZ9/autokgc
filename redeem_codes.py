#!/usr/bin/env python3
# Kingshot Gift Code Redeemer Script v2.0
# Standalone redeemer — pass --code and --csv to redeem for all players.
# See https://github.com/justncodes/ks-giftcode

import argparse
import csv
import hashlib
import os
import random
import sys
import time
from datetime import datetime, timedelta
from glob import glob
import requests

BASE_URL = "https://kingshot-giftcode.centurygame.com"
REDEEM_URL = BASE_URL + "/api/gift_code"
ORIGIN = BASE_URL
KS_ENCRYPT_KEY = "mN4!pQs6JrYwV9"

DELAY = 1.0
RETRY_DELAY = 2
MAX_RETRIES = 3
MAX_FID_ATTEMPTS = 3
TOO_FREQUENT_SLEEP = 60
MAX_COOLDOWNS = 3
MAX_CONSECUTIVE_FAILURES = 10
MAX_KINGDOM_ID = 999999

TRANSPORT_FAILURE = "Redemption request failed"

script_dir = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(script_dir, "redeemed_codes.txt")

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
    "RECHARGE_MONEY ERROR": "Not enough spending for this code",
    "RECHARGE_MONEY_VIP ERROR": "VIP level too low for this code",
    "SIGN ERROR": "Sign error (request encoding issue)",
    "NOT LOGIN": "Session rejected by the server",
}

FATAL_STATUSES = ("TIME ERROR", "CDK NOT FOUND", "USED")
SUCCESS_STATUSES = ("SUCCESS", "SAME TYPE EXCHANGE")

counters = {
    "success": 0, "already_redeemed": 0, "wrong_kingdom": 0,
    "errors": 0, "rate_limited": 0, "requests": 0,
}

error_details = {}
wrong_kingdom_fids = {}
cooldowns = {}
script_start_time = time.time()

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


def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp} - {message}"
    try:
        print(log_entry)
    except UnicodeEncodeError:
        print(log_entry.encode('utf-8', errors='replace').decode('ascii', errors='replace'))
    try:
        with open(LOG_FILE, "a", encoding="utf-8-sig", newline='') as f:
            f.write(log_entry + "\n")
    except Exception as e:
        print(f"LOGGING ERROR to file {LOG_FILE}: {e}")


def get_headers():
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
    sorted_keys = sorted(data.keys())
    encoded = "&".join(f"{key}={data[key]}" for key in sorted_keys)
    return {"sign": hashlib.md5(f"{encoded}{KS_ENCRYPT_KEY}".encode()).hexdigest(), **data}


SESSION = requests.Session()


def make_request(url, payload):
    endpoint = url.split('/')[-1]
    for attempt in range(MAX_RETRIES):
        try:
            counters["requests"] += 1
            response = SESSION.post(url, data=payload, timeout=(10, 30))
            if response.status_code == 200:
                return response
            if response.status_code == 429:
                log(f"Attempt {attempt+1} to {endpoint}: HTTP 429. Backing off...")
                time.sleep(RETRY_DELAY * (attempt + 1) * 2)
                continue
            if response.status_code in (502, 503, 504):
                log(f"Attempt {attempt+1} to {endpoint}: HTTP {response.status_code}. Retrying...")
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
    msg = str(data.get("msg", "Unknown error")).strip('.')
    err_code = data.get("err_code")
    if msg == "SUCCESS": return "SUCCESS"
    if msg == "RECEIVED" and err_code == 40008: return "RECEIVED"
    if msg == "SAME TYPE EXCHANGE" and err_code == 40011: return "SAME TYPE EXCHANGE"
    if msg == "TIME ERROR" and err_code == 40007: return "TIME ERROR"
    if msg == "CDK NOT FOUND" and err_code == 40014: return "CDK NOT FOUND"
    if msg == "USED" and err_code == 40005: return "USED"
    if msg == "TIMEOUT RETRY" and err_code == 40004: return "TIMEOUT RETRY"
    if msg == "TOO FREQUENT" and err_code == 40019: return "TOO FREQUENT"
    if msg == "USER INFO ERROR" and err_code == 40020: return "USER INFO ERROR"
    if err_code == 40001 and "not exist" in msg.lower(): return "ROLE NOT EXIST"
    if msg == "STOVE_LV ERROR" and err_code == 40006: return "STOVE_LV ERROR"
    if msg == "RECHARGE_MONEY ERROR" and err_code == 40017: return "RECHARGE_MONEY ERROR"
    if msg == "RECHARGE_MONEY_VIP ERROR" and err_code == 40018: return "RECHARGE_MONEY_VIP ERROR"
    if "sign error" in msg.lower(): return "SIGN ERROR"
    if msg == "NOT LOGIN": return "NOT LOGIN"
    return msg


def redeem_once(fid, kid, cdk):
    payload = encode_data({
        "fid": fid, "cdk": cdk, "kid": str(kid), "time": str(int(time.time())),
    })
    response = make_request(REDEEM_URL, payload)
    if response is None:
        return TRANSPORT_FAILURE
    try:
        return classify(response.json())
    except ValueError:
        log(f"{fid} - Redemption response was not valid JSON: {response.text[:200]}")
        return "Redemption response invalid JSON"


def redeem_gift_code(fid, kid, cdk, position, total, retry_queue):
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
            log(f"{fid} - Still rate limited after {MAX_COOLDOWNS} cooldowns. Giving up.")
        if status == "TIMEOUT RETRY":
            if attempt < MAX_FID_ATTEMPTS - 1:
                log(f"{fid} - [Attempt {attempt+1}/{MAX_FID_ATTEMPTS}] {status}. Retrying...")
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            log(f"{fid} - Max attempts reached after {status}.")
        break
    friendly = RESULT_MESSAGES.get(status, status)
    log(f"{fid} - Result: {friendly}")
    return status, retry_queue


def parse_csv_row(row):
    fields = [item.strip() for item in row if item.strip()]
    if not fields or fields[0].startswith("#"):
        return []
    if len(fields) == 2 and all(f.isdigit() for f in fields) and int(fields[1]) <= MAX_KINGDOM_ID:
        return [(fields[0], fields[1])]
    return [(f, None) for f in fields if f.isdigit()]


def read_players_from_csv(file_path):
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


def collect_csv_files(csv_input_spec):
    if csv_input_spec is None:
        log("No --csv given. Using every *.csv in the current directory.")
        return sorted(glob(os.path.join(".", "*.csv")))
    if os.path.isdir(csv_input_spec):
        return sorted(glob(os.path.join(csv_input_spec, "*.csv")))
    if "*" in os.path.basename(csv_input_spec) or "?" in os.path.basename(csv_input_spec):
        return sorted(glob(csv_input_spec))
    if os.path.isfile(csv_input_spec):
        return [csv_input_spec]
    log(f"Error: '{csv_input_spec}' does not exist.")
    sys.exit(1)


def load_players(csv_files, default_kingdom):
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
        sys.exit(1)
    players = [(fid, kingdoms[fid] or default_kingdom) for fid in sorted(kingdoms, key=int)]
    duplicates = len(raw) - len(players)
    if duplicates > 0:
        log(f"Removed {duplicates} duplicate entries.")
    return players


def record_result(fid, kid, status):
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


def print_summary(code, players, processed_fids):
    execution_time = str(timedelta(seconds=int(time.time() - script_start_time)))
    log("\n" + "=" * 25 + " Redemption Summary " + "=" * 25)
    log(f"Code Redeemed: {code}")
    log(f"Total Unique Player IDs Found: {len(players)}")
    log(f"Total Player IDs Processed: {len(processed_fids)}")
    remaining = len(players) - len(processed_fids)
    if remaining > 0:
        log(f"Player IDs Not Processed: {remaining}")
    log(f"\nSuccessfully redeemed: {counters['success']}")
    log(f"Already redeemed: {counters['already_redeemed']}")
    log(f"Wrong kingdom: {counters['wrong_kingdom']}")
    log(f"Other Errors/Failures: {counters['errors']}")
    log(f"Rate Limit Events: {counters['rate_limited']}")
    log(f"Total API Requests: {counters['requests']}")
    if wrong_kingdom_fids:
        log("\n--- Wrong Kingdom ---")
        for fid, kid in sorted(wrong_kingdom_fids.items(), key=lambda item: int(item[0])):
            log(f"  {fid}: kingdom {kid} was rejected")
    if error_details:
        log("\n--- Error Details ---")
        for i, (fid, msg) in enumerate(sorted(error_details.items(), key=lambda item: int(item[0]))):
            if i >= 20:
                log(f"  ... (and {len(error_details) - 20} more errors)")
                break
            log(f"  {fid}: {msg}")
    log(f"\nTotal execution time: {execution_time}")
    log("=" * 70)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Redeem a Kingshot gift code for player IDs listed in CSV file(s).")
    parser.add_argument('--code', type=str, required=True, help='The gift code to redeem')
    parser.add_argument('--csv', type=str, help='Path to a CSV file, directory, or *.csv pattern')
    parser.add_argument('--kingdom', '--kid', dest='kingdom', type=int, default=1266,
                        help='Kingdom ID for players without one in CSV (default: 1266)')
    parser.add_argument('--delay', type=float, default=DELAY,
                        help=f'Seconds between players (default: {DELAY})')
    return parser.parse_args()


def main():
    args = parse_args()
    args.delay = max(0.0, args.delay)
    default_kingdom = str(args.kingdom)

    log(f"\n=== Starting redemption script at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    log(f"Gift Code: {args.code}")
    log(f"Default Kingdom: {args.kingdom}")

    csv_files = collect_csv_files(args.csv)
    if not csv_files:
        log("Error: No CSV files found.")
        sys.exit(1)
    log(f"Found {len(csv_files)} CSV file(s): {', '.join(os.path.basename(f) for f in csv_files)}")

    players = load_players(csv_files, default_kingdom)
    if not players:
        log("Error: No valid player IDs loaded. Exiting.")
        sys.exit(1)
    log(f"Total unique player IDs to process: {len(players)}")

    positions = {fid: i + 1 for i, (fid, _) in enumerate(players)}
    retry_queue = {}
    processed_fids = set()
    stop_processing = False
    consecutive_failures = 0

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
                log(f"{cooling} player(s) in cooldown. Waiting {int(wait)}s... "
                    f"Progress: {len(processed_fids)}/{len(players)}")
                time.sleep(wait)
                continue
            log(f"\n--- Processing cycle. Ready: {len(ready)} ---")
            for fid, kid in ready:
                status, retry_queue = redeem_gift_code(
                    fid, kid, args.code, positions[fid], len(players), retry_queue)
                if retry_queue.get(fid, 0) > time.time():
                    continue
                processed_fids.add(fid)
                record_result(fid, kid, status)
                if status in FATAL_STATUSES:
                    log(f"\n *** {RESULT_MESSAGES[status]}! Stopping. ***")
                    stop_processing = True
                    break
                consecutive_failures = consecutive_failures + 1 if status == TRANSPORT_FAILURE else 0
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log(f"\n *** {MAX_CONSECUTIVE_FAILURES} consecutive API failures. Stopping. ***")
                    stop_processing = True
                    break
                time.sleep(args.delay + random.uniform(0, 0.5))
            log(f"--- Cycle finished. Processed: {len(processed_fids)}/{len(players)} ---")
    except KeyboardInterrupt:
        log("\nInterrupted. Re-running is safe.")

    if not stop_processing and len(processed_fids) == len(players):
        log(f"\nAll {len(processed_fids)} player IDs processed.")
    print_summary(args.code, players, processed_fids)


if __name__ == "__main__":
    main()
    sys.exit(0)
