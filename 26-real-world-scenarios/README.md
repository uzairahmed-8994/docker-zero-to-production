# 26 — Real-World Scenarios



## 0. What This Step Is

The previous steps built a foundation: production Dockerfiles, multi-stage builds, health checks, restart policies, resource limits, security hardening, a registry, version tagging, and a CI/CD pipeline. Each step introduced one concept in a controlled environment where the problem was known before it started.

Production does not work that way. Problems arrive without labels. The same symptom — a container that keeps restarting — can have a dozen different root causes. The skill that matters in production is not knowing the answer; it is knowing how to find it.

This step is eight case studies. Each one is a real class of problem that engineers encounter when running Docker in production. The focus is not on what the solution is but on how the investigation moved from symptom to root cause — which commands were run, what each one revealed, and which possibilities were eliminated along the way.

---



## Scenario 1 — The Container That Worked Locally and Failed in Production

### Context

A three-service stack: frontend (Flask, port 5001), backend (Flask + Gunicorn, port 5000), database (Postgres 15). The backend connected to a third-party payment API using an environment variable `PAYMENT_API_KEY`. The stack had been running in development for two weeks without issues. The first production deployment was to a single Ubuntu server.

### What Happened

The deployment ran through the CI/CD pipeline without errors. The images pushed successfully. The server pulled the images and started the containers. `docker compose ps` showed all three as `healthy`. But the first real transaction attempt from a user returned a 500 error. The logs showed:

```
2026-04-29 14:23:01 ERROR app Failed to authenticate with payment API
requests.exceptions.HTTPError: 401 Client Error: Unauthorized
```

In the development environment, the same transaction worked perfectly. Nothing in the code had changed between the last local test and the production deployment.

### Investigation

The 401 from the payment API meant the request was reaching the API but the credentials were wrong or absent. I checked the environment variables inside the running container:

```bash
docker compose exec backend env | grep PAYMENT
```

```
PAYMENT_API_KEY=
```

The variable was present but empty. In development, it had been set in a local `.env` file. On the production server, the `.env` file had not been created — the deployment process only copied `docker-compose.yml` to the server, not the `.env` file.

I looked at how the variable was defined in `docker-compose.yml`:

```yaml
backend:
  environment:
    - PAYMENT_API_KEY=${PAYMENT_API_KEY}
```

This variable substitution syntax reads `PAYMENT_API_KEY` from the shell environment or the `.env` file at `docker compose up` time. On the production server, neither existed. Docker Compose silently substituted an empty string.

I confirmed this was the only missing variable:

```bash
docker compose exec backend env | grep -E "DB_|PAYMENT_|BACKEND_"
```

The database variables were present — those had been hardcoded in the compose file. Only the payment key was absent because it was the only variable using substitution.

### Root Cause

The `.env` file was part of the development environment but was excluded from version control (correctly, per step 22) and was never created on the production server. The deployment process had no step for provisioning secrets. Docker Compose substituted an empty string silently, without error.

### Fix

On the server, I created the `.env` file with the production API key and restarted the backend:

```bash
echo "PAYMENT_API_KEY=prod_key_here" >> /opt/myapp/.env
docker compose up -d backend
```

The next transaction succeeded.

### Prevention

Every environment variable that uses `${VARIABLE}` substitution in `docker-compose.yml` should have a corresponding entry in a deployment checklist or, better, in the CI/CD pipeline's secrets injection. On this server, I added a deploy step that validated all required environment variables were set before starting the containers:

```bash
# In the deployment script, before docker compose up:
required_vars=(PAYMENT_API_KEY DB_PASSWORD)
for var in "${required_vars[@]}"; do
  if [ -z "${!var}" ]; then
    echo "ERROR: Required variable $var is not set"
    exit 1
  fi
done
```

A failed deployment that tells you why it failed is better than a successful deployment that silently breaks at runtime.

---



## Scenario 2 — The Database Connection That Refused to Connect

### Context

The same three-service stack. A junior developer had joined the team and was running the stack locally for the first time. The backend container started, ran the `init_db()` retry loop five times, and exited with:

```
Exception: Could not connect to database after multiple retries
```

The developer had followed the README exactly. The `docker-compose.yml` was unchanged. `docker compose up -d` showed the database as `healthy`.

### What Happened

From the developer's perspective, everything looked correct. The database was healthy. The backend was configured to connect to `db:5432`. It just would not connect.

### Investigation

I started with the backend logs to see the exact error during the retry attempts:

```bash
docker compose logs backend
```

