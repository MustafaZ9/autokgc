#!/bin/bash
# Kingshot Gift Code Scraper & Redeemer — Shell Wrapper
# Usage:
#   ./kingshot.sh                        # Auto: scrape + redeem new codes
#   ./kingshot.sh --scrape-only          # Just check for new codes
#   ./kingshot.sh --code SOMECODE        # Redeem a specific code
#   ./kingshot.sh --csv other.csv        # Use a different player IDs file

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Pass all arguments through to the Python script using the venv explicitly
"$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/kingshot.py" "$@"
