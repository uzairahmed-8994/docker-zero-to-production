# 13 — Database Postgres



## 0. Goal of This Step

Add a real PostgreSQL database to the application stack, connect the backend to it, persist data properly using a named volume, and understand how all the concepts from steps 08 through 12 come together — networks, volumes, environment variables, Compose, into a complete three-tier application.



## 1. What Problem It Solves

Up to this point the backend stored notes in a flat text file inside a named volume. That works for learning volumes, but it is not how real applications store data. Real applications use databases — structured storage with querying, relationships, transactions, and concurrent access.

More importantly, this step is where everything connects. You will use:
- A named volume (step 10/11) to persist Postgres data
- Network isolation (step 09) to keep the database off the frontend network
- Environment variables (step 08) to configure the database connection
- Compose (step 08) to wire all three services together

This is the first time the app looks like a real production architecture.

Until now, the backend stored data in a file.

In this step, we replace that with a real database. That means:
- The backend no longer reads/writes files
- It connects to Postgres over the network
- Data is stored in database tables instead of text files

## 2. Code Changes Required

> **Before reading further, make these changes to files.**

This step introduces a database so the backend needs a new dependency and updated routes. The frontend stays the same.



### Update `backend/requirements.txt`

```
Flask==3.1.3
psycopg2-binary==2.9.9
```

`psycopg2-binary` is the Python PostgreSQL driver. The `-binary` variant includes compiled C libraries so you do not need to install Postgres client libraries separately — important inside a slim Docker image.



### Replace `backend/app.py` completely

```python
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
```



### Create `docker-compose.yml` for this step

```yaml
services:
  frontend:
    build: ./frontend
    ports:
      - "5001:5001"
    environment:
      - BACKEND_URL=http://backend:5000
    networks:
      - frontend-network
    depends_on:
      - backend

  backend:
    build: ./backend
    ports:
      - "5000:5000"
    environment:
      - DB_HOST=db
      - DB_PORT=5432
      - DB_NAME=appdb
      - DB_USER=appuser
      - DB_PASSWORD=secret
    networks:
      - frontend-network
      - backend-network
    depends_on:
      - db

  db:
    image: postgres:15
    environment:
      - POSTGRES_DB=appdb
      - POSTGRES_USER=appuser
      - POSTGRES_PASSWORD=secret
    volumes:
      - postgres-data:/var/lib/postgresql/data
    networks:
      - backend-network

volumes:
  postgres-data:

networks:
  frontend-network:
  backend-network:
```


## 3. What Happened (Experience)

With the code updated and the Compose file in place, let's bring it up and see what happens.



**Step 1 — First startup**

```bash
docker compose up -d
```

Check what is running:

```bash
docker compose ps
# NAME          STATUS    PORTS
# db            running   5432/tcp
# backend       running   0.0.0.0:5000->5000/tcp
# frontend      running   0.0.0.0:5001->5001/tcp
```

All three services running. Now test the backend:

```bash
curl http://localhost:5000/notes
# {"notes": []}
```

Add a note:

```bash
curl -X POST http://localhost:5000/notes \
  -H "Content-Type: application/json" \
  -d '{"note": "stored in postgres"}'
# {"created_at": "...", "id": 1, "note": "stored in postgres"}
```

Read it back:

```bash
curl http://localhost:5000/notes
# {"notes": [{"created_at": "...", "id": 1, "note": "stored in postgres"}]}
```
In previous steps:
- Notes were stored in a file (notes.txt)

Now:
- Notes are stored in a database table (notes)

This is why you now see:

- IDs (auto-generated)
- timestamps (created_at)

Real database. Real ID. Real timestamp. This is no longer a text file.



**Step 2 — The startup order problem**

Bring everything down and back up and watch the backend logs carefully:

```bash
docker compose down -v
docker compose up
```

You will likely see this in the backend logs:

```
backend-1  | Database not ready, retrying... (5 attempts left)
backend-1  | Database not ready, retrying... (4 attempts left)
backend-1  | Database initialized successfully
```

