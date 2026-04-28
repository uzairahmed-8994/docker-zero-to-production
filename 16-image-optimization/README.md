# 16 — Image Optimization



## 0. Goal of This Step

Understand what is still contributing to image size after multi-stage builds, learn how to inspect layers with precision, and make deliberate decisions about base images — including the tradeoffs that come with every choice.



## 1. What Problem It Solves

After step 15, the Dockerfile is multi-stage. The build tools are gone. The pip installation layer does not exist in the runtime image. The image is meaningfully smaller than where it started.

But if you check the actual size, it is still somewhere around 170–220MB. For an application that is essentially a Python web server with a handful of dependencies, that number feels large. The question is: where is it all coming from?

Most people at this point assume the problem is the application code or the dependencies. The app is small — a few Python files. Flask, Gunicorn, psycopg2 — those are not tiny, but they are not hundreds of megabytes either. So what is the rest?

The answer, almost always, is the base image. And the decision about which base image to use turns out to be one of the highest-leverage choices in the entire Dockerfile — with consequences that go well beyond size.



## 2. What Happened (Experience)

The multi-stage Dockerfile from step 15 was working correctly. The app ran, the layers were clean, the build cache was intact. I had done everything the previous steps recommended. But the image size still bothered me. I wanted to understand it precisely rather than accept it as "good enough."


**Step 1 — Looking at the numbers again**

```bash
docker image ls backend
```

```
REPOSITORY   TAG   SIZE
backend      v1    217MB
```

217MB. Multi-stage build, non-root user, no pip install layer in the final stage. Still 217MB.

I ran `docker history` to see the breakdown:

```bash
docker history backend:v1
```

```
IMAGE          CREATED BY                                      SIZE
a1b2c3d4       CMD ["gunicorn" ...]                            0B
<missing>      USER appuser                                    0B
<missing>      COPY --chown=appuser:appgroup . .               18.7kB
<missing>      COPY /install /usr/local                        15.4MB
<missing>      RUN groupadd --gid 1001 ...                     4.1kB
<missing>      WORKDIR /app                                    0B
<missing>      python:3.11.9-slim                              (base layers)
```

The application code was tiny: only 28.7kB. The installed runtime dependencies copied from the builder stage were 15.4MB according to docker history. The rest came from the Python base image layers.


To understand how much of the image actually belonged to my application versus the base image, I checked Docker’s disk usage:

```bash
docker system df -v
```
For backend:v1, Docker showed:

```
SIZE:        217MB  
SHARED SIZE: 147.2MB  
UNIQUE SIZE: 69.45MB  
```

This made the picture clearer. Most of the image size was coming from shared base image layers inherited from python:3.11.9-slim. My application-specific layers were much smaller.

That is the important discovery: after multi-stage builds, the application code is almost irrelevant to the total size. The base image dominates the final image size.


**Step 2 — Understanding what the base image actually contains**

I wanted to see what was inside python:3.11.9-slim, because Docker showed that a large portion of the final image came from shared base image layers. I exec'd into a fresh container and started looking:


```bash
docker run --rm -it python:3.11.9-slim /bin/bash
du -sh /usr/local/lib/python3.11/
# 36MB
du -sh /usr/local/bin/
# 52K
du -sh /usr/lib/
# 52MB
du -sh /usr/bin/
# 21MB
```
The exact numbers will vary slightly depending on the image version and build date. What matters is the distribution — a large portion of the size comes from the Python runtime and the underlying Debian system libraries.


The Python interpreter itself, the standard library, and the system libraries that Python depends on — they were all there. The -slim variant of the official Python image is already stripped of documentation, man pages, and non-essential apt packages. It is called "slim" because it is a reduced version of the full python:3.11.9 image, which is around 1GB. But slim still contains a full Debian base OS with glibc, bash, and everything a Debian system needs to function.


None of this is unnecessary — it is exactly what Python needs to run. But it is still a significant amount of software that becomes part of every container built on this base image.


**Step 3 — Trying to understand layers more precisely**

`docker history` shows layer sizes, but it shows them in reverse order and it does not always make it obvious which layers are the real contributors. I wanted a clearer view of layer composition.

