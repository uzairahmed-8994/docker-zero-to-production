# 14 — Production Dockerfile



## 0. Goal of This Step

Understand what makes a Dockerfile "production-ready" — not as a checklist, but as a set of deliberate decisions about layer structure, security, base image selection, and build behaviour. Take the Dockerfile we have used since step 02 and rebuild it the right way.



## 1. What Problem It Solves

The Dockerfile from step 02 was written to get things running quickly for learning. It did that job well. But it was never designed for production. The problems are not obvious — the app runs fine. The issues are structural:

- Every code change triggers a full dependency reinstall because of poor layer ordering
- The container runs as root with no reason to
- The base image tag is mutable — the same Dockerfile can produce a different image next week
- No `.dockerignore` — the build context is larger than it needs to be, and sensitive files could end up in the image
- The development server is being used where a production server should be

None of these cause an immediate crash. They cause slow builds, security exposure, and unpredictable behaviour that only surfaces under real conditions.

This step is about understanding the *why* behind each Dockerfile decision — because step 15 (multi-stage builds) and step 16 (image optimization) build directly on top of this foundation.


In Step 13, we built a system that works.

In this step, we take that same system and make it production-ready — without changing how it behaves, only how it is built and run.


## 2. Code Changes Required

> **Make these changes before following the experience section.**

Two things need updating from step 13 before this step works correctly.

**`backend/app.py` — move `init_db()` to module level**

In step 13, `init_db()` was called inside `if __name__ == "__main__"`. That block only runs when Python executes the file directly. Gunicorn *imports* the module — it never triggers `__main__`. Move `init_db()` to module level so it runs on import regardless of how the app starts:

```python
# Called at import time — runs whether started by python directly or by gunicorn
init_db()

@app.route("/")
def home():
    ...
```

Place this call after the `init_db()` function definition and before the route definitions. The `if __name__ == "__main__"` block can remain for running locally without Gunicorn — it just should not be the only place `init_db()` is called.

**`backend/requirements.txt`**

```
Flask==3.1.3
psycopg2-binary==2.9.9
gunicorn==21.2.0
```



## 3. What Happened (Experience)

I looked at the Dockerfile that had been working fine since step 02:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
```

Seven lines. Works. But I started asking questions about each line:

- Why `python:3.11-slim` and not `python:3.11-slim` from last month — are they the same image?
- Why is the process running as root?
- What happens under real traffic with `python app.py`?
- When I change one line of code, why does Docker reinstall all dependencies?

Each question exposed a decision that was made by default rather than deliberately. I went through the Dockerfile line by line and fixed each one.



**Observation 1 — Layer ordering is costing rebuild time**

I changed one line in `app.py` and rebuilt:

```bash
time docker compose build backend
```

Docker reinstalled every dependency from scratch. Every time. For two dependencies it takes seconds. I added five more packages to `requirements.txt` just to simulate a real project — rebuild jumped to over a minute for a single line change in `app.py`.

The problem is the layer order. `COPY . .` copies all the code before pip runs. Any change to any file — including `app.py` — invalidates the pip cache. Docker has no way to know that changing `app.py` should not affect the result of `pip install`.

The fix is to separate what changes rarely from what changes often:

```dockerfile
# This layer only rebuilds when requirements.txt changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# This layer rebuilds on every code change — but it is just a file copy, fast
COPY . .
```

After this change I modified `app.py` and rebuilt — pip was fully cached. Only the `COPY . .` layer ran. Rebuild dropped from over a minute to under three seconds.



**Observation 2 — The container runs as root for no reason**

```bash
docker compose exec backend whoami
# root
```

I checked what happens if the app writes a file as root — it can write anywhere in the container. I checked what permissions the app process has — full root. There is no reason a Flask web server needs root. The fix is to create a dedicated user and drop to it before runtime:

```dockerfile
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

# ... install dependencies ...

COPY --chown=appuser:appgroup . .

