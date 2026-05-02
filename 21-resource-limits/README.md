# 21 — Resource Limits



## 0. Goal of This Step

Understand how Docker controls the CPU and memory available to each container, why containers without limits are a shared risk on any host, and how to set limits that protect the stack without starving the services that need resources to function.



## 1. What Problem It Solves

In step 20, the stack became self-healing — crash the backend, it comes back; reboot the server, everything recovers. The operational foundation is solid. But there is still a category of failure that restart policies and health checks cannot address.

Right now, every container in the stack can use as much CPU and memory as the host provides. There are no boundaries. If the backend develops a memory leak — slowly allocating memory on each request and never releasing it — nothing stops it from growing until it has consumed every available megabyte on the server. At that point the OOM killer (the Linux kernel's out-of-memory manager) starts terminating processes. It may kill the backend. It may kill Postgres. It may kill the Docker daemon itself. The restart policy will try to recover, but the underlying pressure remains — the container restarts, leaks again, and the cycle repeats.

The same problem exists for CPU. A runaway loop in a background task, a route that triggers an expensive computation on every request, a dependency that enters a busy-wait state — any of these can saturate the host's CPU, making every other service on the machine slow or unresponsive.

Without resource limits, containers on a shared host compete for resources with no rules. One misbehaving service can degrade or take down every other service on the same machine. Resource limits are how you draw boundaries: this container gets this much memory, this much CPU, and no more. Everything else on the host is protected from whatever happens inside that boundary.



## 2. What Happened (Experience)

The stack from step 20 was running with health checks and restart policies. I had been thinking about what could still go wrong that the current setup could not handle. Memory leaks were the obvious gap. I had seen restart policies recover from crashes — but a slow memory leak does not crash the process immediately. It degrades the whole machine gradually until something else gives way.

I decided to understand the resource situation properly before it became a problem.

**Step 1 — Seeing what the containers are using right now**

I checked the live resource consumption of each container:

```bash
docker stats
```

```
CONTAINER ID   NAME       CPU %   MEM USAGE / LIMIT   MEM %   NET I/O
a1b2c3d4       backend    0.1%    52.3MiB / 7.67GiB   0.67%   1.2MB / 890kB
b2c3d4e5       frontend   0.0%    38.1MiB / 7.67GiB   0.48%   450kB / 320kB
c3d4e5f6       db         0.2%    31.4MiB / 7.67GiB   0.40%   780kB / 1.1MB
```

The `MEM LIMIT` column showed `7.67GiB` — the full memory of the host machine. Every container had access to the entire host memory with no restriction. The backend was using 52MB of its 7.67GB allowance. That looked fine right now. The problem is that nothing would stop it from using 7GB if something went wrong.

I wanted to see this concretely. I introduced a deliberate memory leak into the backend — a route that allocated memory and held onto it:

```python
# temporary — for observation only
_leak = []

@app.route("/leak")
def leak():
    _leak.append(" " * 10_000_000)  # allocate ~10MB per request
    return jsonify({"allocated_chunks": len(_leak)})
```

I hit the endpoint several times and watched `docker stats`:

```bash
watch -n 1 docker stats --no-stream
```

```
CONTAINER     MEM USAGE / LIMIT
backend       52.3MiB / 7.67GiB
backend       62.4MiB / 7.67GiB
backend       72.5MiB / 7.67GiB
backend       82.6MiB / 7.67GiB
```

Growing steadily, no resistance. The container had no idea it was doing anything wrong, and Docker had no mechanism to stop it. Left unchecked, this would eventually consume the host.

I removed the leak route before continuing.

**Step 2 — Understanding what limits are available**

Docker exposes two main resource controls per container: memory limits and CPU limits.

Memory has two distinct settings. `mem_limit` (or `memory` in newer Compose syntax) sets a hard ceiling — the maximum amount of RAM the container can use. If the container tries to allocate beyond this, the OOM killer terminates the offending process inside the container. `memswap_limit` (or `memswap`) controls memory plus swap combined. Setting it equal to `mem_limit` disables swap for that container.

CPU has two mechanisms. `cpus` sets a fractional limit on how many CPU cores the container can use — `0.5` means half a core, `2.0` means two full cores. `cpu_shares` (or `cpus` weight) sets a relative priority between containers rather than an absolute limit — useful when you want to ensure one service gets more CPU than another during contention, without hard-capping either.

The two memory settings and the CPU limit are the ones that matter for this stack.

**Step 3 — Deciding what limits to set**

Setting limits requires knowing what the services actually use. I had seen the baselines in `docker stats`: the backend used around 52MB at idle, the frontend around 38MB, Postgres around 31MB. Under load these numbers grow — Postgres holds more data in its shared buffers, Gunicorn workers use more memory handling concurrent requests.

I also needed to leave headroom. A limit that is too close to the normal usage will cause the container to hit the limit under normal load — not just under a memory leak. That is worse than having no limit. The goal is to cap the pathological case, not to squeeze normal operation.

I decided on limits with deliberate reasoning for each service:

The backend gets `512m` of memory. At 52MB idle with two Gunicorn workers, 512MB gives roughly 10x headroom. A genuine memory leak would still hit this ceiling and be contained before it threatened the host. CPU at `0.5` — half a core is sufficient for a simple Flask app handling moderate traffic.

The frontend is simpler than the backend — it proxies to the backend and does not hold significant state. `256m` of memory, `0.25` CPU.

Postgres needs different reasoning. Postgres uses memory for its `shared_buffers` (default 128MB), working memory for queries, and the OS page cache. Giving it too little memory causes it to thrash — constantly paging data in and out of disk, making every query slower. I gave Postgres `512m` and `0.5` CPU. For a production database this would be much higher, but for this stack it is appropriate.

**Step 4 — Adding limits to docker-compose.yml**

```yaml
services:
  frontend:
    build: ./frontend
    restart: on-failure
    deploy:
      resources:
        limits:
          cpus: "0.25"
          memory: 256m
        reservations:
          cpus: "0.1"
          memory: 128m
    # ... rest of config

  backend:
    build: ./backend
    restart: on-failure
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512m
        reservations:
          cpus: "0.25"
          memory: 256m
    # ... rest of config

  db:
    image: postgres:15
    restart: unless-stopped
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512m
        reservations:
          cpus: "0.25"
          memory: 256m
    # ... rest of config
```

Two fields: `limits` sets the ceiling — the container cannot exceed this. `reservations` sets a guaranteed minimum — Docker will not schedule this container on a host that cannot provide at least this much. In Docker Compose on a single host, `reservations` are not enforced the same way they are in Swarm or Kubernetes, but they document the expected minimum and are picked up by orchestrators when the same Compose file is used there.

I applied the changes:

```bash
docker compose up -d
```

**Step 5 — Confirming the limits are in effect**

```bash
docker stats --no-stream
```

```
CONTAINER     CPU %   MEM USAGE / LIMIT   MEM %
backend       0.1%    52.3MiB / 512MiB    10.2%
frontend      0.0%    38.1MiB / 256MiB    14.9%
db            0.2%    31.4MiB / 512MiB    6.1%
```

The `MEM LIMIT` column had changed. Instead of `7.67GiB`, each container now showed its individual limit. The limits were in effect.

I also confirmed via inspect:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='Memory={{.HostConfig.Memory}} NanoCPUs={{.HostConfig.NanoCPUs}}'
```

```
Memory=536870912 NanoCPUs=500000000
```

Memory in bytes: 536870912 / 1024 / 1024 = 512MB. NanoCPUs: 500000000 / 1000000000 = 0.5 CPUs. The numbers matched what I had configured.

**Step 6 — Watching the memory limit enforce itself**

I re-introduced the memory leak route to see what would happen when the container hit its limit:

```python
_leak = []

@app.route("/leak")
def leak():
    _leak.append(" " * 10_000_000)
    return jsonify({"allocated_chunks": len(_leak)})
```

Rebuilt the backend and hit the endpoint repeatedly while watching `docker stats`:

```bash
watch -n 1 docker stats --no-stream
```

```
CONTAINER   MEM USAGE / LIMIT    MEM %
backend     52.3MiB / 512MiB     10.2%
backend     152.4MiB / 512MiB    29.8%
backend     252.5MiB / 512MiB    49.3%
backend     352.6MiB / 512MiB    68.9%
backend     452.7MiB / 512MiB    88.5%
backend     499.1MiB / 512MiB    97.5%
```

At around 512MB, the process inside the container was killed by the OOM killer. The container exited with a non-zero code. The restart policy (`on-failure`) triggered. The backend restarted, came back healthy, and the leak was gone — because the process state was wiped on restart.

```bash
docker compose ps
```

```
NAME      SERVICE   STATUS    PORTS
backend   backend   healthy   0.0.0.0:5000->5000/tcp
```

The memory leak had been contained. The host never came under pressure. The database and frontend stayed healthy throughout. The limit did exactly what it was supposed to do — it drew a line, the container hit the line, and the restart policy handled the recovery.

I removed the leak route and rebuilt.

**Step 7 — Checking that CPU limits are enforced**

Memory limits are easy to observe. CPU limits are subtler — the container does not crash when it hits the CPU ceiling, it just slows down. I generated sustained CPU load inside the backend to observe the throttling:

```bash
docker compose exec backend python -c "
import time
start = time.time()
# busy loop for 5 seconds
while time.time() - start < 5:
    pass
print('done')
"
```

While that ran, I watched CPU usage in `docker stats`. The backend's CPU stayed around 50% of a single core — matching the `0.5` limit — while the rest of the host remained available to the database and frontend. Without the limit, this busy loop would have consumed 100% of a full core.

The throttling is not visible in the same dramatic way as hitting a memory limit. The container does not crash. It runs slower. That is the intended behaviour — CPU limits shape the resource usage without stopping the process.



## 3. Why It Happens

Linux provides two kernel mechanisms for container resource control: cgroups (control groups) for memory and CPU limits, and namespaces for isolation. Docker uses cgroups to implement resource limits. When you set a memory limit on a container, Docker creates a cgroup with that memory ceiling. The Linux kernel enforces the limit — when a process in that cgroup attempts to allocate memory beyond the ceiling and there is no swap available, the kernel's OOM killer terminates the process that made the allocation.

For CPU, Docker uses the CFS (Completely Fair Scheduler) bandwidth control. The `cpus` setting translates to a CPU period and quota in the CFS: with `cpus: 0.5`, the container gets 50ms of CPU time for every 100ms period. If the container tries to use more than its quota in a period, the kernel throttles it — it pauses the process until the next period starts.

These limits are enforced by the kernel, not by Docker. Docker is configuring kernel primitives. This means the limits are hard — there is no way for a process inside a container to exceed them through normal operation, regardless of what the application code does.

Without limits, every container shares the host's cgroup at the root level, where there is no ceiling. All containers compete for the full resources of the host with no rules.



## 4. Solution

Complete resource limit configuration for all three services:

**`docker-compose.yml` — deploy.resources added to each service:**

```yaml
services:
  frontend:
    build: ./frontend
    restart: on-failure
    deploy:
      resources:
        limits:
          cpus: "0.25"
          memory: 256m
        reservations:
          cpus: "0.1"
          memory: 128m
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5001/"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 10s
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
    image: backend:v1
    restart: on-failure
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512m
        reservations:
          cpus: "0.25"
          memory: 256m
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 15s
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
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512m
        reservations:
          cpus: "0.25"
          memory: 256m
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

volumes:
  postgres-data:

networks:
  frontend-network:
  backend-network:
```

No Dockerfile changes. No application code changes. Resource limits are container configuration, applied at creation time through `docker compose up -d`.



## 5. Deep Understanding

### Memory Limit vs Memory Reservation

`limits.memory` is a hard ceiling. If the container tries to exceed it, the OOM killer intervenes. The container does not get to negotiate — the kernel terminates the process that caused the breach.

`reservations.memory` is a soft request. It tells the scheduler "this container needs at least this much memory to function." In Docker Compose on a single host, the reservation is documented but not enforced in the same way as in a cluster — Docker will not refuse to start the container if the host is under memory pressure. In Swarm or Kubernetes, reservations are used for placement decisions: a node must have at least `reservations.memory` available to receive this container.

Setting a reservation slightly below the expected idle usage, and a limit significantly above the expected peak usage, is a sensible baseline. The reservation communicates the minimum requirement. The limit caps the pathological case.

### The OOM Killer and What It Terminates

When a container hits its memory limit, the Linux kernel's OOM killer selects a process to terminate. Inside a container, the OOM killer's scope is the cgroup — it can only terminate processes within that container. It selects the process with the highest OOM score, which is typically the one using the most memory.

For the backend, PID 1 is Gunicorn's master process. The workers are child processes. If a worker causes the OOM breach, the OOM killer may kill the worker or the master depending on which has the higher score. If the master is killed, the container exits (because PID 1 died) and the restart policy takes over. If a worker is killed but the master survives, Gunicorn may spawn a replacement worker without the container exiting at all.

This means hitting a memory limit does not always produce a visible container restart. Sometimes Gunicorn handles the killed worker internally. The signal is in `docker stats` — memory usage dropping suddenly back to baseline — and in `dmesg` or system logs where the OOM kill event is recorded.

### CPU Limits — Throttling vs Starvation

The `cpus` limit controls the maximum CPU the container can consume. It does not control what happens when the container is not using its full allocation — another container is free to use that CPU capacity. The limit is a ceiling on consumption, not a reservation of capacity.

`reservations.cpus` sets a minimum priority. In a Swarm or Kubernetes environment, it influences placement. On a single host with Docker Compose, it primarily documents the intent: this service requires at least this much CPU to function correctly.

Setting CPU limits too low is a real operational hazard. Postgres is particularly sensitive — if CPU is throttled heavily during a write-heavy workload, transactions take longer to commit, locks are held longer, and concurrency degrades significantly. A Postgres instance with `cpus: 0.1` on a busy workload will appear to be functioning but will be orders of magnitude slower than it should be. The CPU usage in `docker stats` will show it consistently pinned at its limit — a sign that the limit is too tight.

### `docker stats` — Reading It Correctly

```bash
docker stats
```

The output refreshes every second by default. The important columns:

`CPU %` — the percentage of a single CPU core being used. A container with `cpus: 0.5` that shows `50%` is at its ceiling. A container with `cpus: 2.0` that shows `50%` is using one full core — half its allocation.

`MEM USAGE / LIMIT` — current memory usage versus the configured limit. If there is no limit, the host memory is shown as the limit. A container approaching its limit is worth watching.

`MEM %` — memory usage as a percentage of the limit. Above 80% is worth attention. Above 95% means the container is at risk of hitting the OOM killer.

`NET I/O` — cumulative network bytes in and out since the container started. Useful for identifying unexpected traffic, not for real-time rate monitoring.

`BLOCK I/O` — cumulative disk reads and writes. A database with high block I/O under memory pressure is paging — a sign the memory limit may be too low.

```bash
docker stats --no-stream          # single snapshot, useful in scripts
docker stats backend              # single container only
docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"  # custom columns
```

### Sizing Limits for Your Stack

The right limits for a service depend on what the service does. There is no universal correct value. The approach that works:

First, measure. Run `docker stats` under representative load — not idle, not worst-case stress, but the load the service normally sees. Note the peak memory and CPU usage. That is your baseline.

Second, add headroom. A limit at 1.5–2x the normal peak gives the service room to handle traffic spikes and temporary load increases without hitting the ceiling. A limit closer to 1.1x the peak will cause the OOM killer to fire under normal heavy traffic — which is not the intent.

Third, watch and adjust. After setting limits, monitor `docker stats` regularly for the first few days. A container consistently using 90% of its memory limit needs a higher limit. A container using 5% of its memory limit is over-provisioned — the limit can be tightened.

Postgres is a special case. It is aggressive about using available memory for caching. If you give Postgres 512MB, it will use most of it — not as a leak, but intentionally, to keep frequently accessed data in memory. That memory usage is healthy and desired. Do not mistake high Postgres memory usage for a problem.



## 6. Commands

```bash
# ── Observing Resource Usage ───────────────────────────────────────────────

docker stats                                     # live usage for all containers
docker stats --no-stream                         # single snapshot
docker stats backend                             # single container
docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

# ── Verifying Limits Are Applied ──────────────────────────────────────────

# Memory limit in bytes (divide by 1048576 for MB)
docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.Memory}}'

