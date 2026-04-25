from flask import Flask, jsonify, request
import psycopg2
import psycopg2.extras
import os
import socket
import time

app = Flask(__name__)

def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "db"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "appdb"),
        user=os.getenv("DB_USER", "appuser"),
        password=os.getenv("DB_PASSWORD", "secret")
    )

def init_db():
    retries = 5
    while retries > 0:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id SERIAL PRIMARY KEY,
                    note TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            cur.close()
            conn.close()
            print("Database initialized successfully")
            return
        except psycopg2.OperationalError as e:
            print(f"Database not ready, retrying... ({retries} attempts left)")
            retries -= 1
            time.sleep(2)
    raise Exception("Could not connect to database after multiple retries")

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

@app.route("/notes", methods=["GET"])
def get_notes():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, note, created_at FROM notes ORDER BY created_at DESC")
    notes = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({"notes": [dict(n) for n in notes]})

@app.route("/notes", methods=["POST"])
def add_note():
    note = request.json.get("note", "")
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "INSERT INTO notes (note) VALUES (%s) RETURNING id, note, created_at",
        (note,)
    )
    saved = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return jsonify(dict(saved))

@app.route("/notes/<int:note_id>", methods=["DELETE"])
def delete_note(note_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM notes WHERE id = %s", (note_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"deleted": note_id})

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)