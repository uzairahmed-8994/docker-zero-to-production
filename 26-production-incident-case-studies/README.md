# 26 — Production Incident Case Studies



## 0. What This Step Is

The previous steps built capability: writing production Dockerfiles, securing images, adding health checks, enforcing resource limits, tagging releases, pushing to registries, and wiring up CI/CD pipelines. Each step presented a clean problem with a clear objective.

Production does not work that way. Problems arrive unannounced, without labels, at inconvenient hours. The same symptom — a container that will not start, a service returning 502, a deployment that reports success while traffic is failing — can have ten different root causes. The ability to navigate from symptom to root cause, under pressure, with incomplete information, is what separates operational competence from operational anxiety.

This step is nine case studies. Each is a real class of incident encountered by platform engineers, SREs, and infrastructure teams running Docker in production. The scenarios are technology-agnostic: the application could be Node.js, Go, Java, PHP, Python, or anything else. The problems are at the infrastructure layer — the container runtime, the host, the network, the registry, the daemon, the deployment pipeline — and the thinking process is the same regardless of what the application does.

The focus is not on the solution. It is on the investigation: which commands were run, what each one revealed, which hypotheses were formed and eliminated, and how the path from symptom to root cause actually unfolded. Real incidents include dead ends. Real investigations involve ruling things out before finding the answer. That is what these scenarios try to capture.

---



## Scenario 1 — exec format error: The Image That Would Not Start Anywhere Except the Developer's Machine

### Context

A team was preparing the first production deployment of a new service. The Dockerfile had been written and tested locally. The CI/CD pipeline built the image, pushed it to the registry, and reported success. The deployment job pulled the image on the production server and ran `docker compose up -d`. The container exited immediately.

### What Happened

`docker compose ps` showed the container in a restart loop within seconds of starting:

```
NAME        SERVICE     STATUS                       PORTS
api         api         restarting (1) 3 seconds ago
```

The logs showed a single line and nothing else:

```bash
docker compose logs api
```

```
standard_init_linux.go:228: exec user process caused: exec format error
```

No application output. No stack trace. No error from the application itself. The container was not reaching the point where any application code ran.

### Investigation

`exec format error` is a Linux kernel message, not an application message. It means the kernel tried to execute a binary and the binary's format was not recognised — specifically, the ELF header of the executable did not match the CPU architecture the kernel was running on.

The first thing I checked was the architecture of the image that had been pulled:

```bash
docker inspect api --format='{{.Architecture}}'
```

```
arm64
```

The production server was an x86-64 (amd64) machine. The image was arm64. The kernel refused to execute the binary.

I checked where the image had been built. The CI/CD pipeline ran on GitHub Actions and the `ubuntu-latest` runner — which is amd64. But before that image, the developer had pushed manually from their local machine to test the pipeline. I checked the image layers in the registry:

```bash
docker buildx imagetools inspect registry.example.com/api:latest
```

```
Name:      registry.example.com/api:latest
MediaType: application/vnd.docker.distribution.manifest.v2+json
Digest:    sha256:3a7f...

Manifest:
  Name:      registry.example.com/api:latest
  MediaType: application/vnd.docker.distribution.manifest.v2+json
  Platform:  linux/arm64
```

A single-platform manifest. The image in the registry was the one the developer had pushed from their M2 MacBook — arm64 — not the one the CI pipeline had built. The pipeline had built an amd64 image and pushed it, but the developer's manual push had happened *after* the pipeline run and had overwritten the `latest` tag with an arm64 image. The production server pulled `latest` and got the arm64 image.

I confirmed by checking the push timestamp:

```bash
docker buildx imagetools inspect registry.example.com/api:latest --raw \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('annotations',{}))"
```

The registry's web UI confirmed it: the developer's manual push was 23 minutes after the pipeline push. `latest` had been silently overwritten.

### Root Cause

A developer manually pushed an arm64 image from a local Apple Silicon machine to the registry under the `latest` tag, overwriting the amd64 image that the CI pipeline had just built and pushed. The production server — x86-64 — pulled the arm64 image and the kernel refused to execute it, producing `exec format error`.

### Fix

The immediate fix was to force a re-run of the CI pipeline to rebuild and push the correct amd64 image:

```bash
# On the server, after the pipeline rebuilt the correct image:
docker compose pull api
docker compose up -d api
```

```
Pulling api ... done
Recreating api ... done
```

The container started and the application came up normally.

### Prevention

Two separate failures enabled this incident. The first was that direct pushes to the registry from developer machines were permitted. The fix was a registry policy requiring that all pushes to the `latest` tag come from the CI pipeline, not from individuals. Most registries support push protection rules that enforce this.

The second failure was that `latest` was being used as a deployment target at all. If production deploys to a pinned tag — `api:git-a1b2c3d4` — a manual push cannot silently replace the running version because the deployment script references a specific digest. The only thing `latest` is reliable for is local development pulls.

For teams building on Apple Silicon and deploying to x86, the Dockerfile and build pipeline should use `docker buildx build --platform linux/amd64` explicitly. If multi-architecture support is needed, `--platform linux/amd64,linux/arm64` builds a proper multi-arch manifest that resolves correctly on each architecture at pull time.

---



## Scenario 2 — The Reverse Proxy That Kept Returning 502

### Context

A production environment running three containers: an Nginx reverse proxy on port 443, an application container listening on port 8080, and a cache container. The application had been running for three weeks without issues. A deployment went out on a Thursday evening — no code changes, only a configuration change to an environment variable — and within ninety seconds of the deployment completing, the reverse proxy began returning 502 Bad Gateway on every request.

### What Happened

The deployment was a simple compose recreate: `docker compose up -d --force-recreate app`. The container came up healthy. The CI/CD pipeline reported success. But every request through Nginx returned:

```
502 Bad Gateway
nginx/1.25.3
```

The application container was running. `docker compose ps` showed it as healthy. Nginx itself was running. The error was somewhere in the connection between them.

### Investigation

I started with the Nginx error log:

```bash
docker compose exec proxy tail -50 /var/log/nginx/error.log
```

```
2026-05-08 21:14:37 [error] 29#29: *1483 connect() failed (111: Connection refused)
  while connecting to upstream, client: 10.0.1.55, server: api.example.com,
  request: "GET /api/health HTTP/1.1", upstream: "http://172.18.0.4:8080/api/health",
  host: "api.example.com"
```

`Connection refused` to `172.18.0.4:8080`. Nginx knew where the application was supposed to be — IP `172.18.0.4`, port 8080 — but nothing was listening there.

I checked what IP the application container actually had:

```bash
docker inspect app --format='{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
```

```
172.18.0.7
```

The application was at `172.18.0.7`. Nginx was trying to reach `172.18.0.4`. The IP addresses did not match.

I checked how Nginx had been configured to find the upstream:

```bash
docker compose exec proxy cat /etc/nginx/conf.d/app.conf
```

```nginx
upstream app_backend {
    server 172.18.0.4:8080;
}
```

A hardcoded IP address. Not a hostname — a literal IP. This is almost always wrong in containerised environments.

I looked at the deployment history. Three weeks ago, when the stack was first deployed, the application container had been assigned `172.18.0.4` by Docker's overlay network. The Nginx configuration had been written at that time with the hardcoded IP. For three weeks it worked, because Docker reused the same IP when the container was recreated — the container was always stopped and started in the same order, and the IPAM assigned the same address.

Thursday's deployment had used `--force-recreate`, which also recreated the proxy container briefly to reload its configuration. The order of container restarts changed. The application container started after a different sequence and received `172.18.0.7` instead of `172.18.0.4`. Nginx still had `172.18.0.4` compiled into its config and never re-resolved it — because there was nothing to resolve; it was a raw IP, not a hostname.

I confirmed that the application was reachable directly:

```bash
docker compose exec proxy curl -s http://172.18.0.7:8080/health
```

```json
{"status": "ok", "version": "2.4.1"}
```

The application was fine. Nginx was just pointing at the wrong address.

### Root Cause

The Nginx upstream configuration used a hardcoded container IP address rather than the container's DNS name. Docker assigns container IPs dynamically from its internal IPAM pool; they are not guaranteed to remain stable across container recreations, especially when recreation order changes. When the deployment changed the recreation sequence, the application container received a different IP, and Nginx continued trying to reach the old one.

### Fix

I updated the Nginx upstream to use the Docker DNS name — the service name from `docker-compose.yml` — which Docker's internal DNS resolver always resolves to the current container's IP:

```nginx
upstream app_backend {
    server app:8080;
}
```

I rebuilt the proxy image with the corrected config and restarted:

```bash
docker compose up -d --force-recreate proxy
```

The 502 errors stopped immediately. I tested with a deployment cycle — stopped and started the app container five times in different orders — and the proxy resolved correctly each time.

### Prevention

Container IP addresses in Docker networks are ephemeral. Never hardcode them. Every inter-container connection should use the service name as the hostname. Docker's embedded DNS server, which runs at `127.0.0.11` inside every container, resolves service names to the current container IP dynamically. This works across restarts, scaling events, and any other operation that changes container assignment.

When debugging 502 errors through a reverse proxy, the path is always the same: check the proxy error log first (it will tell you the IP it tried and the specific error), then check what IP the upstream container actually has, then trace backward to where the IP in the proxy config came from.

---



## Scenario 3 — The Server That Ran Out of Disk at 3 AM

### Context

A production server had been running a small stack for four months — an application container, a database container, and a reverse proxy. No significant traffic, no recent deployments. At 3:14 AM, the on-call engineer received an alert: the application container had exited and was not restarting. By the time they connected to the server, the container had been in the `Exited` state for eleven minutes with a restart policy of `unless-stopped`, which meant it had stopped trying to restart after enough consecutive failures.

### What Happened

Attempting to restart the container manually:

```bash
docker compose up -d app
```

```
Error response from daemon: failed to create shim task: OCI runtime create failed:
  container_linux.go:380: starting container process caused:
  process_linux.go:545: container init caused:
  rootfs_mount_failed caused:
  mkdir /var/lib/docker/overlay2/abc123def456/merged: no space left on device
```

No space left on device. The server's disk was full.

### Investigation

I checked disk usage immediately:

```bash
df -h
```

```
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        40G   40G     0 100% /
```

One hundred percent. I needed to find what had consumed forty gigabytes. I started with the largest directories:

```bash
du -sh /* 2>/dev/null | sort -rh | head -10
```

```
29G     /var
6.2G    /usr
1.8G    /home
1.1G    /opt
```

Twenty-nine gigabytes in `/var`. I kept drilling:

```bash
du -sh /var/* 2>/dev/null | sort -rh | head -5
```

```
27G     /var/lib
1.1G    /var/log
```

```bash
du -sh /var/lib/* 2>/dev/null | sort -rh | head -5
```

```
26G     /var/lib/docker
```

Twenty-six gigabytes used by Docker. I checked what was in there:

```bash
docker system df
```

```
TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
Images          8         3         2.1GB     1.4GB (67%)
Containers      3         0         2.8GB     2.8GB (100%)
Local Volumes   2         1         145MB     0B (0%)
Build Cache     0         0         0B        0B

```

2.8 gigabytes in containers, all inactive. But that still didn't explain 26 GB of Docker usage. `docker system df` doesn't show log files. I looked directly:

```bash
du -sh /var/lib/docker/containers/*/
```

```
3.2K    /var/lib/docker/containers/a1b2c3.../
22G     /var/lib/docker/containers/d4e5f6.../
1.1G    /var/lib/docker/containers/g7h8i9.../
```

One container directory was 22 gigabytes. I identified which container it was:

```bash
docker ps -a --format '{{.ID}} {{.Names}}' | grep d4e5f6
```

```
d4e5f6789abc    app
```

The application container. I looked inside that directory:

```bash
ls -lh /var/lib/docker/containers/d4e5f6789abc/
```

```
total 22G
-rw-r--r-- 1 root root  22G May  8 03:14 d4e5f6789abc-json.log
-rw-r--r-- 1 root root 5.8K May  8 03:14 config.v2.json
-rw-r--r-- 1 root root  736 May  8 03:14 hostconfig.json
```

A 22-gigabyte JSON log file. The application had been writing to stdout continuously for four months with no log rotation configured. The `json-file` logging driver — Docker's default — had been accumulating every log line to a single file with no size limit.

I checked what the application had been logging so heavily:

```bash
tail -5 /var/lib/docker/containers/d4e5f6789abc/d4e5f6789abc-json.log
```

```json
{"log":"[2026-05-08T03:14:01.334Z] GET /healthz 200 2ms\n","stream":"stdout","time":"2026-05-08T03:14:01.334Z"}
{"log":"[2026-05-08T03:14:02.334Z] GET /healthz 200 2ms\n","stream":"stdout","time":"2026-05-08T03:14:02.334Z"}
```

The health check endpoint. The health check was configured to run every second, and the application was logging every HTTP request — including health check probes — to stdout. At one log line per second for four months: roughly 10 million lines, 22 gigabytes.

### Root Cause

The `json-file` logging driver accumulated four months of application logs — including one health probe log line per second — with no rotation policy configured. The log file grew to 22 GB and consumed the entire available disk, preventing Docker from creating new overlay2 filesystems for container startup.

### Fix

The immediate fix was to recover disk space. I could not truncate the file while the container held it open, but I could clear it safely:

```bash
# Truncate to zero bytes without deleting the file (container holds an open file descriptor)
truncate -s 0 /var/lib/docker/containers/d4e5f6789abc/d4e5f6789abc-json.log
```

Disk usage dropped from 100% to 33% immediately. The container started:

```bash
docker compose up -d app
```

Then I configured log rotation in `docker-compose.yml`:

```yaml
services:
  app:
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "5"
```

This limits log files to 50 MB each and keeps at most five rotated files — 250 MB maximum for this container's logs, regardless of how long it runs.

I also suppressed health check probe logging at the application level, since health probes generate high-frequency noise with no diagnostic value in normal operation.

### Prevention

Every production container should have a logging configuration with explicit size limits. The `json-file` driver's defaults — unlimited size, no rotation — are appropriate for a laptop but not for a server expected to run for months. Apply the `max-size` and `max-file` options globally by editing `/etc/docker/daemon.json` on the host:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "5"
  }
}
```

This applies to every container on the host that does not override it explicitly. Also monitor disk usage on Docker hosts; `/var/lib/docker` should be included in the disk space alert thresholds, and `docker system df` output is worth logging in a daily cron job so growth patterns are visible before they become incidents.

---



## Scenario 4 — The Deployment That Reported Success While Traffic Was Failing

### Context

A team using a CI/CD pipeline that deployed by SSH-ing into the production server and running `docker compose pull && docker compose up -d`. The pipeline had been working correctly for months. A Friday afternoon deployment — a minor change to the application's configuration — ran through the pipeline without errors. The pipeline Slack notification said "Deployment successful ✓". Fifteen minutes later, a user reported that they were seeing an error page. Monitoring showed the error had started exactly when the deployment ran.

### What Happened

The application container was listed as `running`. `docker compose ps` showed no restarts, no unhealthy status. The health check endpoint returned 200. But user-facing requests were failing.

This scenario is one of the more disorienting classes of production incident: everything that is supposed to indicate a problem looks fine, but users are experiencing failures.

### Investigation

I started by looking at what the running container actually was:

```bash
docker inspect app --format='{{.Config.Image}}'
```

```
registry.example.com/app:git-a1b2c3d4
```

The running container's image tag was `git-a1b2c3d4`. I checked what the pipeline had just built and pushed:

```bash
# From the CI logs:
# Built and pushed: registry.example.com/app:git-f9e8d7c6
```

Different SHA. The container was running the old image. The `docker compose pull && docker compose up -d` had not updated it.

I ran `docker compose pull` manually to see what happened:

```bash
docker compose pull app
```

```
Pulling app ... done
```

It said "done" but was it actually pulling? I checked the image digest on disk versus what was in the registry:

```bash
# What's on disk:
docker inspect registry.example.com/app:latest --format='{{.RepoDigests}}'
```

```
[registry.example.com/app@sha256:olddigest...]
```

```bash
# What's in the registry (using skopeo, or checking CI logs):
# registry.example.com/app@sha256:newdigest...
```

The local digest was old. `docker compose pull` had reported "done" but had not actually pulled the new image. I looked at `docker-compose.yml` to understand why:

```yaml
services:
  app:
    image: registry.example.com/app:git-a1b2c3d4
