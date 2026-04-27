# 15 — Multi-Stage Builds



## 0. Goal of This Step

Understand why production images should only contain what is needed to *run* the application — not what was needed to *build* it and learn how multi-stage builds make this separation clean, automatic, and maintainable.



## 1. What Problem It Solves

After step 14 the Dockerfile was improved: better layer ordering, non-root user, pinned base image. The app runs well. But there is still something uncomfortable if you look closely at what is inside the image.

To build the app, pip needs to be present. pip installs Flask, Gunicorn, psycopg2. Once those packages are installed, pip has done its job — it is not needed at runtime. The app does not call pip. Gunicorn does not call pip. Nothing at runtime needs pip.

But pip is still in the image. So is setuptools. So is wheel. So is all the build infrastructure that came with the base image. It is all sitting there in production, doing nothing — except making the image larger and giving an attacker more tools if the container is ever compromised.

The question this step asks is: **can we build the app in one environment and run it in a completely different, cleaner environment?** The answer is multi-stage builds.


## 2. What Happened (Experience)

I had the production Dockerfile from step 14 working correctly. At that point, everything was functional, but I wanted to understand what was actually inside the image instead of assuming it was “optimized”.



**Step 1 — Looking inside the image**

```bash
docker history backend:v1
```

```
IMAGE          CREATED BY                                      SIZE
a1b2c3d4       CMD ["gunicorn" ...]                            0B
<missing>      USER appuser                                    0B
<missing>      COPY --chown=appuser:appgroup . .               8.5kB
<missing>      RUN pip install --no-cache-dir -r requiremen…   52MB
<missing>      COPY requirements.txt .                         312B
<missing>      WORKDIR /app                                    0B
<missing>      RUN groupadd --gid 1001 ...                     4.1kB
<missing>      python:3.11.9-slim                              130MB
```

The **pip install** layer stood out immediately. It was one of the largest layers after the base image.

I then checked what was actually available inside the running container:

```bash
docker run --rm backend:v1 pip --version
# pip 24.0 from /usr/local/lib/python3.11/site-packages/pip (python 3.11)
```

pip was available in the production image. That meant the container still had the ability to install packages at runtime — something the application itself never needed.

I also checked how much space pip itself was taking:

```bash
docker run --rm backend:v1 du -sh /usr/local/lib/python3.11/site-packages/pip
# 15MB  /usr/local/lib/python3.11/site-packages/pip
```

Even on its own, pip occupied a noticeable amount of space. But more importantly, its presence indicated that the build process and runtime environment were still mixed together. 

At this point, the issue became clear. The application was already built but the tools used to build it were still inside the final image.

**Step 2 — The realisation**

The problem is structural. With a single `FROM` instruction, everything happens in one environment. To install packages you need pip. pip installs the packages. pip stays. There is no mechanism in a single-stage Dockerfile to say "use pip to install, then remove pip from the result."

I tried forcing it:

```dockerfile
RUN pip install --no-cache-dir -r requirements.txt && \
    pip uninstall pip setuptools wheel -y
```

But this does not actually help. Each RUN instruction creates a new layer. Even if pip is removed in a later layer, the layer where it originally existed is still part of the image. Docker layers are additive. Removing a file in a later layer does not remove the bytes from earlier layers — it only hides them.

I verified this by rebuilding and checking the image size. It remained almost unchanged.

At that point, it was clear that the issue could not be solved by modifying commands inside a single environment.


```bash
docker image ls backend
# SIZE: 233MB   ← almost identical, pip's bytes are in the layer below
```

The only way to fix this was to separate the environments themselves — use one environment to build, and a completely different one to run.. That is exactly what multi-stage builds do.



**Step 3 — Writing the first multi-stage Dockerfile**

The idea: use two `FROM` instructions. The first builds. The second runs. Only the second becomes the final image.

```dockerfile
# ── Stage 1: Builder ──────────────────────────────────────────────────────
FROM python:3.11.9-slim AS builder

WORKDIR /build

COPY requirements.txt .

# Install into an isolated directory so we can copy it cleanly
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime ──────────────────────────────────────────────────────
FROM python:3.11.9-slim AS runtime

RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Copy only the installed packages from the builder — nothing else crosses over
COPY --from=builder /install /usr/local

COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app:app"]
```

Rebuild:

```bash
docker compose up -d --build
docker image ls backend
```

```
REPOSITORY   TAG   SIZE
backend      v1    217MB   ← was 233MB
```

The size dropped slightly.

I expected pip to disappear from the final image after switching to a multi-stage build. However, when I checked again:


```bash
docker run --rm backend:v1 pip --version
# pip 24.0 from /usr/local/lib/python3.11/site-packages/pip (python 3.11)
```
pip was still present.

This initially felt confusing, but the reason is important.