```
2026-04-29 09:14:02 WARNING app Database not ready, retrying... (5 attempts left):
  connection to server at "db" (172.18.0.3), port 5432 failed:
  FATAL:  password authentication failed for user "appuser"
```

Not a connection timeout. Not a host unreachable. An authentication failure — the database was reachable, but the credentials were wrong.

I checked what the backend was trying to connect with:

```bash
docker compose exec backend env | grep DB_
```

```
DB_HOST=db
DB_PORT=5432
DB_NAME=appdb
DB_USER=appuser
DB_PASSWORD=
```

`DB_PASSWORD` was empty. Same pattern as scenario 1 — missing `.env` file. But I checked the developer's machine and the `.env` file was present:

```bash
cat .env
```

```
DB_USER=appuser
DB_PASSWORD=mysecretpassword
```

The file existed. The variable was defined. But the container had an empty password. I looked more carefully at the `.env` file:

```bash
cat -A .env
```

```
DB_USER=appuser^M$
DB_PASSWORD=mysecretpassword^M$
```

`^M` — Windows-style line endings (CRLF). The developer had created the `.env` file on Windows and then used it on a Linux system. Docker Compose on Linux was reading `DB_PASSWORD=mysecretpassword\r` — the carriage return character was included as part of the value. Postgres received a password with a trailing `\r` and rejected it.

### Root Cause

The `.env` file had Windows line endings (CRLF). On Linux, the carriage return character `\r` was included in the variable value, causing authentication failures.

### Fix

```bash
sed -i 's/\r//' .env
docker compose up -d backend
```

The connection succeeded immediately.

### Prevention

Add `.env` file creation to the developer onboarding documentation with an explicit note about line endings. Alternatively, the deployment validation script from scenario 1 can be extended to check for carriage returns:

```bash
if grep -qP '\r' .env; then
  echo "ERROR: .env file has Windows line endings. Run: sed -i 's/\r//' .env"
  exit 1
fi
```

---



## Scenario 3 — The Container That Kept Restarting

### Context

Production stack running on a single server for three weeks without issues. One morning, monitoring showed the backend container had restarted fourteen times overnight. It was currently `healthy`, but the restart count was alarming.

### What Happened

By the time anyone looked at it, the container appeared to be running normally. Requests were succeeding. The restart count was the only visible evidence of a problem.

### Investigation

I checked the restart count and the current status:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='RestartCount={{.RestartCount}} Status={{.State.Status}}'
```

```
RestartCount=14 Status=running
```

Fourteen restarts, currently running. I looked at the logs, but current logs only showed the most recent container instance — the previous thirteen crash cycles had been lost when the container restarted:

```bash
docker compose logs --since 1h backend
```

Only the startup sequence from the most recent restart. I needed to know what had happened before the last restart. I checked the container's exit code from the previous run:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.State.ExitCode}} {{.State.Error}}'
```

```
0
```

Exit code 0. The container had been exiting cleanly, not crashing. That ruled out an application panic or OOM kill. A clean exit from Gunicorn meant something had asked it to shut down gracefully.

I looked at the system journal for the Docker daemon events overnight:

```bash
sudo journalctl -u docker --since "8 hours ago" | grep backend | head -20
```

```
Apr 29 02:00:01 server dockerd: container backend OOMKilled=false exitCode=0
Apr 29 02:00:03 server dockerd: container backend started
Apr 29 03:00:01 server dockerd: container backend OOMKilled=false exitCode=0
Apr 29 03:00:03 server dockerd: container backend started
```

Every hour, on the hour. This was not a random crash. Something was deliberately stopping the container at a regular interval. I checked the server's cron jobs:

```bash
crontab -l
sudo crontab -l
```

```
0 * * * * /usr/bin/docker restart backend
```

A cron job. Someone had added a `docker restart backend` command to run every hour at minute 0, for reasons that were never documented. The restart policy was dutifully restarting the container after each cron-triggered stop, making the restart count climb.

### Root Cause

An undocumented cron job was restarting the backend container hourly. The restart policy treated each cron-triggered stop as a container exit and counted it, making the situation look like a crash loop when it was actually scheduled restarts.

### Fix

```bash
crontab -e
# Removed the docker restart line
```

The restart count stopped climbing. I left a comment in a shared runbook explaining what had happened.

### Prevention

Any manual Docker operations on production servers — cron jobs, scripts, maintenance procedures — should be documented in the same repository as the deployment configuration. An undocumented cron job is operational debt. The first sign of a problem like this should always include checking `crontab -l` and `sudo crontab -l` alongside the container inspection.