The backend started before Postgres was fully ready to accept connections. Even though `depends_on: db` tells Compose to start `db` first, it only waits for the container to start, not for Postgres inside to finish initializing. The backend's `init_db()` function retries up to 5 times with a 2-second wait between attempts, giving Postgres time to come up.

This retry loop is not optional. Without it, the backend would crash on the first startup attempt and you would get a confusing error.


**Step 3 — Verify data survives container restart**

Add a few more notes. 

```bash
curl -X POST http://localhost:5000/notes \
  -H "Content-Type: application/json" \
  -d '{"note": "note 1"}'

curl -X POST http://localhost:5000/notes \
  -H "Content-Type: application/json" \
  -d '{"note": "note 2"}'

curl -X POST http://localhost:5000/notes \
  -H "Content-Type: application/json" \
  -d '{"note": "note 3"}'
```

Then restart just the backend:

```bash
docker compose restart backend
```

Read notes:
```bash
curl http://localhost:5000/notes
```
All still there. The data is in Postgres, not in the backend container.

Now do the full down and up:

```bash
docker compose down
docker compose up -d
```

Read notes again. The `postgres-data` volume preserved the entire database across a full stack teardown.



**Step 4 — Connect directly to Postgres**

You can open a `psql` shell directly against the running database container.

The `docker compose exec db psql ...` command opens a PostgreSQL shell inside the database container. This allows you to run SQL queries directly against the database without going through the backend API. It proves that the data is stored in the database itself, not in the application.

```bash
docker compose exec db psql -U appuser -d appdb
```

Inside psql:

```sql
SELECT * FROM notes;
--  id |        note        |         created_at
-- ----+--------------------+----------------------------
--   1 | stored in postgres | 2024-01-15 10:23:44.123456

\dt          -- list all tables
\d notes     -- describe the notes table
\q           -- quit
```


You are talking directly to Postgres inside the container. No external database client like pgadmin or local psql needed.



## 4. Why It Happens

**Why does Postgres need a volume at `/var/lib/postgresql/data`?**

This is where Postgres stores all its data files — the actual database contents, write-ahead logs, configuration. Without a volume here, every `docker compose down` wipes the entire database. With the named volume `postgres-data` mounted at that path, the data directory survives on the host and Postgres picks it up on every restart.

This is the volume initialization behavior from step 11 in action. On the very first run, the volume is empty. Postgres initializes the data directory, creates the `appdb` database, sets up `appuser`. On every subsequent run, it finds the data directory already initialized and skips setup — just opens the existing database.

**Why do the environment variables in `db` and `backend` match?**

```yaml
db:
  environment:
    - POSTGRES_DB=appdb       # tells Postgres to create this database
    - POSTGRES_USER=appuser   # tells Postgres to create this user
    - POSTGRES_PASSWORD=secret

backend:
  environment:
    - DB_NAME=appdb           # tells the backend which database to connect to
    - DB_USER=appuser         # tells the backend which user to authenticate as
    - DB_PASSWORD=secret
```

The `POSTGRES_*` variables are read by the official Postgres image on first startup to create the database and user. The `DB_*` variables are read by your Flask app to know how to connect. They must match — if they don't, the backend will get an authentication error.

**Why is the database only on `backend-network`?**

From step 09 — the frontend should never talk directly to the database. Only the backend should. By putting `db` only on `backend-network` and `frontend` only on `frontend-network`, the frontend has no network path to the database at all. Even if a bug in the frontend tried to connect to Postgres, Docker's network isolation would block it.



## 5. Deep Understanding

### The Official Postgres Image

When you use `image: postgres:15`, Docker pulls the official Postgres image from Docker Hub. This image is carefully built to:

1. Accept `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` environment variables
2. On first startup with an empty data directory — initialize Postgres, create the specified database and user
3. On subsequent startups — just start Postgres against the existing data directory, skip initialization

Any `.sql` or `.sh` files you place in `/docker-entrypoint-initdb.d/` inside the container are also executed on first startup. This is how you seed initial data or run schema migrations on first run:

```yaml
db:
  image: postgres:15
  volumes:
    - postgres-data:/var/lib/postgresql/data
    - ./init.sql:/docker-entrypoint-initdb.d/init.sql  # run on first startup only
```

