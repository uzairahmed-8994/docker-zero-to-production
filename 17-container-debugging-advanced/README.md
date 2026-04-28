# 17 — Container Debugging Advanced



## 0. Goal of This Step

Go beyond the basic debugging from step 06 and learn how to diagnose real problems in a multi-container stack — database connection failures, network issues between services, performance problems, and how to debug a running production-like system without stopping it.



## 1. What Problem It Solves

In step 06 we learned the basics — `docker logs`, `docker exec`, exit codes, `docker inspect`. That is enough for a single container. But our stack now has three services: frontend, backend, and Postgres. When something breaks, the failure rarely tells you which container caused it or why.

The frontend returns a 500 error. Is the backend down? Is the database down? Is it a network issue? Is it a code bug? Is it a configuration mismatch? Each of these looks identical from the outside — the frontend just says it failed to get data.

Step 06 gave you the tools to look inside one container. This step gives you the mindset and techniques to investigate a multi-container system where the problem could be anywhere in the chain.



## 2. What Happened (Experience)

Starting with the full three-service stack running. I intentionally broke it in different ways and practiced diagnosing each one from scratch — as if I did not know what I had broken.



**Scenario 1 — The frontend returns 500, reason unknown**

I made the backend unable to reach the database by changing the `DB_HOST` environment variable to a wrong value. Then I hit the frontend:

```bash
curl http://localhost:5001/api
# {"error": "...", "frontend": "error"}
```

500 error. The frontend is alive. Something downstream failed. Step 06 approach: check the obvious logs.

```bash
docker compose logs frontend
# frontend-1  | 172.18.0.1 - - [28/Apr/2026 10:54:08] "GET /api HTTP/1.1" 500 -
```

Frontend received the request and returned 500. Not helpful by itself.

```bash
docker compose logs backend
# Database not ready, retrying... (5 attempts left)
# Database not ready, retrying... (4 attempts left)
# Could not connect to database after multiple retries
```

There it is. The backend tried to connect to the database, failed all retries, and crashed. The frontend's 500 is a downstream symptom — the real failure is in the backend.

But wait — the backend container may behave in two different ways depending on how the application fails.

In this setup, we are using `--preload`. That means Gunicorn loads the application at startup. If initialization code (such as `init_db()`) fails, the entire process exits and the container stops.

So `docker compose ps` may show:

```bash
docker compose ps
# NAME       STATUS    PORTS
# frontend   running   0.0.0.0:5001->5001/tcp
# db         running   5432/tcp
```
The backend container is missing because it crashed during startup.

To confirm:

```bash
curl http://localhost:5000/
# Couldn't connect to server
```

In this case, the failure happened before the application could even start serving requests.

However, this is not the only possible behavior.

If the failure happens after startup — for example during request handling or inside a worker process — the container may continue running but the application inside it is broken:

```bash
docker compose ps
# NAME       STATUS    PORTS
# frontend   running   0.0.0.0:5001->5001/tcp
# backend    running   0.0.0.0:5000->5000/tcp
# db         running   5432/tcp
```

And:

```bash
curl http://localhost:5000/
# 500 Internal Server Error
```
The backend is responding but not functioning correctly. The frontend’s 500 is only a symptom — the actual failure is in the backend, and the logs reveal the cause.

The key distinction is:

Container stopped → failure during startup
Container running but returning 500 → failure during request handling

Understanding this difference helps you identify where to start debugging.



**Scenario 2 —  Debugging a database connection problem when the container has crashed**

The backend cannot reach the database, and because we are using `--preload`, the container exits during startup. This means we cannot use `docker compose exec backend` — there is no running container to exec into.

Instead, we debug from the same network using a temporary container.

First, confirm the backend is not running:

```bash
docker compose ps
# backend container missing or exited
```

Now launch a temporary debug container on the same network:

```bash
docker run --rm -it \
  --network 17-container-debugging-advanced_backend-network \
  python:3.11-slim bash
```

Inside this container, test DNS resolution:

```bash
root@603bdd5adf3f:/ python -c "import socket; print(socket.gethostbyname('db'))"
172.18.0.2
```


DNS works. The db hostname resolves correctly, so it is not a network isolation issue.

Next, check if the port is reachable in the same debug container:

```bash
python -c "
import socket
s = socket.socket()
s.settimeout(2)
result = s.connect_ex(('db', 5432))
print('Port open' if result == 0 else f'Port closed, error: {result}')
s.close()
"
# Port open
```

Port 5432 is reachable. The database container is up and Postgres is listening. So it is not a firewall or network issue.

