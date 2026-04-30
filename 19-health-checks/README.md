# 19 — Health Checks



## 0. Goal of This Step

Understand how Docker's health check system works, why "running" is not the same as "healthy", and how to make the stack aware of its own state — so that dependency ordering, restart decisions, and operational visibility are based on actual application health rather than process existence.



## 1. What Problem It Solves

In step 18, we made the stack tell us what it was doing through logs. That was progress — structured logs, log rotation, application-level context on every operation. But logs are still a reactive tool. You read them after something went wrong, or you follow them live while watching.

The problem that remains is in the dependency chain. The `depends_on` directive in `docker-compose.yml` ensures the database container starts before the backend container. But "started" means the container process is running — it does not mean Postgres is ready to accept connections. The backend can start, call `init_db()` immediately, and find that Postgres is still in its startup sequence. The retry logic in `init_db()` handles this gracefully right now but that retry logic is compensating for something Docker should know how to manage.

There is also the silent failure problem from step 17. A container that starts successfully and then breaks internally looks identical to a healthy container from `docker compose ps`. If the backend's database connection pool becomes exhausted, or if a worker gets stuck in an infinite loop, or if the database goes away after startup — the container status still shows `running`. Docker has no way to distinguish a functioning container from one that is silently failing, unless you tell it how to check.

Health checks are how you tell Docker what "healthy" means for each service.



## 2. What Happened (Experience)

The stack from step 18 was running with structured logging and log rotation. Everything looked correct from the outside. I started thinking about what Docker actually knew about the state of each service versus what I knew from reading the logs.

**Step 1 — Seeing what Docker thinks "healthy" means right now**

I checked the health status of each container:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.State.Health}}'
```

```
<nil>
```

No value. Docker has no concept of health for this container — there is no health check defined, so Docker does not check. The container is either running or it is not. That is the entire state model.

I checked the database container too:

```bash
docker inspect $(docker compose ps -q db) \
  --format='{{.State.Health}}'
```

Also no value. Postgres ships with a tool called `pg_isready` that can check readiness but a health check in Compose is not automatic. You have to define it yourself. Neither container had one.

Then I looked at what `docker compose ps` actually shows when no health check is defined:

```
NAME       SERVICE    STATUS    PORTS
backend    backend    running   0.0.0.0:5000->5000/tcp
db         db         running   5432/tcp
frontend   frontend   running   0.0.0.0:5001->5001/tcp
```

`running`. That is all Docker knows. I watched the backend logs at startup and saw the retry attempts from `init_db()` — the backend was waiting on Postgres while Docker was already calling the container `running`. The two views of the world were inconsistent.

**Step 2 — Adding a health check to the database**

Postgres ships with a tool called `pg_isready`. It connects to the database and exits with status 0 if Postgres is accepting connections, non-zero if not. It is the correct tool for this check.

I added a health check to the `db` service in `docker-compose.yml`:

```yaml
db:
  image: postgres:15
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U appuser -d appdb"]
    interval: 5s
    timeout: 5s
    retries: 5
    start_period: 10s
  environment:
    - POSTGRES_DB=appdb
    - POSTGRES_USER=appuser
    - POSTGRES_PASSWORD=secret
  volumes:
    - postgres-data:/var/lib/postgresql/data
  networks:
    - backend-network
```

The four timing parameters:

`interval: 5s` — run the health check every 5 seconds.
`timeout: 5s` — if the check takes longer than 5 seconds, treat it as failed.
`retries: 5` — a container becomes `unhealthy` after this many consecutive failures.
`start_period: 10s` — during the first 10 seconds after the container starts, failures do not count toward the retry limit. This gives Postgres time to start without immediately being marked unhealthy.

After adding this and bringing the stack down and up again:

```bash
docker compose down
docker compose up -d
```

I watched the database status change in real time:

```bash
watch docker compose ps
```

```
NAME   SERVICE   STATUS                    PORTS
db     db        starting                  5432/tcp
```

A few seconds later:

```
NAME   SERVICE   STATUS                        PORTS
db     db        Up 25 seconds (healthy)       5432/tcp
```

The new status — `healthy` — is Docker reporting the result of the `pg_isready` check. Not just that the container process is running, but that Postgres inside the container is accepting connections.

**Step 3 — Making the backend wait for a healthy database**

With a health check defined on the database, I could update the `depends_on` for the backend to wait not just for the database container to start, but for it to become healthy:

```yaml
backend:
  build: ./backend
  depends_on:
    db:
      condition: service_healthy
