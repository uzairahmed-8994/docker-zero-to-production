# 28 — Interview Questions



## 0. What This Step Is

This step prepares you for technical interviews where Docker is evaluated at a production level — backend engineering, DevOps, SRE, and platform engineering roles. The questions here are not definitions. They are the questions interviewers ask when they want to know whether you have actually run Docker in production, debugged it under pressure, and made architectural decisions with real tradeoffs.

For each question: read the strong answer, understand the reasoning behind it, and think through the follow-up questions before reading their answers. The follow-ups are where most candidates lose marks — they are designed to probe whether your answer was genuine experience or rehearsed talking points.

---



## Debugging



### Q1 — A container keeps restarting. How do you debug it?

**What this question tests:** Whether you know the difference between a symptom and a cause, and whether your debugging approach is systematic or random.

**Strong answer:**

The restart itself tells me nothing except that the container exited and the restart policy triggered. My first step is to find the exit code:

```bash
docker inspect <container> --format='{{.State.ExitCode}} {{.RestartCount}}'
```

Exit code 0 means a clean exit — something sent a graceful shutdown signal or the process decided to stop. Exit code 137 means SIGKILL, most likely the OOM killer. Exit code 1 or any non-zero means an application error.

Once I have the exit code, I read the logs from the most recent startup attempt:

```bash
docker logs <container> --tail 50
```

If it is exit code 137 and `dmesg` shows OOM events, the container is hitting its memory limit — either the limit is too low or there is a leak. If it is exit code 1 with a Python traceback, I read the traceback. If it is exit code 0 and the logs show a clean startup followed by a clean shutdown, something external is stopping the container — I check cron jobs and scripts.

One thing I never do: assume the current logs are representative. In a crash loop, the container restarts with a clean process each time. I need to act fast if I want to catch the crash output before the next restart cycle clears my ability to see it.

**Follow-up questions:**

- The exit code is 0, the application looks healthy in the logs, but the container restarts every hour on the hour. What do you check?
- The container is in a crash loop and the logs are empty — the process never writes anything before dying. How do you debug this?
- Exit code 137, no OOM events in dmesg, memory usage looks normal. What else could cause SIGKILL?
- The restart count is 47 but the container is currently `healthy`. Should you be concerned?

**Red flags:**

Saying "I'd restart the container and see if it helps" — that is what the restart policy is already doing. Saying "I'd check the logs" without being specific about what you look for in the logs. Not knowing what exit codes mean.

---



### Q2 — Your application works locally but fails in production. What is your debugging process?

**What this question tests:** Systematic thinking about environment differences, and whether you know the specific failure modes that distinguish local from production.

**Strong answer:**

"Works locally, fails in production" almost always means an environment difference, not a code difference. The failure modes fall into a short list, and I check them in order.

First, I verify what image is actually running in production:

```bash
docker inspect <container> --format='{{index .RepoDigests 0}}'
```

I cross-reference this against the digest of what was pushed. If they differ, the deployment did not succeed in the way I thought.

Second, I check environment variables:

```bash
docker exec <container> env | sort
```

I look for empty values, missing variables, and values with unexpected whitespace — particularly CRLF line endings if the `.env` file was created on Windows. These are silent failures: the variable appears present but its value is wrong.

Third, I compare the dependency environment. The image was built from `requirements.txt` — but was the local install from the same file? I check whether any package was installed locally via `pip install` without being added to `requirements.txt`. The image is built from the file; the local environment has everything the developer ever installed.

Fourth, I look at network reachability. The local stack had services on `localhost`. The production stack has services on a Docker network. If the application hardcodes `localhost` anywhere instead of reading the hostname from an environment variable, it works locally and fails in production.

**Follow-up questions:**

- Your environment variables are all correct. The image digest matches. The logs show a connection refused to the database. What do you check next?
- The application imports a module that exists on your laptop but not in the image. The CI pipeline passed. How is this possible?
- How do you verify that the image in production is exactly the one that the CI pipeline built and tested?
- What is the difference between an environment variable being absent and being present but empty in Docker Compose?

