# 18 — Logging and Monitoring



## 0. Goal of This Step

Understand how Docker handles logs, where they actually go, why the default behaviour breaks under real conditions, and how to structure logging so that a running system tells you what is happening — before you have to go looking.



## 1. What Problem It Solves

Step 17 was about diagnosing failures after they happened. Every technique in that step required noticing something was wrong first — a 500 error, a missing container, a slow response and then investigating. The debugging was reactive.

The natural next question is: what if the system was telling you what was happening the whole time, in a form you could actually use?

`docker compose logs` works well for a single investigation session. But it has limits that become visible under real conditions. Logs accumulate indefinitely on disk with no size bound. The default log format has no structure — it is plain text, and searching it for a specific error across three services requires reading everything manually. When a container is removed, its logs are removed with it. If the backend crashes and restarts, the logs from before the crash are gone.

None of this matters much when the stack is running locally and you are actively watching it. It starts to matter when the stack runs unattended — in CI, on a server, or in any environment where you are not present when something goes wrong.

This step is about understanding the logging layer that sits beneath `docker compose logs`: how Docker actually collects and stores log output, what controls that behaviour, and how to shape it so that logs are useful rather than just present.



## 2. What Happened (Experience)

The three-service stack from step 17 was running correctly. I had gotten good at reading logs during a debugging session. Then I started thinking about what happened to those logs when I was not watching.

**Step 1 — Finding where the logs actually live**

`docker compose logs` shows logs in the terminal. I had always assumed they were stored somewhere reasonable. I wanted to know exactly where.

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.LogConfig.Type}}'
```

```
json-file
```

The default log driver is `json-file`. Docker is writing every line of stdout and stderr from the container to a JSON file on the host filesystem. I found the actual file:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.LogPath}}'
```

```
/var/lib/docker/containers/a1b2c3.../a1b2c3...-json.log
```

I opened it:

```bash
sudo cat /var/lib/docker/containers/a1b2c3...-json.log | head -5
```

```json
{"log":"[2026-04-29 09:10:35 +0000] [1] [INFO] Starting gunicorn 21.2.0\n","stream":"stderr","time":"2026-04-29T09:10:35.752356904Z"}
{"log":"[2026-04-29 09:10:35 +0000] [1] [INFO] Listening at: http://0.0.0.0:5000 (1)\n","stream":"stderr","time":"2026-04-29T09:10:35.752742416Z"}
{"log":"[2026-04-29 09:10:35 +0000] [1] [INFO] Using worker: sync\n","stream":"stderr","time":"2026-04-29T09:10:35.752795886Z"}
{"log":"[2026-04-29 09:10:35 +0000] [8] [INFO] Booting worker with pid: 8\n","stream":"stderr","time":"2026-04-29T09:10:35.75838354Z"}
{"log":"[2026-04-29 09:10:35 +0000] [9] [INFO] Booting worker with pid: 9\n","stream":"stderr","time":"2026-04-29T09:10:35.848420972Z"}
```

Every log line from the container is a JSON object with the log text, the stream it came from (stdout or stderr), and a timestamp. `docker compose logs` is just a reader that formats these JSON files for the terminal.

The important realisation: this file grows forever. There is no rotation by default. Every log line from every container since it was created is in this file. On a container that has been running for weeks, handling thousands of requests per day, this file can be gigabytes.

I checked what happened to the log file when I recreated the container:

```bash
docker compose up -d --force-recreate backend
docker inspect $(docker compose ps -q backend) --format='{{.LogPath}}'
```

A different path. A new file. The old file was gone or more precisely, it was cleaned up when the old container was removed. The logs from the previous container's lifetime were no longer accessible through `docker compose logs`.

**Step 2 — Noticing what the application was not logging**

With the stack running, I sent several requests and looked at the backend logs:

```bash
docker compose logs backend
```

```
Database initialized successfully
[2026-04-28 10:23:44 +0000] [1] [INFO] Starting gunicorn 21.2.0
[2026-04-28 10:23:44 +0000] [1] [INFO] Listening at: http://0.0.0.0:5000
[2026-04-28 10:23:44 +0000] [7] [INFO] Booting worker with pid: 7
[2026-04-28 10:23:44 +0000] [8] [INFO] Booting worker with pid: 8
172.18.0.3 - - [28/Apr/2026:10:24:01 +0000] "GET /notes HTTP/1.1" 200 142 "-" "curl/7.88.1"
172.18.0.3 - - [28/Apr/2026:10:24:03 +0000] "POST /notes HTTP/1.1" 200 67 "-" "curl/7.88.1"
```

