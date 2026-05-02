# 22 — Security Best Practices



## 0. Goal of This Step

Understand the security posture of the current stack, where the real exposures are, and how to address them systematically — not as a checklist of rules, but as a set of deliberate decisions that reduce the attack surface without breaking what the stack needs to do.



## 1. What Problem It Solves

Across the previous steps, several security decisions were already made deliberately. The containers run as a non-root user (step 14). The build context excludes sensitive files via `.dockerignore` (step 14). The images use pinned base image versions (step 14). Resource limits prevent one container from consuming the host (step 21).

Those decisions addressed the most visible issues. But there are exposures in the current stack that have not been examined. Some of them are in `docker-compose.yml`. Some are in how the stack is networked. Some are in what the containers are capable of doing by default that they should not need to do. And one of the most significant is sitting in plain sight in every `docker-compose.yml` file written so far: database credentials hardcoded as environment variable values.

Security is not a single step — it is a lens applied across every decision. This step applies that lens to the current stack and addresses what remains.



## 2. What Happened (Experience)

The stack from step 21 was running with health checks, restart policies, and resource limits. I had been building it step by step and each step added something specific. But I had not stepped back and asked a security-focused question: if someone gained read access to this repository, or to this server, what would they get?

I started reading through the files with that question in mind.

**Step 1 — Reading the docker-compose.yml as an attacker would**

I opened `docker-compose.yml` and read the environment section for the backend:

```yaml
backend:
  environment:
    - DB_HOST=db
    - DB_PORT=5432
    - DB_NAME=appdb
    - DB_USER=appuser
    - DB_PASSWORD=secret
```

`DB_PASSWORD=secret`. The database password, in plain text, committed to the repository. Every person who has ever cloned this repository has the database password. Every CI/CD system that has ever run against this repository has logged it. Every backup of this repository contains it.

This is the most common credential leak pattern in containerised applications. It is not a hypothetical risk — production database credentials have been exposed this way at real companies, and the password `secret` (or `password`, or `postgres`) is the first thing an attacker tries.

I checked whether a `.env` file approach would help. Docker Compose supports loading variables from a `.env` file in the same directory, and the `.env` file can be excluded from version control via `.gitignore`. The compose file becomes:

```yaml
backend:
  environment:
    - DB_HOST=db
    - DB_PORT=5432
    - DB_NAME=appdb
    - DB_USER=${DB_USER}
    - DB_PASSWORD=${DB_PASSWORD}
```

And the `.env` file:

```
DB_USER=appuser
DB_PASSWORD=secret
```

The compose file is now safe to commit. The `.env` file is added to `.gitignore` and stays off version control. The credential is not in the repository.

I created the `.env` file and updated `docker-compose.yml` to use variable substitution. Then tested that it still worked:

```bash
docker compose up -d
docker compose exec backend env | grep DB_
```

```
DB_HOST=db
DB_PORT=5432
DB_NAME=appdb
DB_USER=appuser
DB_PASSWORD=secret
```

The variables were present inside the container. The `.env` file was doing its job. I added it to `.gitignore`:

```
.env
.env.*
```

**Step 2 — Examining what the containers can do by default**

Beyond credentials, I wanted to understand what capabilities the containers had that they should not need. Linux capabilities are a set of privileges that are normally reserved for root — things like mounting filesystems, binding to low-numbered ports, loading kernel modules, modifying network interfaces. Docker grants a default set of capabilities to every container, even when running as a non-root user.

I checked what capabilities the backend container currently had:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.CapAdd}} {{.HostConfig.CapDrop}}'
```

```
[] []
```

Empty on both sides — no capabilities explicitly added, none explicitly dropped. This means the container was running with Docker's default capability set, which includes capabilities the Flask application has no reason to need: `NET_RAW` (raw network packet manipulation), `SYS_CHROOT` (changing root directory), `MKNOD` (creating device files), and others.

The principle from step 14 applies here too: every privilege a process does not need is a privilege an attacker cannot use. I dropped all capabilities and added back only what was needed:

```yaml
backend:
  cap_drop:
    - ALL
  cap_add:
    - NET_BIND_SERVICE   # only needed if binding to ports below 1024 — not needed here