**Red flags:**

Saying "I'd add print statements" without first checking the environment. Not knowing how to verify the running image digest. Assuming the CI pipeline guarantees the image is correct without explaining what the CI pipeline actually tested.

---



### Q3 — Your logs show no error, but the system is returning wrong data. How do you approach this?

**What this question tests:** Debugging non-obvious failures, understanding multi-worker application behaviour, and knowing the difference between application health and correctness.

**Strong answer:**

"No error, wrong data" is more subtle than a crash. I start by establishing what "wrong" means precisely — is the data stale, is it from the wrong record, does it change between requests?

If the data changes between identical requests, my first suspicion is per-worker in-process state. With multiple Gunicorn workers, each worker is an independent Python process. Module-level variables — caches, counters, connection pools not properly shared — have independent values in each worker. A request routed to worker 1 returns different data than one routed to worker 2.

I check:

```bash
docker exec <container> ps aux | grep gunicorn
# How many workers?

docker exec <container> grep -r "_cache\|global\|module_level" /app/*.py
# Any in-process mutable state?
```

If the data is consistently stale regardless of which worker handles it, the issue is a cache that is not invalidated on writes, or a query that is reading from a replica that is lagging.

I always verify the database directly:

```bash
docker exec db psql -U appuser -d appdb -c "SELECT * FROM notes ORDER BY created_at DESC LIMIT 5;"
```

If the database has the correct data and the application returns the wrong data, the bug is between the database and the response. If the database also has wrong data, the bug is in the write path.

**Follow-up questions:**

- Your application has two Gunicorn workers. Worker 1 has a cache that was refreshed 5 seconds ago. Worker 2 has one refreshed 55 seconds ago. A user creates a note and then immediately reads the notes list. Describe all the possible outcomes.
- How would you fix in-process caching in a multi-worker Flask application?
- The data is correct in the database but the API returns a 200 with an empty list. No errors. What are the possible causes?
- How does `read_only: true` on the container filesystem interact with this class of bug?

---



## Networking



### Q4 — One container cannot reach another. Walk me through your debugging.

**What this question tests:** Understanding Docker networking layers — DNS, network membership, port binding — and the ability to isolate which layer is failing.

**Strong answer:**

I treat container-to-container connectivity as three separate problems that look identical from the outside: DNS resolution, network membership, and the service actually listening.

I start with DNS:

```bash
docker exec backend python -c "import socket; print(socket.gethostbyname('db'))"
```

If this fails, the two containers are not on the same Docker network. In Docker Compose, services on different named networks cannot resolve each other by service name. I check:

```bash
docker inspect backend --format='{{json .NetworkSettings.Networks}}' | python -m json.tool
docker inspect db --format='{{json .NetworkSettings.Networks}}' | python -m json.tool
```

If DNS resolves but the connection fails:

```bash
docker exec backend python -c "
import socket
s = socket.socket()
s.settimeout(3)
print('open' if s.connect_ex(('db', 5432)) == 0 else 'refused')
"
```

Connection refused means the service is not listening on that port. The database container may be starting up, may have crashed, or may be listening on a different port. Connection timeout means there is a network policy or firewall between them — unusual in Docker but possible with custom network configurations.

If the connection succeeds but the application still cannot talk to the service, the problem is at the application layer — credentials, protocol, TLS configuration.

**Follow-up questions:**

- Two services are on different Docker networks. What is the quickest way to make them able to communicate without restarting either container?
- Your `docker-compose.yml` has `ports: ["5432:5432"]` on the database service. Does this affect container-to-container communication?
- A service can reach the database by IP but not by hostname. What does this tell you?
- What happens to container DNS when you scale a service to multiple replicas with `docker compose up --scale backend=3`?

**Red flags:**

Jumping straight to checking port mappings without first checking network membership. Not knowing that Docker's internal DNS uses service names, not container names. Confusing `ports` (host binding) with inter-container connectivity.

---