```

Previously: `depends_on: - db` — Docker starts the backend after the database container process starts. Postgres might not be ready.

Now: `condition: service_healthy` — Docker holds the backend container until the database health check passes. Postgres is confirmed accepting connections before the backend attempts `init_db()`.

I brought the stack down completely, deleted the volume to force a fresh database initialisation, and brought it up again:

```bash
docker compose down -v
docker compose up -d
docker compose logs --timestamps --follow
```

```
2026-04-29T10:00:00Z db       | database system is ready to accept connections
2026-04-29T10:00:02Z db       | (health check passing)
2026-04-29T10:00:02Z backend  | Backend application starting...
2026-04-29T10:00:02Z backend  | Database initialized successfully
```

The backend no longer started until the database was confirmed healthy. The retry logic in `init_db()` still exists — it is good defensive programming — but it no longer needs to compensate for a race condition. The race condition was eliminated by the health check dependency.

**Step 4 — Adding a health check to the backend**

The database was now telling Docker when it was ready. The backend had no equivalent signal. I could see from the logs that Gunicorn had started and workers were booted but Docker still had no way to confirm that the Flask application was actually accepting HTTP requests.

I added a dedicated `/health` route to `app.py`:

```python
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200
```

One route. No database query. No business logic. No external calls. Just a 200 response that proves the application process is alive and HTTP is working. I deliberately kept it this minimal — a health check that touches the database would make the backend's health depend on the database's availability, which conflates two separate concerns.

The next question was what command to use in the health check. `curl -f <url>` is the standard choice — it exits with a non-zero status code if the response is not 2xx, which is exactly the signal Docker needs. 

Alpine does not include curl by default. Since this project uses python:3.11.9-alpine, I added curl using Alpine's package manager:



```dockerfile
# add this in Dockerfile runtime
RUN apk add --no-cache curl
```

Then added the health check to the backend service:

```yaml
backend:
  build: ./backend
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
    interval: 10s
    timeout: 5s
    retries: 3
    start_period: 15s
```

`curl -f` specifically: without the `-f` flag, curl exits 0 even on a 500 response — the health check would always pass regardless of what the application returned. The `-f` flag is not optional.

> **Note:** If you prefer not to add curl to the image, Python's standard library works as an alternative: `test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:5000/health')\""]`. curl is cleaner and easier to read; urllib avoids the extra dependency.

After rebuilding and bringing the stack up:

```bash
docker compose ps
```

```
NAME       SERVICE    STATUS     PORTS
db         db         healthy    5432/tcp
backend    backend    healthy    0.0.0.0:5000->5000/tcp
frontend   frontend   running    0.0.0.0:5001->5001/tcp
```

The database and backend now showed `healthy`. The frontend showed `running` — no health check defined yet.

**Step 5 — Watching a health check fail**

I wanted to see what happens when a health check starts failing. I modified the `/health` route temporarily to return a 500:

```python
@app.route("/health")
def health():
    return jsonify({"status": "error"}), 500
```

Rebuilt the backend and waited. After the first health check failure, the status changed:

```bash
docker compose ps
```

```
NAME       SERVICE    STATUS                   PORTS
backend    backend    healthy:starting         0.0.0.0:5000->5000/tcp
```

Still healthy but showing starting — one failure is not enough. After three consecutive failures (`retries: 3`):