USER appuser
```

After this, `whoami` inside the container returns `appuser`. The process runs with minimal privilege.



**Observation 3 — The base image tag can change silently**

`FROM python:3.11-slim` is a tag on Docker Hub that gets updated with security patches. The same tag today and the same tag in three months can point to different image layers. I built the image twice a week apart — the image IDs were different despite the code being identical.

Pinning to a patch version makes builds more stable:

```dockerfile
FROM python:3.11.9-slim
```

Now the base layer is consistent across builds until I explicitly change it.



**Observation 4 — The build context includes things it should not**

```bash
docker build -t backend:v1 ./backend
# => transferring context: 3.2MB
```

3.2MB for a Flask app with two files. The build context was including `__pycache__`, `.pyc` files, and other noise. A `.dockerignore` file fixes this:

```
__pycache__
*.pyc
*.pyo
.env
.env.*
*.log
.git
README.md
tests/
```

After adding it, the build context dropped to 12KB. Smaller context means faster builds — Docker sends the context to the build daemon before building starts.


**The production Dockerfile after all fixes:**

```dockerfile
FROM python:3.11.9-slim

RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app:app"]
```



## 4. Why It Happens

A Dockerfile is a recipe for building an image. Docker executes it top to bottom and caches every layer. The cache key for each layer is determined by the instruction itself and the state of everything above it. This means:

- If a `COPY` copies a file that changed → that layer and everything below it is invalidated
- If a `RUN` command changes → that layer and everything below it is invalidated
- Layers above the change are always reused

This is not a bug — it is the design. The entire caching system is built on this top-to-bottom invalidation model. Writing a Dockerfile well means understanding this model and designing layer order around it deliberately.

The root user problem exists because Docker containers run as root by default unless told otherwise. Docker does not enforce least privilege — that is the developer's responsibility. Most base images are designed to be flexible, which means they default to root. You have to explicitly create and switch to a non-root user.



## 5. Deep Understanding

### The Layer Model — How Docker Builds Images

Every instruction in a Dockerfile creates a layer. An image is a stack of read-only layers:

```
Layer 5: COPY --chown . .                  ← your code
Layer 4: RUN pip install                   ← dependencies
Layer 3: COPY requirements.txt .           ← requirements file
Layer 2: WORKDIR /app                      ← working directory
Layer 1: FROM python:3.11.9-slim           ← base image (itself many layers)
```

When Docker rebuilds, it walks this stack from top to bottom. The moment it finds a layer whose cache is invalid, it rebuilds that layer and every layer below it. Layers above the invalidated one are always pulled from cache.

This is why ordering matters so much. The question to ask for every line in a Dockerfile is: **how often does this change?** Put things that change rarely near the top. Things that change often go near the bottom.

```
Rarely changes:    base image, system packages, dependency installs
Sometimes changes: configuration files, environment setup
Often changes:     application source code
```

The correct order follows this hierarchy naturally.

### Cache Invalidation in Practice

The `COPY` instruction invalidates its cache when the content of the copied files changes. Docker computes a checksum of every file in the COPY source and compares it to the cached checksum. If anything changed — even a comment in a file — the layer is invalidated.

This is why `COPY requirements.txt .` as a separate step before `COPY . .` is so important. Docker caches the pip install layer against the checksum of `requirements.txt` alone. Your `app.py` changes do not affect that checksum. Dependencies only reinstall when `requirements.txt` actually changes.

You can see exactly which layers are cached during a build:

```bash
docker build -t backend:v1 ./backend
# => CACHED [2/5] RUN groupadd ...          ← cache hit
# => CACHED [3/5] COPY requirements.txt .   ← cache hit
# => CACHED [4/5] RUN pip install ...       ← cache hit — requirements unchanged
# => [5/5] COPY --chown ...                 ← rebuilt — code changed
```

The `CACHED` label is your confirmation that the layer ordering is working correctly.

### `.dockerignore` — The Build Context

When you run `docker build`, Docker first assembles a **build context** — all the files in the directory you pointed to (`./backend`). This entire context is sent to the Docker daemon before a single Dockerfile instruction runs. Only after the context is transferred does Docker start executing instructions.

A `.dockerignore` file tells Docker which files to exclude from the context. The format is identical to `.gitignore`:

```
__pycache__        # compiled Python files — regenerated at runtime anyway
*.pyc              # bytecode — not needed in the image
.env               # secrets — must never end up in an image
.git               # version control history — large and irrelevant
tests/             # test files — not needed at runtime
README.md          # documentation — not needed at runtime
```

Two reasons this matters:
1. **Speed** — smaller context transfers faster to the daemon, especially on remote Docker hosts
2. **Security** — `.env` files, credential files, and private keys cannot accidentally end up baked into the image if they are excluded from the context

A `.dockerignore` file should exist alongside every Dockerfile. It is not optional in a production setup.

### Non-Root User — Principle of Least Privilege

The security principle of least privilege says: every process should have exactly the permissions it needs to do its job, and no more. A Flask web server needs to:
- Read its own source files
- Listen on a port above 1024 (port 5000 — no root required)
- Connect to a database

It does not need to:
- Write to system directories
- Install packages
- Read other users' files
- Modify system configuration

Running as root grants all of these without restriction. Running as `appuser` with uid 1001 grants none of them.

The `--chown` flag on `COPY` is important here:

```dockerfile
COPY --chown=appuser:appgroup . .
```

This sets file ownership at copy time. The alternative — copying as root then running `RUN chown -R appuser /app` — creates an extra layer that duplicates all file metadata, inflating the image. `--chown` achieves the same result without the extra layer.

### Base Image Selection and Pinning

The `python:3.11.9-slim` base image is a good choice for Python web apps:

- `-slim` variant: stripped down, no build tools, no documentation, much smaller than the full image
- `3.11.9`: patch version pinned — stable across builds

The full family of Python image variants:

| Tag | Size | Use case |
|-----|------|----------|
| `python:3.11` | ~900MB | Full Debian — avoid unless you need obscure packages |
| `python:3.11-slim` | ~130MB | Stripped Debian — good for most web apps |
| `python:3.11-alpine` | ~50MB | Alpine Linux — tiny but can cause issues with C extensions |

Alpine Linux is tempting because of its small size but `psycopg2-binary` and some other packages with C extensions can behave unexpectedly on Alpine due to its use of `musl libc` instead of `glibc`. For Python apps with binary dependencies, `-slim` is the safer choice. Step 16 covers image size reduction in more depth.

On pinning: `3.11.9-slim` pins the patch version. The tag is still technically mutable on Docker Hub — it could be updated. For absolute reproducibility, pin by digest:

```dockerfile
FROM python:3.11.9-slim@sha256:a8140b8f...
```

A digest is immutable — it refers to one exact image forever. Most teams pin by patch version and treat digest pinning as optional hardening for high-compliance environments.

### The CMD Instruction and Signal Handling

```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app:app"]
```

Two forms of CMD exist:

**Exec form** (array): `CMD ["gunicorn", ...]`
- The process runs directly as PID 1
- Receives OS signals (SIGTERM, SIGINT) directly
- Gunicorn handles SIGTERM by finishing in-flight requests then shutting down gracefully

**Shell form** (string): `CMD gunicorn ...`
- Docker wraps this in `/bin/sh -c "..."`
- The shell becomes PID 1
- OS signals go to the shell, not to gunicorn
- Gunicorn may be killed without finishing in-flight requests

Always use exec form for CMD in production Dockerfiles. The difference is critical for graceful shutdown — when `docker stop` sends SIGTERM, you want your application to receive it, not a shell wrapper.



## 6. Commands

```bash
# ── Building ───────────────────────────────────────────────────────────────

