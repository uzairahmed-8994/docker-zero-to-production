# top of file
# raise Exception("crash on startup")

from flask import Flask, jsonify, request
import psycopg2
import psycopg2.extras
import os
import socket
import time
import logging

app = Flask(__name__)

# ── Logging Setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)

logger.info("Backend application starting...")

# ── Database Connection ────────────────────────────────────────────────────
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
            logger.info("Database initialized successfully")
            return
        except psycopg2.OperationalError as e:
            logger.warning(f"Database not ready, retrying... ({retries} attempts left): {e}")
            retries -= 1
            time.sleep(2)
    raise Exception("Could not connect to database after multiple retries")

# Called at import time — runs whether started by 'python app.py' or gunicorn
init_db()

# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200
    
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
    logger.info("Fetching all notes")
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, note, created_at FROM notes ORDER BY created_at DESC")
    notes = cur.fetchall()
    cur.close()
    conn.close()
    logger.info(f"Returned {len(notes)} notes")
    return jsonify({"notes": [dict(n) for n in notes]})

@app.route("/notes", methods=["POST"])
def add_note():
    payload = request.json or {}
    note = payload.get("note", "")

    logger.info(f"Creating note: {note[:50]}")

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

    logger.info(f"Created note with id={saved['id']}")
    return jsonify(dict(saved))

@app.route("/notes/<int:note_id>", methods=["DELETE"])
def delete_note(note_id):
    logger.info(f"Deleting note id={note_id}")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM notes WHERE id = %s", (note_id,))
    conn.commit()
    cur.close()
    conn.close()

    logger.info(f"Deleted note id={note_id}")
    return jsonify({"deleted": note_id})

# ── Global Error Handler ───────────────────────────────────────────────────

@app.errorhandler(Exception)
def handle_exception(e):
    logger.exception(f"Unhandled exception on {request.method} {request.path}")
    return jsonify({"error": "internal server error"}), 500

# ── Entry Point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

