from flask import Flask, jsonify, request
import socket
import os

app = Flask(__name__)

NOTES_FILE = "/app/data/notes.txt"

@app.route("/")
def home():
    return jsonify({
        "message": "Hello from Backend - instant changes",
        "hostname": socket.gethostname()
    })

@app.route("/api/data")
def data():
    return jsonify({
        "data": "This is data from backend service"
    })

@app.route("/notes", methods=["GET"])
def get_notes():
    if not os.path.exists(NOTES_FILE):
        return jsonify({"notes": []})
    with open(NOTES_FILE) as f:
        notes = f.read().splitlines()
    return jsonify({"notes": notes})

@app.route("/notes", methods=["POST"])
def add_note():
    os.makedirs(os.path.dirname(NOTES_FILE), exist_ok=True)
    note = request.json.get("note", "")
    with open(NOTES_FILE, "a") as f:
        f.write(note + "\n")
    return jsonify({"saved": note})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)