At this point, DNS and network connectivity are confirmed. That means the failure is at the application layer — most likely environment configuration.

Since the backend container is not running, inspect its configuration directly:

```bash
docker compose config | grep DB_
# DB_HOST=wrong-host
# DB_PORT=5432
# DB_NAME=appdb
# DB_USER=appuser
# DB_PASSWORD=secret
```

`DB_HOST=wrong-host`. There is the problem. The environment variable is wrong. Fix it in `docker-compose.yml`, recreate the container:

```bash
docker compose up -d backend
```
Once fixed, the backend starts successfully.

This is the debugging pattern when a container crashes at startup:

- Confirm container state (ps)
- Use a sidecar container for DNS and network checks
- Inspect configuration separately (Compose or inspect)
- Fix and restart

Even when the application cannot start, the system can still be debugged by isolating each layer independently.





**Scenario 3 — Debugging without restarting**

I wanted to understand how to debug a running container without restarting it.


In production, restarting a service is not always an option. You may need to investigate a live issue while the system is still handling traffic.

To simulate this, I intentionally broke the API layer without touching the database logic.

First, I modified the `/notes` route in `app.py` to introduce a bug:

```python
@app.route("/notes", methods=["GET"])
def get_notes():
    raise Exception("Forced error for debugging")
```

I then rebuilt and started the container once to simulate a broken running service:

```bash
docker compose up -d --build
```

Now, when I called the endpoint:

```bash
curl http://localhost:5000/notes
```
I got:

`500 Internal Server Error`

At this point, after the container was running in a broken state, I did not restart it again to debug the issue.

Confirm the container is running:
```bash
docker compose ps
```

Instead of debugging through the API, I executed Python code directly inside the running container:

```bash
docker compose exec backend python -c "
from app import get_db
import psycopg2.extras
conn = get_db()
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute('SELECT * FROM notes')
rows = cur.fetchall()
print(rows)
conn.close()
"
```

Output:

```bash
Database initialized successfully
[]
```

This result tells us:

- Database connection is working
- Query is valid
- Table exists
- Data access is correct

But the API endpoint /notes is still failing.

This means:

`Database layer → working`
`Application route → broken`

The bug is not in the database or query. It is in the route logic itself — exactly where we introduced the error.

Why this works

Normally, a request flows like this:
`Client → HTTP → Flask route → Database → Response`

In this scenario, I skipped the HTTP and Flask route layers and directly executed the database logic using the same application code.

This isolates the system:

`If this command fails → problem is in DB or connection`
`If this command works → problem is in API layer`

This technique — running Python snippets inside the running container using the app's own modules — is one of the most powerful debugging tools available.



**Scenario 4 — Understanding what is happening right now**

Sometimes the issue is not a crash but slow behaviour or unexpected resource usage. I wanted to see what the backend was actually doing at a given moment.

Check all processes inside the backend container:

```bash
docker compose exec backend ps aux
# PID   USER     TIME  COMMAND
# 1     appuser  0:02  {gunicorn} /usr/local/bin/python /usr/local/bin/gunicorn --preload --bind 0.0.0.0:5000 --workers 2 --timeout 60 app:app
# 7     appuser  0:00  {gunicorn} /usr/local/bin/python /usr/local/bin/gunicorn --preload --bind 0.0.0.0:5000 --workers 2 --timeout 60 app:app
# 8     appuser  0:00  {gunicorn} /usr/local/bin/python /usr/local/bin/gunicorn --preload --bind 0.0.0.0:5000 --workers 2 --timeout 60 app:app
# 21    appuser  0:00  ps aux
```

Even though the commands look identical, these represent different roles:

- PID 1 → Gunicorn master process  
- PID 7, 8 → worker processes  
- Last entry → the `ps` command itself  

This confirms that Gunicorn started correctly with one master and two workers as configured.

Check live resource usage across all containers:

```bash
docker stats
# NAME       CPU %   MEM USAGE / LIMIT   MEM %   NET I/O
# frontend   0.04%    40MB / 11.59GB        0.33%    ...
# backend    0.03%    30MB / 11.59GB        0.25%    ...
# db         0.03%    40MB / 11.59GB        0.34%    ...
```

All healthy. Under load I can watch these numbers change in real time. If the backend's memory climbs steadily without dropping, that is a memory leak. If CPU spikes on the database under a specific request, that is a slow query.



**Scenario 5 — The hardest one: no logs, no errors, wrong results**

This type of issue is the most difficult to debug.

There is no crash. No error in logs. The container is healthy. The endpoint returns 200. But the data is wrong.

For example:

```bash
curl http://localhost:5000/notes
# {"notes": [{"id": 3, ...}, {"id": 1, ...}, {"id": 2, ...}]}
```

The order is incorrect. There are no logs to guide the investigation.
At this point, the only option is to isolate each layer of the system.

Start by checking the database directly. If the data is already wrong there, the problem is at the source.

```bash
docker compose exec db psql -U appuser -d appdb
```

Inside psql, run the query manually:

```sql
SELECT id, note, created_at FROM notes ORDER BY created_at DESC;
```

If the result is correct here, the database is not the problem.

Next, move one layer up and check what the backend receives from the database. Instead of going through the API, execute the query directly inside the running container:

```bash
docker compose exec backend python -c "
from app import get_db
import psycopg2.extras
conn = get_db()
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
cur.execute('SELECT id, note, created_at FROM notes ORDER BY created_at DESC')
rows = cur.fetchall()
for r in rows: print(dict(r))
conn.close()
"
```

This show the data exactly as Python received it — still correctly ordered. At this point, two layers are verified:

The database is correct.
The Python data layer is correct.

But the HTTP response is still wrong.

That leaves only one place where the bug can exist: the application logic that processes the data before returning it.

This is the key idea behind debugging silent failures. There are no logs to follow, so the system has to be broken down into layers and each one verified independently. By the time only one layer remains unverified, that is where the problem is.

The pattern becomes:

Database → Python data → HTTP response

Each step reduces uncertainty until the faulty layer is identified.

This kind of issue is common in real systems. Everything looks healthy, monitoring shows no alerts, and logs are empty but the output is incorrect. In these cases, debugging is not about reading errors, it is about systematically proving what is working and isolating what is not.



## 3. Why It Happens

Multi-container debugging is harder than single-container debugging for one core reason: **failures propagate across service boundaries and the symptom appears in a different place than the cause.**

The frontend returns 500 not because of a frontend bug but because the backend is broken. The backend is broken not because of a backend bug but because the database credentials are wrong. Reading only the frontend logs tells you nothing useful. Reading only the backend logs tells you the symptom but not the root cause without also reading the environment variables.

This chain of causation is the normal state of distributed systems. The debugging skill is not knowing which command to run — it is knowing how to trace a failure backwards from symptom to cause, one service at a time.



## 4. Solution

**The multi-container debugging workflow:**

**1. Always start with `docker compose ps`**
Which containers are running? Which have exited? This is the first picture.

**2. Follow the request chain backwards from the failure**
Frontend failed → check frontend logs → backend returned error → check backend logs → database unreachable → check database logs and connectivity.

**3. Separate the layers of the problem**
- Is the container running? (`docker compose ps`)
- Is the application inside it healthy? (hit its endpoint directly)
- Can it reach its dependencies? (DNS check, port check)
- Are its credentials correct? (`env | grep DB_`)
- Is the data correct? (query directly with psql or python -c)

**4. Use `exec` to get inside the failing container and reproduce the problem manually**
Do not rely on the HTTP layer to expose the problem. Run the code directly.

**5. Read all service logs together, not separately**
`docker compose logs` interleaves everything with timestamps. The sequence of events across services tells the story.



## 5. Deep Understanding

### Reading Logs Across Services Together

```bash
docker compose logs --timestamps
```

Timestamps reveal causality. You can see: at 10:23:44 the backend crashed, at 10:23:45 the frontend started returning 500. The 1-second gap confirms the frontend was fine until the backend failed — not the other way around.

```bash
docker compose logs --timestamps --tail 30
```

The last 30 lines from all services, with timestamps, interleaved. This is the fastest way to understand what happened in the last few seconds before a failure.

### `docker inspect` for Runtime State

Step 06 introduced `docker inspect`. In a multi-container context it becomes more powerful:

```bash
# What environment variables is the backend actually running with?
docker inspect $(docker compose ps -q backend) \
  --format='{{range .Config.Env}}{{println .}}{{end}}'
```

This shows the actual runtime environment — not what is in the Compose file but what the container received. Useful when you suspect environment variable substitution failed silently.

```bash
# What is the container's actual health status?
docker inspect $(docker compose ps -q backend) \
  --format='{{.State.Health.Status}}'
```

We cover health checks in step 19, but this is where you read the result.

```bash
# When did the container last restart?
docker inspect $(docker compose ps -q backend) \
  --format='{{.State.StartedAt}}'
```

If a container keeps restarting, this timestamp keeps changing. Comparing it to the current time tells you how recently it crashed.

### Using a Debug Sidecar Container

