from flask import Flask, jsonify
import socket

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "message": "Hello from Backend",
        "hostname": socket.gethostname()
    })

@app.route("/api/data")
def data():
    return jsonify({
        "data": "This is data from backend service"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)