There is a tool called `dive` that makes this significantly easier. It is not part of Docker — it is a separate CLI tool that you install on your machine:

On Ubuntu (including WSL2), you can install it using:

```bash
# Ubuntu
DIVE_VERSION=$(curl -sL "https://api.github.com/repos/wagoodman/dive/releases/latest" | grep '"tag_name":' | sed -E 's/.*"v([^"]+)".*/\1/')
curl -fOL "https://github.com/wagoodman/dive/releases/download/v${DIVE_VERSION}/dive_${DIVE_VERSION}_linux_amd64.deb"
sudo apt install ./dive_${DIVE_VERSION}_linux_amd64.deb
```

Once installed:

```bash
dive backend:v1
```

`dive` opens an interactive terminal UI that lets you navigate through each layer and see exactly which files were added, modified, or removed. You can see the actual contents of the filesystem at each layer boundary — not just the size number.

When I inspected the image using dive, the structure became much clearer. The total image size was around 217MB, and the amount of wasted space was minimal, which confirmed that the image was already efficient.

The majority of the size still came from the base image and system libraries. The application code and copied dependencies were small in comparison.

This made something very clear: even after optimizing the build process using multi-stage builds, the majority of the image size still comes from the base image.

The optimization in Step 15 removed unnecessary build-time artifacts, but it did not — and cannot — change what is included in the base image.

At this point, the question is no longer “how do I reduce my layers?”, but:

“What am I choosing as my base image, and what does it include?”


**Step 4 — The obvious next question**

If slim is already the reduced version, and it still contributes a large portion of the final image size, what would happen if I used something smaller?

The obvious candidate is Alpine Linux. Alpine is a minimal Linux distribution built around musl libc instead of glibc, and the BusyBox toolkit instead of GNU coreutils. The base Alpine image is around ~7MB. The Python Alpine image — `python:3.11.9-alpine` — is around ~55MB. That is less than half of what `python:3.11.9-slim` weighs.

The exact sizes vary depending on the image version and local cache, but the relative difference between full, slim, and alpine remains consistent.

I changed two lines in the Dockerfile:

```dockerfile
FROM python:3.11.9-alpine AS builder
FROM python:3.11.9-alpine AS runtime

# Also change this block in Dockerfile:
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

# To this block for Alpine:
RUN addgroup -g 1001 appgroup && \
    adduser -D -u 1001 -G appgroup appuser

# Note: Alpine uses `addgroup`/`adduser` instead of `groupadd`/`useradd` — different commands, same result.
    
```

now build:

```bash
docker compose up -d --build
```

The build succeeded. Image size:

```bash
docker image ls backend
```

```
REPOSITORY   TAG   SIZE
backend      v1    99.2MB
```

99MB. A real reduction. 

In this setup, the build completed successfully and the application started without errors.

However, this outcome is not universal. Alpine introduces a different runtime environment, and some dependencies may fail to build or run due to missing system libraries or compatibility differences. When that happens, additional packages or deeper troubleshooting are required to make the application work.


**Step 5 — The musl problem**

Alpine uses musl libc, while standard Python base images use glibc (via Debian). These are different implementations of the C standard library. The differences are usually small, but they can matter depending on the application.

In this setup, the build completed successfully and the application worked as expected. However, this depends on the dependencies being used.

On Alpine, some Python packages cannot use pre-built binaries and may fall back to compiling from source. This introduces additional requirements such as compilers and system libraries.

This has practical consequences:

- Builds can become slower due to compilation
- The environment may require additional system dependencies
- Some behaviour may differ under musl (for example DNS or locale handling)
- Debugging issues can be harder because musl/glibc differences are not always obvious

The size reduction comes from a smaller base OS and a more minimal runtime environment, but fewer tools and libraries are available by default.

For simple applications, Alpine works well. For more complex workloads with compiled dependencies, the added complexity may outweigh the size savings.

**Step 6 — Stepping back to understand the actual options**

After that experiment, I stopped trying to minimise the number and started trying to understand the decision:

```bash
# See the base image options and their sizes
docker pull python:3.11.9
docker pull python:3.11.9-slim
docker pull python:3.11.9-alpine

docker image ls python
```