### Q5 — Explain the difference between Docker's bridge network, host network, and overlay network. When would you use each?

**What this question tests:** Network architecture understanding and practical decision-making, not memorised definitions.

**Strong answer:**

Bridge is the default. Each container gets an IP on a virtual network isolated from the host. Containers communicate by service name via Docker's embedded DNS. Host ports are mapped explicitly with `ports:`. This is the right choice for most Docker Compose deployments — it gives isolation without complexity.

Host networking removes the network namespace entirely. The container shares the host's network stack. Port mappings are irrelevant — if the container binds to port 5000, port 5000 on the host is bound. This is useful when you need maximum network performance or when running network monitoring tools that need to see all host traffic. The tradeoff is that you lose network isolation — a container on host networking can bind to any port on the host and reach anything the host can reach.

Overlay networks span multiple Docker hosts — they are used in Docker Swarm for communication between containers running on different machines. In Docker Compose on a single host, overlay networks are not needed.

In practice: bridge for applications, host for monitoring agents and network tools that need host-level visibility, overlay only in Swarm deployments.

**Follow-up questions:**

- Two containers on different bridge networks need to communicate. How do you achieve this without putting them on the same network?
- What are the security implications of running a container on the host network?
- In Docker Swarm, how does a container on node A communicate with a container on node B without knowing the other node's IP?

---



## Performance



### Q6 — Your containerised application is noticeably slower than the same application running directly on the host. How do you diagnose and resolve this?

**What this question tests:** Understanding that containerisation itself has overhead, and knowing which layer that overhead comes from.

**Strong answer:**

I start by measuring where the slowness actually is, because "slower" is not a useful diagnosis.

```bash
docker stats --no-stream
```

If CPU is at the configured limit, the container is CPU-throttled. The `cpus` setting in Compose translates to CFS bandwidth control in the kernel — the container gets a fixed CPU quota per scheduling period. If the application is compute-bound and the quota is too low, every operation takes longer. The fix is to increase the CPU limit or reduce the quota's per-period granularity.

If memory is near the limit, the container may be doing excessive garbage collection under memory pressure, or the application is paging. I look at block I/O in `docker stats` — high block I/O under memory pressure means the process is swapping.

If CPU and memory are both fine, I check whether the slowness is network-related. Docker's bridge networking adds a small amount of latency compared to direct host-to-host communication — typically sub-millisecond, but it compounds if there are many small requests. Applications making hundreds of small database queries in a loop feel this more than applications making a few large queries.

If none of these, I look at volume mounts. Bind mounts on macOS and Windows have significant I/O overhead because they cross the VM boundary between the host filesystem and the Docker VM. On Linux, bind mounts have near-zero overhead. An application that does a lot of file I/O through a bind mount on macOS can be 10–50x slower than on Linux.

**Follow-up questions:**

- Your application runs at 100ms on the host and 400ms in the container. CPU and memory look fine. What is the next thing you check?
- What is the performance difference between a named volume and a bind mount? In what situation does it matter?
- You set `cpus: 0.5` on the backend. Under load, the application responds slowly but CPU usage shows 45%. Why might the application still be throttled?
- How does the `--network host` flag affect application performance, and when is it worth the security tradeoff?

---



### Q7 — Memory usage in your container grows steadily over days without any obvious leak. How do you find and fix it?

**What this question tests:** Memory debugging methodology, Python-specific knowledge, and the ability to distinguish intentional memory use from a leak.

**Strong answer:**

The first thing I establish is whether the growth is linear or logarithmic. Linear growth over time is a classic leak — the application accumulates objects at a rate proportional to its activity. Logarithmic growth that plateaus is normal — Postgres, for example, grows its shared buffer cache aggressively up to its configured limit and then holds steady.

For a Python application I start with the growth rate:

```bash
while true; do
  echo "$(date): $(docker stats --no-stream --format '{{.MemUsage}}' backend)"
  sleep 300
done
```

If growth is linear, I look at what Python objects are accumulating. The Python GC tracks everything — I can ask it:

