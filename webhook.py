import os
import subprocess
from flask import Flask, jsonify, request

app = Flask(__name__)

# Basic security token to prevent random internet people from triggering it
# Set an environment variable TRIGGER_TOKEN on your hosting platform
# e.g., TRIGGER_TOKEN=my-secret-token
SECRET_TOKEN = os.environ.get("TRIGGER_TOKEN", "default-insecure-token")

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": " Kingshot Webhook Server is running",
        "message": "Send a GET or POST request to /trigger with the correct token to run the scraper."
    })

@app.route("/trigger", methods=["GET", "POST"])
def trigger_scraper():
    # 1. Simple Authentication
    # Allow token to be passed in header or as an URL parameter (?token=...)
    provided_token = request.headers.get("Authorization")
    if not provided_token:
        provided_token = request.args.get("token")
        
    # If using Bearer token format, strip it
    if provided_token and provided_token.startswith("Bearer "):
        provided_token = provided_token.split(" ")[1]

    if provided_token != SECRET_TOKEN:
        # If token is 'default-insecure-token', let it pass but warn in logs
        if SECRET_TOKEN == "default-insecure-token":
            print("WARNING: Using default insecure token. Please set TRIGGER_TOKEN environment variable.")
        else:
            return jsonify({"error": "Unauthorized. Invalid token."}), 401

    # 2. Run the script securely
    try:
        print("Trigger received! Starting kingshot.py...")
        # Run it non-blocking if you prefer, or blocking so we can return the result
        # Since it's a cron job, blocking is usually fine unless it runs > 30s.
        # We will use subprocess.run to block and return output.
        
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kingshot.py")
        
        # We set timeout to 50 seconds to avoid knocking over typical 60s load balancer timeouts
        result = subprocess.run(
            ["python", script_path],
            capture_output=True,
            text=True,
            timeout=50
        )
        
        output = result.stdout
        error = result.stderr
        
        return jsonify({
            "status": "success",
            "message": "Scraper executed.",
            "output": output,
            "error": error if error else None
        }), 200

    except subprocess.TimeoutExpired:
        return jsonify({
            "status": "partial_success",
            "message": "Scraper started but took too long to finish. It may still be running in the background."
        }), 202
        
    except Exception as e:
        print(f"Error running scraper: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == "__main__":
    # Get port from environment variable or default to 5000
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
