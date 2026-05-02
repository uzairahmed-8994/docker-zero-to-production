# 27 — Production Troubleshooting



## 0. What This Step Is

Step 26 was eight case studies — how investigations moved from symptom to root cause in unhurried retrospect. This step is different. It is a reference you open when production is broken right now and you need to know what to do in the next two minutes.

The structure follows the real incident-response sequence: triage first, stabilise second, investigate third. Root cause comes after the service is back up — not before. This order matters more than any individual command.

Everything here assumes the three-service stack built across steps 14–25. The patterns apply to any Docker Compose deployment.

---



## 1. Incident Response Flow



### Phase 1 — Triage (minutes 0–2)

**Goal: determine what is broken and how badly.**

```bash
docker compose ps
```

Read the STATUS column.

| Status | Meaning |
|---|---|
| `healthy` | Container running, health check passing |
| `unhealthy` | Container running, health check failing |
| `starting` | Container starting or within `start_period` |
| `restarting` | Container exited, restart policy triggered |
| `exited (N)` | Container stopped, N is the exit code |
| `running` | No health check defined — unknown actual state |

```bash
docker stats --no-stream
```

Read CPU and memory.

| Signal | What it means |
|---|---|
| MEM % above 85% | Near memory limit — OOM kill risk |
| CPU % pegged at limit | CPU throttled — service is slower than it should be |
| MEM USAGE / LIMIT shows full host RAM | No resource limit set |

**After 2 minutes you should know:** which service is affected, whether it is down (exited/restarting) or degraded (unhealthy/high resources), and whether it is one service or multiple.



### Phase 2 — Stabilisation (minutes 2–5)

**Goal: restore service to users as fast as possible. Root cause comes later.**

If the container is restarting or exited and the previous version was stable:

```bash
# Rollback to the last known good image
# Edit docker-compose.yml: change the image tag to the previous version
docker compose pull
docker compose up -d
```

If the container is unhealthy but still running and a restart might clear it:

```bash
docker compose restart backend
```

If you do not know the previous good tag and need to buy time:

```bash
docker compose logs --tail 50 backend
# Read the error. Is it a config issue? A code bug? A dependency?
# If config: fix the environment variable, restart
# If code: rollback the image
# If dependency (DB down): restart the dependency first
```

**The rule: stabilise before investigating.** A service that is back up and serving degraded traffic is better than a service that is down while you read logs. Users are waiting.



### Phase 3 — Investigation (minutes 5–20)

**Goal: understand what caused the failure.**

Run these in order. Stop when you find the cause.

**Step 1 — Read the logs**

```bash
docker compose logs --tail 100 backend
docker compose logs --timestamps --since 30m
```

Look for: the first error line (not the last — the last is usually a consequence), exception tracebacks, connection refused messages, authentication failures.

**Step 2 — Verify what is actually running**

```bash
# Which image is the container running?
docker inspect $(docker compose ps -q backend) \
  --format='Image={{.Config.Image}} Digest={{index .RepoDigests 0}}'

# Is it the image you think it is?
# Compare against what docker-compose.yml specifies
```

This step is skipped more often than it should be. The container that is misbehaving may not be running the image you think it is.

**Step 3 — Verify environment variables**

```bash
docker compose exec backend env | sort
```

Look for: empty values where values are expected, wrong values, missing variables entirely. An empty value and a missing variable look different — `KEY=` versus no output at all.

**Step 4 — Check health check history**

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{json .State.Health}}' | python -m json.tool
```

The `Log` array contains the last five health check results with exact output and exit codes. If the health check is failing, this shows exactly what the check command returned.

**Step 5 — Check restart count and exit history**

```bash
docker inspect $(docker compose ps -q backend) \
  --format='RestartCount={{.RestartCount}} ExitCode={{.State.ExitCode}}'