```
REPOSITORY   TAG            SIZE
python       3.11.9         1.48GB
python       3.11.9-slim    198MB
python       3.11.9-alpine   81MB
```

The exact sizes vary depending on the image version and local cache, but the relative differences between full, slim, and alpine remain consistent.


Full image: ~1.48GB. Includes the full Debian environment, development headers, and tooling. Useful for development or debugging environments where maximum compatibility and utilities are required. Not suitable for production due to size.

Slim: ~198MB. Debian-based with glibc and the full Python runtime, but without unnecessary extras. Pre-built wheels install cleanly, and debugging tools are generally available. This is the most balanced and commonly used base for production.

Alpine: ~81MB. Musl-based and significantly smaller, but introduces a different runtime environment. Some dependencies may require additional setup or behave differently compared to glibc-based systems.

There is no universally correct choice. The decision depends on the application's dependencies, operational requirements, and tolerance for additional complexity.

**Step 7 — What I actually changed**

At this point, I stopped assuming and started verifying what was actually being included in the build.

The `.dockerignore` file was already in place, so instead of adding new rules, I checked whether it was doing its job correctly.

I inspected what ends up inside the container:


```bash
docker run --rm backend:v1 ls -la /app

# drwxr-xr-x    1 root     root          4096 Apr 28 07:49 .
# drwxr-xr-x    1 root     root          4096 Apr 28 09:37 ..
# -rw-r--r--    1 appuser  appgroup        89 Apr 27 13:24 .dockerignore
# -rw-r--r--    1 appuser  appgroup      1010 Apr 28 07:40 Dockerfile
# -rw-r--r--    1 appuser  appgroup      2661 Apr 27 13:24 app.py
# drwxr-xr-x    2 appuser  appgroup      4096 Apr 27 13:24 data
# -rw-r--r--    1 appuser  appgroup        52 Apr 27 13:24 requirements.txt
```

Only Python source files, configuration files, and requirements.txt were present. No .git directory, no local virtual environments, no editor files. The build context was already clean.

This confirmed that .dockerignore was working as intended. Even though these files would not have made it into the final image due to the multi-stage build, keeping the build context small still matters. Every build sends the context to the Docker daemon, and in CI/CD pipelines that run frequently, unnecessary data transfer adds up.

Next, I verified whether package installation was introducing hidden overhead. The dependencies accounted for most of the application layer size, which is expected. I also checked whether pip was leaving behind cache files:

```bash
docker run --rm backend:v1 du -sh /root/.cache/pip 2>/dev/null || echo "no cache"

# no cache
```
The output confirmed that no cache was present. This was already handled correctly by using --no-cache-dir during installation.

At this point, there was nothing obvious left to remove. The image was not large because of mistakes — it was large because of legitimate runtime requirements.



**Step 8 — The final picture**

After step 15 (multi-stage builds), the image reduced from ~255MB to ~217MB by removing build-time dependencies and unnecessary layers.

Switching to Alpine reduced it further:

```bash
docker image ls backend

# IMAGE        ID             DISK USAGE   CONTENT SIZE 
# backend:v1   d7fabd19bb67       99.2MB         24.6MB  

```
This was a significant reduction, achieved primarily by changing the base image rather than modifying the application itself.

The more important result was that I now understood exactly where every megabyte came from:

```
base image (Alpine):            ~80MB
installed packages:             ~20–30MB
application code:               ~0.01MB
additional layers & metadata:   small overhead
```

The application code is negligible. Most of the size comes from the base image and the installed dependencies.

This leads to a more important conclusion: reducing image size is not about aggressively removing files, but about choosing the right base image and understanding what your application actually needs.

Slim provides a predictable and fully compatible environment. Alpine provides a smaller footprint, but introduces a different runtime environment that may require additional consideration depending on the dependencies used.

There is no universally correct choice. Only tradeoffs between size, compatibility, and operational simplicity.



## 3. Why It Happens

Docker images are composed of layers. Every layer is a filesystem snapshot — the diff between the current state and the previous state. When you pull an image, every layer is pulled separately and cached. When you push an image, only layers that do not already exist in the registry are pushed.