Gunicorn was logging every HTTP request in its access log format. That is useful for knowing that requests arrived and what status code was returned. But there was nothing from the application code itself. If a note was created, the log said `POST /notes 200` — not what note was created, not how long the database query took, not which worker handled it.

If something went wrong inside a route — a malformed payload, a database constraint violation, an unexpected null — Gunicorn would log a 500, but the application code would log nothing unless I explicitly wrote a log statement. The application was silent about everything it was doing internally.

**Step 3 — Adding structured logging to the application**

I wanted the application to emit useful information about its own operations — not just that a request happened, but what the application did in response to it.

Python's built-in `logging` module writes to stdout by default when configured correctly, which means Docker picks it up automatically. I added logging to `app.py`:

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)
```

Then added log statements to the routes:

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
    logger.info(f"Returned {len(notes)} notes")
    return jsonify({"notes": [dict(n) for n in notes]})

@app.route("/notes", methods=["POST"])
def add_note():
    note = request.json.get("note", "")
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
```

I also added error logging to `init_db()`:

```python
except psycopg2.OperationalError as e:
    logger.warning(f"Database not ready, retrying... ({retries} attempts left): {e}")
```

After rebuilding and sending a few requests:

```bash
docker compose logs backend
```

```
2026-04-28 10:31:00 INFO app Fetching all notes
2026-04-28 10:31:00 INFO app Returned 3 notes
172.18.0.3 - - [28/Apr/2026:10:31:00 +0000] "GET /notes HTTP/1.1" 200 142
2026-04-28 10:31:02 INFO app Creating note: buy milk
2026-04-28 10:31:02 INFO app Created note with id=4
172.18.0.3 - - [28/Apr/2026:10:31:02 +0000] "POST /notes HTTP/1.1" 200 67
```

Now the logs showed what the application was actually doing — the Gunicorn access log showed the HTTP layer, and the application log showed the business layer. When a 500 appeared, I could now see exactly which operation failed and what data it was operating on.

**Step 4 — Discovering the log size problem**

I left the stack running and sent a large number of requests to simulate a day of activity:

```bash
for i in $(seq 1 500); do curl -s http://localhost:5000/notes > /dev/null; done
```

Then checked the log file size:

```bash
docker inspect $(docker compose ps -q backend) --format='{{.LogPath}}' | \
  xargs sudo du -sh
```

```
~130KB    /var/lib/docker/containers/a1b2c3...-json.log
```

~130KB from 500 requests. That is manageable. But extrapolate to a real service handling thousands of requests per day with verbose logging — the log file grows continuously and Docker never truncates or rotates it by default.

The fix is log rotation, configured in `docker-compose.yml`:

```yaml
backend:
  build: ./backend
  image: backend:v1
  logging:
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"
```

`max-size: "10m"` — each log file is at most 10MB before it rotates. `max-file: "3"` — keep at most 3 rotated files. Total disk usage for backend logs is bounded at 30MB regardless of how long the container runs or how many requests it handles.

After adding this and recreating the container, the log rotation was in effect. `docker compose logs` still works exactly the same way — it reads from the current log file. The only change is that the log storage is now bounded.

**Step 5 — Understanding what `docker compose logs` cannot do**

With log rotation configured, I noticed a side effect. After the log file rotated, `docker compose logs` only showed the current file — not the rotated ones. Log lines that had rolled out of the current file were not visible from `docker compose logs`.

This is the point where `docker compose logs` reaches its limit. It is a good tool for a local development session or a quick investigation. It is not a log aggregation system. For anything beyond that — keeping logs across container recreations, searching logs from all services in one place, alerting on specific log patterns — you need a log collector.

I added a simple log collector to the stack: Promtail feeding into Loki, readable through Grafana. This is a common lightweight stack for this purpose. But even before getting to that, the more immediate change was understanding what the application itself should be logging and ensuring those logs were meaningful.

**Step 6 — Reading logs across services properly**

One technique from step 17 that deserved more attention: reading all service logs together with timestamps.

```bash
docker compose logs --timestamps --tail 50
```

This interleaves the last 50 lines from every service with their actual timestamps. The order of events across services becomes visible. I could see:

```
2026-04-28T10:31:00Z backend   | INFO app Creating note: buy milk
2026-04-28T10:31:00Z backend   | INFO app Created note with id=4
2026-04-28T10:31:00Z db        | LOG:  statement: INSERT INTO notes...
2026-04-28T10:31:00Z backend   | 172.18.0.3 - - "POST /notes HTTP/1.1" 200 67
```