```

Actually — the backend binds to port 5000, which is above 1024 and does not require `NET_BIND_SERVICE`. I dropped everything and added nothing:

```yaml
backend:
  cap_drop:
    - ALL
```

I applied the change and confirmed the backend still started and served requests correctly. A Flask app connecting to Postgres over a standard TCP socket needs no special Linux capabilities. Dropping all of them costs nothing and removes an entire class of potential privilege escalation.

I applied the same to the frontend. The database is different — Postgres needs certain capabilities to function correctly. I left the database at its default capability set rather than risk breaking it by dropping something it needs.

**Step 3 — Examining the network exposure**

I looked at how the services were networked. The current `docker-compose.yml` defined two networks:

```yaml
networks:
  frontend-network:
  backend-network:
```

The backend was on both networks. The frontend was on `frontend-network` only. The database was on `backend-network` only. This was already a reasonable separation — the frontend could not reach the database directly.

But I checked what ports were exposed to the host:

```yaml
backend:
  ports:
    - "5000:5000"

frontend:
  ports:
    - "5001:5001"

db:
  ports:           # this was not in the compose file — but worth checking
```

The database had no `ports` entry, which was correct — Postgres was only accessible from within the `backend-network`, not from the host or the outside world. But I noticed that the backend's port 5000 was mapped to `0.0.0.0:5000` — bound on all interfaces of the host. That means the backend API was accessible from anywhere that could reach the host machine, not just from the frontend container or from localhost.

For the current stack, where the frontend calls the backend directly via Docker's internal network (`http://backend:5000`), there is no reason for the backend to be accessible from the outside at all. The `ports` mapping exists for development convenience — so you can run `curl http://localhost:5000/notes` from the host machine. In production, the only entry point should be the frontend.

I changed the backend port binding to localhost only:

```yaml
backend:
  ports:
    - "127.0.0.1:5000:5000"
```

Now port 5000 was only accessible from the host machine itself — not from any external network. The frontend still reached the backend via the internal Docker network (`http://backend:5000`), which was unaffected by the host port binding.

**Step 4 — Making the filesystem read-only**

The Flask application reads its source files at startup and then serves HTTP requests. It does not write anything to its own filesystem during normal operation. The database writes go to Postgres, not to files in the container.

A container with a writable filesystem is slightly more dangerous than a container with a read-only one. If an attacker finds a way to execute arbitrary code inside the container — through a vulnerability in Flask, in a dependency, or in the application code — a writable filesystem lets them create files, modify the application, or install tools. A read-only filesystem prevents this.

I made the backend's filesystem read-only:

```yaml
backend:
  read_only: true
```

Rebuilt and started the stack:

```bash
docker compose up -d
docker compose logs backend
```

It failed. Gunicorn tries to write a pid file when it starts. The read-only filesystem prevented it. I checked where it was trying to write:

```bash
docker compose logs backend | grep "error\|Error\|permission"
```

```
[ERROR] Worker with pid 8 exited with code 1
```

More investigation showed the issue was Gunicorn's temporary worker files. The fix is to mount a tmpfs — a temporary in-memory filesystem — at the paths Gunicorn needs to write to:

```yaml
backend:
  read_only: true
  tmpfs:
    - /tmp
    - /run
```

With those two tmpfs mounts, Gunicorn had writable temporary space while the rest of the filesystem remained read-only. The backend started correctly.

I verified the read-only enforcement:

```bash
docker compose exec backend touch /app/test.txt
```

```
touch: /app/test.txt: Read-only file system
```

The filesystem was read-only. The application could not write to its own directory. A successful attack that reached code execution inside the container would find a significantly more constrained environment.

