from flask import Flask, jsonify
import requests
import os

app = Flask(__name__)

# backend service name (will come from Docker network)
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")

@app.route("/")
def home():
    return jsonify({
        "message": "Hello from Frontend"
    })

@app.route("/api")
def call_backend():
    try:
        response = requests.get(f"{BACKEND_URL}/api/data", timeout=3)
        return jsonify({
            "frontend": "ok",
            "backend_response": response.json()
        })
    except Exception as e:
        return jsonify({
            "frontend": "error",
            "error": str(e)
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)