pip is part of the base image (python:3.11.9-slim). Multi-stage builds do not remove anything that already exists in the base image. They only control what is added during the build process.

To understand what actually changed, I looked at the image history:

```bash
docker history backend:v1
```
In the single-stage build, there was a layer where dependencies were installed::

```bash
RUN pip install ...
```

In the multi-stage build, that layer is gone. Instead, I see:

```bash
COPY /install /usr/local
```
This means:

The dependency installation happened in the builder stage
Only the installed packages were copied into the runtime stage
The build step itself is not part of the final image

So even though pip still exists (because of the base image), the build process and its layers are no longer included in the runtime image.

This is the key idea of multi-stage builds:
we do not necessarily remove tools from the base image — we prevent build-time layers and artifacts from being included in the final image.


For Python images, the size reduction may be modest because the base image already contains the interpreter and pip. The main benefit is structural — separating build and runtime — not just reducing size.


The application still works exactly the same:

```bash
curl http://localhost:5000/notes
# {"notes":[]}
```

Flask, Gunicorn, and psycopg2 are all present. The system behaves the same — but the way the image is constructed is fundamentally different.

**Step 4 — Understanding what `COPY --from` actually does**

The line that makes this work:

```dockerfile
COPY --from=builder /install /usr/local
```

This copies files from a *previous stage* rather than from the host filesystem. The builder installed packages into `/install` using `--prefix=/install`. That flag tells pip to put everything under one directory instead of scattering across system paths.

The result in the builder:

```
/install/
  lib/python3.11/site-packages/flask/
  lib/python3.11/site-packages/gunicorn/
  lib/python3.11/site-packages/psycopg2/
  bin/gunicorn
```

One directory. Everything under it. `COPY --from=builder /install /usr/local` drops that entire tree into `/usr/local` in the runtime stage That location is already part of Python’s default search path, so the installed packages are immediately usable without any additional configuration. 

Flask, Gunicorn, and psycopg2 are available exactly as expected.

What is important here is not just what was copied, but what was not.

Only the installed packages crossed from the builder stage into the runtime stage. The process that created them — the installation step — does not exist in the final image.

pip itself is still present in the runtime image, but that is because it comes from the base image (python:3.11.9-slim), not from the builder stage. The runtime stage did not perform any installation; it only received the results.



**Step 5 — Verifying what crossed and what did not**

```bash
# In the builder — pip is here
docker build --target builder -t backend:builder ./backend
docker run --rm backend:builder pip --version
# pip 24.0 ...

# In the runtime — pip is also here (from the base image)
docker run --rm backend:v1 pip --version
# pip 24.0 ...

## But the important difference is in how dependencies appear

# Packages installed in builder — available in runtime 
docker run --rm backend:v1 python -c "import flask; print(flask.__version__)" 
# 3.1.3

docker run --rm backend:v1 python -c "import gunicorn; print(gunicorn.__version__)"
# 21.2.0
```

The packages crossed the boundary. The installer did not.

There is no RUN pip install ... layer in the runtime image. The dependencies exist because they were copied, not because they were installed during the runtime stage.

This is the separation we were aiming for — build in one place, run in another.



## 3. Why It Happens

A single-stage Dockerfile has one environment for both building and running. Everything used during the build — pip, setuptools, compilers — lives alongside everything needed at runtime. There is no mechanism to remove them after the fact because Docker layers are additive: a later layer removing a file does not remove the bytes from the layer where the file was added. The image carries all of it.

Multi-stage builds change this model by introducing multiple `FROM` instructions. Each `FROM` starts from a clean base image, with its own independent filesystem. Nothing is inherited automatically from a previous stage.

The only way data moves between stages is through explicit copy operations:

```dockerfile
COPY --from=builder /install /usr/local
```
This means the final image contains only what is intentionally transferred into it. Everything else from the builder stage — including installation steps and temporary artifacts — does not appear in the final image because it was never copied.

It is important to note that this separation applies to what is added during the build process. Anything already present in the base image still exists in the runtime stage. Multi-stage builds do not modify or strip the base image; they control what is added on top of it.

The final image is created entirely from the last stage. Earlier stages are used only during the build process. They do not appear as standalone images, they are not pushed to registries, and they are not pulled in production environments. They exist only as intermediate steps during the build.



## 4. Solution

The solution is to separate the build environment from the runtime environment using multiple stages within the same Dockerfile.

The first stage performs the build. It installs dependencies and prepares everything required for the application to run. This stage can include any tools necessary for that process.

The second stage is responsible only for running the application. It starts from a clean base image and receives only the final artifacts from the builder stage.


**The multi-stage Dockerfile:**

```dockerfile
# ── Stage 1: Builder ──────────────────────────────────────────────────────
FROM python:3.11.9-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: Runtime ──────────────────────────────────────────────────────
FROM python:3.11.9-slim AS runtime

RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app:app"]
```