```

The image tag in `docker-compose.yml` was pinned to the old SHA — `git-a1b2c3d4` — which was the tag that existed on disk. `docker compose pull` had checked the registry for `git-a1b2c3d4`, found it present (it had been pushed months ago and never deleted), reported that it was up to date, and done nothing. The deployment pipeline had updated the application code, built a new image with a new SHA, pushed it to the registry, but had never updated the tag in `docker-compose.yml`. The `docker-compose.yml` file was pointing at the old image. The new image was in the registry but nothing was referencing it.

I checked the application code that was actually running to confirm:

```bash
docker compose exec app cat /app/build-info.json
```

```json
{"version": "2.3.0", "commit": "a1b2c3d4", "built_at": "2026-04-12T09:22:00Z"}
```

Three weeks old. Everything since that date had not been deployed despite ten pipeline runs each reporting "Deployment successful."

### Root Cause

The CI/CD pipeline built and pushed a new image on each run but never updated the `image:` tag in `docker-compose.yml`. The `docker-compose.yml` in the repository pointed at a pinned SHA from three weeks ago. Every deployment pulled that specific (old) image, found it cached on the server, did nothing, reported success, and left the old container running. The new images accumulated in the registry undeployed.

### Fix

The deployment pipeline was updated to do two things: write the new image tag into `docker-compose.yml` before deploying, and verify after deployment that the running container's image digest matched what had just been pushed:

```bash
# In the CI/CD deploy step:
NEW_TAG="git-${GITHUB_SHA:0:8}"

# Update docker-compose.yml to reference the new tag:
sed -i "s|registry.example.com/app:.*|registry.example.com/app:${NEW_TAG}|g" docker-compose.yml

# Deploy:
docker compose pull app
docker compose up -d app

# Verify the running container is actually the new image:
RUNNING_DIGEST=$(docker inspect app --format='{{.Image}}')
EXPECTED_DIGEST=$(docker inspect registry.example.com/app:${NEW_TAG} --format='{{.Id}}')

if [ "$RUNNING_DIGEST" != "$EXPECTED_DIGEST" ]; then
  echo "ERROR: Running container digest does not match pushed image"
  exit 1
fi
```

The verification step catches the failure mode before the pipeline reports success.

### Prevention

A deployment pipeline that does not verify the post-deployment state is not a deployment pipeline — it is a deployment attempt. At minimum, after `docker compose up -d`, the pipeline should confirm that the running container's image SHA matches the SHA that was just pushed. This catches image pull failures, `docker-compose.yml` sync issues, and any other condition where the container that came up is not the container that was intended.

Also: commit the updated `docker-compose.yml` back to the repository after a successful deployment, so the file in source control reflects what is actually running in production. If it doesn't, the repository becomes misleading and rollbacks require archaeology.

---



## Scenario 5 — The Deployment That Killed Itself With Its Own Health Check

### Context

A team had recently added health checks to all services following the guidance in an earlier step. The health check for the application container called the `/health` endpoint every 10 seconds, with a 5-second timeout, 3 retries, and a 30-second start period. The application itself initialised a warm cache on startup that took approximately 45 seconds on the production server (the dataset was larger than in development).

The next deployment after health checks were added resulted in a restart storm: the container started, was killed at approximately 45 seconds, restarted, was killed again, and repeated indefinitely.

### What Happened

`docker compose ps` showed:

```
NAME    SERVICE   STATUS                         PORTS
app     app       restarting (12) 4 minutes ago
```

Twelve restarts in four minutes. The container was clearly being killed before it could fully initialise.

### Investigation

I looked at the logs from the most recent restart cycle:

```bash
docker compose logs --tail=30 app
```

```
2026-05-08 16:42:01 INFO  Starting application...
2026-05-08 16:42:01 INFO  Loading configuration...
2026-05-08 16:42:03 INFO  Connecting to database... connected.
2026-05-08 16:42:03 INFO  Warming cache — fetching 84,000 records...
2026-05-08 16:42:03 INFO  Cache warm: 0%
2026-05-08 16:42:13 INFO  Cache warm: 12%
2026-05-08 16:42:23 INFO  Cache warm: 24%
2026-05-08 16:42:33 INFO  Cache warm: 37%
2026-05-08 16:42:43 INFO  Cache warm: 49%
```

The logs stopped at 49%. The container was being killed at approximately the 42-second mark — 12 seconds after the 30-second start period expired. The health check was firing three times with no success (because the HTTP server was not yet accepting connections) and killing the container.

I checked the health check configuration:

```bash
docker inspect app --format='{{json .Config.Healthcheck}}'
```

```json
{
  "Test": ["CMD-SHELL", "curl -sf http://localhost:8080/health || exit 1"],
  "Interval": 10000000000,
  "Timeout": 5000000000,
  "Retries": 3,
  "StartPeriod": 30000000000
}
```

Interval: 10 seconds. Retries: 3. Start period: 30 seconds. The maths: the start period expired after 30 seconds, then 3 failed checks × 10 seconds = 30 more seconds before the container was marked unhealthy and killed. The container needed 45 seconds but was only given 60 total before being declared dead. On a slow day or a loaded server, it would be less.

But there was another problem. I looked at what happened when the health check ran during the 30-second start period:

```bash
docker inspect app --format='{{json .State.Health}}'
```

```json
{
  "Status": "starting",
  "FailingStreak": 0,
  "Log": [
    {
      "Start": "2026-05-08T16:42:11.334Z",
      "End": "2026-05-08T16:42:11.341Z",
      "ExitCode": 7,
      "Output": "curl: (7) Failed to connect to localhost port 8080 after 0 ms: Connection refused\n"
    }
  ]
}
```

The health check was running and failing during the start period — that was expected, and Docker does not count those failures. But each failing health check was also consuming a small amount of CPU and adding connection noise to the application's startup phase. With 8 workers initialising and cache loading in parallel, this was not significant in isolation.

The core issue was the `StartPeriod`. The application's initialisation time on the production server consistently exceeded the start period. The start period needs to be longer than the maximum expected initialisation time, with headroom for slow days.

I also noticed the health check command itself:

```bash
curl -sf http://localhost:8080/health || exit 1
```

`curl` was not installed in the application's image. The `|| exit 1` was masking the failure:

```bash
docker compose exec app which curl
```

```
# (no output — curl not found)
```

```bash
docker compose exec app curl http://localhost:8080/health
```

```
bash: curl: command not found
```

The health check was exiting with code 127 (command not found), not code 7 (connection refused). Docker treated both as health check failures, so the restart loop still happened. But the error messages in the health log were misleading because they suggested a network connectivity problem when the actual issue was a missing binary.

### Root Cause

Two compounding issues. First, the `StartPeriod` (30 seconds) was shorter than the application's initialisation time (45+ seconds on the production server), causing health checks to start counting failures before the application had a chance to become ready. Second, the health check used `curl`, which was not present in the production image; the health check was failing with `command not found` rather than a meaningful health signal.

### Fix

I updated the health check in `docker-compose.yml`:

```yaml
services:
  app:
    healthcheck:
      test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost:8080/health"]
      interval: 15s
      timeout: 10s
      retries: 3
      start_period: 90s