**Step 5 — Scanning the image for known vulnerabilities**

I had been building on `python:3.11.9-slim` since step 14. The image contained the Python interpreter, the Debian base OS, and all the system libraries those depend on. Any of those could have known CVEs — vulnerabilities that were discovered and published after the image was built.

Docker Desktop includes a vulnerability scanner accessible through `docker scout`:

```bash
docker scout quickview backend:v1
```

```
  Target     │  backend:v1
    digest   │  sha256:a1b2c3...
  
  Overview
                    │    Analyzed Image
  ─────────────────────────────────────────────
  Version         │ 21.06
  Vulnerabilities │    2C    4H    12M    3L
```

Two critical vulnerabilities, four high, twelve medium, three low. Some of these were in the Debian base packages — packages that `python:3.11.9-slim` inherits but that are not needed by the Flask application. Some were in the Python packages themselves.

I looked at the critical ones:

```bash
docker scout cves backend:v1 --only-severity critical
```

The critical CVEs were in system libraries in the Debian base — `libssl` and `libc`. These were not in packages I had explicitly installed; they came with the base image. The fix was to update the base image to get the patched versions:

```bash
docker pull python:3.11.9-slim
```

Docker Hub had updated `3.11.9-slim` with the patched system libraries. Rebuilding picked up the updated base:

```bash
docker compose build --no-cache backend
docker scout quickview backend:v1
```

```
  Vulnerabilities │    0C    1H    9M    3L
```

The critical vulnerabilities were gone. The remaining ones were in packages without available patches yet — worth tracking but not immediately actionable.

This was an important realisation: keeping the base image tag pinned (`3.11.9-slim`) while regularly pulling and rebuilding ensures you get security patches without unintentionally picking up breaking changes from a major version bump. The tag is stable; the underlying layers get updated by the maintainer when patches are available.

**Step 6 — Reviewing what was already done and what the stack now looks like**

I stepped back and made a full list of the security decisions now in effect across the stack:

From step 14: non-root user (`appuser`, uid 1001), pinned base image, `.dockerignore` excluding sensitive files, exec form CMD for signal handling.

From this step: `.env` file for credentials, capability drop on application containers, localhost-only port binding for the backend, read-only filesystem with tmpfs for writable paths, vulnerability scanning as part of the build process.

Each of these addresses a different attack surface. Together they reduce the blast radius significantly: a compromised container cannot write to its own filesystem, cannot use raw network capabilities, cannot expose the database to the outside world, and cannot leak credentials through the repository history.



## 3. Why It Happens

Container security issues are usually not novel attack types — they are familiar problems (credential exposure, overprivileged processes, unnecessary network exposure) applied to a container context. Docker does not automatically secure these things. It provides the mechanisms — capability dropping, read-only filesystems, network isolation, non-root users — but using them is the developer's responsibility.

The default Docker configuration is designed for compatibility and ease of use, not for minimal privilege. A fresh container has writable filesystems, a default capability set that includes more than most applications need, and no restrictions on network exposure beyond what the compose file specifies. Production security means explicitly configuring each of these away from the default.

The `.env` pattern for credentials works because Docker Compose reads the `.env` file automatically before processing `docker-compose.yml`. Variable references like `${DB_PASSWORD}` are substituted at compose file parse time, before the values are passed to the container. The credential is never in the compose file — only in the `.env` file, which lives outside version control.

Read-only filesystems work because the `read_only: true` directive mounts the container's root filesystem as read-only at the kernel level. The tmpfs mounts are independent writable filesystems that the kernel provides in memory — they are not part of the container image and exist only for the container's lifetime.



## 4. Solution

The complete security configuration for this step, applied across `docker-compose.yml`, application files, and the project directory structure:

**`.env` — credentials file (not committed to version control):**

```
DB_USER=appuser
DB_PASSWORD=your_secure_password_here
POSTGRES_USER=appuser
POSTGRES_PASSWORD=your_secure_password_here
```