**What each decision means:**

`AS builder` / `AS runtime` — names for each stage. Names make the Dockerfile readable and let you reference stages by name instead of index.

`--prefix=/install` — installs packages into a single isolated directory instead of system paths. Makes the `COPY --from` transfer clean and predictable.

`COPY --from=builder /install /usr/local` — copies only the installed packages from the builder. Nothing else. This is the line that creates the separation.

Both stages use the same base image (`python:3.11.9-slim`) — important for Python to ensure the interpreter version and binary compatibility are identical between build and runtime.



## 5. Deep Understanding

### Layer Deletion Does Not Work — Here Is Proof

This is the reason multi-stage builds exist. It is worth proving to yourself:

```dockerfile
RUN pip install --no-cache-dir -r requirements.txt  # Layer A: pip + packages = 52MB
RUN pip uninstall pip setuptools wheel -y           # Layer B: removes files from view
```

Check the size — almost identical to before the uninstall. The bytes from Layer A are still in the image. Layer B only removes them from the *filesystem view* of the final container. They are still stored in Layer A, which is still part of the image.

Docker layers are immutable and additive. You cannot subtract from a previous layer. The only way to produce an image without those bytes is to never add them in the first place — which is exactly what the runtime stage does by starting fresh.

### How Docker Processes Multiple Stages

Each `FROM` creates a new stage with a completely clean filesystem:

```
Stage: builder
  Starts with: python:3.11.9-slim
  Adds: pip install results at /install
  Total filesystem: base OS + Python + pip + packages + build tools

  → Discarded after build. Never becomes an image unless explicitly tagged.

Stage: runtime
  Starts with: python:3.11.9-slim (fresh copy — does NOT inherit builder)
  Adds: /install contents copied from builder
  Adds: application code
  Total filesystem: base OS + Python + packages + code

  → This becomes the final image.
```

The runtime stage has no knowledge of what existed in the builder stage except what was explicitly copied. It is not a continuation — it is a fresh start that selectively imports artifacts.

### Build Cache Still Works Across Stages

Multi-stage builds do not sacrifice the caching strategy from step 14. Each stage has its own layer cache, independently:

```bash
# Edit app.py, rebuild
docker build -t backend:v1 ./backend

# => CACHED [builder 1/3] FROM python:3.11.9-slim
# => CACHED [builder 2/3] COPY requirements.txt .
# => CACHED [builder 3/3] RUN pip install          ← cached, requirements unchanged
# => CACHED [runtime 1/3] FROM python:3.11.9-slim
# => CACHED [runtime 2/3] RUN groupadd ...
# => [runtime 3/3] COPY --chown ... . .            ← only this reruns
```

Changing `app.py` only invalidates the final `COPY` in the runtime stage. The builder stage — including the pip install — is fully cached. You get all the caching benefits of step 14 across both stages independently.

### Targeting a Stage for Debugging

When something fails in the runtime stage and you cannot understand why, build only to the builder stage and inspect:

```bash
docker build --target builder -t backend:builder ./backend
docker run --rm -it backend:builder /bin/sh
```

You are now inside the build environment with full access to pip, the installed packages at `/install`, and all build tools. You can manually test whether packages installed correctly, check file paths, and debug before the runtime stage tries to use them. This flag is invaluable when multi-stage builds behave unexpectedly.

### Security — Reduced Attack Surface

Every tool in a container is a potential tool for an attacker. If someone exploits a vulnerability in the app and gets code execution inside the container, what can they do?

In the single-stage image: they have pip. They can run `pip install` and bring in any package — including malicious ones. They have build tools. They have more ways to escalate and move laterally.

In the multi-stage runtime image: build tools from the build process are gone. pip may still exist because it is part of the base image, but the installation process and its layers are no longer present. The set of things an attacker can do from inside the container is structurally smaller — not because of a security policy or firewall, but because those tools literally do not exist in the image. You cannot exploit a tool that is not there.

This is called attack surface reduction and it is one of the security arguments for multi-stage builds that matters in production audits and compliance reviews.

### The Pattern Scales to Compiled Languages

For Python the size improvement is meaningful but not dramatic — both stages use the same Python interpreter. For compiled languages the difference is extreme:

```dockerfile
# Stage 1: Build — needs Go compiler (~800MB)
FROM golang:1.22-alpine AS builder
WORKDIR /src
COPY . .
RUN go build -o app .

# Stage 2: Runtime — needs only the binary
FROM alpine:3.19 AS runtime
COPY --from=builder /src/app /app
CMD ["/app"]
```

Builder stage: ~800MB. Runtime image: ~10MB. The entire Go toolchain, standard library, and build cache — all discarded. The final image is just the compiled binary running on a minimal Alpine OS.