```python
import gc
gc.collect()
counts = {}
for obj in gc.get_objects():
    t = type(obj).__name__
    counts[t] = counts.get(t, 0) + 1
for name, count in sorted(counts.items(), key=lambda x: -x[1])[:10]:
    print(f'{count:8d}  {name}')
```

A high count of a specific application type — a model class, a database row type, a custom object — points directly at the code that creates those objects. I then find every place that object is created and ask: is there a reference being held that prevents garbage collection?

The most common cause in Flask applications is module-level mutable state: a list or dict at the top level of a module that accumulates items and is never cleared. Module-level state in Gunicorn workers persists for the lifetime of the worker process.

**Follow-up questions:**

- The memory growth stops at 300MB and holds steady for a week. Is this a leak?
- Your Flask application has `_cache = {}` at module level and never clears it. Under what conditions does this become a problem, and how does the number of Gunicorn workers affect it?
- The resource limit from step 21 catches the leak and restarts the container before it takes down the host. Is this an acceptable long-term solution?
- What is the difference between a Python memory leak and a C extension memory leak, and how does the debugging approach differ?

---



## Deployment



### Q8 — You push a new image and your deployment breaks. What is your immediate response, and how do you prevent this class of failure?

**What this question tests:** Incident response prioritisation, rollback discipline, and process thinking about deployment safety.

**Strong answer:**

Immediate response: rollback before investigating. The deployment broke something and users are affected. Investigating root cause takes time. Rolling back takes 90 seconds if the previous version tag is known.

```bash
# Edit docker-compose.yml: revert image tag to previous version
docker compose pull
docker compose up -d
```

Once users are back, I investigate the failed deployment from the logs of the failed container run — either from the pipeline log or from `docker logs` if the container is still in a stopped state.

To prevent this class of failure, the pipeline must test the image before it reaches production. At minimum: build the image, start it, verify the health endpoint responds. Better: run integration tests against the full stack. The test step is the gate — a deployment that breaks production means either the tests did not cover the failure mode or there were no tests.

The specific failure I check first after any deployment break is a missing dependency. A `ModuleNotFoundError` means a package was installed locally via `pip install` but not added to `requirements.txt`. The local environment had it; the image built from `requirements.txt` did not. CI passed because the test step did not import the new module.

Using pinned version tags rather than `latest` makes rollback instant — you know exactly what to roll back to. With `latest`, you need to find the previous digest from the registry history, which costs time during an incident.

**Follow-up questions:**

- You need to roll back but the previous version tag was `latest` and has been overwritten. How do you find the previous image?
- Your deployment broke not because of a code bug but because a configuration value changed on the server. How does your rollback strategy differ?
- How do you design a deployment process that makes rollback a one-line change?
- What is the difference between a blue-green deployment and a rolling restart in terms of rollback ability?

**Red flags:**

Saying "I'd read the logs first" before stabilising — users are down while you read logs. Not knowing how to verify that a rollback actually took effect. Using `latest` in production and not having a strategy for rollback.

---



### Q9 — What is wrong with using the `latest` tag in production, and what should you use instead?

**What this question tests:** Understanding tag mutability and the operational consequences of relying on mutable references in production systems.

**Strong answer:**

The `latest` tag is a mutable pointer. When you push a new image, `latest` moves to point to the new image. Two `docker pull myimage:latest` calls on different days can return completely different images — same tag, different content, different behaviour.

In production this creates two specific problems. First, rollback becomes ambiguous. If your `docker-compose.yml` says `image: myusername/backend:latest` and you need to roll back, rolling back to `latest` deploys whatever `latest` currently points to — which is the broken version you just pushed. You have no stable reference to the previous good state.

Second, an automated deployment system that runs `docker compose pull && docker compose up -d` on a cron job will silently pick up whatever is tagged `latest` at that moment, including untested or broken images that were pushed to the same tag by a developer working locally.