This is a bind mount (step 12) used for initialization — a good example of combining concepts.

### Connection Pooling

The backend currently opens a new database connection for every request and closes it after. This is fine for learning but problematic in production. Opening a connection to Postgres takes 20-100ms and involves authentication, process forking on the Postgres side, and memory allocation. Under load, this becomes a bottleneck.

Production apps use **connection pooling** — a pool of pre-opened connections that requests borrow and return. Libraries like `psycopg2` with `psycopg2.pool`, or tools like `PgBouncer` (a separate container that sits between your app and Postgres), manage this. For your current learning setup, one connection per request is fine.

### Why `psycopg2-binary` Not `psycopg2`

The regular `psycopg2` package needs the Postgres client libraries installed on the system (`libpq-dev`, `gcc`, etc.) to compile during `pip install`. Inside a `python:3.11-slim` image, those are not present.

`psycopg2-binary` bundles everything it needs — no system dependencies required. The downside is slightly larger image size and that it is not recommended for production deployments by the psycopg2 maintainers (they prefer you compile against your system's libpq). For development and learning, binary is the right choice. For production, step 14 covers production Dockerfiles where you would install the proper build tools and use the non-binary version.

### Database Credentials in Environment Variables

The credentials (`appuser`, `secret`) are in the Compose file in plain text. This is acceptable for local development but never acceptable for production. In production you use:

- **Docker Secrets** — Docker's built-in mechanism for injecting secrets as files
- **External secret managers** — AWS Secrets Manager, HashiCorp Vault, etc.
- **`.env` files** — at minimum, move credentials out of the Compose file into a `.env` file that is in `.gitignore`

The `.env` approach is the minimum standard:

```bash
# .env file (never commit this)
DB_PASSWORD=realpassword123
POSTGRES_PASSWORD=realpassword123
```

```yaml
# docker-compose.yml (safe to commit)
db:
  environment:
    - POSTGRES_PASSWORD=${DB_PASSWORD}
backend:
  environment:
    - DB_PASSWORD=${DB_PASSWORD}
```

Compose reads `.env` automatically from the same directory as `docker-compose.yml`.

### The Retry Pattern Is a Real Production Pattern

The `init_db()` retry loop in the backend is not a hack — it is a recognized production pattern called **retry with backoff**. In distributed systems, services start at different speeds. You cannot guarantee the database is ready when the app starts. The options are:

1. **Retry in the app** — what we did. Simple, works well for startup.
2. **`depends_on` with health checks** — Compose waits until Postgres passes a health check before starting the backend. More robust, covered in step 19.
3. **External wait script** — a shell script (`wait-for-it.sh`) that polls the database port before starting the app. Common in older setups.

For production, option 2 (health checks) is preferred because it handles both initial startup and database restarts during operation. For now, the retry loop is a working solution.

### Inspecting the Postgres Data Volume

```bash
docker volume inspect 13-database-postgres_postgres-data
# Mountpoint: /var/lib/docker/volumes/13-database-postgres_postgres-data/_data

sudo ls /var/lib/docker/volumes/13-database-postgres_postgres-data/_data
# PG_VERSION  base  global  pg_hba.conf  pg_ident.conf  postgresql.conf  ...
```

These are actual Postgres data files on your host. This is what persists across container restarts. You should never modify these files directly — always interact with them through Postgres.



## 6. Commands

```bash
# ── Starting the Stack ─────────────────────────────────────────────────────

docker compose up -d
docker compose up -d --build      # after code changes to backend

# ── Testing the API ────────────────────────────────────────────────────────

curl http://localhost:5000/notes
curl -X POST http://localhost:5000/notes \
  -H "Content-Type: application/json" \
  -d '{"note": "your note here"}'
curl -X DELETE http://localhost:5000/notes/1

# ── Connecting to Postgres Directly ───────────────────────────────────────

docker compose exec db psql -U appuser -d appdb

# Inside psql:
# SELECT * FROM notes;
# \dt          list tables
# \d notes     describe table
# \q           quit

# ── Viewing Logs ───────────────────────────────────────────────────────────

docker compose logs db            # postgres logs
docker compose logs backend       # see retry messages on startup
docker compose logs -f            # follow all services

# ── Volume Management ──────────────────────────────────────────────────────

docker volume ls                  # find postgres-data volume
docker volume inspect 13-database-postgres_postgres-data
docker compose down               # keeps postgres-data volume
docker compose down --volumes     # wipes database completely

# ── Database Backup ───────────────────────────────────────────────────────

# Proper postgres dump (better than volume backup for databases)
docker compose exec db pg_dump -U appuser appdb > backup.sql

# Restore from dump
docker compose exec -T db psql -U appuser appdb < backup.sql
```



## 7. Real-World Notes

`pg_dump` is the correct way to back up a Postgres database — not copying the volume's raw files. `pg_dump` produces a portable SQL file that can be restored into any Postgres instance, any version, on any machine. Raw volume backups are Postgres-version-specific and can be corrupted if taken while Postgres is writing. Always use `pg_dump` for database backups.

In production, nobody runs a raw Postgres container with a local volume for critical data. The industry standard is managed database services — AWS RDS, Google Cloud SQL, Azure Database for PostgreSQL. They handle automated backups, point-in-time recovery, read replicas, failover, and version upgrades. You use a Postgres container in Docker for local development and testing. For production data that matters, you use a managed service.

The three-tier architecture you now have — frontend, backend, database — with network isolation between tiers, is the foundational pattern of web application infrastructure. Every variation you will encounter in your career (microservices, serverless, Kubernetes deployments) is a variation on this same pattern. Understanding it at the Docker level gives you the mental model for all of them.

Never put database credentials in your Compose file and commit it to a public repository. This has caused massive security breaches. Use `.env` files for local development (add to `.gitignore`), and use proper secret management for production. The example in this README uses hardcoded values purely for learning clarity.


## 8. Exercises

**Exercise 1 — Bring it up and use the full API**
Bring the stack up, add five notes, read them all, delete one by ID, read again and confirm it is gone. Then bring the stack all the way down with `docker compose down` (not `--volumes`) and back up. Confirm all remaining notes are still there. You are now using a real database with real persistence.

**Exercise 2 — Watch the retry loop**
Bring the stack completely down. Run `docker compose up` (not detached) so you can see all logs. Watch the backend retry connecting to the database while Postgres initializes. Count how many retries it takes. Then try increasing the number of retries in `init_db()` and reducing the sleep time — observe when it starts working.

**Exercise 3 — Connect with psql and explore**
Run `docker compose exec db psql -U appuser -d appdb`. Add a note via the API in another terminal, then run `SELECT * FROM notes;` in psql and see it appear. Run `\d notes` to see the table schema. Run `\dt` to list all tables. You are talking directly to Postgres — no ORM, no abstraction.

**Exercise 4 — Break the credentials intentionally**
Change `DB_PASSWORD` in the backend's environment to `wrongpassword`. Bring the stack down and up. Watch the backend fail to connect and exhaust all retries. Read the error in the logs — it will be a clear Postgres authentication error. Fix the password and bring it up again.

**Exercise 5 — Verify network isolation**
Exec into the frontend container and try to connect to the database:
```bash
docker compose exec frontend /bin/sh
# inside:
ping db
# should fail — db is not on frontend-network
```
Then exec into the backend and try:
```bash
docker compose exec backend /bin/sh
# inside:
ping db
# should work — backend is on backend-network
```
This is step 09's network isolation applied to a real database.

**Exercise 6 — Backup and restore**
Add several notes. Run `pg_dump` to create `backup.sql`. Run `docker compose down --volumes` to completely wipe the database. Bring the stack up — database is empty. Restore from `backup.sql` using `psql`. Read the notes — they are back. This is the full backup/restore cycle you would use in production.

**Exercise 7 — Use a `.env` file**
Create a `.env` file in the step directory:
```
DB_PASSWORD=newpassword
POSTGRES_PASSWORD=newpassword
```
Update `docker-compose.yml` to use `${DB_PASSWORD}` and `${POSTGRES_PASSWORD}`. Wipe the volume (password change requires fresh database), bring the stack up. Confirm it works. Add `.env` to `.gitignore`. This is the minimum production hygiene for credentials.