The backend logs its intent, the database logs the actual SQL, the backend logs the HTTP response. The full lifecycle of a single request, visible across service boundaries, in sequence.

Following logs live during an active incident:

```bash
docker compose logs -f --timestamps backend db
```

This follows backend and database logs together in real time. When a request fails, both sides of the failure are visible simultaneously — the application error and the database error that caused it, without switching between terminals.



## 3. Why It Happens

Docker captures container logs by intercepting stdout and stderr. Every process running as PID 1 in a container — which for this stack is Gunicorn — has its stdout and stderr captured by the Docker daemon. Anything written to stdout goes into the log. Anything written to stderr goes into the log. Anything written to a file inside the container does not.

This is important: **if the application writes logs to a file instead of stdout, Docker never sees them.** `docker compose logs` shows nothing. The logs exist only inside the container's writable layer, inaccessible from outside unless you exec in and read the file directly. In a container context, stdout is the logging transport. Every logging configuration in the application should ensure output goes to stdout.

Python's logging module writes to stderr by default, not stdout. This is fine — Docker captures both streams and `docker compose logs` shows both. But it means the application and Gunicorn may write to different streams. That is manageable, but worth knowing when you see `docker compose logs backend 2>&1` in scripts — the `2>&1` is redirecting stderr to stdout to merge them.

The log driver system exists because different environments have different needs for where logs go. A developer running locally wants `docker compose logs`. A production environment might want logs shipped directly to Elasticsearch, Splunk, or CloudWatch. The log driver is the mechanism Docker uses to route log output to the right destination. `json-file` is the default and the right choice for local development and simple server deployments. In managed container environments like ECS or GKE, the log driver is typically set at the platform level.



## 4. Solution

The complete logging setup for this stack:

**1. Configure log rotation in docker-compose.yml for every service that generates significant log volume:**

```yaml
services:
  backend:
    build: ./backend
    image: backend:v1
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    # ... rest of service config

  frontend:
    build: ./frontend
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    # ... rest of service config

  db:
    image: postgres:15
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    # ... rest of service config
```

**2. Configure the application to log to stdout with a useful format:**

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger(__name__)
```

**3. Log at the right level:**

```python
logger.debug("...")    # development detail — disable in production
logger.info("...")     # normal operations — what the app is doing
logger.warning("...")  # something unexpected but recoverable
logger.error("...")    # a failure that affected a request
logger.exception("...") # an error with full traceback
```

**4. Log errors with context:**

```python
@app.errorhandler(Exception)
def handle_exception(e):
    logger.exception(f"Unhandled exception on {request.method} {request.path}")
    return jsonify({"error": "internal server error"}), 500
```

A global error handler catches any unhandled exception and logs it with a full traceback. Without this, a bug in a route produces a 500 in the Gunicorn access log with no application-level context.



## 5. Deep Understanding

### What Docker Actually Captures

Every process in a container writes to file descriptors. File descriptor 1 is stdout. File descriptor 2 is stderr. Docker attaches to both of these at the container level — intercepting everything written to either stream and routing it through the log driver.

This is transparent to the application. The Flask app does not know Docker exists. The logging module writes to stderr (or stdout if configured that way). Docker intercepts the bytes at the file descriptor level and handles them.

The consequence is that log configuration inside the container (Python's logging module) and log handling outside the container (Docker's log driver) are independent layers. The application decides what to write and in what format. Docker decides where those bytes go after they leave the container.

### Structured Logging — Plain Text vs JSON

The current log format is human-readable plain text:

```
2026-04-28 10:31:00 INFO app Creating note: buy milk
```

This is easy to read in a terminal. It is hard to parse programmatically. Searching for all log lines where `id=4` across thousands of lines requires grep on free-form text.

The alternative is structured logging — emitting log lines as JSON:

```json
{"time": "2026-04-28T10:31:00Z", "level": "INFO", "logger": "app", "msg": "Creating note", "note": "buy milk"}
```

Every field is a named key with a typed value. A log aggregation system can index these fields and answer queries like "show me all requests where `id=4` in the last hour" without full-text search.

Python's standard logging module does not emit JSON by default. The `python-json-logger` library adds a JSON formatter:

```python
from pythonjsonlogger import jsonlogger