```

A restart count climbing means the problem is recurring. Exit code 0 means a clean exit (deliberate shutdown or clean crash). Non-zero means an error exit or a kill signal.

**Step 6 — Check resource usage history**

```bash
docker stats --no-stream
```

If memory is near the limit, the OOM killer may be involved even if the container is currently running.



### Phase 4 — Root Cause

**Goal: identify the single specific cause.**

Narrow the problem to one layer:

```
Application layer  →  docker compose logs
Configuration      →  docker compose exec backend env
Network layer      →  docker compose exec backend curl / ping
Database layer     →  docker compose exec db pg_isready
Resource layer     →  docker stats
Image layer        →  docker inspect (image digest, labels)
```

Eliminate layers one at a time. Do not jump to conclusions. The symptom is almost never in the same place as the cause.



### Phase 5 — Fix and Verify

After applying the fix:

```bash
# Confirm the service is healthy
docker compose ps

# Confirm the restart count stopped climbing
docker inspect $(docker compose ps -q backend) \
  --format='RestartCount={{.RestartCount}}'

# Confirm the health check is passing
docker inspect $(docker compose ps -q backend) \
  --format='{{.State.Health.Status}}'

# Send a real request and verify the response
curl -f http://localhost:5000/health
curl http://localhost:5000/notes

# Watch logs for 2 minutes to confirm stability
docker compose logs -f --timestamps backend
```

Do not close the incident until all three are true: the service is `healthy`, the restart count has stopped climbing, and real requests are succeeding.

---



## 2. Failure Playbooks



### Container Not Starting

**Symptoms**

`docker compose ps` shows `exited` immediately after `docker compose up`. The container never reaches `starting` or `healthy`.

**Commands**

```bash
# Read the exit reason
docker compose logs backend

# Check exit code
docker inspect $(docker compose ps -q backend) \
  --format='ExitCode={{.State.ExitCode}} Error={{.State.Error}}'

# Try running the container interactively to see the startup error
docker run --rm -it \
  --env-file .env \
  -e DB_HOST=db \
  myusername/backend:v1.0.1 \
  /bin/sh
```

**What to look for**

| Log output | Cause |
|---|---|
| `ModuleNotFoundError` | Dependency missing from `requirements.txt` |
| `No such file or directory` | Missing file in image — wrong COPY path |
| `permission denied` | File ownership issue — check `--chown` in Dockerfile |
| `port already in use` | Another process on the host using the same port |
| `exec format error` | Image built for wrong CPU architecture |
| Exit code 1, no log | Application panicked before logging initialised |

**Next action**

If exit code 1 with no readable output: run the container interactively to see the raw error. If `ModuleNotFoundError`: the image is missing a dependency — fix `requirements.txt` and rebuild. If port conflict: identify and stop the conflicting process or change the host port mapping.



### Container Restarting

**Symptoms**

`docker compose ps` shows `restarting`. The restart count in `docker inspect` is climbing. The service is intermittently available.

**Commands**

```bash
# Current restart count
docker inspect $(docker compose ps -q backend) \
  --format='RestartCount={{.RestartCount}} ExitCode={{.State.ExitCode}}'

# Logs from the most recent startup attempt
docker compose logs --tail 50 backend

# System-level OOM events
sudo dmesg | grep -i "oom\|killed" | tail -10

# Check if something external is restarting the container
crontab -l
sudo crontab -l
```

**What to look for**

| Exit code | Cause |
|---|---|
| 0 | Clean exit — application shut itself down, or something sent SIGTERM |
| 1 | Application error at startup |
| 137 | SIGKILL — OOM killer or manual `docker kill` |
| 139 | Segfault |
| 143 | SIGTERM — graceful shutdown signal |

**Next action**

Exit code 137 with `dmesg` showing OOM: the container hit its memory limit. Increase the limit or find the memory leak. Exit code 0 repeatedly: something is sending a stop signal — check cron jobs, scripts, and health check configurations. Exit code 1: the application is crashing on startup — read the logs for the specific error.



### 500 / 502 Errors

**Symptoms**

HTTP requests are returning 500 (application error) or 502 (upstream connection failure). Users are seeing errors.

**Commands**

```bash
# What error is the application logging?
docker compose logs --tail 50 backend | grep -i "error\|exception\|traceback"

# Is the backend container actually running?
docker compose ps backend

# Can the frontend reach the backend?
docker compose exec frontend curl -f http://backend:5000/health