**`.gitignore` — ensure `.env` is excluded:**

```
.env
.env.*
```

**`.dockerignore` — already present from step 14, confirm `.env` is excluded:**

```
.env
.env.*
__pycache__
*.pyc
.git
```

**`docker-compose.yml` — full security-hardened configuration:**

```yaml
services:
  frontend:
    build: ./frontend
    restart: on-failure
    read_only: true
    tmpfs:
      - /tmp
      - /run
    cap_drop:
      - ALL
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
      - "127.0.0.1:5001:5001"
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
    read_only: true
    tmpfs:
      - /tmp
      - /run
    cap_drop:
      - ALL
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
      - "127.0.0.1:5000:5000"
    environment:
      - DB_HOST=db
      - DB_PORT=5432
      - DB_NAME=appdb
      - DB_USER=${DB_USER}
      - DB_PASSWORD=${DB_PASSWORD}
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
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER} -d appdb"]
      interval: 5s
      timeout: 5s
      retries: 5
      start_period: 10s
    environment:
      - POSTGRES_DB=appdb
      - POSTGRES_USER=${DB_USER}
      - POSTGRES_PASSWORD=${DB_PASSWORD}
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

No Dockerfile changes beyond what step 14 already established. No application code changes. Security hardening at this level is configuration — it lives in `docker-compose.yml`, `.env`, and `.gitignore`.



## 5. Deep Understanding

### Credentials — The `.env` File Pattern and Its Limits

The `.env` file keeps credentials out of version control. That is a genuine improvement over hardcoded values. But it has limits worth understanding.

The credentials still exist in plaintext on the filesystem of whoever runs `docker compose up`. If that is a developer's laptop, the file is as secure as the laptop. If that is a CI/CD server, the file needs to be placed there by some mechanism — typically a secrets manager or pipeline secrets injection — which means there is still an external system that holds the credential.

For production deployments, the `.env` approach is a step in the right direction but not the final answer. The production-grade approach uses a secrets manager — HashiCorp Vault, AWS Secrets Manager, Docker Swarm secrets, Kubernetes secrets — where credentials are fetched at runtime and injected into the container environment without ever being written to a file on the host filesystem. For this stack running on a single host, `.env` with a tight file permission (`chmod 600 .env`) is reasonable.

One important nuance: environment variables are visible to any process running inside the container via `/proc/<pid>/environ`. They can also be read via `docker inspect`. They are not secret once they are inside the container — they are just not in the repository. This is a meaningful improvement, but it is not end-to-end encryption of the credential.

### Linux Capabilities — What Docker Grants by Default

Docker's default capability set includes these capabilities that most application containers do not need:

`CHOWN` — change file ownership. Needed during image build but not at runtime if files are owned correctly.
`DAC_OVERRIDE` — bypass file read/write/execute permission checks. Rarely needed.
`FSETID` — set the setuid/setgid bits. Not needed.
`FOWNER` — bypass filesystem ownership checks. Not needed.
`MKNOD` — create special files. Not needed.
`NET_RAW` — use raw sockets. Allows crafting arbitrary network packets — a significant capability for a web server to have.
`SETGID`, `SETUID` — change user/group identity. Needed if the container switches users at startup; not needed if `USER` is already set in the Dockerfile.
`SETPCAP` — modify process capabilities. Not needed.
`SYS_CHROOT` — change root directory. Not needed.

Dropping all capabilities with `cap_drop: ALL` removes every one of these. The Flask application connects to a database over a normal TCP socket and serves HTTP on port 5000. None of the above capabilities are required for this. An attacker who achieves code execution inside the container finds a process with no special Linux capabilities — substantially harder to escalate from.

The trade-off is that `cap_drop: ALL` occasionally breaks things that assumed a capability would be present. Running the container and confirming it works after dropping capabilities is essential — which is why the experience section went through the read-only filesystem failure and fix explicitly.

### Read-Only Filesystem — What It Prevents

A read-only root filesystem prevents four categories of attack:

**Persistence.** An attacker who executes code inside the container cannot write a backdoor, a cron job, or any file that would survive or spread. Each restart is a clean state.

**Tampering.** Application code cannot be modified at runtime. An attacker cannot replace `app.py` with a modified version or inject code into installed packages.

**Tool installation.** Many post-exploitation techniques involve downloading and running tools — `wget`, `curl`, compilers. A read-only filesystem blocks all of these at the kernel level.

**Log injection.** An attacker cannot write fake log entries to log files that might be forwarded to a SIEM and used to cover tracks or trigger false alerts — because log files, if they are in the container filesystem, cannot be written to.

The `tmpfs` mounts at `/tmp` and `/run` are necessary because several standard tools write to these paths during normal operation. Gunicorn writes worker state files. Python writes temporary files during imports. These are writable but ephemeral — they exist in memory only, are not persisted to disk, and are gone when the container stops.

### Port Binding — `0.0.0.0` vs `127.0.0.1`

When Docker maps a port with `- "5000:5000"`, it binds on `0.0.0.0` by default — all network interfaces on the host. This means the port is reachable from the local network, from the internet (if the host has a public IP), and from any other machine that can reach the host.

Changing to `- "127.0.0.1:5000:5000"` binds only on the loopback interface. The port is accessible from the host machine itself but not from any external network. The container-to-container traffic between frontend and backend still goes through Docker's internal network — the host port binding is only relevant for traffic coming from outside Docker.

The practical impact: in the current stack, the backend API is a private API. It is called by the frontend, not by end users. There is no reason for it to be publicly accessible. Binding to `127.0.0.1` enforces this. If someone misconfigures a firewall rule or the host gets a public IP unexpectedly, the backend remains inaccessible from outside.

The database has no `ports` entry at all — it is reachable only from containers on the `backend-network`. This was already correct. The same principle now applies to the backend.

### Vulnerability Scanning — Understanding the Output

`docker scout` (or `trivy`, `grype`, or `snyk container test`) scans the image's installed packages against a database of known CVEs. The output categorises findings by severity: Critical, High, Medium, Low.

Critical and High CVEs with available fixes should be addressed. The fix is almost always: update the base image and rebuild. Image maintainers push patched versions of their images when CVEs are fixed. Pulling and rebuilding picks up the patches.

Medium and Low CVEs require judgment. Many are in packages that are installed but never executed by the application — development headers, documentation tools, optional features. A CVE in a package the application never calls has a different risk profile than a CVE in a package called on every request.

The most important output from a scanner is the trend: is the count going up or down? A freshly built image that passes a scan clean is a baseline. An image that was clean last month and now shows two Critical CVEs has drifted — something in the supply chain has been patched and the image needs a rebuild.

Scanning is not a one-time activity. It belongs in the CI/CD pipeline, running on every image build, with alerts when new Critical or High CVEs appear.



## 6. Commands

```bash
# ── Credentials via .env ───────────────────────────────────────────────────