The correct approach is pinned semantic version tags for production: `v1.0.4`. This tag is written in the `docker-compose.yml` file, committed to version control, and only changed deliberately as part of a deployment. The deployment history is the git history of that file — you can see exactly when each version was deployed, by whom, and roll back by reverting the commit.

For CI builds between releases, commit SHA tags: `git-a1b2c3d`. Immutable by design — the git SHA never changes, so the tag never needs to move.

**Follow-up questions:**

- You are running `v1.0.4` in production. You need to ship a hotfix urgently. Walk me through the version tagging from the fix commit to production deployment.
- What is the difference between a tag being mutable and an image being overwritten? Can you have one without the other?
- Your staging environment uses `latest` and your production uses `v1.0.4`. A bug appears in production but not in staging, even though staging is on a newer version. How do you approach this?
- What is a digest, and in what situation would you use a digest rather than a version tag in production?

---



## Architecture



### Q10 — Why not run the entire application in one container?

**What this question tests:** Understanding operational concerns around deployability, scaling, fault isolation, and the principle of single responsibility in container design.

**Strong answer:**

There are several reasons, and they matter at different scales.

Independent deployability. If the frontend and backend are in the same container, deploying a frontend-only change requires rebuilding and restarting the backend. With separate containers, each service deploys independently. This reduces the blast radius of every deployment.

Independent scaling. If the backend needs three instances and the frontend needs one, you cannot achieve this with a single container. With separate containers you scale each independently.

Fault isolation. If the backend crashes in a single-container setup, the whole application is down — including the frontend, which might be capable of showing a degraded-mode UI. Separate containers mean a backend crash affects only the backend.

Different lifecycle. The database needs to persist data across restarts and requires a volume. The application server is stateless and can be replaced freely. Running them in the same container means the database restart policy, the volume configuration, and the process management all have to satisfy the needs of both simultaneously — which is architecturally awkward.

Process management. Docker is designed for single-process containers — it monitors PID 1. A container running multiple processes needs a supervisor (like `supervisord`), which adds complexity and obscures individual process health.

The exception: tightly coupled processes that are always deployed together and are meaningless without each other. Even then, a multi-stage build with a single output image is usually better than an actual multi-process container.

**Follow-up questions:**

- If one-process-per-container is the principle, how does a container running Gunicorn with four worker processes fit this model?
- You have a background task that runs alongside the Flask application. Should it be in the same container or a separate one?
- What is a sidecar container pattern, and when would you use it?
- At what point does the operational overhead of managing many small containers outweigh the isolation benefits?

---



### Q11 — When would you choose Docker Compose over Kubernetes, and when would you choose Kubernetes over Docker Compose?

**What this question tests:** Architectural judgement and the ability to reason about operational tradeoffs, not just knowledge of features.

**Strong answer:**

Docker Compose is the right tool when the deployment target is a single host, the team is small, and the operational overhead of Kubernetes is not justified by the workload. Compose gives you service definition, networking, volume management, health checks, restart policies, and resource limits — everything this stack has built across the last fourteen steps. For a team of two running a web application on one server, Compose is sufficient and significantly simpler to operate.

Kubernetes is the right tool when you need things Compose cannot provide: running across multiple nodes, automatic rescheduling of failed pods to healthy nodes, zero-downtime rolling deployments with automatic rollback, horizontal autoscaling based on CPU or custom metrics, and the ability to manage hundreds of microservices with consistent configuration. Kubernetes also has a vastly larger ecosystem of tooling — service meshes, secrets management, advanced networking policies.

The decision is not about application complexity — it is about operational requirements. A complex application with ten services can run perfectly well on Compose if it runs on a single host and a human can restart a failed service within minutes. A simple application with three services needs Kubernetes if it must tolerate node failures without human intervention.

The honest middle ground: many teams use Compose locally and in CI, and deploy to Kubernetes in production. The `docker-compose.yml` format is not directly translatable to Kubernetes manifests, but the concepts — services, environment variables, health checks, resource limits — map directly.

**Follow-up questions:**