The base image contributes the first layer (or multiple layers). Those layers include the operating system, the language runtime, and everything the image maintainer decided to include. You inherit all of it. There is no way to remove a layer from a base image — you can only add layers on top, or choose a different base.

This is why base image selection matters more than most individual build instructions. A single `FROM` line defines the floor from which all optimization begins.

Layers that add content add to the image size permanently. Layers that delete content do not recover that size — the deletion is itself recorded as a new layer, but the original bytes remain in the layer where they were created. This is the behaviour step 15 demonstrated when trying to remove pip after installing it. The same principle applies to any attempt to clean up in a later instruction.

The only way to produce a layer that contains "A installed but not B" is to install A in a stage where B never existed or to use a single `RUN` instruction that both installs and cleans up in the same layer, before Docker commits the diff. Both approaches have limits, but the second is relevant for one specific case: package manager caches.

When you run `apt-get install`, apt downloads packages to `/var/cache/apt/archives` and then installs them. If you run `apt-get install` in one `RUN` instruction and `rm -rf /var/cache/apt` in a separate `RUN`, the cache files remain in the layer where they were downloaded — the cleanup has no effect on image size. But if you do both in the same `RUN` instruction:

```dockerfile
RUN apt-get update && apt-get install -y libpq-dev && rm -rf /var/lib/apt/lists/*
```

The cache never gets committed to a layer because the cleanup happens before Docker takes the snapshot. The resulting layer contains the installed package but not the cache. This is why you see that pattern in almost every well-written Dockerfile.



## 4. Solution

The full optimization is not a single change — it is a set of decisions made in order, each building on the previous:

**1. Understand your baseline before changing anything**

```bash
docker history backend:v1
dive backend:v1   # if installed
```

Know where the bytes are before deciding what to reduce.

**2. Choose the right base image for your application**

`python:X.Y.Z-slim` provides a predictable and widely compatible environment based on Debian and glibc. Most Python packages work exactly as documented, and pre-built binaries install without additional effort.

`python:X.Y.Z-alpine` produces significantly smaller images, but introduces a different runtime environment. In this case, Alpine worked without issues and reduced the image size substantially. However, compatibility should always be verified based on the dependencies used.

There is no default choice — the correct base image depends on the balance between size, compatibility, and operational simplicity.


**3. Keep `.dockerignore` maintained**

Every file you do not exclude is included in the build context and can end up in your image if a COPY instruction is not precise enough.

```
.git
__pycache__
*.pyc
venv/
.env
*.md
.pytest_cache
```

This is a living file. It should be updated whenever the project structure changes.

**4. Keep system package installation minimal and clean**

For Debian-based images:

```dockerfile
# For Debian
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev && \
    rm -rf /var/lib/apt/lists/*
```

`--no-install-recommends` prevents apt from installing packages that were not explicitly requested. `rm -rf /var/lib/apt/lists/*` removes the package index after installation, since it is not needed at runtime and the index is regenerated by `apt-get update` when needed.

**5. Use pip's `--no-cache-dir` flag**

```dockerfile
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt
```

pip stores downloaded packages in a local cache directory. That cache is useful for repeated local installs but has no value in a build layer — the layer is immutable, and the next build starts fresh. Without `--no-cache-dir`, the cache adds size to the layer for no benefit.

**6. Be precise with COPY instructions**

Copying only what is needed keeps the layer size accurate. `COPY . .` copies everything not excluded by `.dockerignore`. For production images, it is often worth being more explicit:

```dockerfile
COPY --chown=appuser:appgroup app.py config.py ./
COPY --chown=appuser:appgroup templates/ templates/
```

This documents intent and prevents accidental inclusion of files that happen to not be in `.dockerignore`.



## 5. Deep Understanding

### Why Multi-Stage Alone Is Not Enough

The multi-stage build from step 15 solved the build-vs-runtime separation. But it did not change what the base image contributes, and it did not change how the installed packages are structured. After multi-stage builds, the remaining size is determined almost entirely by:

1. The base image
2. The installed packages
3. Any files copied into the image