```

Changes made: replaced `curl` with `wget` (which was present in the image), increased the start period to 90 seconds (twice the maximum observed initialisation time, with headroom), increased the interval to 15 seconds to reduce noise during startup, and increased the timeout to 10 seconds to account for slow health endpoint responses under load.

I also added a check to the CI build step that verified the health check command existed in the image before pushing:

```bash
docker run --rm registry.example.com/app:${NEW_TAG} which wget
```

If the command exits non-zero, the build fails. A health check that references a missing binary provides no safety; it is worse than no health check at all because it creates a restart storm.

### Prevention

When adding health checks to a service with non-trivial initialisation, always measure actual startup time on production-class hardware, not development hardware. Development machines are typically faster; startup times are longer under production load. Set the `start_period` to at least 1.5× the observed maximum startup time, not the average. Verify that the health check command (whether `curl`, `wget`, or a custom binary) exists in the production image. A health check that cannot run its own check command is a restart trigger, not a health monitor.

---



## Scenario 6 — Docker Hub Rate Limits Broke Deployment at the Worst Possible Time

### Context

A team was in the middle of rolling back a bad deployment during a live incident — users were affected, the engineering team was on a call, every minute mattered. The rollback procedure was to update the tag in `docker-compose.yml` and run `docker compose pull && docker compose up -d`. The base images in the application's Dockerfile were pulled from Docker Hub (specifically, `node:20-alpine` for the builder stage and `alpine:3.19` for the runtime stage). The server had not pulled these images recently; they had been evicted from the local cache after a `docker system prune` the week before.

### What Happened

The rollback command was run:

```bash
docker compose pull
```

```
Pulling app ... error
```

```
Error response from daemon: toomanyrequests: You have reached your pull rate limit.
You may increase the limit by authenticating and upgrading:
https://www.docker.io/increase-rate-limit
```

Docker Hub rate-limited the pull. The rollback could not proceed. The incident clock was running.

### Investigation

Docker Hub's rate limits apply per IP address for unauthenticated pulls (100 pulls per 6 hours for free accounts as of early 2026) and per account for authenticated pulls (200 pulls per 6 hours for free accounts). The CI/CD pipeline had been running builds all day and had exhausted the rate limit for the server's IP address.

I checked which images were actually needed versus which were already local:

```bash
docker images | grep -E "node|alpine"
```

```
node      20-alpine     3b4a1c...    3 weeks ago    135MB
alpine    3.19          d5e6f7...    3 weeks ago    7.7MB
```

The base images were cached from three weeks ago — not current, but present. The issue was that `docker compose pull` was trying to pull the *application* image (which referenced a registry that was not rate-limited), but also re-checking the base image layers, some of which were cached under their old digests but Docker was trying to verify against Docker Hub and hitting the rate limit before it could confirm.

Actually — I looked more carefully at what `docker compose pull` was doing. The application image in this case was built from a Dockerfile that used `FROM node:20-alpine`. The final application image was in a private registry. But `docker compose pull` only pulls the service images defined in `docker-compose.yml`, not the base images. The rate limit should not apply here.

I ran the pull again with verbose output:

```bash
docker compose pull app 2>&1
```

```
Pulling app...
Pulling from registry.example.com/app
...
Error response from daemon: toomanyrequests: You have reached your pull rate limit.
```

The error was coming from the private registry pull, not from Docker Hub. The private registry in this case was a proxy cache — it was configured to pull missing layers from Docker Hub transparently. The layers of the application image included the `node:20-alpine` layers that had been incorporated at build time. When the registry served those layers, it was fetching them from Docker Hub as a pull-through cache, and the cache's Docker Hub credentials were rate-limited.

This was a registry infrastructure problem, not a Docker client problem.

### Fix

The immediate fix for the rollback was to use the previously-cached local image, bypassing the pull:

```bash
# The old image was still on disk from the last successful deployment:
docker images | grep "registry.example.com/app"
```

```
registry.example.com/app   git-a1b2c3d4   f0e1d2c3b4a5   2 days ago    312MB
```

```bash
# Update docker-compose.yml to reference the cached image tag and restart without pulling:
sed -i "s|app:.*|app:git-a1b2c3d4|g" docker-compose.yml
docker compose up -d app  # uses local cache, does not pull
```

The service came back up using the cached image. The incident was resolved without needing to pull anything.

The registry infrastructure fix — adding authenticated Docker Hub credentials to the pull-through cache — was done after the incident:

```bash
# On the registry server (Harbor example):
# Set Docker Hub credentials in the proxy cache configuration
# so pulls are authenticated and subject to the higher rate limit
```

### Prevention

Several layers of protection can eliminate this class of incident:

First, never delete images from production servers without confirming that the registry can deliver them. `docker system prune` should be used carefully — or not at all — on production hosts. Keep at least the last two deployed versions of every service image locally cached.

Second, if using Docker Hub as an upstream for a pull-through cache, configure authentication. Authenticated pulls get a substantially higher rate limit. Unauthenticated rate limits will be exhausted quickly on busy CI systems.

Third, for critical services, consider using a private registry mirror that has already ingested the required base images, eliminating the Docker Hub dependency from the pull path entirely. When a deployment or rollback cannot proceed because a third-party service is rate-limiting you, the operational independence of your deployment process is compromised.

---



## Scenario 7 — Port Already Allocated: The Container That Would Not Start After a Crash

### Context

A single-server deployment running an application stack behind a reverse proxy. The reverse proxy container listened on ports 80 and 443. One evening, the host server lost power briefly — not a graceful shutdown, a hard power cut. When power was restored, the automated startup (systemd unit) ran `docker compose up -d`. The proxy container failed to start:

```
Error response from daemon: driver failed programming external connectivity on endpoint proxy
(sha256:abc123...): Error starting userland proxy: listen tcp4 0.0.0.0:80: bind: address already in use
```

### What Happened

Port 80 was already bound by something else. The reverse proxy could not start. With the proxy down, the entire stack was unreachable.

### Investigation

First, I identified what was holding port 80:

```bash
sudo ss -tlnp | grep ':80'
```

```
LISTEN  0  128  0.0.0.0:80  0.0.0.0:*  users:(("docker-proxy",pid=1847,fd=4))
```

A `docker-proxy` process was holding the port, with PID 1847. This is Docker's userland proxy — it handles port mapping from host to container. But `docker compose ps` showed the proxy container as `created`, not `running`:

```
NAME    SERVICE   STATUS    PORTS
proxy   proxy     created
db      db        running   5432/tcp
app     app       running   8080/tcp
```

A container in the `created` state has been created by Docker but has not been successfully started. Yet a `docker-proxy` process for its port mapping already existed. This is the residue of a crash — Docker had begun the startup sequence for the container, allocated the port in the host kernel, started the userland proxy process, but then the power cut had killed the container before it completed initialisation. The `docker-proxy` process survived the restart (it was a host process, not a container process), and on the new startup attempt, Docker tried to allocate port 80 again and found it already taken — by its own leftover proxy process.

I confirmed this was a docker-proxy process and not something else:

```bash
ps -p 1847 -o pid,ppid,comm,args
```

```
  PID  PPID COMM     ARGS
 1847     1 docker-proxy  /usr/bin/docker-proxy -proto tcp -host-ip 0.0.0.0 -host-port 80 -container-ip 172.18.0.2 -container-port 80
