#!/usr/bin/env python3
# Kingshot Gift Code Redeemer Script Version 1.0.0
# See https://github.com/justncodes/ks-giftcode

import os
import requests
import time
import hashlib
import json
import csv
import argparse
import sys
from datetime import datetime
from glob import glob

# Configuration
REDEEM_URL = "https://kingshot-giftcode.centurygame.com/api/gift_code"
WOS_ENCRYPT_KEY = "mN4!pQs6JrYwV9"  # The secret key

DELAY = 1 # Seconds between each redemption, less than 1s may result in being blocked
MAX_KINGDOM_ID = 999999  # A CSV field this small is a kingdom, never a player ID
RETRY_DELAY = 2  # Seconds between retries
MAX_RETRIES = 3  # Max retry attempts per request

script_dir = os.path.dirname(os.path.abspath(__file__)) # store log in same directory as script
LOG_FILE = os.path.join(script_dir, "redeemed_codes.txt")

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

# Log messages to file and console
def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp} - {message}"

    try:
        print(log_entry)
    except UnicodeEncodeError:
        cleaned = log_entry.encode('utf-8', errors='replace').decode('ascii', errors='replace')
        print(cleaned)

    try:
        with open(LOG_FILE, "a", encoding="utf-8-sig") as f:
            f.write(log_entry + "\n")
    except Exception as e:
        print(f"{timestamp} - LOGGING ERROR: Could not write to {LOG_FILE}. Error: {e}")
        print(f"{timestamp} - ORIGINAL MESSAGE: {log_entry}")

# Generate the sign, an MD5 hash sent with the POST payload
def encode_data(data):
    secret = WOS_ENCRYPT_KEY
    sorted_keys = sorted(data.keys())

    encoded_data = "&".join(
        [
            f"{key}={json.dumps(data[key]) if isinstance(data[key], dict) else data[key]}"
            for key in sorted_keys
        ]
    )

    return {"sign": hashlib.md5(f"{encoded_data}{secret}".encode()).hexdigest(), **data}

# Send POST and handle retries if failed
def make_request(url, payload):
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(url, json=payload)

            if response.status_code == 200:
                response_data = response.json()
                msg_content = response_data.get("msg", "")
                if isinstance(msg_content, str) and msg_content.strip('.') == "TIMEOUT RETRY":
                    if attempt < MAX_RETRIES - 1:
                        log(f"Attempt {attempt+1}: Server requested retry for payload: {payload.get('fid', 'N/A')}")
                        time.sleep(RETRY_DELAY)
                        continue
                    else:
                        log(f"Attempt {attempt+1}: Max retries reached after server requested retry for payload: {payload.get('fid', 'N/A')}")
                        return response

                return response

            log(f"Attempt {attempt+1} failed for FID {payload.get('fid', 'N/A')}: HTTP {response.status_code}, Response: {response.text[:200]}")

        except requests.exceptions.RequestException as e:
            log(f"Attempt {attempt+1} failed for FID {payload.get('fid', 'N/A')}: RequestException: {str(e)}")
        except json.JSONDecodeError as e:
             log(f"Attempt {attempt+1} failed for FID {payload.get('fid', 'N/A')}: JSONDecodeError: {str(e)}. Response text: {response.text[:200]}")

        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)

    log(f"All {MAX_RETRIES} attempts failed for request to {url} with FID {payload.get('fid', 'N/A')}.")
    return None