---



## Scenario 4 — The Memory That Never Came Back

### Context

The backend container had a 512MB memory limit (from step 21). After running for about four days, `docker stats` showed the backend consistently using 480–490MB — near its ceiling. Every few days, the container would hit the limit, get OOM-killed, and restart. After the restart, memory usage would drop back to the baseline 52MB. Then the climb would begin again.

### What Happened

The restart policy was handling the OOM kills correctly — the service recovered every time. But the underlying climb was not normal. Healthy Flask applications do not gradually consume 430MB above their idle baseline over four days.

### Investigation

I watched the memory usage pattern over time using `docker stats` with a logging wrapper:

```bash
while true; do
  echo "$(date): $(docker stats --no-stream --format '{{.MemUsage}}' backend)"
  sleep 300
done >> /tmp/memory_log.txt
```

After 24 hours the log showed a perfectly linear climb: roughly 3MB added every 5 minutes. Not a spike, not random fluctuation — a steady, consistent increase. That pattern is characteristic of an accumulation, not a burst.

I needed to see what was holding the memory. I exec'd into the container and checked the Python process memory breakdown:

```bash
docker compose exec backend pip install memory-profiler --quiet
docker compose exec backend python -c "
import psutil, os
proc = psutil.Process(os.getpid())
print(f'RSS: {proc.memory_info().rss / 1024 / 1024:.1f} MB')
"
```

Then I looked at what Python objects were accumulating. I added a temporary debug endpoint to the application:

```python
@app.route("/debug/memory")
def debug_memory():
    import gc
    import sys
    gc.collect()
    # count object types
    counts = {}
    for obj in gc.get_objects():
        t = type(obj).__name__
        counts[t] = counts.get(t, 0) + 1
    top = sorted(counts.items(), key=lambda x: -x[1])[:10]
    return jsonify({"top_types": top})
```

I called it and got:

```json
{
  "top_types": [
    ["dict", 84231],
    ["list", 23451],
    ["str", 198432],
    ["RealDictRow", 47821]
  ]
}
```

`RealDictRow` — 47,821 instances. That is the psycopg2 row object type returned by `cursor_factory=psycopg2.extras.RealDictCursor`. These objects were not being released. I searched the codebase for any place where database results might be held in memory rather than returned and discarded.

I found it in the notes listing endpoint. Three weeks earlier, someone had added an in-memory cache to reduce database load:

```python
_notes_cache = []
_cache_timestamp = 0

@app.route("/notes", methods=["GET"])
def get_notes():
    global _notes_cache, _cache_timestamp
    if time.time() - _cache_timestamp < 60:
        return jsonify({"notes": _notes_cache})
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, note, created_at FROM notes ORDER BY created_at DESC")
    _notes_cache = cur.fetchall()   # storing RealDictRow objects in a module-level list
    _cache_timestamp = time.time()
    cur.close()
    conn.close()
    return jsonify({"notes": [dict(n) for n in _notes_cache]})
```

The cache stored `RealDictRow` objects — not plain dicts — in a module-level list. When new notes were added, `_notes_cache` was replaced with a new list, but the old list's objects were not being garbage-collected correctly because psycopg2's `RealDictRow` objects held references back to their cursor context. The list grew with every cache refresh.

### Root Cause

An in-memory cache stored psycopg2 `RealDictRow` objects at the module level. These objects held circular references that prevented garbage collection. The cache was refreshed every 60 seconds, adding more objects without fully releasing the previous set. Over four days the accumulated unreleased objects consumed nearly 430MB.

### Fix

```python
# Convert to plain dicts before caching — breaks the circular references
_notes_cache = [dict(n) for n in cur.fetchall()]
```

After the fix, the memory usage climbed to around 58MB at steady state and stayed flat for two weeks of observation.

### Prevention

Module-level mutable state in a Flask application running under Gunicorn is dangerous. Each worker process has its own copy of the module state, and none of it is shared or cleaned up automatically. The resource limit from step 21 was working correctly — it contained the damage and triggered a recovery restart. But the restart was masking an underlying leak that needed fixing. A resource limit is not a substitute for finding and fixing the leak; it is the safety net that prevents it from taking down the host while you find it.

---



## Scenario 5 — The Wrong Data With No Error

### Context

The notes application had been running in production for a month. A user reported that after adding a note, they could see it briefly in the list, but after refreshing the page, the note they had just added would sometimes disappear and then reappear seconds later. No errors in the application logs. No 5xx responses. The data was not being lost — it was just inconsistently visible.