# Is the database reachable from the backend?
docker compose exec backend python -c "
import psycopg2, os
conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    port=os.getenv('DB_PORT'),
    database=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD')
)
print('DB connection: ok')
conn.close()
"
```

**What to look for**

| Error | Cause |
|---|---|
| 502 from frontend | Backend container is down or not accepting connections |
| 500 with traceback in logs | Application exception — read the specific traceback |
| `Connection refused` in backend logs | Database is down or backend is misconfigured |
| `authentication failed` in backend logs | Wrong database credentials |
| 500 with no log output | Exception handler swallowing the error — check the global error handler |

**Next action**

502: confirm the backend container is running and healthy. If it is running but returning 502, check that the frontend's `BACKEND_URL` environment variable is correct and that both services are on the same Docker network. 500 with a traceback: read the traceback — it identifies the exact line and error type.



### Slow Performance

**Symptoms**

Requests are completing but taking seconds instead of milliseconds. `docker stats` may show elevated CPU or memory. No errors in logs.

**Commands**

```bash
# Resource usage
docker stats --no-stream

# Is the container CPU-throttled?
docker inspect $(docker compose ps -q backend) \
  --format='NanoCPUs={{.HostConfig.NanoCPUs}} Memory={{.HostConfig.Memory}}'

# Add timing to a slow endpoint temporarily
docker compose exec backend python -c "
import time, urllib.request
start = time.time()
urllib.request.urlopen('http://localhost:5000/notes')
print(f'Response time: {time.time()-start:.3f}s')
"

# Check database query performance
docker compose exec db psql -U appuser -d appdb \
  -c "SELECT pid, query, query_start, state FROM pg_stat_activity WHERE state='active';"

# Check for long-running queries
docker compose exec db psql -U appuser -d appdb \
  -c "SELECT query, extract(epoch FROM now()-query_start) AS seconds
      FROM pg_stat_activity
      WHERE state='active' AND query_start IS NOT NULL
      ORDER BY seconds DESC LIMIT 5;"
```

**What to look for**

| Signal | Cause |
|---|---|
| CPU % at or near NanoCPUs limit | CPU throttled — limit too low for load |
| MEM % above 90% | Near memory limit — GC pressure causing slowdowns |
| Long-running Postgres queries | Missing index, slow query, lock contention |
| Slow DB connection time | Connection pool exhausted, too many connections |
| Slow at application layer only | Code-level issue — N+1 queries, synchronous I/O |

**Next action**

CPU-throttled: increase the CPU limit and monitor. Memory pressure: check for leaks. Slow DB queries: run `EXPLAIN ANALYZE` on the specific query to identify missing indexes or bad query plans.



### Wrong Data / Inconsistent Behaviour

**Symptoms**

No errors. Requests succeed. But responses contain stale data, missing records, or data that changes between requests for no apparent reason.

**Commands**

```bash
# How many Gunicorn workers are running?
docker compose exec backend ps aux | grep gunicorn