```

Parent PID 1 — this process had been re-parented to init during the crash, meaning Docker daemon was no longer tracking it. It was an orphaned docker-proxy process.

I checked for any other orphaned docker-proxy processes on other ports:

```bash
ps aux | grep docker-proxy | grep -v grep
```

```
root  1847  0.0  0.0  docker-proxy -proto tcp -host-ip 0.0.0.0 -host-port 80 ...
root  1849  0.0  0.0  docker-proxy -proto tcp -host-ip 0.0.0.0 -host-port 443 ...
```

Two orphaned processes, one for each port. Both needed to be cleared.

### Fix

```bash
# Kill the orphaned docker-proxy processes:
sudo kill 1847 1849

# Verify ports are free:
sudo ss -tlnp | grep -E ':80|:443'
# (no output)

# Start the stack:
docker compose up -d
```

```
Starting proxy ... done
Starting app   ... done
Starting db    ... done (already running)
```

The stack came up cleanly.

### Prevention

Ungraceful host shutdowns leave Docker in inconsistent states. Three practices reduce the blast radius:

First, configure Docker to start as a systemd service with `After=network.target` and `Restart=on-failure`, and configure the compose stack with a systemd unit that has `After=docker.service` and `RemainAfterExit=yes`. On boot, Docker starts, then the compose stack starts — in the right order, giving Docker time to clean up its state.

Second, Docker 20.10+ improved orphaned proxy handling, but older daemon versions are still susceptible. Keeping the Docker daemon patched matters.

Third, add a pre-start check in the deployment unit that clears port conflicts:

```bash
# Before docker compose up:
for port in 80 443; do
  pid=$(ss -tlnp | grep ":${port}" | awk -F'pid=' '{print $2}' | cut -d',' -f1)
  if [ -n "$pid" ]; then
    comm=$(ps -p "$pid" -o comm=)
    if [ "$comm" = "docker-proxy" ]; then
      echo "Killing orphaned docker-proxy on port ${port} (PID ${pid})"
      kill "$pid"
    fi
  fi
done
```

This is a targeted cleanup, not a blanket kill — it only removes `docker-proxy` processes, so it will not accidentally kill an Apache or Nginx process that legitimately holds the port.

---



## Scenario 8 — The Bind Mount That Silently Erased the Application

### Context

A developer was testing a new deployment approach on a staging server. The application image contained all compiled assets at `/app/dist`. The new approach was to use a bind mount so that updated static files could be swapped in without rebuilding the image. They added this to `docker-compose.yml`:

```yaml
services:
  app:
    image: registry.example.com/app:latest
    volumes:
      - ./dist:/app/dist
```

They created a `dist/` directory on the host, copied a few test files in, and ran `docker compose up -d`. The container started. But the application immediately returned 500 errors on any route that served static assets.

### What Happened

The application was running. No crash, no restart loop. The container was healthy. But every request for a static asset — CSS, JS, images — returned a 500 error from the application server.

### Investigation

I looked at the application logs:

```bash
docker compose logs app
```

```
2026-05-08 11:23:01 ERROR [renderer] ENOENT: no such file or directory,
  open '/app/dist/main.bundle.js'