### What Happened

No error meant no stack trace, no log line pointing at the problem. The symptom was purely behavioural: eventually-consistent reads, which should not exist in a single-database application with synchronous writes.

### Investigation

My first suspicion was a caching issue — either the in-memory cache from the previous scenario or something in the Postgres layer. I checked whether the cache fix had been deployed:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{index .Config.Labels "org.opencontainers.image.revision"}}'
```

```
d4e5f6a7b8c9...
```

I cross-referenced this with the git commit that fixed the cache:

```bash
git log --oneline | grep "cache fix"
# e1f2a3b4 Fix RealDictRow cache memory leak
```

```bash
git rev-parse e1f2a3b4
# e1f2a3b4...
```

Different SHA. The cache fix had been committed but the deployed image was from an earlier commit. The deployment pipeline had not run since the fix was merged. The fix was in the repository but not in production.

But even the original cache had a 60-second TTL — data should appear within a minute, not disappear and reappear randomly. I dug deeper.

I looked at the Gunicorn worker configuration:

```yaml
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app:app"]
```

Two workers. Each worker was a separate Python process. Each process had its own copy of `_notes_cache`. Worker 1 might have a cache from 30 seconds ago. Worker 2 might have a cache from 5 seconds ago. A request hitting worker 1 would see older data than a request hitting worker 2. Whether the note appeared or not depended on which worker handled the request — a coin flip on each page load.

### Root Cause

Two Gunicorn workers each maintained independent module-level caches with different refresh times. A write to the database updated neither cache. A subsequent read would hit whichever worker happened to serve the request, each potentially returning a different view of the data. The inconsistency was not a bug in a traditional sense — it was the predictable consequence of in-process caching in a multi-worker setup.

### Fix

The in-memory cache was removed entirely. The read-after-write consistency issue disappeared:

```python
@app.route("/notes", methods=["GET"])
def get_notes():
    logger.info("Fetching all notes")
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, note, created_at FROM notes ORDER BY created_at DESC")
    notes = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({"notes": [dict(n) for n in notes]})
```

If caching was genuinely needed for performance, the correct solution was a shared cache external to the application — Redis or Memcached — where all workers read and write from the same source.

### Prevention

In-process state in multi-worker applications produces consistency bugs that are difficult to reproduce because the behaviour depends on which worker handles each request. Before adding any module-level mutable state to a Flask application, ask: if two copies of this code run simultaneously with independent state, what inconsistencies can occur? If the answer is any, move the state out of the process.

---



## Scenario 6 — The Deployment That Broke Production

### Context

A Tuesday afternoon release. The backend had been updated with a new endpoint. The CI/CD pipeline ran successfully. The deployment job SSHed into the server and ran `docker compose pull && docker compose up -d`. The pipeline reported success. Within two minutes of the deployment, error rates on the frontend spiked to 100%.

### What Happened

All frontend requests that called the backend were returning 502. The frontend logs showed:

```
2026-04-29 15:43:02 ERROR app Backend request failed: HTTPConnectionPool(host='backend', port=5000):
  Max retries exceeded with url: /api/data
  (Caused by NewConnectionError: Failed to establish a new connection: [Errno 111] Connection refused)
```

The backend container was not accepting connections. I checked its status:

```bash
docker compose ps
```

```
NAME       SERVICE    STATUS                 PORTS
backend    backend    restarting (1)         0.0.0.0:5000->5000/tcp
frontend   frontend   healthy                0.0.0.0:5001->5001/tcp
db         db         healthy                5432/tcp
```

The backend was in a restart loop immediately after deployment.

### Investigation

I followed the backend logs in real time:

```bash
docker compose logs -f backend
```

```
2026-04-29 15:43:05 INFO app Backend application starting...
Traceback (most recent call last):
  File "/usr/local/lib/python3.11/site-packages/gunicorn/arbiter.py", line 589, in spawn_worker
  File "/app/app.py", line 4, in <module>
    from flask_limiter import Limiter
ModuleNotFoundError: No module named 'flask_limiter'
[2026-04-29 15:43:05 +0000] [1] [INFO] Worker failed to boot.
```

`ModuleNotFoundError: No module named 'flask_limiter'`. A new dependency had been added to the code but not to `requirements.txt`. The module existed on the developer's laptop — it had been installed locally via `pip install flask-limiter` without updating the requirements file. The local build worked because the local environment had the package. The CI pipeline built the image from `requirements.txt`, which did not include it. The image in the registry was broken.

I verified by inspecting the pushed image:

```bash
docker run --rm myusername/backend:latest \
  python -c "import flask_limiter" 2>&1
