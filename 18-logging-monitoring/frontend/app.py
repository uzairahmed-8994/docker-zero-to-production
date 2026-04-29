from flask import Flask, jsonify, request
import requests
import os
import logging
import time

app = Flask(__name__)

# ── Logging Setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)

logger.info("Frontend application starting...")

# ── Configuration ──────────────────────────────────────────────────────────
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")

# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return jsonify({
        "message": "Hello from Frontend"
    })

@app.route("/api")
def call_backend():
    start = time.time()
    logger.info(f"Calling backend: {BACKEND_URL}/api/data")

    try:
        response = requests.get(f"{BACKEND_URL}/api/data", timeout=3)
        duration = round((time.time() - start) * 1000, 2)

        logger.info(f"Backend responded with status={response.status_code} in {duration}ms")

        return jsonify({
            "frontend": "ok",
            "backend_response": response.json()
        })

    except requests.exceptions.Timeout:
        logger.error("Backend request timed out")
        return jsonify({
            "frontend": "error",
            "error": "backend timeout"
        }), 504

    except Exception as e:
        logger.exception(f"Backend request failed: {e}")
        return jsonify({
            "frontend": "error",
            "error": "internal error"
        }), 500

# ── Global Error Handler ───────────────────────────────────────────────────

@app.errorhandler(Exception)
def handle_exception(e):
    logger.exception(f"Unhandled exception on {request.method} {request.path}")
    return jsonify({"error": "internal server error"}), 500

# ── Entry Point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)