# Create .env file (once — never commit this)
cat > .env << 'EOF'
DB_USER=appuser
DB_PASSWORD=your_secure_password_here
EOF

# Verify compose can read the variables
docker compose config | grep -A5 "environment"

# Confirm the variable is present inside the container
docker compose exec backend env | grep DB_PASSWORD

# ── Checking and Dropping Capabilities ────────────────────────────────────

# View current capabilities on a running container
docker inspect $(docker compose ps -q backend) \
  --format='CapAdd={{.HostConfig.CapAdd}} CapDrop={{.HostConfig.CapDrop}}'

# After adding cap_drop: ALL — confirm
docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.CapDrop}}'
# Should show: [ALL]

# ── Read-Only Filesystem ──────────────────────────────────────────────────

# Check if filesystem is read-only
docker inspect $(docker compose ps -q backend) \
  --format='ReadOnly={{.HostConfig.ReadonlyRootfs}}'

# Test read-only enforcement from inside the container
docker compose exec backend touch /app/test.txt
# Expected: touch: /app/test.txt: Read-only file system

# Confirm tmpfs mounts are writable
docker compose exec backend touch /tmp/test.txt
# Expected: success (tmpfs is writable)

# ── Port Binding ──────────────────────────────────────────────────────────

# Check what interfaces ports are bound to on the host
docker compose ps
# Look for 127.0.0.1:5000->5000/tcp vs 0.0.0.0:5000->5000/tcp