# CPU limit in nanocpus (divide by 1000000000 for cores)
docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.NanoCPUs}}'

# Both together
docker inspect $(docker compose ps -q backend) \
  --format='Memory={{.HostConfig.Memory}} NanoCPUs={{.HostConfig.NanoCPUs}}'

# All containers — name, memory limit, cpu limit
docker inspect $(docker compose ps -q) \
  --format='{{.Name}}: mem={{.HostConfig.Memory}} cpus={{.HostConfig.NanoCPUs}}'

# ── Applying Limits ────────────────────────────────────────────────────────

docker compose up -d                             # recreates containers with new config
# No rebuild needed — limits are container config, not image config

# ── Stress Testing (requires stress-ng inside the container) ───────────────

# Install stress-ng temporarily for testing
docker compose exec backend apt-get install -y stress-ng
docker compose exec backend stress-ng --vm 1 --vm-bytes 400M --timeout 10s

# ── Reading OOM Events ────────────────────────────────────────────────────

# On Linux hosts — OOM kills are logged to kernel ring buffer
sudo dmesg | grep -i "oom\|kill"
sudo dmesg | grep -i "memory cgroup"
```



## 7. Real-World Notes

Resource limits are one of the most commonly skipped steps in Docker deployments, and one of the most commonly regretted ones. The typical sequence is: deploy without limits, run in production for weeks, one service develops a memory leak or gets hit with unusual traffic, it consumes the entire host, the OOM killer starts terminating processes at random, everything goes down. Post-incident, limits get added. They should have been there from the start.

The `deploy.resources` syntax in docker-compose.yml is the Compose v3 syntax that is also understood by Docker Swarm. This is deliberate design — the same Compose file can be deployed to a single host with `docker compose up` or to a Swarm cluster with `docker stack deploy`, and the resource limits transfer correctly. When migrating to Kubernetes, the `limits` and `reservations` map directly to Kubernetes resource `limits` and `requests`, with the same semantics.

Postgres deserves special attention when setting memory limits. Postgres reads its `shared_buffers` configuration on startup and expects to allocate that memory immediately. If `shared_buffers` in the Postgres config is larger than the container's memory limit, Postgres will fail to start. The default `shared_buffers` in Postgres 15 is 128MB. A container memory limit below 256MB is likely to cause Postgres startup problems, even before accounting for working memory and OS overhead.

CPU limits also interact with Postgres in a non-obvious way. Postgres uses parallel query execution for large queries — it spawns multiple worker processes that each consume CPU. If the CPU limit is tight, parallel queries will be throttled severely, making them slower than their single-threaded equivalent. For development stacks, this rarely matters. For production, Postgres CPU limits should be set with the query workload in mind.

`docker stats` in a terminal is useful for a manual check. For ongoing monitoring, integrating container metrics into a proper monitoring system — Prometheus with cAdvisor, Datadog, CloudWatch Container Insights — gives you historical visibility, alerting, and trend analysis. A memory limit at 85% for five seconds is worth a manual look. A memory limit at 85% for three hours every evening at peak traffic is a pattern that requires capacity planning.



## 8. Exercises

**Exercise 1 — Establish your baseline**

Before adding any limits, run `docker stats --no-stream` and record the memory and CPU usage for all three containers. Send some requests to generate activity first:

```bash
for i in $(seq 1 20); do curl -s http://localhost:5000/notes > /dev/null; done
docker stats --no-stream
```

Note the memory usage for each service. These numbers are your baseline — the foundation for deciding what limits are appropriate.

**Exercise 2 — Confirm there are no limits by default**

Check the current memory limit on the backend:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.Memory}}'
```