handler = logging.StreamHandler()
handler.setFormatter(jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
logging.getLogger().addHandler(handler)
```

For a local development stack, plain text is fine. For any system where logs will be collected and searched, structured JSON logging is worth the small setup cost.

### Log Levels and What They Mean in Practice

Log levels are a filter, not just a label. Setting `level=logging.INFO` means DEBUG messages are silently dropped — they are never written to stdout, never seen by Docker, never stored anywhere. This is how you control log verbosity without changing code:

```yaml
backend:
  environment:
    - LOG_LEVEL=DEBUG    # verbose during investigation
```

```python
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
```

In development, set `LOG_LEVEL=DEBUG` and see every internal operation. In production, leave it at `INFO` and see only normal operations and above. When diagnosing a specific issue in production, temporarily set `LOG_LEVEL=DEBUG` on the affected service and recreate the container — you get the verbose logs for that session without permanently changing the deployment.

### Log Drivers — The Full Picture

The `logging` section in docker-compose.yml configures which log driver Docker uses for that container. The main options:

`json-file` — logs written to JSON files on the host. Default. Works everywhere. Readable with `docker compose logs`. Requires manual rotation configuration to avoid unbounded growth.

`local` — similar to json-file but uses a more compact binary format and has rotation enabled by default. Not readable with standard tools outside Docker. Slightly more efficient for high-volume logging.

`none` — logs discarded entirely. Used deliberately for containers that produce noisy but unimportant output.

`syslog`, `journald`, `awslogs`, `gelf`, `fluentd` — logs shipped directly to an external system. When using these drivers, `docker compose logs` no longer works — the logs never hit the local filesystem. In managed cloud environments this is typically the correct choice: logs go directly to CloudWatch, Stackdriver, or whatever the platform provides.

The decision about which driver to use is an infrastructure decision, not an application decision. The application writes to stdout. The environment decides where those bytes go.

### `docker compose logs` Flags Worth Knowing

```bash
docker compose logs -f backend              # follow live
docker compose logs --timestamps backend    # include timestamps from Docker
docker compose logs --tail 100              # last 100 lines across all services
docker compose logs --since 30m             # logs from last 30 minutes
docker compose logs --since 2026-04-28T10:00:00  # logs since a specific time
docker compose logs backend db              # only backend and db, not frontend
```

`--since` is particularly useful for incident investigation. If something broke at 10:30 and you know it, `--since 10:25` gives you five minutes of context across all services without scrolling through hours of normal output.

### What Not to Log

Two things that seem useful but cause problems in production:

**Secrets in log lines.** A log statement like `logger.info(f"Connecting with password={password}")` puts credentials in a file on the host that may be readable by log aggregation systems, shipped to external services, and retained indefinitely. Log what the application is doing, not the values of sensitive variables.

**High-cardinality data at INFO level.** Every database query result, every request body, every user ID at INFO level produces enormous log volume that contains mostly noise. Debug level exists for this data. Info level should describe operations, not data.



## 6. Commands

```bash
# ── Reading Logs ───────────────────────────────────────────────────────────

docker compose logs                              # all services, all history
docker compose logs backend                      # single service
docker compose logs -f backend                   # follow live
docker compose logs --timestamps                 # include Docker-level timestamps
docker compose logs --tail 50                    # last 50 lines per service
docker compose logs --since 30m                  # last 30 minutes
docker compose logs --since 2026-04-28T10:00:00  # since specific time
docker compose logs backend db                   # multiple services together
docker compose logs -f --timestamps backend db   # follow two services live

# ── Inspecting Log Configuration ──────────────────────────────────────────

docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.LogConfig.Type}}'      # which log driver

docker inspect $(docker compose ps -q backend) \
  --format='{{.LogPath}}'                         # path to log file on host

docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.LogConfig.Config}}'     # log driver options

# ── Log File on Host ──────────────────────────────────────────────────────

# Find and check size of raw log file (requires sudo)
docker inspect $(docker compose ps -q backend) --format='{{.LogPath}}' | \
  xargs sudo du -sh

# Read raw JSON log format
docker inspect $(docker compose ps -q backend) --format='{{.LogPath}}' | \
  xargs sudo cat | head -5

# ── Filtering Logs ─────────────────────────────────────────────────────────

docker compose logs backend | grep ERROR          # filter for errors
docker compose logs backend | grep "notes"        # filter for a specific route
docker compose logs --timestamps | grep "10:31"   # filter by time pattern

# ── Log Rotation Verification ─────────────────────────────────────────────