```
NAME       SERVICE    STATUS      PORTS
backend    backend    unhealthy   0.0.0.0:5000->5000/tcp
```

`unhealthy`. The container is still running — Docker does not automatically stop or restart a container that becomes unhealthy. This is an important distinction: the health check is a signal, not a recovery mechanism. It tells Docker the container is broken. What to do about that is the job of restart policies, which step 20 covers. In Docker Compose without a restart policy, an unhealthy container stays unhealthy and keeps running or not running until you intervene. `docker inspect` showed the full health check history:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{json .State.Health}}' | python -m json.tool
```

```json
{
  "Status": "unhealthy",
  "FailingStreak": 3,
  "Log": [
    {
      "Start": "2026-04-29T10:05:00Z",
      "End": "2026-04-29T10:05:00Z",
      "ExitCode": 22,
      "Output": "curl: (22) The requested URL returned error: 500"
    },
    ...
  ]
}
```

The health check log records every check attempt — the start and end time, the exit code, and the exact output from the check command. When something goes wrong, this log is often the fastest way to understand what the health check is seeing.

I reverted the `/health` route to return 200 and rebuilt. The status returned to `healthy` after three consecutive successful checks.

**Step 6 — Adding a health check to the frontend**

The dependency chain was almost complete. The backend waited for a healthy database. But the frontend still started as soon as the backend container process started — not when the backend was actually serving requests. I had seen this pattern before with the database, and the fix was the same.

The frontend's health check should confirm that the frontend itself is responding, not that it can reach the backend. Each service is responsible for its own health signal. I added the check to `docker-compose.yml`:

```yaml
frontend:
  build: ./frontend
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:5001/"]
    interval: 10s
    timeout: 5s
    retries: 3
    start_period: 10s
```

And tightened the `depends_on` condition to match:

```yaml
frontend:
  depends_on:
    backend:
      condition: service_healthy
```

The full dependency chain was now: `frontend` waits for `backend` to be healthy, `backend` waits for `db` to be healthy. The stack starts in sequence, each service confirmed operational before the next one begins. No more race conditions, no more retry logic compensating for timing — the orchestration handles it.



## 3. Why It Happens

Docker's default state model for a container is binary: the process is running, or it is not. This is the operating system's view of the world — a process either exists or it does not. Docker inherits this view.

But "the process is running" and "the application is healthy" are not the same thing. A Gunicorn process can be running with all workers stuck on database queries that will never complete. A Postgres process can be running during its startup sequence before it accepts connections. An application can start successfully and then gradually degrade as memory leaks accumulate or connection pools exhaust.

The health check system adds a third state: the container is running, and someone has verified that it is doing what it is supposed to do. That verification is whatever you define it to be — an HTTP request, a CLI command, a database query. Docker runs this check on a configurable schedule and maintains a rolling history of results.

A health check is not just a test — it is a contract between the service and the system running it. It defines what "working" means for that service in a way that Docker, orchestrators, and load balancers can all act on. Without that contract, every system that depends on your container has to guess.

The `depends_on` condition mechanism uses health status as a synchronisation primitive. `service_healthy` means "do not start this container until the other container's health check passes." This makes startup ordering based on application readiness rather than process existence — which is what the retry logic in `init_db()` was working around.



## 4. Solution

The complete health check configuration across all three services:

**`app.py` — add a `/health` endpoint to the backend:**

```python
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200
```

**Add curl for the health check command:**

```dockerfile
# Backend
RUN apk add --no-cache curl && \
    addgroup -g 1001 appgroup && \
    adduser -D -u 1001 -G appgroup appuser