Sometimes the runtime image is too minimal to have the tools you need. The non-root user cannot install packages. The image has no `curl`, no `wget`, no network tools.

The solution is a **debug sidecar** — a temporary container that shares the same network as your stack:

```bash
docker run --rm -it \
  --network 17-container-debugging-advanced_backend-network \
  alpine /bin/sh
```

Now inside Alpine you have `wget`, `ping`, `nc`, and anything else you install with `apk add`. You can probe the backend and database from the same network perspective as any other container:

```bash
# Inside the sidecar:
wget -qO- http://backend:5000/
ping db
nc -zv db 5432
```

This is useful when you need to diagnose a network problem from inside the Docker network without modifying the actual service containers.

### `docker diff` — What Changed in a Running Container

```bash
docker diff 17-container-debugging-advanced-backend-1
```

```
C /app
A /app/somefile.txt
```

`C` = changed, `A` = added, `D` = deleted. This shows every file that differs between the running container and its original image. If someone exec'd in and modified a file, it shows up here. If the application wrote something unexpected, it shows up here. Useful for confirming whether a container has been modified from its image or finding temporary files an app is writing that you did not know about.

### Copying Files Out of a Container

Sometimes you need to extract a file from inside a container — a log file the app wrote, a generated config, a core dump:

```bash
docker cp 17-container-debugging-advanced-backend-1:/app/app.py ./extracted-app.py
```

And the reverse — copying a file into a running container without rebuilding:

```bash
docker cp ./patched-app.py 17-container-debugging-advanced-backend-1:/app/app.py
```

Copying in is a last resort for urgent production debugging. It modifies the running container's writable layer — the change is lost on next restart. But it lets you test a fix without a full rebuild cycle. Always follow up with a proper image rebuild.

### Reading Postgres Logs Directly

Postgres logs are verbose and useful:

```bash
docker compose logs db
```

```
LOG:  database system was shut down at ...
LOG:  entering standby mode
LOG:  redo starts at ...
LOG:  consistent recovery state reached at ...
LOG:  database system is ready to accept read only connections
```

On a connection authentication failure you will see:

```
FATAL:  password authentication failed for user "appuser"
DETAIL:  Connection matched pg_hba.conf line 99: "host all all all scram-sha-256"
```

This is more specific than the Python error. The Python stack trace says "authentication failed." The Postgres log says which pg_hba.conf rule matched and which authentication method was used. When credentials problems are subtle, the database logs tell you more than the application logs.

### `docker stats` for Performance Debugging

```bash
docker stats --no-stream
```

One snapshot of all containers' resource usage. Use `--no-stream` in scripts — without it, `docker stats` runs forever.


What to look for:

- **Memory climbing steadily** on the backend → memory leak in the Python code
- **High CPU on db** during a specific request → slow query, missing index
- **High NetIO on backend** → large payloads being transferred, or chatty database queries
- **BlockIO on db** → database doing a lot of disk reads, possible missing index or full table scan



## 6. Commands


```bash
# ── Stack-Level Overview ───────────────────────────────────────────────────

docker compose ps                              # which containers are running
docker compose logs --timestamps               # all logs with timestamps
docker compose logs --timestamps --tail 30     # last 30 lines across services
docker compose logs -f backend                 # follow backend logs live

# ── Inside a Running Container ─────────────────────────────────────────────

docker compose exec backend env | grep DB_     # check environment variables
docker compose exec backend ps aux             # check running processes
docker compose exec backend python -c "..."    # run Python snippets inline

# ── Network Debugging (when backend is running) ────────────────────────────

# DNS resolution
docker compose exec backend python -c \
  "import socket; print(socket.gethostbyname('db'))"

# Port reachability
docker compose exec backend python -c "
import socket
s = socket.socket()
s.settimeout(2)
print(s.connect_ex(('db', 5432)))
s.close()
"

# ── Network Debugging (when backend is NOT running) ────────────────────────

# Find network name
docker network ls

# Launch debug sidecar
docker run --rm -it \
  --network <your-project>_backend-network \
  python:3.11-slim bash

# Inside sidecar:
python -c "import socket; print(socket.gethostbyname('db'))"

python -c "
import socket
s = socket.socket()
s.settimeout(2)
print(s.connect_ex(('db', 5432)))
s.close()
"

# ── Inspect Runtime State ──────────────────────────────────────────────────

# Get container ID dynamically
docker compose ps -q backend

# Environment variables
docker inspect $(docker compose ps -q backend) \
  --format='{{range .Config.Env}}{{println .}}{{end}}'

# Container start time
docker inspect $(docker compose ps -q backend) \
  --format='{{.State.StartedAt}}'

# Health status (only if defined)
docker inspect $(docker compose ps -q backend) \
  --format='{{.State.Health.Status}}'

# Files changed vs image
docker diff $(docker compose ps -q backend)

# ── Database Debugging ─────────────────────────────────────────────────────

docker compose exec db psql -U appuser -d appdb   # open postgres shell
docker compose logs db                             # view postgres logs

# ── Performance ────────────────────────────────────────────────────────────

docker stats                                       # live monitoring
docker stats --no-stream                           # one-time snapshot

# ── File Operations ────────────────────────────────────────────────────────

# Copy file OUT of container
docker cp $(docker compose ps -q backend):/app/app.py ./extracted.py

# Copy file INTO container (temporary debug only)
docker cp ./patched.py $(docker compose ps -q backend):/app/app.py
```