All three are under your control, but they require different approaches. The base image is a selection decision. The installed packages are constrained by what your application actually needs — you cannot make them smaller without changing the application. Copied files are controlled by precise COPY instructions and a maintained `.dockerignore`.

The multi-stage build was the structural improvement. Image optimization is the precision work that follows.

### Layer Caching Interacts With Image Size

There is a tension between build speed and image size that is worth understanding. The layer cache works by hashing the instruction and its inputs. If nothing has changed, the cached layer is reused. This is fast and desirable during development.

But the cache also means that decisions made early in the Dockerfile — including the base image choice — are cached and rarely revisited. If you chose `python:3.11.9-slim` in week one and it got cached, you might not think about that choice again for months. Image optimization requires deliberately re-examining decisions that have been cached and accepted.

### Slim vs Alpine: The Real Tradeoff

The size difference between `python:3.11.9-slim` (130MB) and `python:3.11.9-alpine` (55MB) is real, but the comparison is incomplete without understanding what changes:

| Factor | python-slim | python-alpine |
|---|---|---|
| Base OS | Debian (glibc) | Alpine (musl) |
| C extension packages | Install from pre-built wheels | Must compile from source |
| Build complexity | Low | Higher (needs gcc, dev headers) |
| Debugging tools | Bash, standard utils available | Minimal shell, BusyBox |
| Compatibility | Very high | Requires testing |
| Image size | ~130-200MB base | ~55-80MB base |

The Alpine image can produce significantly smaller final images, but the result depends on the application’s dependencies. In this case, Alpine worked without issues and reduced the final image size substantially.

However, Alpine introduces a different runtime environment. Some dependencies may require additional system packages or behave differently compared to glibc-based systems. This does not always happen, but it must be verified rather than assumed.


For Python specifically, the musl/glibc difference is most likely to surface in:
- Packages with compiled components
- Applications that rely on specific locale or DNS behaviour
- Dependencies that expect a glibc-based environment


The practical approach is to start with a predictable base (such as `slim`), measure the image size, and then evaluate Alpine if the size reduction is meaningful. If Alpine works cleanly for the application, it can be a valid optimization. If not, the added complexity may outweigh the benefit.


### When Image Size Actually Matters

Not all image size is equal in terms of operational impact:

In CI/CD pipelines, the pull time of a base image matters on every build if the pipeline runner does not have a persistent layer cache. A registry that stores a 200MB image versus a 100MB image adds seconds to every deployment.

In autoscaling environments (Kubernetes, ECS), new pods pull the image before starting. If a spike in traffic triggers ten new pods simultaneously, the image pull time is on the critical path to those pods becoming ready and handling traffic. Smaller images mean faster scale-up response.

In environments with bandwidth constraints — some cloud regions, edge deployments, IoT — pull time is a hard constraint, not a preference.

In stable, single-instance deployments where images are pulled once and cached indefinitely, the size difference is nearly irrelevant.

Knowing which environment you are in changes which optimizations are worth the complexity cost.

### `dive` and What It Reveals

`dive` is useful beyond just seeing layer sizes. It shows you the file-level diff of every layer — what was added, what was modified, what was deleted. This reveals things that `docker history` does not:

- Files that were added in one layer and "deleted" in a later layer but still exist in the image
- Package manager caches that were not cleaned up
- Development headers or documentation that should not be in a production image
- Unexpected files from a COPY instruction that was too broad

Running `dive` on an image before declaring it production-ready is a good habit. It makes the composition of the image visible rather than assumed.

```bash
dive backend:v1
```

The image details section shows:

Total image size: ~65MB
Potential wasted space: ~611KB

This indicates that the image is highly efficient, with almost no wasted space. Nearly all layers contribute directly to the final runtime, which is typically considered an optimized result.

Images with very low wasted space indicate that layering is done correctly, with minimal leftover or discarded data from previous build steps.


## 6. Commands