# After configuring max-size and max-file, recreate the container and confirm:
docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.LogConfig.Config}}'
# Should show: map[max-file:3 max-size:10m]
```



## 7. Real-World Notes

In production environments, `docker compose logs` is rarely the final destination for log analysis. It is the first tool you reach for — it works immediately, requires nothing extra, and shows you what is happening right now. But logs that matter are usually forwarded somewhere persistent: an ELK stack (Elasticsearch, Logstash, Kibana), Loki with Grafana, CloudWatch, Datadog, or any of a dozen other systems.

The value of those systems is not just storage. It is searchability across time and across services. A question like "show me every request that touched note id=4 in the last 24 hours, across all services" is answerable in seconds in a log aggregation system with structured logs and takes manual effort with `grep` on raw files. The investment in structured logging at the application level pays off every time you need to answer a question like that.

Log rotation is non-negotiable for any container running longer than a few hours in production. Unrotated logs on a server running multiple containers for weeks have filled disks and caused outages. The `max-size` and `max-file` options in docker-compose.yml cost nothing to configure and prevent a category of operational failure.

The most common logging mistake in containerised applications is writing logs to a file inside the container instead of stdout. Frameworks and libraries sometimes default to file-based logging. The result is that `docker compose logs` shows nothing, the developer assumes the application is not logging, adds more `print()` statements, and never finds the file that has everything. Always verify that log output reaches stdout. The test is simple: `docker compose logs` should show your application's log output within seconds of the container starting.



## 8. Exercises

**Exercise 1 — Find your log files on disk**

With the stack running, run:

```bash
docker inspect $(docker compose ps -q backend) --format='{{.LogPath}}'
```

Note the path. Check its size with `sudo du -sh <path>`. Open it and read the raw JSON format. Send a few requests with `curl http://localhost:5000/notes` and watch the file grow. This makes the logging layer concrete — not an abstraction, but an actual file on your filesystem.

**Exercise 2 — Add application logging to app.py**

Add the `logging.basicConfig` configuration and a `logger` to `app.py`. Add at least one `logger.info()` call to each route — log what the route is doing and what it returned. Rebuild and send requests. Use `docker compose logs backend` to confirm your log lines appear alongside the Gunicorn access log. Then use `grep` to filter just your application log lines from the Gunicorn lines.

**Exercise 3 — Configure log rotation**

Add the `logging` block with `max-size: "10m"` and `max-file: "3"` to the backend service in docker-compose.yml. Recreate the container (`docker compose up -d backend`). Verify the configuration took effect:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.LogConfig.Config}}'
```

Confirm you see `max-file:3 max-size:10m` in the output.

**Exercise 4 — Use `--since` to investigate a time window**

Send a burst of requests, wait 2 minutes, send another burst. Now use:

```bash
docker compose logs --since 1m backend
```

Confirm you only see the second burst. Then use:

```bash
docker compose logs --since 3m backend
```

Confirm you see both bursts. This is the `--since` flag as it works in a real incident investigation — narrowing to the relevant time window without reading the full log history.

**Exercise 5 — Follow two services simultaneously**

Open two terminals. In the first, run:

```bash
docker compose logs -f --timestamps backend db
```

In the second, run:

```bash
curl -X POST http://localhost:5000/notes \
  -H "Content-Type: application/json" \
  -d '{"note": "test logging"}'
```

Watch the first terminal. You should see the backend log the operation and the Postgres log the SQL statement, interleaved with timestamps showing they happened within milliseconds of each other. This is the technique for watching a request travel through two services simultaneously.

**Exercise 6 — Add a global error handler**

Add this to `app.py`:

```python
@app.errorhandler(Exception)
def handle_exception(e):
    logger.exception(f"Unhandled exception on {request.method} {request.path}")
    return jsonify({"error": "internal server error"}), 500
```

Then temporarily break a route by adding `raise Exception("test error")` at the top of `get_notes`. Rebuild and hit `curl http://localhost:5000/notes`. Check `docker compose logs backend` — you should see the full Python traceback in the logs, not just Gunicorn's `500` line. Remove the `raise Exception` and rebuild. This is the difference between a 500 that tells you nothing and a 500 that tells you exactly what broke and where.

**Exercise 7 — Prove stdout vs file logging**

Temporarily add this to `app.py` just after the Flask app is created:

```python
file_handler = logging.FileHandler('/app/app.log')
logging.getLogger().addHandler(file_handler)
```

Rebuild and send requests. Run `docker compose logs backend` — the file-based log lines do not appear. Now exec into the container and read the file directly:

```bash
docker compose exec backend cat /app/app.log
```

The logs are there — inside the container, invisible to Docker. Remove the file handler and rebuild. This exercise makes tangible the rule that container logs must go to stdout to be visible outside the container.