# Alternative check
ss -tlnp | grep 5000
# Should show 127.0.0.1:5000 not 0.0.0.0:5000

# ── Vulnerability Scanning ────────────────────────────────────────────────

# Quick overview (requires Docker Desktop or docker scout CLI)
docker scout quickview backend:v1

# CVEs by severity
docker scout cves backend:v1
docker scout cves backend:v1 --only-severity critical,high

# Alternative: trivy (open source, works without Docker Desktop)
trivy image backend:v1
trivy image --severity CRITICAL,HIGH backend:v1

# ── Full Security Audit of a Container ────────────────────────────────────

docker inspect $(docker compose ps -q backend) --format='
User:        {{.Config.User}}
ReadOnly:    {{.HostConfig.ReadonlyRootfs}}
CapDrop:     {{.HostConfig.CapDrop}}
CapAdd:      {{.HostConfig.CapAdd}}
NetworkMode: {{.HostConfig.NetworkMode}}
'
```



## 7. Real-World Notes

The security improvements in this step are not theoretical hardening — they each address a real failure mode that has caused real incidents. Credentials in version control have leaked production databases. Overprivileged containers have been used to escape to the host in CVE exploitation chains. Publicly bound backend APIs have been scraped, abused, or used as entry points when firewall rules were misconfigured. Writable container filesystems have been used to plant persistent backdoors.

None of these are exotic attacks. They are the standard playbook for anyone probing a containerised application.

The `.env` pattern is appropriate for development and simple single-server deployments. For anything more sensitive — financial data, health data, regulated industries — Docker Secrets, HashiCorp Vault, or the cloud provider's secrets manager is the right tool. The key property these provide that `.env` does not: the secret is never written to disk on the host. It is fetched at runtime and injected directly into the container's memory.

Capability dropping occasionally breaks things, and the first time it does the instinct is to add the capability back. The better approach is to understand which capability is needed and add only that one. `strace` inside the container can show exactly which system calls an application makes — and from there, which capabilities it actually requires. For a Flask app serving HTTP and connecting to Postgres, the list is very short.

Image vulnerability scanning should be integrated into the build pipeline, not run manually. The gap between when a CVE is published and when the image is rebuilt is the window of exposure. A pipeline that scans every build and fails on new Critical CVEs closes that window automatically. Most CI systems (GitHub Actions, GitLab CI, Jenkins) have ready-made integrations for `trivy` that take under ten minutes to set up.

One thing this step does not cover is secrets rotation — the process of changing credentials periodically and updating the running stack. In Docker Compose, this requires restarting the containers with the new credentials. In Kubernetes, secrets can be updated without restarting pods (depending on how they are mounted). Rotation is part of a complete credential management story but is outside the scope of a single-host Docker Compose deployment.



## 8. Exercises

**Exercise 1 — Find the plaintext credential in your repository history**

Before making any changes, run:

```bash
git log --all -p | grep -i "DB_PASSWORD\|password\|secret"
```

If the credentials were committed in any previous step, they will appear in the git history. This is permanent — removing the value from the current file does not remove it from git history. This exercise makes the commit-history exposure concrete: the damage from committing a credential is not undone by a follow-up commit.

**Exercise 2 — Move credentials to `.env`**

Create a `.env` file with the database credentials. Update `docker-compose.yml` to use `${DB_USER}` and `${DB_PASSWORD}` instead of hardcoded values. Add `.env` to `.gitignore`. Then verify the stack still works:

```bash
docker compose up -d
docker compose exec backend env | grep DB_
```

Confirm the variables appear correctly inside the container. Then verify the compose file substitution:

```bash
docker compose config | grep -A 10 "environment"
```

The `docker compose config` command shows the fully-resolved compose file — with variables substituted. Confirm the actual credential values appear in the resolved config but not in the `docker-compose.yml` source file.

**Exercise 3 — Drop capabilities and confirm nothing breaks**

Add `cap_drop: ALL` to the backend and frontend services. Apply the change:

```bash
docker compose up -d
```

Verify the services are healthy:

```bash
docker compose ps
curl http://localhost:5000/health
curl http://localhost:5001/
```

Confirm the capabilities were dropped:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{.HostConfig.CapDrop}}'
```