# Is there any in-process caching?
docker compose exec backend grep -r "cache\|_cache\|global" /app/*.py

# Check which worker handled each request (add worker PID to logs temporarily)
# Then make requests and see if different PIDs return different data

# Verify the database has the expected data directly
docker compose exec db psql -U appuser -d appdb \
  -c "SELECT COUNT(*), MAX(created_at) FROM notes;"
```

**What to look for**

| Signal | Cause |
|---|---|
| Different responses to identical requests | Per-worker in-process cache with independent state |
| Data appears then disappears | Multi-worker cache inconsistency |
| Database has correct data but API returns stale | In-memory cache not invalidated on write |
| Data correct sometimes, wrong sometimes | Race condition between workers |

**Next action**

If multiple Gunicorn workers exist and the application has module-level mutable state: the state is per-worker and inconsistent by design. Remove the in-process cache or move it to a shared external store (Redis, database). Verify the database directly to confirm the data is correct at the source — if it is correct in the DB and wrong in the API, the problem is between the DB and the response.



### Network / Connectivity Issues

**Symptoms**

One service cannot reach another. DNS resolution fails. Connections time out or are refused.

**Commands**

```bash
# Can the backend reach the database by hostname?
docker compose exec backend ping -c 3 db

# Can the backend resolve the database hostname?
docker compose exec backend python -c "import socket; print(socket.gethostbyname('db'))"

# Is the database port actually open?
docker compose exec backend python -c "
import socket
s = socket.socket()
s.settimeout(3)
result = s.connect_ex(('db', 5432))
print('Port open' if result == 0 else f'Port closed (error {result})')
s.close()
"

# What networks is each container on?
docker inspect $(docker compose ps -q backend) \
  --format='{{json .NetworkSettings.Networks}}' | python -m json.tool

docker inspect $(docker compose ps -q db) \
  --format='{{json .NetworkSettings.Networks}}' | python -m json.tool

# Are the two services on the same network?
docker network ls
docker network inspect <network-name>
```

**What to look for**

| Error | Cause |
|---|---|
| `ping: db: Name or service not known` | Services on different Docker networks |
| `Connection refused` | Service is down or not listening on that port |
| `Connection timed out` | Network ACL, firewall, or wrong host/port |
| DNS resolves but connection fails | Service is on the right network but not running |

**Next action**

DNS failure (`Name not known`): the services are on different networks. Check `docker-compose.yml` — both services must be on the same named network. Connection refused: the target service is not running or not listening. Connection timeout: check firewall rules on the host and verify the port mapping is correct.



### Resource Exhaustion

**Symptoms**

Container OOM-killed (exit code 137). Requests time out. Host becomes unresponsive. `docker stats` shows memory at 100% of limit.

**Commands**

```bash
# Current memory usage vs limit
docker stats --no-stream --format \
  "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}"

# OOM kill events
sudo dmesg | grep -i "oom\|kill" | tail -20

# Memory limit configured
docker inspect $(docker compose ps -q backend) \
  --format='MemoryLimit={{.HostConfig.Memory}}'
# 0 = no limit (dangerous)

# Memory growth over time — run in background
while true; do
  echo "$(date '+%H:%M:%S'): $(docker stats --no-stream \
    --format '{{.MemUsage}}' backend)"
  sleep 60
done

# Inside the container — what Python objects exist?
docker compose exec backend python -c "
import gc
gc.collect()
counts = {}
for obj in gc.get_objects():
    t = type(obj).__name__
    counts[t] = counts.get(t, 0) + 1
for name, count in sorted(counts.items(), key=lambda x: -x[1])[:10]:
    print(f'{count:8d}  {name}')
"
```

**What to look for**

| Signal | Cause |
|---|---|
| Linear memory growth over hours/days | Memory leak in application code |
| Sudden spike then OOM kill | Single request allocating too much |
| Memory limit = 0 | No limit set — container can take down the host |
| High object counts in GC output | Uncollected objects — circular references, module-level lists |

**Next action**

No memory limit set: add one immediately (step 21). OOM kill with limit set: determine whether the limit is too low for normal operation or whether there is a leak. Check the memory growth pattern — linear = leak, spiky = specific operation. If a leak, identify the object type accumulating and trace it back to the code that creates and holds those objects.

---



## 3. Command Reference

Every command listed here. Purpose and signal for each.


### State Commands

```bash
# Full stack status — first command to run in any incident
docker compose ps

# Single service status
docker compose ps backend

# All containers on the host (not just this compose project)
docker ps -a

# Detailed container state including exit code, health, restart count
docker inspect $(docker compose ps -q backend) \
  --format='
  Status:        {{.State.Status}}
  ExitCode:      {{.State.ExitCode}}
  RestartCount:  {{.RestartCount}}
  Health:        {{.State.Health.Status}}
  Image:         {{.Config.Image}}
  '

# What image is actually running (digest = immutable ID)
docker inspect $(docker compose ps -q backend) \
  --format='{{index .RepoDigests 0}}'

# Resource limits configured
docker inspect $(docker compose ps -q backend) \
  --format='Memory={{.HostConfig.Memory}} NanoCPUs={{.HostConfig.NanoCPUs}}'
```


### Log Commands

```bash
# Last 100 lines, all services
docker compose logs --tail 100

# Single service, follow live
docker compose logs -f backend

# With timestamps (use for cross-service correlation)
docker compose logs --timestamps backend

# Last 30 minutes
docker compose logs --since 30m backend

# Specific time window
docker compose logs --since 2026-04-29T10:00:00 --until 2026-04-29T10:30:00 backend

# Filter for errors only
docker compose logs backend | grep -i "error\|exception\|traceback\|fatal"

# Follow two services simultaneously
docker compose logs -f --timestamps backend db
```


### Debug Inside Container

```bash
# Open a shell
docker compose exec backend /bin/sh

# Run a single command
docker compose exec backend env | sort
docker compose exec backend cat /app/app.py

# Check environment variables
docker compose exec backend env | grep DB_
docker compose exec backend env | grep -v "^PATH\|^HOSTNAME"

# Test database connectivity from inside the backend
docker compose exec backend python -c "
import psycopg2, os
conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    database=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD')
)
print('connected')
conn.close()
"

# Check what processes are running inside the container
docker compose exec backend ps aux

# Check which files the application has open
docker compose exec backend ls -la /proc/1/fd | head -20
```


### Resource Commands

```bash
# Live resource usage — refreshes every second
docker stats

# Single snapshot — useful in scripts
docker stats --no-stream

# Custom format — name, CPU, memory usage, memory percent
docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

# All containers, no stream, sorted by memory
docker stats --no-stream --format "{{.Name}} {{.MemPerc}}" | sort -k2 -rn
```


### Network Commands

```bash
# List all Docker networks
docker network ls

# See which containers are on a network
docker network inspect backend-network

# Test DNS resolution from inside a container
docker compose exec backend python -c \
  "import socket; print(socket.gethostbyname('db'))"

# Test port connectivity
docker compose exec backend python -c "
import socket
s = socket.socket()
s.settimeout(3)
r = s.connect_ex(('db', 5432))
print('open' if r == 0 else f'closed ({r})')
"

# Check which host ports are bound
ss -tlnp | grep -E "5000|5001|5432"
```


### Recovery Commands

```bash
# Restart a single service
docker compose restart backend

# Recreate a single service (picks up config changes)
docker compose up -d backend

# Recreate all services
docker compose up -d

# Pull latest images and recreate
docker compose pull && docker compose up -d

# Force recreate even if nothing changed
docker compose up -d --force-recreate backend

# Full teardown and restart (loses container state, keeps volumes)
docker compose down && docker compose up -d

# Full teardown including volumes (destroys database data)
docker compose down -v && docker compose up -d
# WARNING: -v deletes named volumes — database data is gone
```

---



## 4. Decision Patterns

These are the mental models that determine whether an investigation takes 5 minutes or 50 minutes.


**Symptom is not root cause**

The container restarting is not the problem — it is the response to the problem. The 502 is not the problem — it is the user-visible consequence. Always ask: what caused this symptom? Then ask it again about the answer. Stop when you reach something that has a concrete fix.


**Stabilise before investigating**

Users are down. Rolling back takes 90 seconds. Reading logs to find root cause takes 20 minutes. Roll back first. Investigate on a staging environment or from the logs of the failed deployment. The sequence is: restore service, then understand why.


**The restart policy hides failures**

A container that has restarted 40 times and is currently `healthy` looks identical to one that has been running for a week without interruption — until you check `RestartCount`. High restart counts mean the system is recovering from repeated failures. The policy is doing its job. The underlying failure still needs to be found.

```bash
docker inspect $(docker compose ps -q backend) --format='{{.RestartCount}}'
```

Any number above 3 in a stable deployment deserves investigation.


**Healthy does not mean correct**

The health check tests one thing: whatever the `test` command checks. For this stack, it is `curl -f http://localhost:5000/health` — a 200 response from the `/health` endpoint. A container can be `healthy` and simultaneously: returning wrong data, failing on 90% of routes, missing environment variables, running the wrong image version, or leaking memory. `healthy` means the health check passed. Nothing more.


**Always verify what is actually running**

The image tag in `docker-compose.yml` says `v1.0.4`. The container may be running `v1.0.3` if it was not recreated after the compose file was updated. Always confirm:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{index .RepoDigests 0}}'
```

Cross-reference the digest against the registry. If they differ, the compose file and the running container are out of sync.


**Environment variables are the most common single point of failure**

Before concluding that there is a code bug, a network issue, or a database problem, verify every environment variable the service depends on:

```bash
docker compose exec backend env | sort
```

Look for empty values. Look for values with unexpected whitespace. Look for variables that exist in development but are absent in production. More production incidents trace back to a missing or malformed environment variable than to any other single cause.


**Logs are the source of truth — but only for the current instance**

`docker compose logs` shows logs from the currently running container instance. When a container restarts, the previous instance's logs are gone unless you have a log aggregation system (step 18). In a crash loop, you are always reading the most recent startup attempt, not the original failure. If you need to catch a crash before the restart policy clears it:

```bash
# Get the container ID (including stopped containers)
docker ps -a | grep backend

# Read logs from a specific stopped container by ID
docker logs <container-id>
```

Act fast — the restart policy will create a new container and the stopped one may be cleaned up.


**Isolate one layer at a time**

The layers from application to infrastructure:

```
Request path:  User → Frontend → Backend → Database
```

Test each layer independently:

```bash
# Is the database up?
docker compose exec db pg_isready -U appuser -d appdb

# Can the backend reach the database?
docker compose exec backend python -c "import psycopg2, os; ..."

# Is the backend responding?
docker compose exec backend curl -f http://localhost:5000/health

# Can the frontend reach the backend?
docker compose exec frontend curl -f http://backend:5000/health

# Is the frontend responding to users?
curl -f http://localhost:5001/
```

Confirm each layer works before moving up. A database that is down causes backend errors that look like backend bugs until you check the database.

---



## 5. Production Reality

These are the checks that experienced engineers run by habit — before assuming anything, before reading code, before rebuilding.


**Always check the restart count first**

```bash
docker inspect $(docker compose ps -q) \
  --format='{{.Name}}: restarts={{.RestartCount}}'
```

A count above 3 on a service that has been running for hours is a problem hiding behind the restart policy.


**Never trust a tag — verify the digest**

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{index .RepoDigests 0}}'
```

The tag in `docker-compose.yml` is what you asked for. The digest is what you got. They should match. When they do not, the deployment did not do what you thought it did.


**Confirm resource limits are set**

```bash
docker inspect $(docker compose ps -q) \
  --format='{{.Name}}: mem={{.HostConfig.Memory}} cpu={{.HostConfig.NanoCPUs}}'
```

Memory = 0 means no limit. On a shared server, a container with no memory limit can consume all available RAM and take down every other service.


**Read health check history, not just status**

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{json .State.Health.Log}}' | python -m json.tool
```

`healthy` tells you the last check passed. The log tells you whether the last five checks all passed or whether it flipped between healthy and unhealthy repeatedly.


**The `.env` file checklist for production servers**

Every time a deployment fails in a way that was not reproduced locally, run this before reading any code:

```bash
# 1. Does the .env file exist?
ls -la .env

# 2. Does it have Unix line endings?
file .env
# should say: ASCII text
# if it says: ASCII text, with CRLF line terminators → run: sed -i 's/\r//' .env

# 3. Are all required variables present and non-empty?
docker compose exec backend env | sort | grep -v "^PATH\|^HOME\|^HOSTNAME"
# Scan for empty values: KEY= with nothing after the equals sign

# 4. Do the values match what was intended?
# Compare against the last known working .env or the secrets manager
```


**The 90-second rollback**

When a deployment breaks production, the fastest path to stability is always rollback. This requires knowing the previous version tag before every deployment — write it down or keep it in a deployment log.

```bash
# Update docker-compose.yml: change image tag to previous version
# Then:
docker compose pull
docker compose up -d

# Confirm the rollback took effect
docker inspect $(docker compose ps -q backend) \
  --format='{{index .RepoDigests 0}}'
```

If the compose file uses `latest` instead of a pinned version, rollback requires knowing the previous digest — which is why `latest` in production is an operational liability.


**After the incident — what to record**

Before closing any production incident, write down:

- What was the symptom and when did it start?
- What was the root cause?
- What was the fix?
- What was the deploy digest before and after?
- What should be changed to prevent recurrence?

This record is the institutional memory that makes the next incident faster to resolve. Without it, the same problem recurs with no shorter investigation time.