# Frontend
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*
```

**`docker-compose.yml` — full configuration:**

```yaml
services:
  frontend:
    build: ./frontend
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5001/"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 10s
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    ports:
      - "5001:5001"
    environment:
      - BACKEND_URL=http://backend:5000

    networks:
      - frontend-network
    depends_on:
      backend:
        condition: service_healthy

  backend:
    build: ./backend
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 15s
    image: backend:v1
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
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
      db:
        condition: service_healthy

  db:
      image: postgres:15
      healthcheck:
        test: ["CMD-SHELL", "pg_isready -U appuser -d appdb"]
        interval: 5s
        timeout: 5s
        retries: 5
        start_period: 20s
      logging:
        driver: json-file
        options:
          max-size: "10m"
          max-file: "3"
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



## 5. Deep Understanding

### The Four Health Check Parameters

Every health check has four timing parameters. Understanding them prevents two common mistakes: checks that flip unhealthy during normal startup, and checks that are too infrequent to catch degradation quickly.

`interval` controls how often Docker runs the check. At 10 seconds, the container's health status is at most 10 seconds stale. Lower intervals give faster detection at the cost of more check overhead — for an HTTP check against a local endpoint this overhead is negligible, but for a check that queries a database it might matter.

`timeout` is the maximum time the check command is allowed to run before Docker treats it as failed. If the health check hangs — the application is completely unresponsive — the timeout prevents the check from waiting forever. Set this lower than `interval`.

`retries` is the number of consecutive failures required to transition from `healthy` to `unhealthy`. A single failed check does not immediately mark the container unhealthy. This prevents transient blips — a momentary spike in response time, a brief network hiccup — from triggering an unhealthy status. Three is a reasonable default for most services.

`start_period` is a grace period after container startup during which failures do not count toward `retries`. This is essential for services that take time to initialise. Without `start_period`, a slow-starting application would accumulate failed checks during normal startup and become `unhealthy` before it was even ready. During `start_period`, a successful check immediately transitions the container to `healthy` — the grace period ends as soon as the check passes, not when the timer expires.

### What the Health Check Command Should Be

The check command runs inside the container, in the same environment as the application. Whatever command you use must be available in the image.

For HTTP services: `curl -f <url>` is the standard choice. `-f` makes curl exit with status 22 if the response code is 4xx or 5xx, which Docker treats as a failed check. Without `-f`, curl exits successfully even on 500 responses — the health check always passes regardless of application state.

For databases: use the database's own readiness tool. `pg_isready` for Postgres, `mysqladmin ping` for MySQL, `redis-cli ping` for Redis. These tools are designed exactly for this purpose and are already present in the official images.

For services without curl or wget: the Python standard library includes `urllib.request`. It is verbose but requires nothing extra installed. For production images where minimising layers matters more than convenience, `urllib.request` avoids adding `curl` as a dependency just for health checking.

The check command's exit code is all that matters. Exit 0 → healthy. Any non-zero exit code → failed check.

### The Three Health States

A container with a defined health check has three possible states:

`starting` — the container has started but has not yet passed its first health check. This is the initial state. During `start_period`, the container stays in `starting` even if checks fail. Once a check passes, the container moves to `healthy`.

`healthy` — the most recent check passed. Docker considers the container operational.

`unhealthy` — the last `retries` consecutive checks failed. The container is still running — Docker does not stop or restart it automatically in Docker Compose. It is a signal, not an action. Taking action on unhealthy containers is handled by restart policies (step 20) or orchestrators like Kubernetes.

This is an important distinction. An `unhealthy` container in Docker Compose is visible but not acted upon. Docker is telling you something is wrong. What you do about it is up to the deployment configuration.

### What the `/health` Endpoint Should and Should Not Do

The health endpoint exists to answer one question: is this application process alive and capable of handling requests? It should be as simple as possible.

It should not query the database. If the database is down and the health check queries the database, the backend becomes `unhealthy` — which is accurate, but it means the backend appears broken even if every non-database endpoint is functioning. A health endpoint that depends on external services makes the health status of your container a function of its dependencies' health, which is a different problem.

It should not do anything slow. The health check runs every `interval` seconds indefinitely. Any latency in the health endpoint adds up across thousands of checks over days of uptime.

