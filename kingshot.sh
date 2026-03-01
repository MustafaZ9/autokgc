#!/bin/bash
# Kingshot Gift Code Scraper & Redeemer — Shell Wrapper
# Usage:
#   ./kingshot.sh                        # Auto: scrape + redeem new codes
#   ./kingshot.sh --scrape-only          # Just check for new codes
#   ./kingshot.sh --code SOMECODE        # Redeem a specific code
#   ./kingshot.sh --csv other.csv        # Use a different player IDs file

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Activate virtual environment
source "$SCRIPT_DIR/venv/bin/activate"

# Pass all arguments through to the Python script
python3 "$SCRIPT_DIR/kingshot.py" "$@"

# Deactivate when done
deactivate
