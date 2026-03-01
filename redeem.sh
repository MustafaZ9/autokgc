#!/bin/bash

# 1. Check if the user forgot to type the code
if [ -z "$1" ]; then
  echo "❌ Error: Missing gift code."
  echo "Usage: ./redeem.sh <YOUR_CODE>"
  exit 1
fi

# 2. Activate the virtual environment
# This assumes the 'venv' folder is in the same directory as this script
source venv/bin/activate

# 3. Run the python script
# Using "$1" passes the first word you type after the script name as the code
echo "🚀 Redeeming code: $1..."
python redeem_codes.py --csv lod.csv --code "$1"

# 4. Deactivate the venv when done (optional, keeps your terminal clean)
deactivate