- Your Docker Compose application is growing and you are considering migrating to Kubernetes. What are the first three pain points in your current setup that would justify the migration cost?
- Docker Swarm exists between Compose and Kubernetes. Why did most of the industry bypass it?
- You have a single-server deployment that needs zero-downtime deployments. Is Kubernetes necessary, or can Compose achieve this?
- How does the `deploy.resources` section of a docker-compose.yml relate to Kubernetes resource requests and limits?

**Red flags:**

Saying "Kubernetes is always better" — it carries enormous operational overhead that is unjustified for simple deployments. Saying "Compose is only for development" — many production workloads run on Compose appropriately. Not being able to describe a specific pain point that would drive the migration.

---



### Q12 — How do you design a container that is easy to debug in production without compromising security?

**What this question tests:** The tension between security hardening and operational debuggability, and whether the candidate has thought through both sides.

**Strong answer:**

The security hardening from step 22 — read-only filesystem, dropped capabilities, non-root user — makes containers significantly harder to debug interactively. You cannot write files, you cannot install debugging tools, you cannot run as root to inspect system resources.

The resolution is to design for external observability rather than internal access. If the container logs everything it should be logging, you should rarely need to exec into it. The logging design matters more than the ability to shell in.

Concretely: structured logging at INFO level that covers every operation, a global error handler that logs exceptions with full tracebacks, health check endpoints that report internal state, and resource metrics visible from outside the container via `docker stats` and `docker inspect`. With these in place, most production problems are diagnosable from outside the container.

For the cases where you do need to exec in — a problem that is not visible in logs — the production image should have a minimum set of tools: a shell, `curl`, and `ps`. Dropping all capabilities does not prevent you from exec-ing in as the running user and reading files, checking processes, and testing network connectivity. What it prevents is privilege escalation from within the container.

The second approach is debug images: a separate image tag that adds debugging tools on top of the production image, used only when needed and never deployed as the running image. `FROM myimage:v1.0.4 as debug` with additional packages gives you a controllable debug environment without baking those tools into the production image permanently.

**Follow-up questions:**

- Your production container has `read_only: true` and `cap_drop: ALL`. A process inside the container is behaving unexpectedly. What debugging tools are still available to you?
- What is the operational risk of temporarily deploying a debug image to production to diagnose an issue?
- How would you add a `/debug/pprof` style endpoint to a Flask application without exposing it publicly?
- Your security team requires that no shell is present in production images. How does this change your debugging strategy?

---



## CI/CD



### Q13 — Your CI pipeline passes but the production deployment fails. What are the most common causes and how do you design against them?

**What this question tests:** Understanding the gap between what CI validates and what production requires, and the ability to reason about pipeline design.

**Strong answer:**

A pipeline that passes but produces a broken deployment means the pipeline did not validate something that production requires. The common gaps:

Environment differences. The CI environment has secrets and configuration injected by the pipeline. Production has different values — or the values are missing entirely. The pipeline tested with `DB_PASSWORD=testpass`. Production has an empty `DB_PASSWORD` because the `.env` file was never created. The image is correct; the environment is not.

Missing dependencies. The pipeline built the image from `requirements.txt` and ran the health check. The health check hit `/health` which imports nothing new. A new feature imports `flask-limiter` which is not in `requirements.txt`. The image builds and the health check passes. The feature endpoint crashes in production.

Image digest mismatch. The pipeline built and pushed `v1.0.4`. The production server is still running `v1.0.3` because `docker compose up -d` was run before `docker compose pull` completed, or the compose file was not updated.

Infrastructure changes. The database schema changed in this deployment. The migration ran in CI against a clean database. Production has 3 years of data with a different schema, and the migration fails silently.

Design against this: make the test step exercise the new code paths, not just the health endpoint. Validate required environment variables before starting the container. Record and verify the image digest after deployment. Run schema migrations as a separate step with explicit success verification before restarting the application.

**Follow-up questions:**