It should return 200 on success and any non-2xx code on failure. Some teams add a deeper check that tests database connectivity and returns 503 if the database is unreachable — this is called a "liveness check with dependency validation." It is useful for load balancer health checks (remove the backend from rotation if it cannot reach the database) but needs careful thought in Docker Compose, where an unhealthy backend does not trigger any automatic remediation.

### `depends_on` Conditions

Three conditions are available:

`service_started` — the default when you write `depends_on: - db`. The dependent container starts after the dependency container's process starts. No health check required.

`service_healthy` — the dependent container starts after the dependency's health check passes. Requires a health check defined on the dependency.

`service_completed_successfully` — the dependent container starts after the dependency exits with status 0. Used for one-shot initialisation containers — a database migration container that runs, migrates, and exits before the application starts.

`service_healthy` is the right condition for any long-running service that has a meaningful health check. It replaces retry-based startup logic in application code with a system-level guarantee.

### Reading Health Check History

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{json .State.Health}}' | python3 -m json.tool
```

The health check history log contains the last five check results by default. Each entry has:

- `Start` / `End` — the exact time the check ran and completed
- `ExitCode` — 0 for success, non-zero for failure
- `Output` — the stdout and stderr from the check command

When a health check is failing and you do not understand why, this log shows you exactly what the check command produced. A `curl: (7) Failed to connect` means the application is not listening. A `curl: (22) The requested URL returned error: 503` means the application is responding but returning an error. These are different problems with different diagnoses.



## 6. Commands

```bash
# ── Checking Health Status ─────────────────────────────────────────────────

docker compose ps                               # shows healthy/unhealthy/running/starting

# Health status of a specific container
docker inspect $(docker compose ps -q backend) \
  --format='{{.State.Health.Status}}'

# Full health check history (last 5 checks)
docker inspect $(docker compose ps -q backend) \
  --format='{{json .State.Health}}' | python3 -m json.tool

# Health status of all containers at once
docker inspect $(docker compose ps -q) \
  --format='{{.Name}}: {{.State.Health.Status}}'

# ── Watching Health Status Change ─────────────────────────────────────────

watch docker compose ps                         # refresh every 2s — watch starting → healthy

# ── Testing the Health Check Manually ─────────────────────────────────────

# Run the same command Docker runs, from inside the container
docker compose exec backend curl -f http://localhost:5000/health
docker compose exec db pg_isready -U appuser -d appdb

# ── Startup Ordering Verification ─────────────────────────────────────────

docker compose down -v                          # full teardown including volumes
docker compose up -d                            # fresh startup
docker compose logs --timestamps --follow       # watch startup sequence across services

# ── Triggering and Observing an Unhealthy State ───────────────────────────

# Temporarily break the health endpoint, wait for retries to exhaust
# docker compose ps will show: unhealthy
# docker inspect shows the failing check output
```



## 7. Real-World Notes

Health checks are required infrastructure in any environment that makes automated decisions based on container state. Kubernetes uses liveness and readiness probes — the same concept, different names. Load balancers use health endpoints to decide which backend instances receive traffic. Autoscalers use health status to decide whether to replace an instance. Without health checks, all of these systems fall back to "is the process running?" which is an insufficient signal.

In Docker Compose specifically, an `unhealthy` container does not automatically restart — that is covered by restart policies in step 20. Health checks signal state. Restart policies and orchestrators decide what to do about that state. Understanding them as separate concerns prevents confusion when a container becomes unhealthy and nothing happens automatically. Docker is not broken, it is waiting for you to define the response.

The `/health` endpoint convention is so common it has become a de facto standard. Most reverse proxies, load balancers, and monitoring systems expect to find a health endpoint at `/health` or `/healthz` (the Kubernetes convention). Implementing it in step 19 means the application already speaks this convention before it ever gets near an orchestrator.

A subtle point about `start_period`: it does not delay the container from being usable — it only delays failed checks from counting. If the application starts in 3 seconds and `start_period` is 15 seconds, the container becomes `healthy` at 3 seconds when the first check passes. `start_period` is a ceiling on how long startup failures are tolerated, not a mandatory wait time.

The most common health check mistake is making the check too complex — querying external services, doing database writes, running expensive operations. A health check that causes side effects or takes significant time will degrade the service it is meant to protect. Keep the check minimal: prove the process is alive and can respond.



## 8. Exercises

**Exercise 1 — Watch the startup sequence with health checks**

Bring the entire stack down including volumes and bring it back up while following logs:

```bash
docker compose down -v
docker compose up -d
watch docker compose ps
```

In a second terminal, follow the logs with timestamps:

```bash
docker compose logs --timestamps --follow
```

Observe the database move from `starting` to `healthy`. Observe the backend hold in `starting` while the database health check runs. Observe the backend move to `healthy` only after the database confirms ready. Map the log timestamps to the status transitions in `docker compose ps`.

**Exercise 2 — Read the health check history**

With the stack running and healthy, inspect the full health check log for the backend:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{json .State.Health}}' | python3 -m json.tool
```