2026-05-08 11:23:02 ERROR [renderer] ENOENT: no such file or directory,
  open '/app/dist/vendor.bundle.js'
2026-05-08 11:23:03 ERROR [renderer] ENOENT: no such file or directory,
  open '/app/dist/index.html'
```

The application could not find files in `/app/dist`. I exec'd into the container to check:

```bash
docker compose exec app ls /app/dist/
```

```
test-file.txt
```

One file — the test file the developer had placed in the `dist/` directory on the host. None of the compiled application assets were there.

I checked what was in the image at that path before the mount:

```bash
docker run --rm registry.example.com/app:latest ls /app/dist/
```

```
index.html
main.bundle.js
vendor.bundle.js
fonts/
images/
static/
```

All the compiled assets were present in the image. The bind mount had completely replaced the directory — every file that the build process had compiled into the image at `/app/dist` was now invisible, shadowed by the host directory that contained only a test file.

This is how bind mounts work: when you mount a host path over a container path, the container path's original contents become inaccessible for the duration of the mount. The image's files are not deleted, but they are completely shadowed by the host directory. If the host directory is empty or has different contents, the application sees exactly that.

The developer had expected the host files to be merged with the image files, or for the image files to remain accessible alongside the mounted files. That is not what happens.

### Root Cause

A bind mount of `./dist:/app/dist` replaced the application's compiled asset directory — which was baked into the image at build time — with a host directory that contained only a test file. The application attempted to serve files it expected to find at `/app/dist`, found only the host directory's contents, and returned errors for every missing file.

### Fix

For the immediate issue: remove the bind mount and use the image's built-in assets:

```yaml
services:
  app:
    image: registry.example.com/app:latest
    # volumes removed
```

For the actual goal — deploying updated static files without rebuilding the image — the correct approach depends on the requirement. If the static files are truly independent of the application (a content team updating a marketing site, for example), they should be served by a separate container (Nginx serving files from a named volume, with a separate process to update the volume contents). If the static files are compiled output of the application build, they should be baked into the image.

### Prevention

Bind mounts completely replace their target directory. This is not a bug — it is by design, and it is the correct behaviour for development workflows where you want the container to see your local source code. But on a server, if the target path contains files the application needs (binary assets, compiled output, configuration that was baked in at build time), those files become inaccessible the moment the mount is active.

Before adding a bind mount to a production service, audit what exists at the mount target path inside the image:

```bash
docker run --rm <image> ls -la /path/to/target/
```

If the directory is non-empty, the mount will shadow those contents. If the application needs them, the mount strategy needs to change — either mount to a different path, use a named volume initialised from the image contents, or restructure the Dockerfile to separate mutable and immutable paths.

---



## Scenario 9 — Inter-Container DNS Stopped Resolving After a Compose Update

### Context

A stack with four services: `api`, `worker`, `cache`, and `db`. The `worker` service communicated with `cache` (Redis) and `db` (Postgres). Everything had been stable for six weeks. A routine deployment updated the `api` service only — no changes to `worker`, `cache`, or `db`. The deployment ran `docker compose up -d`, which only recreated the `api` container.

Within 60 seconds of the deployment, `worker` began logging connection errors to `cache`. The errors were intermittent at first, then continuous:

```
2026-05-08 14:31:07 ERROR [worker] Redis connection failed:
  Error: getaddrinfo ENOTFOUND cache
2026-05-08 14:31:08 ERROR [worker] Redis connection failed:
  Error: getaddrinfo ENOTFOUND cache
```

`cache` was the service name used in the worker's Redis connection string. It had been resolving correctly for six weeks.

### What Happened

The `cache` container was running. `docker compose ps` showed it as healthy. But `worker` could not resolve its hostname.

### Investigation

I started by testing DNS resolution from inside the `worker` container:

```bash
docker compose exec worker nslookup cache
```

```
;; connection timed out; no servers could be reached
```

DNS was completely broken, not just for `cache` — the worker could not reach the DNS server at all. I checked the DNS configuration inside the container:

```bash
docker compose exec worker cat /etc/resolv.conf
```

```
nameserver 127.0.0.11
options ndots:0
```

`127.0.0.11` is Docker's embedded DNS resolver. It should always be reachable from inside a container on a Docker network. But the DNS queries were timing out.

I checked the worker container's network interface:

```bash
docker compose exec worker ip addr
```

```
1: lo: <LOOPBACK,UP,LOWER_UP>
    inet 127.0.0.1/8 scope host lo
# (no eth0 or other interface listed)
```

No `eth0`. The worker container had no network interface other than loopback. It had been disconnected from the overlay network.

I looked at the network attachments:

```bash
docker network inspect myapp_default --format='{{json .Containers}}' | python3 -m json.tool
```

```json
{
  "a1b2c3api": {
    "Name": "api",
    "IPv4Address": "172.18.0.2/16"
  },
  "d4e5f6cache": {
    "Name": "cache",
    "IPv4Address": "172.18.0.3/16"
  },
  "g7h8i9db": {
    "Name": "db",
    "IPv4Address": "172.18.0.4/16"
  }
}
```

Three containers in the network — `api`, `cache`, and `db`. `worker` was not listed. The worker container was not attached to the application network.

I looked at why. The `docker compose up -d` that deployed `api` had apparently touched the network:

```bash
docker network ls | grep myapp
```

```
abc123def456   myapp_default   bridge   local
```

I checked the worker container's network list directly:

```bash
docker inspect worker --format='{{json .NetworkSettings.Networks}}'
```

```json
{
  "bridge": {
    "IPAddress": "172.17.0.3"
  }
}
```

The worker was attached to `bridge` — Docker's default bridge network — not to `myapp_default`. The default bridge network does not have Docker's embedded DNS resolver; it does not support service name resolution at all. That explained why `nslookup cache` timed out.

How did the worker end up on the default bridge? I checked the worker container's creation time:

```bash
docker inspect worker --format='{{.Created}}'
```

```
2026-04-21T08:14:22.334Z
```

This container had been created 17 days ago. It was not a container that `docker compose up -d` had created today. Looking at `docker-compose.yml`, I saw the worker did not have an explicit `networks:` definition:

```yaml
services:
  worker:
    image: registry.example.com/worker:latest
    # no networks: key