The value should be `[ALL]`. The fact that the application continues to work confirms it does not actually need any of the dropped capabilities.

**Exercise 4 — Enable read-only filesystem**

Add `read_only: true` and the `tmpfs` mounts to the backend service:

```yaml
read_only: true
tmpfs:
  - /tmp
  - /run
```

Apply and check the logs:

```bash
docker compose up -d
docker compose logs backend
```

If the backend fails to start, read the error carefully — it will tell you which path it could not write to. Add that path to the `tmpfs` list. Once the backend is healthy, verify the read-only enforcement:

```bash
docker compose exec backend touch /app/canary.txt
# Expected: Read-only file system

docker compose exec backend touch /tmp/canary.txt
# Expected: success
```

Two different results from two different paths — read-only root filesystem with writable tmpfs.

**Exercise 5 — Verify port binding**

Before changing the port binding, check which interface the backend is bound to:

```bash
docker compose ps
```

Look at the `PORTS` column. It should show `0.0.0.0:5000->5000/tcp` — bound on all interfaces.

Change the backend port binding to `127.0.0.1:5000:5000`. Apply:

```bash
docker compose up -d
docker compose ps
```

The `PORTS` column should now show `127.0.0.1:5000->5000/tcp`. Confirm the backend is still reachable from localhost:

```bash
curl http://localhost:5000/health
```

Now try to confirm external access is blocked — if you have a second machine on the same network, try accessing `http://<host-ip>:5000/health` from it. It should fail. The backend is still reachable from within Docker's internal network (the frontend can still call it), but the host-level port is now restricted to loopback.

**Exercise 6 — Run a vulnerability scan**

Scan the backend image:

```bash
# With docker scout (Docker Desktop)
docker scout quickview backend:v1

# With trivy (install: https://aquasecurity.github.io/trivy)
trivy image backend:v1
```

Read the output. Note how many Critical and High CVEs exist. Then pull the latest base image and rebuild:

```bash
docker pull python:3.11.9-slim
docker compose build --no-cache backend
trivy image backend:v1
```

Compare the CVE counts before and after the rebuild. A reduced count means the image maintainer has pushed patches that your rebuild picked up. This is the build-and-scan cycle that keeps images current.

**Exercise 7 — Full security audit**

Run this against every container in the stack:

```bash
for service in frontend backend db; do
  echo "=== $service ==="
  docker inspect $(docker compose ps -q $service) --format='
  User:        {{.Config.User}}
  ReadOnly:    {{.HostConfig.ReadonlyRootfs}}
  CapDrop:     {{.HostConfig.CapDrop}}
  CapAdd:      {{.HostConfig.CapAdd}}
  '
done
```

For each service, read the output and check:
- `User` should be non-empty (non-root) for frontend and backend
- `ReadOnly` should be `true` for frontend and backend
- `CapDrop` should be `[ALL]` for frontend and backend
- `CapAdd` should be `[]` for frontend and backend

The database will show different values — that is expected. This exercise gives you a single command you can run at any time to confirm the security posture of the running stack.