```

```
ModuleNotFoundError: No module named 'flask_limiter'
```

Confirmed. The image was missing the dependency.

### Fix

The immediate fix was a rollback to the previous version:

```bash
# On the server:
sed -i 's/backend:latest/backend:git-previoussha/' docker-compose.yml
docker compose pull
docker compose up -d backend
```

Within 90 seconds the error rate dropped to zero. Then I fixed the root cause: added `flask-limiter==3.5.0` to `requirements.txt`, committed, and let the pipeline rebuild and deploy correctly.

### Prevention

Two things failed here. First, the CI pipeline built the image from `requirements.txt` but the test step only checked the health endpoint — it did not import the new module. The test step should have run any smoke tests that exercise the new code. Second, the developer ran `pip install` locally without updating `requirements.txt`. A pre-commit hook or a CI check that compares installed packages against `requirements.txt` would catch this before it reaches the registry.

The faster fix would have been easier if version tags were pinned in `docker-compose.yml` rather than using `latest`. With a pinned version, rollback is a one-line change. With `latest`, you have to find the previous commit's SHA from the registry history, which costs time during an incident.

---



## Scenario 7 — The Application That Got Slower Every Day

### Context

The application had been in production for six weeks. Initially it responded to note listing requests in about 40ms. By week six, the same endpoint was taking 800ms to 1200ms. No code had changed. No traffic increase. The slowdown was gradual and consistent.

### What Happened

The endpoint itself was simple — `SELECT * FROM notes ORDER BY created_at DESC`. On a table with a few hundred rows, this should be fast. The slowdown was not visible in application logs because the logging only recorded that the endpoint was called, not how long the database query took.

### Investigation

I added timing to the endpoint temporarily to isolate where the time was being spent:

```python
import time

@app.route("/notes", methods=["GET"])
def get_notes():
    t0 = time.time()
    conn = get_db()
    t1 = time.time()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, note, created_at FROM notes ORDER BY created_at DESC")
    notes = cur.fetchall()
    t2 = time.time()
    cur.close()
    conn.close()
    logger.info(f"connect={t1-t0:.3f}s query={t2-t1:.3f}s rows={len(notes)}")
    return jsonify({"notes": [dict(n) for n in notes]})
```

The log output showed:

```
connect=0.003s query=0.847s rows=18423
```

18,423 rows. The notes table had grown to over 18,000 rows. Nobody had been deleting notes — the application had no delete-old-notes functionality, and users had been adding notes continuously. The query was scanning the entire table on every request.

I checked the table structure inside the database:

```bash
docker compose exec db psql -U appuser -d appdb -c "\d notes"
```

```
                                 Table "public.notes"
   Column   |            Type             |
------------+-----------------------------+
 id         | integer                     |
 note       | text                        |
 created_at | timestamp without time zone |

Indexes:
    "notes_pkey" PRIMARY KEY, btree (id)
```

No index on `created_at`. The `ORDER BY created_at DESC` clause was causing a full table sequential scan on every request, sorting 18,000 rows each time. In week one with 50 rows this was invisible. By week six with 18,000 rows it dominated the response time.

I confirmed with `EXPLAIN`:

```bash
docker compose exec db psql -U appuser -d appdb \
  -c "EXPLAIN ANALYZE SELECT id, note, created_at FROM notes ORDER BY created_at DESC LIMIT 50;"
```

```
Seq Scan on notes  (cost=0.00..421.23 rows=18423 ...)
  (actual time=0.012..847.231 rows=18423 ...)
Sort  (cost=...)
  Sort Method: external merge  Disk: 2840kB
Planning Time: 0.3 ms
Execution Time: 852.1 ms
```

Sequential scan, external merge sort spilling to disk. All 18,000 rows were being read and sorted on every request.

### Fix

Adding an index on `created_at`:

```bash
docker compose exec db psql -U appuser -d appdb \
  -c "CREATE INDEX CONCURRENTLY idx_notes_created_at ON notes (created_at DESC);"