```

For services without an explicit `networks:` key, Compose automatically attaches them to the project's default network — but only when Compose creates the container. This container was 17 days old. Something outside of Compose had created it — or Compose had created it before the `docker-compose.yml` had the `networks:` structure that defined `myapp_default`.

I found the issue in the deployment history. Three weeks ago, there had been a migration from the default Docker bridge to a named network (`myapp_default`). The deployment at that time had recreated `api`, `cache`, and `db` — because those services had a configuration change — but `worker` had not been recreated because it had no changes. So `worker` was the only container that had been created under the old network configuration and never recreated since. All the others had been recreated during the migration and correctly joined `myapp_default`. Worker had been left on `bridge`.

For three weeks, `worker` had been on the wrong network but communicating successfully because the Redis and Postgres ports had also been published to the host, and `worker` was reaching them via the host IP — which happened to work because the host was reachable on `172.17.0.1` from the default bridge. This roundabout path stopped working when, as part of today's `api` deployment, the cache port mapping was tightened and the host port was removed from the `cache` service definition.

### Root Cause

A network migration three weeks prior had recreated all services except `worker` (which had no configuration changes). The `worker` container remained on the default bridge network rather than the named application network. It had been communicating via host port mappings rather than internal DNS, which masked the misconfiguration. When the cache port mapping was removed during today's deployment, the fallback path broke and the DNS failure became visible.

### Fix

Recreate the worker container so Compose attaches it to the correct network:

```bash
docker compose up -d --force-recreate worker
```

```
Stopping worker  ... done
Recreating worker ... done
```

```bash
docker inspect worker --format='{{json .NetworkSettings.Networks}}'
```

```json
{
  "myapp_default": {
    "IPAddress": "172.18.0.5"
  }
}
```

Worker was now on the correct network. DNS resolution immediately began working:

```bash
docker compose exec worker nslookup cache
```

```
Server:    127.0.0.11
Address:   127.0.0.11#53

Non-authoritative answer:
Name:   cache
Address: 172.18.0.3
```

### Prevention

After any network topology change, verify that all containers are attached to the correct networks — not just the containers that were recreated:

```bash
docker network inspect myapp_default --format='{{range .Containers}}{{.Name}} {{end}}'
```

Compare this against the services defined in `docker-compose.yml`. Every service should appear in the network. Any container missing from the network is on the wrong network.

During major infrastructure changes (network renames, adding named networks, migrating from bridge to overlay), force-recreate all services at once rather than selectively recreating only those with configuration changes:

```bash
docker compose up -d --force-recreate
```

This is more disruptive than a rolling update but ensures all containers are in a consistent network state. The alternative is a class of bug where services appear to work (via fallback routing paths) until a secondary change removes the fallback, making the misconfiguration suddenly visible days or weeks after the original change.

---



## A Note on Debugging Methodology

These nine scenarios share a pattern in how the investigations moved. Three principles appeared in every case.

**Start with what the container sees, not what you think you configured.** In scenario 2, the Nginx config hardcoded an IP that had worked for three weeks. In scenario 9, the worker was on the correct network according to the compose file but not in reality. `docker inspect` and `docker compose exec` are the ground truth; everything else is a hypothesis. The investigation should start with observation — what is actually true about the running system — before moving to explanation.

**The symptom and the cause are often in different places.** The 502 errors in scenario 2 were caused by a configuration file that had worked correctly for weeks. The ENOENT errors in scenario 8 were caused by a volume mount, not by anything the application did wrong. The DNS failures in scenario 9 were caused by a network migration that happened three weeks earlier. Following the chain from observed symptom to causal mechanism requires resisting the temptation to stop at the first plausible explanation.

**Stability can mask misconfiguration.** Several of these scenarios involved systems that had been running incorrectly for weeks or months without failing — because a fallback path or a lucky circumstance was compensating. The disk full scenario accumulated logs for four months. The worker was on the wrong network for three weeks. The deployment pipeline had been pointing at a stale image for ten pipeline runs. These failures did not start the moment the misconfiguration occurred; they started the moment the compensating condition changed. This is why periodic audits of running state — checking that containers are on the expected networks, that image digests match what was deployed, that log directories are within expected sizes — are worth more than fixing failures after they occur.

```bash
# A minimal audit to run regularly on any Docker production host:

# Check all container image SHAs match expected deployments:
docker compose ps -q | xargs -I{} docker inspect {} --format='{{.Name}} {{.Config.Image}} {{.Image}}'

# Check all services are on the expected network:
docker network inspect <project>_default --format='{{range .Containers}}{{.Name}} {{end}}'

# Check disk usage:
docker system df
df -h /var/lib/docker

# Check for orphaned processes:
ps aux | grep docker-proxy | grep -v grep

# Check restart counts:
docker compose ps -q | xargs -I{} docker inspect {} \
  --format='{{.Name}} restarts={{.RestartCount}}'
```

None of these commands are expensive. Running them as a weekly or daily check would have surfaced the misconfigurations in scenarios 3, 4, 7, and 9 before they produced incidents.