Python cannot reach this extreme because it is interpreted — the runtime still needs the Python interpreter. But the principle is identical. You only ship what is needed to run.



## 6. Commands

```bash
# ── Building ───────────────────────────────────────────────────────────────

docker compose up -d --build
docker build -t backend:v1 ./backend

# ── Targeting a Specific Stage ─────────────────────────────────────────────

docker build --target builder -t backend:builder ./backend
docker run --rm -it backend:builder /bin/sh      # debug the build environment

# ── Comparing Before and After ────────────────────────────────────────────

docker image ls                                  # compare sizes
docker history backend:v1                        # layers in runtime image only

# ── Verifying What Is Present and Absent ──────────────────────────────────

docker run --rm backend:v1 pip --version         # may still work (pip comes from base image)
docker run --rm backend:v1 python -c "import flask; print(flask.__version__)"  # should work
docker run --rm backend:v1 ls /usr/local/lib/python3.11/site-packages          # packages present

# ── Inspecting What the Builder Stage Has ────────────────────────────────

docker run --rm backend:builder ls /install
docker run --rm backend:builder ls /install/lib/python3.11/site-packages
docker run --rm backend:builder pip --version    # pip is here in builder
```



## 7. Real-World Notes

Multi-stage builds are standard practice in any serious Docker workflow — not an advanced technique. If you are writing a production Dockerfile and it is not multi-stage, that is the exception requiring justification, not the rule.

The size reduction matters operationally. Smaller images pull faster from registries to production servers, cost less in registry storage, and start faster when scaling. In a Kubernetes cluster scaling up ten new pods under load, the difference between pulling a 500MB image and a 150MB image is felt directly in how quickly those pods become ready.

In production security reviews and compliance audits (SOC 2, ISO 27001), unnecessary tools in container images are consistently flagged as findings. "Why does your production web server have a package installer?" is not a question you want to answer. Multi-stage builds make that question structurally impossible — the tools are not there to find.

Step 16 (image optimization) goes further — analysing layers with the `dive` tool, choosing between Alpine and slim base images, and reducing image size beyond what multi-stage alone achieves. The multi-stage structure from this step is the foundation that makes those optimizations effective.



## 8. Exercises

**Exercise 1 — Prove layer deletion does not work**
In the single-stage Dockerfile from step 14, add a second `RUN` instruction after pip install that uninstalls pip:
```dockerfile
RUN pip uninstall pip setuptools wheel -y
```
Rebuild and check the image size — almost unchanged. Check inside the container — pip is gone from the filesystem view but the image is the same size. Then remove that line and implement multi-stage instead. Compare sizes. This proves why multi-stage is the only real solution.

**Exercise 2 — Implement multi-stage and verify**
Write the two-stage Dockerfile from this step. Rebuild. Confirm: image is smaller, `pip --version` fails inside the runtime image, `import flask` succeeds, the app responds correctly on all endpoints. Run `docker history backend:v1` — you see only the runtime stage layers.

**Exercise 3 — See what the builder stage contains**
Build to the builder target and exec in:
```bash
docker build --target builder -t backend:builder ./backend
docker run --rm -it backend:builder /bin/sh
ls /install
ls /install/lib/python3.11/site-packages
pip --version
```
Then exec into the runtime image. pip is gone. The packages are there. You are seeing both sides of the `COPY --from` boundary directly.

**Exercise 4 — Verify caching across stages**
Build once to warm the cache. Edit `app.py` — change a response message. Rebuild and read the output line by line. Every builder stage layer should be `CACHED`. Only the final `COPY` in the runtime stage should rerun. Then edit `requirements.txt` — add a comment. Rebuild. Now the pip install layer invalidates and reruns. The caching strategy from step 14 is fully intact across two stages.

**Exercise 5 — Three-stage pattern**
Refactor the Dockerfile to use three stages — `base`, `builder`, `runtime` — where `base` holds the shared `groupadd/useradd` setup and both other stages inherit from it:
```dockerfile
FROM python:3.11.9-slim AS base
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

FROM base AS builder
...

FROM base AS runtime
...
```
Build and verify it works. Then change the `groupadd` command in `base` — observe that both downstream stages are invalidated. This is the three-stage pattern used in larger real-world projects to avoid duplicating shared setup.

**Exercise 6 — The Go example**
Create a minimal Go app to feel the extreme version of this pattern. Create `main.go`:
```go
package main
import "fmt"
func main() { fmt.Println("hello from go") }
```
Write the two-stage Dockerfile using `golang:1.22-alpine` as builder and `alpine:3.19` as runtime. Build it. Check the runtime image size — under 15MB. Check the golang base image size: `docker image ls golang`. The ratio between them is the full argument for why multi-stage builds exist for compiled languages.