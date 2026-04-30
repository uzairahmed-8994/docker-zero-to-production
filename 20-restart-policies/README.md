# 20 — Restart Policies



## 0. Goal of This Step

Understand how Docker decides whether to restart a container after it exits, what the four restart policies actually do, and how to configure each service in the stack with the right policy — so that the system recovers from failures automatically instead of waiting for someone to notice and intervene.



## 1. What Problem It Solves

In step 19, we gave Docker a way to know when a container is unhealthy. The health check is the signal. But at the end of that step, something was left deliberately unresolved: when the backend became `unhealthy`, the container sat there in that state. Nothing happened. Docker detected the problem and did nothing about it.

That is correct behaviour — health checks and restart policies are separate concerns. Health checks answer "is this container working?" Restart policies answer "what should Docker do when a container stops?"

Right now the stack has no restart policies. If the backend crashes — a genuine exception that takes down the Gunicorn process, a `kill` signal, a resource exhaustion — the container stops. `docker compose ps` shows it as `exited`. It stays that way until someone runs `docker compose up` again. In a local development environment, that is a minor inconvenience. On a server running unattended at 3am, it is an outage.

Restart policies are how you tell Docker what to do when a container stops unexpectedly. They are the response that health checks were waiting for.



## 2. What Happened (Experience)

The stack from step 19 was running with health checks on all three services and a clean dependency chain. I had just watched the backend go `unhealthy` and return to `healthy` but I noticed that in neither case did Docker take any automatic action. It detected the state change and reported it. That was all.

I started asking the next question: what happens when a container does not just become unhealthy but actually exits?

**Step 1 — Seeing what happens when a container crashes with no restart policy**

I deliberately crashed the backend container by killing the Gunicorn process:

```bash
docker compose exec backend kill 1
```

PID 1 in the container is the Gunicorn master process. Sending it a SIGTERM causes it to shut down. The container exited with status 0.

I checked the stack:

```bash
docker compose ps -a
```

```
NAME       SERVICE    STATUS     PORTS
frontend   frontend   healthy    0.0.0.0:5001->5001/tcp
db         db         healthy    5432/tcp
backend    backend    exited (0)
```

`exited`. The backend was gone. The frontend was still running — it would return errors on any request that needed the backend, but Docker had no mechanism to bring the backend back. I could see when it exited, I could read the logs from before the crash, but nothing was going to restart it.