```bash
# ── Inspecting Image Size and Layers ──────────────────────────────────────

docker image ls backend                          # see total image size
docker history backend:v1                        # show layer sizes in reverse order
dive backend:v1                                  # interactive layer and file explorer

# ── Comparing Base Images ─────────────────────────────────────────────────

docker pull python:3.11.9
docker pull python:3.11.9-slim
docker pull python:3.11.9-alpine
docker image ls python                           # compare sizes side by side

# ── Disk Usage Breakdown Inside a Container ───────────────────────────────

docker run --rm backend:v1 du -sh /usr/local/lib/python3.11/
docker run --rm backend:v1 du -sh /usr/local/lib/python3.11/site-packages/
docker run --rm backend:v1 du -sh /app

# ── Installing dive (Linux / WSL) ─────────────────────────────────────────

DIVE_VERSION=$(curl -sL "https://api.github.com/repos/wagoodman/dive/releases/latest" | grep '"tag_name":' | sed -E 's/.*"v([^"]+)".*/\1/')
curl -fOL "https://github.com/wagoodman/dive/releases/download/v${DIVE_VERSION}/dive_${DIVE_VERSION}_linux_amd64.deb"
sudo apt install ./dive_${DIVE_VERSION}_linux_amd64.deb

# ── Verifying Image Contents ──────────────────────────────────────────────

docker run --rm backend:v1 ls -la /app
```


## 7. Real-World Notes

Base image choice is one of the few Dockerfile decisions that tends to be made once and not revisited. In practice, most teams pick a base image when they first write the Dockerfile, it works, and it stays. Optimization reviews rarely happen unless something forces them — a deployment slowdown, a security audit finding, or a new engineer who asks why the image is so large.

The result is that production images in many organisations are significantly larger than they need to be, not because anyone made a bad decision, but because no one made a deliberate decision. The default image was used, the default worked, and the question of size was never asked.

In regulated environments, image size has a compliance dimension that goes beyond storage costs. Larger images contain more software, and every piece of software is a potential vulnerability. Security scanners like Trivy and Snyk report CVEs by package. An image built on full Debian with development headers will have more CVEs than an image built on slim — not because the application is less secure, but because there is more surface area to scan. Audit findings are proportional to surface area.

Alpine images do appear in production Python deployments. They work well for applications that are entirely pure-Python or that have a small, well-tested set of C-extension dependencies. They are the right choice when the operational constraints justify the compatibility testing cost. They are not automatically the right choice just because they are smaller.

The tools in this step — `docker history`, `dive`, careful `.dockerignore` maintenance — are not things you use once and discard. They are the ongoing visibility layer for your images. Reviewing them periodically, especially after adding new dependencies or changing base image versions, is the practice that keeps image size under control rather than quietly growing over months.



## 8. Exercises

**Exercise 1 — Identify your size contributors precisely**

Run `docker history backend:v1` and record every layer and its size. Calculate what percentage of the total image is the base image layer, what percentage is the installed packages layer, and what percentage is the application code. This exercise makes the composition visible in numbers, not abstractions.

**Exercise 2 — Explore the base image interactively**

Pull `python:3.11.9-slim` and exec into it:

```bash
docker run --rm -it python:3.11.9-slim /bin/bash
du -sh /usr/local/lib/python3.11/
du -sh /usr/local/bin/
du -sh /usr/lib/
```

Map the major size contributors. Understand what you are inheriting before you build on top of it. Then do the same with `python:3.11.9-alpine` and compare the results.

**Exercise 3 — Install and run dive**

Install `dive` on your machine. Run it against `backend:v1`. Navigate through the layers using the arrow keys. Find the layer where your application's dependencies were installed. Check the efficiency score at the bottom of the screen. Look for any files in the image that you did not expect to be there.


**Exercise 4 — Measure your build context**

Add `--progress=plain` to your docker build command and find the line that reports the build context transfer size. Then remove your `.dockerignore` entirely and rebuild. Compare the two context sizes. Re-add the `.dockerignore` and confirm it returns to the smaller size. This makes the effect of `.dockerignore` concrete rather than theoretical.


**Exercise 5 — Full optimization audit**

Take the Dockerfile from step 15 and produce two variants: one optimized for minimum image size (Alpine base, careful cleanup, precise COPY instructions) and one optimized for maximum compatibility and debugging ease (slim base, bash available, standard utilities). Build both. Document the size of each. Write down the tradeoff you are making when choosing one over the other. This is the exercise that turns the decision into a policy rather than a guess.