- Your pipeline tests the Docker image by starting it and hitting the health endpoint. What is this test not validating?
- How do you validate that the image deployed to production is exactly the image that the pipeline built?
- A developer merges a PR that adds a new environment variable. The pipeline passes. Production breaks. What process change prevents this?
- What is the difference between a smoke test and an integration test in the context of a Docker deployment pipeline?

---



### Q14 — How do you validate a Docker image before deploying it to production?

**What this question tests:** Practical knowledge of deployment safety mechanisms beyond "the build succeeded."

**Strong answer:**

Validation happens at multiple gates, not one.

In the CI pipeline: build the image, start it with `docker run`, wait for the health check to pass, make at least one real request to a non-trivial endpoint. If the application has a test suite, run it inside the container against a test database — this validates both the image contents and the application behaviour.

Vulnerability scanning: `trivy image myimage:v1.0.4` before the push step. A Critical CVE with a fix available should block the deployment. The scan result goes into the pipeline log as a permanent record.

Image metadata verification: check that the OCI labels in the image match the expected version, git SHA, and build time. A mismatch means the image was not built by the expected pipeline run.

After push, before deploy: verify the digest of the image in the registry matches the digest that was pushed. Registries do not lose data, but this check catches tag manipulation.

After deploy: verify the running container's digest matches the pushed digest. `docker inspect` the container, compare against the registry manifest. If they differ, the deployment did not complete correctly.

```bash
# After deployment:
docker inspect $(docker compose ps -q backend) \
  --format='{{index .RepoDigests 0}}'
# Compare against: docker manifest inspect myimage:v1.0.4 | grep digest
```

**Follow-up questions:**

- Your vulnerability scanner reports a Critical CVE in a system library that your application does not call. Do you block the deployment?
- You have validated the image in staging. How much of that validation do you trust to carry over to production?
- What is the minimal useful test that is worth adding to a pipeline that currently has no image testing?
- How do you handle the case where the vulnerability scanner has no database entry for a very new CVE?

---



## Production Thinking



### Q15 — You are on-call and get paged at 3am. The service is down. Walk me through your first five minutes.

**What this question tests:** Incident response discipline under pressure — whether you have an automatic procedure or whether you think from first principles each time.

**Strong answer:**

I have a fixed procedure for the first two minutes that does not require thinking.

First: `docker compose ps`. This tells me which service is affected and what state it is in — exited, restarting, unhealthy. If everything shows `healthy`, the problem is at a layer Docker does not know about — a network upstream, a DNS change, the host itself.

Second: `docker stats --no-stream`. Is any container near its resource limit? Memory at 98% means OOM kill is imminent or already happened. CPU pinned at the limit means the service is throttled.

Third: I check the restart count. A count climbing fast means the service has been crashing for a while and the restart policy has been masking it. The user impact started before I got paged.

At this point I know enough to make the first decision: can I stabilise immediately or do I need to investigate first? If the container is exited and there is a known previous good version, I roll back before reading another log line. If the state is unhealthy but running and users are partially served, I have more time.

After stabilising: I read the logs from the time of the incident with timestamps, cross-reference with the deployment history, and identify whether the incident was caused by a recent deployment or by a runtime event.

Everything I do in those five minutes goes into an incident log. When I am fully awake and have fixed the problem, I write the post-mortem while the details are fresh.

**Follow-up questions:**

- You roll back and the service comes back up. 20 minutes later, it goes down again with the same symptom, despite running the previous version. What does this tell you?
- Your rollback fails because the previous image is no longer in the registry. How does this happen, and what process prevents it?
- The service is `healthy` according to Docker but users are receiving 500 errors. How is this possible, and what do you check?
- After the incident, what are the three most important things to document, and why?

---



### Q16 — How do you isolate a production issue to a specific layer — application, container runtime, networking, or infrastructure?

**What this question tests:** Systematic debugging methodology and the ability to reason about complex systems under pressure.

**Strong answer:**

I work from the outside in. Start with the user-visible symptom and progressively narrow the scope.

First: can the problem be reproduced from outside the host?

```bash
curl -f http://hostname:5001/health
```