I checked what restart policy was currently set after restarting backend:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.RestartPolicy.Name}}'
```

```
no
```

`no`. The default. Docker will never restart this container, regardless of how or why it stopped.

**Step 2 — Understanding the four options before changing anything**

Before adding any policy, I wanted to understand what each one actually does. I ran a simple test container with each policy and observed the behaviour.

The options are `no`, `always`, `on-failure`, and `unless-stopped`. I had seen references to these before but had never tested them side by side. The names are descriptive but not precise — the interesting cases are in the edge conditions.

I started with `always`:

```bash
docker run -d --restart=always --name test-always alpine sh -c "echo started && exit 0"
```

The container ran the command, exited successfully (exit code 0), and Docker immediately restarted it. Then it exited again and Docker restarted it again. The container was in a restart loop — even though nothing had gone wrong.

This was the first important realisation: `always` means always. It does not check whether the exit was intentional or whether the container succeeded. If the container exits for any reason — crash, success, manual stop — Docker restarts it.

I tried `on-failure`:

```bash
docker run -d --restart=on-failure --name test-onfailure alpine sh -c "echo started && exit 0"
```

The container ran, exited with code 0, and stayed stopped. No restart loop.

Then I changed the exit code:

```bash
docker run -d --restart=on-failure --name test-onfailure2 alpine sh -c "echo started && exit 1"
```

The container ran, exited with code 1, and Docker restarted it. Then it exited with code 1 again, Docker restarted again. The loop continued. The distinction: `on-failure` only restarts when the exit code is non-zero.

```bash
docker rm -f test-always test-onfailure test-onfailure2
```

I now had the core mental model: `always` restarts on any exit; `on-failure` restarts only on failure exits. The fourth option — `unless-stopped` — behaves identically to `always` except for one case: if you manually stop the container with `docker stop`, it will not restart automatically. `always` would restart even after a manual stop (after the Docker daemon restarts).

**Step 3 — Deciding which policy belongs on which service**

With the mental model clear, I looked at each service in the stack and thought about what should happen when each one exits.

The database is a stateful service. If it crashes, it should come back — the data is in a volume, so a restart recovers the previous state. A database that stays down permanently because of a transient crash is worse than one that restarts and recovers. `unless-stopped` is the right choice: restart on any exit, but respect an intentional `docker stop` during maintenance.

The backend is a stateless application server. If it crashes, restarting it is always the right move. The question is which crashes should trigger a restart. A clean exit (code 0) from Gunicorn would be unusual in production — that typically means the process was asked to shut down deliberately. A non-zero exit means something went wrong. `on-failure` is the more precise choice: restart on crashes, respect clean shutdowns.

The frontend is the same class of service as the backend — stateless application server. Same reasoning, same policy: `on-failure`.

**Step 4 — Adding restart policies to docker-compose.yml**

I added the `restart` field to each service:

```yaml
services:
  frontend:
    build: ./frontend
    restart: on-failure
    # ... rest of config

  backend:
    build: ./backend
    restart: on-failure
    # ... rest of config

  db:
    image: postgres:15
    restart: unless-stopped
    # ... rest of config
```

The `restart` field in docker-compose.yml maps directly to Docker's `--restart` flag. No rebuild needed — restart policies are applied to the container at creation time, not baked into the image. I recreated the containers to apply the new policies:

```bash
docker compose up -d
```

I confirmed the policy was set:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.RestartPolicy.Name}}'
```

```
on-failure
```

**Step 5 — Watching a restart policy trigger**

To observe restart behaviour correctly, the container must exit due to an internal failure. Restart policies do not trigger when processes inside the container are killed or when the container is stopped manually.

I simulated a real application failure by introducing a crash at startup:

```python
# At the top of app.py
raise Exception("crash on startup")
```
Then I started only the backend service to isolate the behaviour:

```bash
docker compose up -d --build backend
docker compose ps
```

At first, the container appeared briefly as `health: starting` then docker detected the failure and applied the restart policy.

The container entered a restart loop. Each time the application crashed during startup, Docker restarted the container automatically.

This confirmed that restart: on-failure is working as expected — the container is restarted only when it exits with a non-zero code due to an internal failure.


I checked the restart count:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.RestartCount}}'
```

```
13
```

Docker tracks how many times a container has restarted. This number persists until the container is recreated. A high restart count is a signal worth monitoring — a container that has restarted fifty times in an hour is not healthy, even if it is currently `healthy`.


**Step 6 — Understanding the backoff behaviour**

With the application still crashing on startup, I observed what happens over time.

The first restart happened almost immediately. Subsequent restarts showed increasing delays between attempts:

```bash
backend    Restarting (1)
backend    Restarting (1) 3 seconds ago
backend    Restarting (1) 10 seconds ago
```

Docker applies an exponential backoff to repeated restart attempts. Each failure increases the delay before the next restart, up to a maximum of approximately 5 minutes.

This behaviour is intentional:

- It prevents a tight crash loop from consuming CPU continuously
- It reduces noise in logs and monitoring systems
- It gives external dependencies time to recover if the failure is temporary

I checked the restart count:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.RestartCount}}'
```

The number increased with each restart attempt.

This is important in production: a container may appear “running” or even “healthy” at a given moment, but a high restart count reveals instability. RestartCount is often a more reliable signal than current status.


**Step 7 — Testing the `unless-stopped` distinction**

I wanted to confirm that `unless-stopped` on the database actually respected a manual stop while `on-failure` would not automatically restart after a clean exit.