# Redeem a gift code for a player and return the response
def redeem_gift_code(fid, kid, cdk):
    if not str(fid).strip().isdigit():
        log(f"Skipping invalid FID: '{fid}'")
        return {"msg": "Invalid FID format"}
    fid = str(fid).strip()

    try:
        log(f"Processing K{kid}-{fid}")

        # === Redeem Request (now includes kid) ===
        redeem_payload = encode_data({
            "fid": fid,
            "cdk": cdk,
            "kid": str(kid),
            "time": str(int(time.time()))
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
        log(f"Unexpected error during redemption for {fid}: {str(e)}")
        return {"msg": f"Unexpected Error: {str(e)}"}

# Parse a single CSV row into (fid, kid) pairs
def parse_csv_row(row):
    """One CSV row -> [(fid, kid or None), ...].

    A two-field row whose second field is small enough to be a kingdom is a
    `fid,kid` pair; anything else is a plain list of player IDs.
    """
    fields = [item.strip() for item in row if item.strip()]
    if not fields or fields[0].startswith("#"):
        return []

    if len(fields) == 2 and all(f.isdigit() for f in fields) and int(fields[1]) <= MAX_KINGDOM_ID:
        return [(fields[0], fields[1])]

    return [(f, None) for f in fields if f.isdigit()]

# Read player IDs (and optional kingdoms) from a CSV file
def read_player_ids_from_csv(file_path):
    """
    Reads player IDs from a CSV file.
    Supports:
      - One FID per line
      - Comma-separated FIDs
      - fid,kid pairs (second column is kingdom if <= MAX_KINGDOM_ID)
    Strips whitespace from each ID and ignores empty entries.
    """
    players = []
    try:
        # Using utf-8-sig to handle potential BOM (Byte Order Mark)
        with open(file_path, mode="r", newline="", encoding="utf-8-sig") as file:
            log(f"Reading {file_path}")
            reader = csv.reader(file)
            ignored = 0
            for row in reader:
                parsed = parse_csv_row(row)
                players.extend(parsed)
                if row and not parsed and row[0].strip() and not row[0].strip().startswith("#"):
                    ignored += 1

            with_kingdom = sum(1 for _, kid in players if kid)
            log(f"Read {len(players)} player IDs from {file_path} ({with_kingdom} with a kingdom)")
            if ignored:
                log(f"Warning: Ignored {ignored} non-numeric or malformed rows in {file_path}")

    except FileNotFoundError:
        raise
    except Exception as e:
        log(f"Error reading or processing CSV file {file_path}: {str(e)}")
        return [] # Return empty list on other read errors, allowing script to continue

    return players

# Print summary of actions
def print_summary():
    log("\n=== Redemption Complete ===")
    log(f"Successfully redeemed: {counters['success']}")
    log(f"Already redeemed: {counters['already_redeemed']}")
    log(f"Errors/Failures: {counters['errors']}")

# Main script
if __name__ == "__main__":
    # Set up command-line argument parsing
    parser = argparse.ArgumentParser(description="Redeem gift codes for player IDs from a CSV file.")
    parser.add_argument("--csv", required=True, help="Path to the CSV file containing player IDs (or *.csv for all files in a folder).")
    parser.add_argument("--code", required=True, help="The gift code to redeem.")
    parser.add_argument("--kingdom", "--kid", dest="kingdom", type=int,
                        help="Default kingdom ID for players whose CSV row does not carry one.")
    args = parser.parse_args()

    default_kingdom = str(args.kingdom) if args.kingdom is not None else None

    # Log initialization message
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(f"\n=== Starting redemption for gift code: {args.code} at {start_time} ===")
    if args.kingdom is not None:
        log(f"Default Kingdom: {args.kingdom}")

    # Handle *.csv input
    if args.csv == "*.csv":
        # Use the script's directory if no folder is specified
        csv_files = glob(os.path.join(script_dir, "*.csv"))
    else:
        # Use the specified folder or file
        if os.path.isdir(args.csv):
            csv_files = glob(os.path.join(args.csv, "*.csv"))
        else:
            csv_files = [args.csv]

    if not csv_files:
        log("Error: No CSV files found.")
        sys.exit(1)

    # Process all CSV files
    for csv_file in csv_files:
        try:
            players = read_player_ids_from_csv(csv_file)
            log(f"Loaded {len(players)} player entries from {csv_file}")

            # Resolve kingdoms: use CSV-provided kid, fall back to --kingdom
            missing_kingdom = [fid for fid, kid in players if not kid]
            if missing_kingdom and not default_kingdom:
                log(f"Error: {len(missing_kingdom)} player ID(s) have no kingdom and --kingdom was not given.")
                log("Fix this by passing --kingdom <id>, or by writing the CSV as 'fid,kid' per line.")
                sys.exit(1)

            # Redeem gift code for each player
            for fid, kid in players:
                kid = kid or default_kingdom
                result = redeem_gift_code(fid, kid, args.code)

                raw_msg = result.get('msg', 'Unknown error').strip('.')
                friendly_msg = RESULT_MESSAGES.get(raw_msg, raw_msg)

                # Exit immediately if code is expired or claim limit reached
                if raw_msg == "TIME ERROR":
                    log("Code has expired! Script will now exit.")
                    print_summary()
                    sys.exit(1)
                elif raw_msg == "USED":
                    log("Claim limit reached! Script will now exit.")
                    print_summary()
                    sys.exit(1)

                # Update counters based on result
                if raw_msg in ["SUCCESS", "SAME TYPE EXCHANGE"]:
                    counters["success"] += 1
                elif raw_msg == "RECEIVED":
                    counters["already_redeemed"] += 1
                elif raw_msg == "TIMEOUT RETRY":
                    pass
                else:
                    counters["errors"] += 1

                log(f"Result: {friendly_msg}")
                time.sleep(DELAY)

        except FileNotFoundError:
            log(f"Error: CSV file '{csv_file}' not found")
        except Exception as e:
            log(f"Error processing {csv_file}: {str(e)}")

    # Print final summary
    print_summary()