Read every field in the output. Find the `FailingStreak` (should be 0). Find the last check's `ExitCode` (should be 0). Find the `Output` from the curl command. Now do the same for the database container. Compare the two structures.

**Exercise 3 — Trigger and observe an unhealthy state**

Temporarily modify the `/health` route in `app.py` to return 500:

```python
@app.route("/health")
def health():
    return jsonify({"status": "error"}), 500
```

Rebuild the backend. Watch `docker compose ps` — count the seconds until the status moves from `healthy` to `unhealthy`. Calculate how many checks ran based on the `interval` and `retries` settings. Read the health check history with `docker inspect` and find the exact curl error in the output. Revert the route, rebuild, and watch the container return to `healthy`.

**Exercise 4 — Test the health check commands manually**

Run the same commands Docker runs for each service's health check, but manually from inside the container:

```bash
docker compose exec backend curl -f http://localhost:5000/health
docker compose exec db pg_isready -U appuser -d appdb
```

Confirm both succeed. Now test what happens when the check would fail — stop Postgres temporarily and run `pg_isready` again:

```bash
docker compose stop db
docker compose exec db pg_isready -U appuser -d appdb
```

The command fails with a non-zero exit code. Start the database again. This gives you direct experience with the commands that Docker is running on your behalf.

**Exercise 5 — The `start_period` experiment**

Set `start_period: 0s` on the backend health check. Bring the stack completely down and up again:

```bash
docker compose down -v
docker compose up -d
watch docker compose ps
```

Watch what happens to the backend during startup. Depending on how quickly Gunicorn and `init_db()` complete, you may see the backend briefly flash `unhealthy` before settling to `healthy` — failed checks during startup now count immediately. Restore `start_period: 15s`, bring the stack down and up again, and observe that the backend stays in `starting` during initialisation without flipping to `unhealthy`. This makes the purpose of `start_period` concrete.

**Exercise 6 — Verify `depends_on` with `service_healthy` is working**

Remove the `service_healthy` condition from the backend's `depends_on` — revert to:

```yaml
depends_on:
  - db
```

Bring the stack fully down including volumes and up again, following logs with timestamps:

```bash
docker compose down -v && docker compose up -d
docker compose logs --timestamps --follow
```

Watch whether the backend starts before Postgres is ready. You may see the `init_db()` retry messages as the backend polls for a database that is still initialising. Restore `condition: service_healthy`, repeat, and observe the backend wait cleanly for a healthy database before starting.

**Exercise 7 — Health check without curl**

Remove the `curl` installation from the Dockerfile. Change the backend health check to use Python's `urllib.request` instead:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:5000/health')\""]
  interval: 10s
  timeout: 5s
  retries: 3
  start_period: 15s
```

Rebuild and confirm the container still transitions to `healthy`. This demonstrates that curl is a convenience, not a requirement — the health check mechanism works with any command available in the image.