I stopped the database manually:

```bash
docker compose stop db
```

```bash
docker compose ps
```

```
NAME   SERVICE   STATUS     PORTS
db     db        exited (0)
```

`exited`. With `unless-stopped`, a manual `docker stop` (which sends SIGTERM → clean exit code 0) leaves the container stopped. It does not restart automatically. This is the intended behaviour: if I stop the database deliberately for maintenance, I do not want Docker to undo that.

I brought it back manually:

```bash
docker compose start db
```

**Note — Docker restart behaviour**

Restart policies do not behave the same during a Docker daemon restart.

- `on-failure` only restarts containers when they exit due to an internal failure (non-zero exit code)
- `unless-stopped` and `always` also restore containers after a Docker restart or system reboot

Additionally, Docker Compose dependency rules (`depends_on` with `service_healthy`) are not re-evaluated after a daemon restart. Containers are restored independently based on their restart policy.

For this reason, restart behaviour should be tested using application failures rather than daemon restarts.


## 3. Why It Happens

When a container's process exits, Docker's container runtime records the exit code and checks the restart policy. This happens at the kernel level — Docker is notified by the operating system when PID 1 in the container namespace terminates. The restart policy decision is made immediately and synchronously.

The policy evaluation is simple:

- `no` — do nothing, regardless of exit code
- `on-failure` — if exit code is non-zero, schedule a restart; if exit code is 0, do nothing
- `always` — schedule a restart, regardless of exit code
- `unless-stopped` — same as `always`, except: if the container was stopped via `docker stop` before the daemon shut down, do not restart it when the daemon comes back up

The backoff algorithm starts at 100ms and doubles on each consecutive failure: 100ms → 200ms → 400ms → 800ms → ... → 5 minutes maximum. The backoff counter resets after a container has been running stably for 10 seconds. A container that crashes immediately on every start accumulates backoff quickly. A container that starts, runs for 30 seconds, then crashes resets the counter and gets a fresh 100ms delay on the next failure.

This backoff behaviour is why restart policies are not a substitute for fixing the underlying problem. They keep the service available during transient failures. They do not prevent the failure from happening repeatedly.



## 4. Solution

The complete restart policy configuration for this stack:

**`docker-compose.yml` — restart policies added to each service:**

```yaml
services:
  frontend:
    build: ./frontend
    restart: on-failure
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
    restart: on-failure
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
      restart: unless-stopped
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

No Dockerfile changes. No application code changes. Restart policies are container-level configuration — they live in `docker-compose.yml` and take effect when the container is created.



## 5. Deep Understanding

### The Four Policies — Precise Definitions

The names of the policies are intuitive but the edge cases matter in practice.

`no` is the default. The container exits, it stays exited. This is correct for one-shot containers — migration scripts, data import jobs, test runners — where exiting is the expected terminal state.

`on-failure` restarts when the exit code is non-zero. Exit code 0 means success — the process decided it was done. Exit code non-zero means something went wrong. For application servers and background workers, a non-zero exit almost always indicates a bug or an external failure worth recovering from. A clean exit from Gunicorn (code 0) would only happen if the process received SIGTERM and handled it gracefully — which is the expected shutdown path, not a failure.

`always` restarts on any exit, including clean exits. The practical consequence appeared in the test: a container that runs a command and exits with code 0 immediately enters a restart loop. `always` is rarely the right choice for application servers because it cannot distinguish a deliberate shutdown from a crash. It is appropriate for containers that should never be down — a proxy, a monitoring agent — where even a clean restart should be treated as unexpected.

`unless-stopped` is `always` with one exception: if you manually called `docker stop` before the Docker daemon was last shut down, the container will not be restarted when the daemon comes back up. In practice, this means `unless-stopped` respects intentional human intervention. You stop it, it stays stopped across reboots. It crashes, Docker restarts it. This is the correct policy for databases and stateful services that you want to survive server reboots but that you also need to be able to maintain.

### Restart Policies and Health Checks Together

This is where the two systems connect. A restart policy triggers when a container's process exits — when the process terminates. A health check triggers when the container's process is running but the application inside it is broken.

These are different failure modes:

**Process exits:** The Gunicorn master process crashes due to a Python exception, OOM killer, or signal. The container stops. The restart policy kicks in, restarts the container, and the health check starts over from `starting`.

**Process running but unhealthy:** All Gunicorn workers are stuck on a database query that will never complete. Gunicorn is running. The container is `running`. But the health check is failing because `/health` is not responding. The restart policy does nothing — the process has not exited.

This second case is the gap that step 20 does not fully close. In Docker Compose, an `unhealthy` container with a restart policy still does not restart automatically — the restart policy only responds to process exit. In Kubernetes, liveness probes can be configured to restart a container when health checks fail repeatedly. In Docker Compose, the combination of health checks and restart policies covers process crashes but not application-level hangs.

Understanding this gap matters. If the backend becomes `unhealthy` because of a database connection pool exhaustion, `on-failure` will not help. A human needs to intervene, or the backend needs internal recovery logic.

### What the Restart Count Tells You

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.RestartCount}}'
```