```

After the index was created, the same query took 2ms. I also added a `LIMIT 100` to the query so the endpoint would never return an unbounded result set regardless of table size.

The index creation was done live — `CONCURRENTLY` means Postgres built the index without locking the table. No downtime required.

### Prevention

Query performance that depends on table size is a problem that hides during development and emerges in production. Any query with an `ORDER BY` on a column that is not indexed will degrade linearly as the table grows. Database migrations should include indexes on any column used in `ORDER BY`, `WHERE`, or `JOIN` clauses. The application's health check could also be extended to include a database query performance check — if the query takes more than a threshold time, the health check fails, which would have surfaced this much earlier.

---



## Scenario 8 — The Environment Variable That Was Ignored

### Context

The backend had a configurable log level — `LOG_LEVEL=DEBUG` would enable verbose logging, `LOG_LEVEL=INFO` was the default. A developer was trying to enable debug logging on the production server to diagnose a different issue. They set the variable in `docker-compose.yml` and restarted the backend. The log output did not change — still INFO level. They set it in the `.env` file. Still no change. They tried `docker compose down && docker compose up -d`. Still INFO level.

### What Happened

The variable was being set correctly. `docker compose exec backend env | grep LOG_LEVEL` showed `LOG_LEVEL=DEBUG`. But the application was logging at INFO level regardless.

### Investigation

I checked how the log level was being read in the application:

```python
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
```

`level=logging.INFO` was hardcoded. The application read the environment variable correctly in some earlier version, but the logging setup had been simplified at some point and the `os.getenv("LOG_LEVEL", "INFO")` call had been replaced with a literal.

But that was not the full story. Even after fixing the code to read from the environment:

```python
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
```

The debug logging still did not appear for one developer. I exec'd into their container and checked:

```bash
docker compose exec backend env | grep LOG_LEVEL
```

```
LOG_LEVEL=DEBUG
```

Correct. I checked what level the root logger was actually set to:

```bash
docker compose exec backend python -c "
import logging
print(logging.getLogger().level)
print(logging.getLevelName(logging.getLogger().level))
"
```

```
20
INFO
```

Level 20 is INFO. The root logger was INFO despite the environment variable being DEBUG. I read the application code more carefully and found a second `logging.basicConfig` call further down the file — inside the `init_db()` function, added during debugging two weeks earlier and never removed:

```python
def init_db():
    logging.basicConfig(level=logging.INFO)  # accidental duplicate
    ...
```

`logging.basicConfig` is a no-op if the root logger already has handlers configured. But if it is called before the first `basicConfig`, it sets the level. Depending on import order and Gunicorn's worker initialisation sequence, sometimes the `init_db` call ran first and set INFO, and the main `basicConfig` with DEBUG was then a no-op.

### Root Cause

A duplicate `logging.basicConfig` call inside `init_db()` was racing with the top-level logging setup. In some worker initialisation orders, the duplicate call ran first with `level=INFO`, locking the log level before the correct `level=DEBUG` call ran — which was then silently ignored.

### Fix

Removed the duplicate `logging.basicConfig` from `init_db()`. The logging configuration was centralised at module top level, called exactly once:

```python
# At module level, called once
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)
```

After the fix, setting `LOG_LEVEL=DEBUG` in the environment produced debug output as expected.

### Prevention

`logging.basicConfig` should be called exactly once, at the top level of the application entry point, before any other code runs. It should never appear inside functions that may be called multiple times or in unpredictable order. Any logging setup inside library code or initialisation functions should use `logging.getLogger(__name__)` to get a named logger rather than configuring the root logger.

---



## A Note on Debugging Patterns

Looking across these eight scenarios, a few patterns repeat regardless of the specific problem:

**The symptom and the root cause are rarely in the same place.** Container restarts pointed to a cron job. Slow queries pointed to a missing index. Wrong data pointed to multi-worker caching. Starting from the symptom and working backward — eliminating possibilities one at a time — is the only reliable path to the root cause.

**Environment variables deserve their own investigation step.** Four of the eight scenarios involved an environment variable that was either absent, had incorrect whitespace, was hardcoded over, or was being set after the thing that read it had already run. When a configuration change does not produce the expected effect, the first question is always: does the running container actually have the value I think it does?

```bash
docker compose exec <service> env | grep <VARIABLE>
```

This single command has short-circuited more debugging sessions than any other.

**The restart policy can hide the problem.** A container that restarts successfully looks healthy. The restart count is the number that reveals whether health is real or just recovered. A system where restart counts climb steadily is a system that has a problem the restart policy is masking.

**Read the actual logs before assuming.** `docker compose logs backend` is not expensive. The specific error message — `ModuleNotFoundError`, `FATAL: password authentication failed`, `Seq Scan on notes` — almost always points directly at the cause. The investigation in each scenario above started with the logs and ended when the logs' implications were fully traced.