docker compose up -d --build
docker compose build --no-cache backend    # force full rebuild, bypass cache

# ── Observing Layer Cache ──────────────────────────────────────────────────

docker build -t backend:v1 ./backend       # watch for CACHED labels per layer
docker history backend:v1                  # see every layer, its size, its command

# ── Verifying Security ────────────────────────────────────────────────────

docker compose exec backend whoami         # should return: appuser
docker compose exec backend id             # uid=1001(appuser) gid=1001(appgroup)

# ── Inspecting the Image ──────────────────────────────────────────────────

docker image ls                            # all images and sizes
docker image ls backend                    # just backend
docker run --rm backend:v1 ls -la /app     # see file ownership inside image
docker run --rm backend:v1 whoami          # verify non-root without compose

# ── Build Context Size ────────────────────────────────────────────────────

# Watch "transferring context" line in build output — shows context size
# Before .dockerignore vs after — compare the numbers
```



## 7. Real-World Notes

In real teams the Dockerfile lives in version control and gets reviewed in pull requests like any other code. A change to the base image version, the layer order, or the CMD is a meaningful decision with security and performance implications — not a cosmetic edit.

The layer caching strategy in this step is one of the highest-leverage Dockerfile optimisations available and costs nothing. On a large Python project with dozens of dependencies, the difference between cached and uncached pip installs in CI can be the difference between a 2-minute pipeline and a 15-minute one. Multiply that across every pull request and every developer and it adds up to real hours.

The `.dockerignore` file is especially important in teams. Without it, a developer who puts credentials in a `.env` file in the project directory can accidentally bake those credentials into the image and push it to a registry — where they sit permanently in the layer history even if the file is later removed. `.dockerignore` makes this class of mistake impossible.

Step 15 (multi-stage builds) extends the layer model further — using separate build and runtime stages to produce images that contain only what is needed at runtime, with build tools completely absent. Step 16 goes deeper on image size reduction. Both of those steps build directly on the layer understanding from this step.



## 8. Exercises

**Exercise 1 — Measure the bad layer order**
Temporarily swap the layer order in the Dockerfile — put `COPY . .` before `RUN pip install`. Rebuild once to warm the cache. Now change one comment in `app.py` and rebuild again, timing it:
```bash
time docker compose build backend
```
Watch pip reinstall everything. Restore the correct order, rebuild once to warm cache, change the same comment, time again. The pip layer should be `CACHED`. This is the entire argument for layer ordering, felt in real seconds.

**Exercise 2 — Read the layer cache output**
Build the backend and read the output carefully. Find the lines labelled `CACHED` and the lines that actually ran. Change `app.py` and rebuild — which layers show `CACHED` now? Change `requirements.txt` and rebuild — which layers are invalidated? Build a mental model of exactly which change invalidates which layer.

**Exercise 3 — Inspect every layer with `docker history`**
Run `docker history backend:v1`. You will see every layer, its size, and the command that created it. Identify which layer is the largest. Identify the pip install layer. Identify your code layer. This is the complete breakdown of what is inside your image.

**Exercise 4 — Verify non-root security**
With the production Dockerfile in place, exec into the container:
```bash
docker compose exec backend id
```
Confirm uid=1001. Now try to write to a root-owned directory:
```bash
docker compose exec backend sh -c "touch /root/test"
# Permission denied
```
Now temporarily remove `USER appuser` from the Dockerfile, rebuild, try the same command — it succeeds. Restore `USER appuser`. The hands-on difference between root and non-root.

**Exercise 5 — Prove `.dockerignore` works**
Create a file `backend/secret.env` with content `PASSWORD=supersecret`. Build without `.dockerignore` and check inside the image:
```bash
docker run --rm backend:v1 cat /app/secret.env
# PASSWORD=supersecret
```
The secret is in the image. Now add `*.env` to `backend/.dockerignore`, rebuild, check again:
```bash
docker run --rm backend:v1 cat /app/secret.env
# cat: /app/secret.env: No such file or directory
```
The file never entered the image. Delete `secret.env`. This is exactly how credentials leak into images in real projects.

**Exercise 6 — Compare exec form vs shell form CMD**
Change CMD to shell form: `CMD gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 60 app:app`. Rebuild and run `docker compose exec backend ps aux`. You will see `/bin/sh -c gunicorn...` as PID 1 and gunicorn as a child process. Now run `docker stop` and watch how long it takes — the shell may not forward SIGTERM to gunicorn cleanly, causing Docker to wait the full 10-second timeout before force-killing. Restore exec form CMD and repeat — `docker stop` completes immediately as gunicorn handles SIGTERM gracefully.