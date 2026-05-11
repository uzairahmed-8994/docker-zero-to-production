# 🐳 Docker: Zero to Production

![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white) ![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white) 
![Level](https://img.shields.io/badge/Level-Beginner%20→%20Production-brightgreen?style=flat-square) ![Type](https://img.shields.io/badge/Type-Hands--On-orange?style=flat-square) ![Steps](https://img.shields.io/badge/28%20Steps-Production%20Ready-red?style=flat-square)

A hands-on Docker learning repository that takes a real three-service application from a single Flask file to a fully hardened, monitored, and CI/CD-deployed production stack.

Every concept is introduced through something breaking, not through a definition.

---

## Why This Repository Exists

This repository covers everything between "I can run a container" and "I can operate this in production, debug it under pressure, and explain every decision I made."

The questions this repository answers that most tutorials skip:

✔ Why does my build take 3 minutes when I change one line?
✔ Why does the app work locally but fail in production?
✔ What happens to my data when the container restarts?
✔ How do I roll back a broken deployment at 3am?
✔ How do I know what is actually running on the server right now?

---

## What You Build

A production-grade three-service stack running inside Docker:

```bash
User
  ↓
Frontend (Flask) — port 5001
  ↓
Backend (Flask + Gunicorn) — port 5000
  ↓
Database (PostgreSQL) — persistent storage via Docker volume
```


---

## What You Will Learn

### 🧱 Foundations
- Container lifecycle, image layers, build cache behaviour
- Dockerfile best practices — why order matters, what each instruction costs
- Networking, service discovery, port binding vs internal communication

### ⚙️ Multi-Service Architecture
- Docker Compose — networking, volumes, dependency ordering
- Postgres integration — connection handling, retry logic, data persistence
- Bind mounts vs named volumes — when each is appropriate and why

### 🔴 Production Hardening
- Non-root users, read-only filesystems, dropped Linux capabilities
- Health checks with `start_period`, `depends_on` conditions, health state lifecycle
- Restart policies — `on-failure` vs `unless-stopped`, backoff behaviour
- Resource limits — memory ceilings, CPU throttling, OOM kill behaviour

### 🔐 Security
- Secrets management with `.env` files — what it protects and what it does not
- Vulnerability scanning with `docker scout` / `trivy`
- Capability model — what Docker grants by default and what to drop

### 🚀 Deployment
- Docker registries — push/pull, layer deduplication, private registries
- Version tagging — semver, git SHA tags, floating vs pinned, OCI labels
- CI/CD pipeline — GitHub Actions: build, test, scan, push, deploy

### 🔥 Real Engineering Skills
- 8 production case studies — memory leaks, wrong data, broken deployments, slow queries
- Incident response playbook — triage, stabilise, investigate, fix
- 17 production-level interview questions with strong answers and follow-ups

---

## 📚 Learning Approach

This is not a copy-paste tutorial.

Each step follows a consistent structure built around a real problem:

```
Problem          → what the current setup cannot do
What Happened    → first-person encounter with the issue
Why It Happens   → the mechanism underneath the symptom
Solution         → the deliberate fix with explicit reasoning
Deep Understanding → concepts that generalise beyond this step
Exercises        → hands-on tasks, including breaking things on purpose
```

The philosophy: you learn more from one container behaving unexpectedly and understanding why, than from ten tutorials that always work perfectly.

---

## 🗂️ Full Course Structure

### 🟢 Part 1 — Foundations

| Step | Topic |
|------|-------|
| [01](./01-basic-flask-app) | Basic Flask App — the application that runs through the entire course |
| [02](./02-first-dockerfile) | First Dockerfile — containerising the app for the first time |
| [03](./03-running-containers) | Running Containers — `run`, `stop`, `start`, `rm`, and what each does |
| [04](./04-container-lifecycle) | Container Lifecycle — states, restarts, and what actually persists |
| [05](./05-docker-networking) | Docker Networking — how containers communicate and what isolation means |
| [06](./06-container-debug-basics) | Debug Basics — `exec`, `logs`, `inspect` — seeing inside a container |

---

### 🟡 Part 2 — Multi-Container Systems

| Step | Topic |
|------|-------|
| [07](./07-manual-multi-container) | Manual Multi-Container — frontend + backend connected by hand |
| [08](./08-docker-compose) | Docker Compose — replacing manual wiring with a compose file |
| [09](./09-compose-networking) | Compose Networking — named networks, service discovery, isolation |

---

### 🟠 Part 3 — Data and Persistence

| Step | Topic |
|------|-------|
| [10](./10-compose-volumes) | Compose Volumes — attaching storage to containers |
| [11](./11-volumes-persistence) | Volume Persistence — proving data survives restarts |
| [12](./12-bind-mounts) | Bind Mounts — host directories, development workflows, tradeoffs |
| [13](./13-database-postgres) | PostgreSQL Integration — a real database with connection handling |

---

### 🔴 Part 4 — Production Readiness

| Step | Topic |
|------|-------|
| [14](./14-production-dockerfile) | Production Dockerfile — layer ordering, non-root user, Gunicorn |
| [15](./15-multi-stage-builds) | Multi-Stage Builds — separating build tools from runtime |
| [16](./16-image-optimization) | Image Optimization — base image selection, `dive`, size vs compatibility |
| [17](./17-container-debugging-advanced) | Advanced Debugging — systematic investigation across layers |
| [18](./18-logging-monitoring) | Logging and Monitoring — structured logs, rotation, `docker stats` |
| [19](./19-health-checks) | Health Checks — `HEALTHCHECK`, `depends_on` conditions, state lifecycle |
| [20](./20-restart-policies) | Restart Policies — `on-failure`, `unless-stopped`, backoff behaviour |
| [21](./21-resource-limits) | Resource Limits — memory ceilings, CPU throttling, OOM behaviour |
| [22](./22-security-best-practices) | Security Best Practices — secrets, capabilities, read-only filesystem, scanning |

---

### ⚡ Part 5 — Deployment and Real World

| Step | Topic |
|------|-------|
| [23](./23-docker-registry) | Docker Registry — push/pull workflow, private registry, layer deduplication |
| [24](./24-version-tagging) | Version Tagging — semver, git SHA tags, OCI labels, rollback strategy |
| [25](./25-ci-cd-with-docker) | CI/CD with Docker — GitHub Actions: build → test → push → deploy |
| [26](./26-real-world-scenarios) | Real-World Scenarios — 8 case studies from actual production failures |
| [27](./27-production-troubleshooting) | Production Troubleshooting — incident response playbook and failure guides |
| [28](./28-interview-questions) | Interview Questions — 17 production-level Q&A with follow-ups |

---

## 🧭 How to Use This Repository

```
✔ Follow steps in order — each step builds on the previous one
✔ Run every command — understanding comes from the encounter, not the reading
✔ Break things intentionally — exercises include deliberate failures
✔ Read the "Why It Happens" section — this is what most tutorials skip
✔ Use steps 26 and 27 as references during any real Docker incident
```

---

## 👤 Who This Is For

- **Backend engineers** who use Docker daily but want to understand what is actually happening beneath `docker compose up`
- **DevOps / platform engineers** who need to operate, debug, and make architecture decisions in production
- **Students preparing for technical interviews** at companies where Docker, CI/CD, and production debugging are core requirements
- **Anyone** who has completed a basic Docker tutorial and found it did not prepare them for real problems

---

## 🏁 What You Will Be Able to Do

After completing this repository:

```
✔ Identify what is wrong with any docker-compose.yml in under 2 minutes
✔ Debug a container that is restarting, leaking memory, or returning wrong data — systematically
✔ Build a CI/CD pipeline that builds, tests, scans, tags, pushes, and deploys automatically
✔ Roll back a broken production deployment in 90 seconds
✔ Answer production-level Docker interview questions with real reasoning, not memorised answers
```

---

## ⭐ Final Note

Most engineers learn Docker by running commands until things work.

This repository teaches you to understand why they work — and exactly what to do when they do not.

**28 steps. One real stack. Built from scratch. Broken on purpose. Deployed to production.**

---

*Start at [Step 01 →](./01-basic-flask-app)*