A restart count of 0 means the container has been running continuously since it was created. A restart count of 1 means it crashed and recovered once. A restart count growing over time — 5, 10, 20 — means the container is in a crash loop that the restart policy is masking.

The restart policy keeps the service available during these crashes. That is its job. But it creates a risk: a service that crashes every few minutes but restarts quickly can look `healthy` in `docker compose ps` while being fundamentally broken. The restart count is the number that reveals this. A monitoring system that alerts when `RestartCount` exceeds a threshold catches the crash loop before it degrades into something more serious.

### Exponential Backoff — Why It Exists

Without backoff, a container in a crash loop would restart at full speed indefinitely. It would consume CPU trying to start, fail immediately, consume CPU trying to start again, in a tight loop that degrades the host. The backoff prevents this.

The backoff also has a diagnostic value: a container waiting 5 minutes between restart attempts has been failing continuously for long enough that Docker has hit the ceiling. Seeing a container sit in `restarting` for minutes is a stronger signal than seeing it flip in and out of `running` quickly. It tells you the failure is not transient — it has been crashing repeatedly for an extended period.

The 10-second stability reset matters for understanding the restart count over time. A container that starts, runs for 30 seconds, crashes, restarts, runs for 30 seconds, crashes is not accumulating backoff — each cycle resets the timer. The restart count grows but the delay stays at 100ms. The backoff only accumulates when crashes happen faster than the stability window.

### `restart: always` After a Daemon Restart

One behaviour worth testing explicitly: with `restart: always`, every container starts automatically when the Docker daemon starts. This means your entire Docker Compose stack can come up automatically after a server reboot — without running `docker compose up`. Many teams rely on this for simple server deployments: install Docker, run `docker compose up -d` once with `restart: always` or `unless-stopped`, and the stack comes back after every reboot automatically.

This is different from using a systemd service to manage Docker Compose. The restart policy approach is simpler — no extra service file required. The tradeoff is that the ordering guarantees of `depends_on` and `condition: service_healthy` only apply at `docker compose up` time. After a daemon restart, Docker restores containers individually and the `depends_on` ordering may not be strictly observed. For most stacks, the retry logic and health checks handle this — the backend retries its database connection until Postgres is ready, regardless of which container Docker happens to start first.



## 6. Commands