You should see `0`. Zero means no limit — the container can use the full host memory. Do the same for the frontend and database. All three should show `0`.

**Exercise 3 — Add limits and verify they apply**

Add `deploy.resources` limits to all three services in `docker-compose.yml`. Apply them:

```bash
docker compose up -d
```

Now check the memory limit again:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.Memory}}'
```

You should see a non-zero value. Convert it to MB: divide by 1048576. Confirm it matches what you configured. Do the same for all three containers.

**Exercise 4 — Watch the limit in docker stats**

Run `docker stats` live:

```bash
docker stats
```

Note the `MEM USAGE / LIMIT` column. It should now show your configured limit instead of the host total memory. Send a burst of requests:

```bash
for i in $(seq 1 50); do curl -s http://localhost:5000/notes > /dev/null; done
```

Watch the memory usage fluctuate during the burst. Observe that it stays well within the limit under normal load. This confirms the limit is protecting the host without affecting normal operation.

**Exercise 5 — Introduce a memory leak and watch the limit enforce itself**

Add this route to `app.py` temporarily:

```python
_leak = []

@app.route("/leak")
def leak():
    _leak.append(" " * 10_000_000)  # ~10MB per call
    return jsonify({"chunks": len(_leak)})
```

Rebuild the backend. In one terminal, watch `docker stats` live:

```bash
watch -n 1 docker stats --no-stream
```

In another terminal, hit the leak endpoint repeatedly:

```bash
for i in $(seq 1 60); do curl -s http://localhost:5000/leak; sleep 0.5; done
```

Watch the memory usage climb toward the limit. When it hits the ceiling, observe the container restart (visible in `docker compose ps`). After the restart, confirm the memory usage returns to the idle baseline — the restart cleared the leak. Remove the leak route and rebuild when done.

**Exercise 6 — CPU limit observation**

While `docker stats` is running in one terminal, generate sustained CPU load in the backend:

```bash
docker compose exec backend python -c "
import time
start = time.time()
while time.time() - start < 10:
    pass
"
```

Watch the `CPU %` column for the backend in `docker stats`. It should plateau at approximately the CPU limit you configured, rather than climbing to 100% of a full core. This is the throttle in action — the container is not crashing, it is just slowing down at the ceiling.

**Exercise 7 — Find the right limit for your service**

This exercise is about judgment, not observation. After running exercises 1–6, answer these questions in writing before moving to step 22:

What memory limit did you set for the backend, and what was the reasoning? How much headroom does it give above the idle baseline? What would happen if the backend received 10x normal traffic — would it stay within the limit? What would you change about the limits after seeing the actual usage in exercises 4 and 5?

There is no single correct answer. The point is to connect the numbers in `docker stats` to deliberate decisions in `docker-compose.yml` — which is the entire skill that resource limits require.