If this fails: the issue is at or before the frontend. If it succeeds but the symptom involves the backend: the frontend is healthy, the issue is deeper.

Second: can the frontend reach the backend through Docker's internal network?

```bash
docker exec frontend curl -f http://backend:5000/health
```

If this fails: networking or the backend itself. If it succeeds: the issue is at the application layer inside the backend.

Third: can the backend reach the database?

```bash
docker exec backend python -c "import psycopg2, os; conn = psycopg2.connect(...); print('ok')"
```

If this fails: database layer. If it succeeds: the issue is in the application logic, not the infrastructure.

This approach gives me a precise answer to "which layer" within three commands. Everything deeper than that — reading logs, checking query plans, profiling the application — is targeted at the specific layer that failed.

The important discipline: do not skip layers. A backend that cannot reach the database produces application errors that look like code bugs. A network partition between frontend and backend produces frontend errors that look like frontend bugs. The symptom is in the wrong layer.

**Follow-up questions:**

- Each layer check passes individually, but the end-to-end request still fails. What class of problem produces this outcome?
- Your backend can connect to the database with the Python psycopg2 test, but `GET /notes` returns a 500. What layer is the problem in?
- How do you isolate whether a slow response is caused by the application code or the database query?
- Describe a case where the networking layer check would pass but container-to-container communication would still fail.

---



### Q17 — What does a production-ready Docker setup look like? What is the minimum viable configuration?

**What this question tests:** End-to-end understanding of what production actually requires, and the ability to prioritise the things that matter from the things that are nice to have.

**Strong answer:**

Production-ready is not a single feature — it is the combination of properties that make the system operable when something goes wrong, not just when everything is working.

The minimum viable set, in priority order:

Non-root user. The process running in the container should not be root. If the container is compromised, the blast radius is contained. This is a one-line Dockerfile change with no operational cost.

Pinned image versions. Both the base image in the Dockerfile and the image reference in `docker-compose.yml`. Unpinned versions mean builds are not reproducible and deployments are not reversible.

Health checks with proper `start_period`. Docker needs to know when a container is healthy, not just when it is running. Without health checks, `depends_on` ordering is unreliable and the system has no automated way to detect degraded state.

Restart policies. Services should recover from crashes automatically. `on-failure` for application servers, `unless-stopped` for databases.

Resource limits. Memory limits prevent one container from consuming the host. Without them, a memory leak or a traffic spike takes down every service on the machine.

Structured logging to stdout. Logs that go to files inside the container are invisible outside it. Logs with consistent format are searchable. Log rotation prevents disk exhaustion.

Beyond the minimum, in rough priority: `.dockerignore` to prevent credential leaks, read-only filesystem, capability dropping, vulnerability scanning in CI, and a registry with versioned tags.

**Follow-up questions:**

- Of the items you listed, which one is most frequently missing in real production deployments you have seen, and what was the consequence?
- A team argues that resource limits are unnecessary because their application has been stable for two years without them. How do you respond?
- What is the first thing you check when you inherit a Docker deployment from another team and need to assess whether it is production-ready?
- Production-ready also means observable. What is the minimum observability setup for a Docker Compose stack?

---



## 1. How to Use This Step

**In an interview:** These questions test reasoning, not recall. The interviewers are watching how you think through the problem, not whether your answer matches a textbook. State your assumptions, explain your reasoning, and when you are uncertain, say what you would check rather than guessing.

**For preparation:** For each question, close this document and answer it out loud before reading the strong answer. The follow-up questions are where preparation pays off — they push on the edges of each answer where shallow knowledge collapses. Practice answering the follow-ups cold.

**On red flags:** The red flags listed are patterns that signal a candidate has theoretical knowledge but limited production experience. The best way to avoid them is to connect every answer to a specific thing you actually did: a command you ran, a failure you debugged, a decision you made and its consequence.

Every answer in this step reflects something that was built, broken, debugged, or decided across steps 14–27. If any answer feels abstract, go back to the step where that concept was introduced and re-read the experience section.