```bash
# ── Checking Restart Policy ────────────────────────────────────────────────

docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.RestartPolicy.Name}}'         # current policy name

docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.RestartPolicy}}'              # full policy including MaxRetry

# ── Checking Restart Count ─────────────────────────────────────────────────

docker inspect $(docker compose ps -q backend) \
  --format='{{.RestartCount}}'                          # times restarted since creation

# All containers and their restart counts
docker inspect $(docker compose ps -q) \
  --format='{{.Name}}: restarts={{.RestartCount}} policy={{.HostConfig.RestartPolicy.Name}}'

# ── Watching a Container Restart ──────────────────────────────────────────

watch docker compose ps                                  # observe status: restarting → healthy

# ── Manual Stop vs Crash — the unless-stopped Distinction ─────────────────

docker compose stop db                                  # manual stop — stays stopped
docker compose start db                                 # manual start
docker compose ps db                                    # confirm status

# ── Applying New Policies Without Rebuild ─────────────────────────────────

docker compose up -d                                    # recreates containers with new policy
# Note: image is unchanged — only container config is updated
```



## 7. Real-World Notes

Restart policies are the simplest form of self-healing infrastructure. They do not fix bugs — they keep services available while bugs are being fixed, or recover from transient failures that resolve on their own. A database connection that drops briefly, a temporary network partition, a memory spike that kills a worker — all of these can be recovered from by simply restarting the process. Restart policies make that recovery automatic.

The gap between health checks and restart policies is real and worth understanding before it surprises you in production. A container can be `unhealthy` and still running — the restart policy will not trigger. This is the common confusion: "I have a restart policy, why didn't the container restart when it became unhealthy?" The answer is that the process did not exit. In Docker Compose, you have to watch the restart count and the health status together. Neither alone tells the complete story.

For production deployments on a single server, `unless-stopped` on all long-running services is a reasonable default. It means the stack survives server reboots automatically and survives crashes automatically, while still respecting intentional maintenance stops. `on-failure` is slightly more precise for application containers — it distinguishes intentional shutdowns from crashes but the practical difference is small if your deployment process always goes through `docker compose up` rather than `docker stop` / `docker start`.

For anything beyond a single server — multiple servers, load balancing, rolling deployments — restart policies are the beginning of the conversation, not the end. Kubernetes, ECS, and similar orchestrators have more sophisticated recovery mechanisms that can restart containers based on health check failures, redistribute load during restarts, and maintain availability across rolling updates. Restart policies in Docker Compose establish the same conceptual foundation; the orchestrator mechanisms extend it.

The restart count is worth monitoring. In a well-behaved production stack, restart counts should stay at 0 or occasionally hit 1 after a transient failure. A count that keeps climbing is a sign that the restart policy is masking an ongoing problem. The policy is doing its job — keeping the service up but the underlying problem still needs to be found.



## 8. Exercises

**Exercise 1 — Confirm the default policy**

Before changing anything, check the current restart policy on all three containers:

```bash
docker inspect $(docker compose ps -q) \
  --format='{{.Name}}: {{.HostConfig.RestartPolicy.Name}}'
```

All three should show `no`. This confirms the default and gives you a baseline before adding policies.

**Exercise 2 — Observe a container staying down**

Without any restart policy, simulate a failure by stopping the backend:

```bash
docker compose stop backend
docker compose ps
```
The backend remains exited. This is the behavior restart policies are designed to change.



**Exercise 3 — Add restart policies and observe recovery**

Add:
```bash
backend:
  restart: on-failure

db:
  restart: unless-stopped
```
Apply:
```bash
docker compose up -d
```

Now simulate a failure by introducing a crash in the backend:

```python
# top of app.py
raise Exception("crash on startup")
```

Rebuild and watch:
```bash
watch docker compose ps
```




**Exercise 4 — Observe restart count**

Check how many times the container restarted:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.RestartCount}}'
```
The number increases with each restart attempt. This is a key operational signal.

**Exercise 5 — Observe backoff behavior**

Leave the backend crashing (from previous exercise).

Watch restart timing:
```bash
watch docker compose ps
```

Notice:

first restart is immediate
later restarts are delayed

This demonstrates Docker’s exponential backoff.


**Exercise 6 — Test unless-stopped behavior**

Stop database manually and that container remains stopped to test the unless-stopped policy