## 7. Real-World Notes

In production you rarely have the luxury of reproducing a problem locally. The bug happens under specific load, with specific data, in a specific environment. The debugging techniques in this step — reading logs across services, checking environment variables, running code inline with `exec`, probing network connectivity — work against live production containers just as well as local ones. That is why learning them matters.

The debug sidecar pattern is standard practice in Kubernetes, where it is called an ephemeral container or debug container. You attach a temporary container with debug tools to a running pod without modifying the pod itself. The Docker equivalent is running a container on the same network. The mental model is identical.

`docker cp` into a running container is a break-glass operation. It modifies a running production container directly — which violates the immutability principle. Use it only when the alternative is downtime. Always rebuild and redeploy properly immediately after. If you find yourself doing `docker cp` regularly, that is a sign the deployment process needs improvement.

The most common real-world debugging mistake is reading only one service's logs in isolation. The failure is almost always in the interaction between services — the timing, the data passed between them, the network path. `docker compose logs --timestamps` across all services simultaneously is almost always the right starting point.



## 8. Exercises

**Exercise 1 — Break and diagnose a connection failure**
Change `DB_HOST` to a wrong value in `docker-compose.yml`. Recreate the backend: `docker compose up -d backend`. Hit `curl http://localhost:5001/api` — it fails. Now diagnose it using only the tools from this step: check `docker compose ps`, read logs across services with timestamps, check environment variables with `exec`. Find the cause without looking at the Compose file directly. Fix it and verify.

**Exercise 2 — DNS and port debugging**
With the stack running correctly, exec into the backend and run both the DNS check and the port check from the experience section. Confirm `db` resolves to an IP and port 5432 is open. Then temporarily stop the database (`docker compose stop db`) and run the port check again — it should fail. Start the database again. This builds muscle memory for the connectivity diagnosis flow.

**Exercise 3 — The debug sidecar**
Find your backend network name with `docker network ls`. Launch a sidecar:
```bash
docker run --rm -it --network <backend-network-name> alpine /bin/sh
```
From inside the sidecar: `wget -qO- http://backend:5000/`, `ping db`, `nc -zv db 5432`. You are probing the stack from inside the network, with tools that do not exist in the production images. Exit the sidecar — it disappears cleanly.

**Exercise 4 — Run code directly inside the container**
Exec into the backend and run the Python snippet from Scenario 3 — query the database directly using `get_db()` without going through HTTP. Confirm it returns data. Then modify the query to order by `id ASC` instead of `created_at DESC` — confirm you see the difference. This is the technique for debugging data issues without the HTTP layer in the way.

**Exercise 5 — `docker diff` in action**
Exec into the running backend and create a file: `touch /app/debug-was-here.txt`. Exit. Run `docker diff <container-name>`. You should see the file listed as added (`A`). Now you know how to spot unexpected filesystem modifications in a running container — useful for detecting if someone or something modified the container without your knowledge.

**Exercise 6 — Read Postgres logs for a credentials failure**
Change `DB_PASSWORD` to a wrong value in the backend's environment. Recreate it. Watch the backend fail. Then run `docker compose logs db` and find the authentication failure message from Postgres itself. Compare the detail in the Postgres log to the Python error in the backend log — the database log is more specific. Fix the password and verify recovery.

**Exercise 7 — `docker stats` under load**
With the stack running, open two terminals. In the first, run `docker stats`. In the second, send a burst of requests:
```bash
for i in $(seq 1 50); do curl -s http://localhost:5000/notes > /dev/null; done
```
Watch the backend's CPU and memory change during the burst. Watch the database's CPU spike as queries arrive. Watch the numbers settle back down after the burst finishes. This is what performance debugging looks like before adding any profiling tools.