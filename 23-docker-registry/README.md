# 23 — Docker Registry



## 0. Goal of This Step

Understand what a Docker registry is, how images move from a local build to a shared location, and how to push, pull, tag, and manage images using both Docker Hub and a private registry — so that images built on one machine can be used on another.



## 1. What Problem It Solves

Everything built so far has stayed on one machine. `docker compose build` produces an image. `docker compose up` runs it. The image lives in the local Docker image cache and nowhere else. If someone else on the team wants to run the same stack, they have to build it themselves from the source code. If you want to deploy the stack to a server, you have to either copy the source code there and build again, or find another way to get the image to that machine.

This is the gap a registry fills. A registry is a storage and distribution system for Docker images. You build the image once, push it to the registry, and then any machine with network access and the right credentials can pull and run it — without access to the source code, without a build step, without needing to install Python or any build tool.

This is how production deployments actually work. The CI/CD pipeline builds the image, pushes it to the registry with a version tag, and the deployment process pulls that exact image onto the production server. The image that was tested is the image that runs. No rebuilding, no "it worked on my machine" — the artifact is the image, and the registry is where artifacts live.

There is also a subtler problem this solves: image reproducibility. Even with a pinned base image tag, two separate `docker compose build` runs on different days can produce slightly different images if a dependency version has changed or if the build cache was cold. When the registry holds a specific tagged image, every deployment of that tag runs the exact same bytes that were tested.



## 2. What Happened (Experience)

The stack from step 22 was building clean, secure images locally. I started thinking about what it would take to run this stack on a different machine — a teammate's laptop, a staging server, a cloud VM. The answer was: everything. They would need the source code, the right Python version on the build machine, the same pip packages resolvable, and the same build cache to avoid long rebuild times.

I wanted to understand how images actually move between machines in real deployments.

**Step 1 — Understanding what already exists locally**

I looked at what images were in my local Docker cache:

```bash
docker image ls
```

```
REPOSITORY   TAG       IMAGE ID       CREATED         SIZE
backend      v1        a1b2c3d4e5f6   2 hours ago     195MB
frontend     latest    b2c3d4e5f6a7   2 hours ago     148MB
python       3.11.9-slim  c3d4e5f6a7b8  3 days ago   130MB
postgres     15        d4e5f6a7b8c9   5 days ago      379MB
```

These images existed only on this machine. No other machine knew they existed. The `backend:v1` image I had been building and hardening across the previous nine steps was trapped on my local disk.

I also noticed the tagging scheme. `backend:v1` was a reasonable local name. But if I were going to push this to a shared registry, the name needed to say more — specifically, it needed to say where the image came from and who it belonged to. A tag like `v1` has no namespace. On Docker Hub, image names follow the pattern `username/repository:tag`. On a private registry, they follow `registry-hostname/repository:tag`.

**Step 2 — Creating a Docker Hub account and repository**

Docker Hub is the default public registry that Docker uses when no registry is specified. When you run `docker pull python:3.11.9-slim`, Docker pulls from Docker Hub. It is free for public repositories and has a free tier for private repositories.

I created an account at `hub.docker.com` and noted my username — I will use `myusername` throughout this step as a placeholder. The actual username matters because it becomes part of the image name.

I created a repository called `backend` on Docker Hub through the web interface. The full image name would be `myusername/backend`.

**Step 3 — Tagging the image for the registry**

The local image was named `backend:v1`. To push it to Docker Hub under my account, it needed to be tagged with my username as the namespace:

```bash
docker tag backend:v1 myusername/backend:v1
```

`docker tag` does not copy the image — it creates a new name that points to the same image layers. Both `backend:v1` and `myusername/backend:v1` now referred to the same image ID. I confirmed:

```bash
docker image ls | grep backend
```

```
REPOSITORY           TAG   IMAGE ID       CREATED       SIZE
backend              v1    a1b2c3d4e5f6   2 hours ago   195MB
myusername/backend   v1    a1b2c3d4e5f6   2 hours ago   195MB
```

Same `IMAGE ID`. Same layers. Two names pointing to the same content.

I also tagged it as `latest` — the conventional tag for the most recent version:

```bash
docker tag backend:v1 myusername/backend:latest
```

The `latest` tag has no special meaning to Docker itself — it is just a tag like any other. But many tools and conventions default to pulling `latest` when no tag is specified. The convention is: `latest` points to the most recent stable build, and versioned tags like `v1` point to specific immutable releases.

**Step 4 — Logging in and pushing**

Before pushing, I needed to authenticate with Docker Hub:

```bash
docker login
```

Docker prompted for my username and password. After entering them, Docker stored the credentials in the local credential store. The login persisted across terminal sessions until I explicitly logged out.

I pushed the image:

```bash
docker push myusername/backend:v1
```

```
The push refers to repository [docker.io/myusername/backend]
f1a2b3c4d5e6: Pushing  52.4MB/127MB
a2b3c4d5e6f7: Pushed
b3c4d5e6f7a8: Layer already exists
c4d5e6f7a8b9: Layer already exists
d5e6f7a8b9c0: Layer already exists
v1: digest: sha256:abc123... size: 2847
```

Several layers showed `Layer already exists`. Those were the base image layers — `python:3.11.9-slim` was already on Docker Hub (it lives there), so Docker did not need to upload them. Only the layers that were unique to my image — the installed packages and the application code — were actually transferred. The push took about 20 seconds instead of the several minutes it would have taken to upload 195MB from scratch.

I pushed the `latest` tag as well:

```bash
docker push myusername/backend:latest
```

```
The push refers to repository [docker.io/myusername/backend]
v1: digest: sha256:abc123... size: 2847
```

Instant. Docker recognised that `latest` pointed to the same image as `v1` — the same digest — so there was nothing new to push. Only the tag reference was updated.

**Step 5 — Pulling the image on another machine**

I removed the local image to simulate a fresh machine:

```bash
docker rmi myusername/backend:v1 myusername/backend:latest backend:v1
```

And pulled the image from the registry:

```bash
docker pull myusername/backend:v1
```

```
v1: Pulling from myusername/backend
a1b2c3d4: Pull complete
b2c3d4e5: Pull complete
c3d4e5f6: Pull complete
d4e5f6a7: Pull complete
Digest: sha256:abc123...
Status: Downloaded newer image for myusername/backend:v1
```

The image came down. I ran it directly without any source code present:

```bash
docker run --rm -e DB_HOST=localhost myusername/backend:v1 python -c "import app; print('import ok')"
```

The import worked. The image contained everything needed to run the application — the Python interpreter, the installed packages, and the application code. No source code, no build step, no Python installation required on the host.

**Step 6 — Running a private registry locally**

Docker Hub is public by default. For images that should not be publicly accessible — proprietary code, internal tools, images containing environment-specific configuration — a private registry is necessary.

Docker provides an official registry image that can be run as a container. I started one:

```bash
docker run -d \
  --name local-registry \
  -p 5005:5000 \
  --restart unless-stopped \
  -v registry-data:/var/lib/registry \
  registry:2
```

This started a private registry on port 5005 of my local machine. The registry data was persisted in a named volume so images survived container restarts.

I verified it was running:

```bash
curl http://localhost:5005/v2/
```

```json
{}
```

An empty JSON object. The registry API was responding. It was ready to receive images.

I tagged the backend image for this private registry:

```bash
docker tag backend:v1 localhost:5005/backend:v1
```

The registry hostname and port are part of the image name. `localhost:5005/backend:v1` tells Docker to push to the registry at `localhost:5005`, into a repository called `backend`, with the tag `v1`.

I pushed it:

```bash
docker push localhost:5005/backend:v1
```

```
The push refers to repository [localhost:5005/backend]
f1a2b3c4: Pushed
a2b3c4d5: Pushed
v1: digest: sha256:def456... size: 2847
```

All layers pushed — the private registry did not already have any of these layers, unlike Docker Hub which had the base Python image.

I confirmed the image was in the registry:

```bash
curl http://localhost:5005/v2/backend/tags/list
```

```json
{"name":"backend","tags":["v1"]}
```

The registry confirmed the `backend` repository contained the `v1` tag. I deleted the local image and pulled from the private registry:

```bash
docker rmi localhost:5005/backend:v1
docker pull localhost:5005/backend:v1
```

```
v1: Pulling from backend
f1a2b3c4: Pull complete
a2b3c4d5: Pull complete
Digest: sha256:def456...
Status: Downloaded newer image for localhost:5005/backend:v1
```

The image came from the private registry. A team on the same network could push and pull from this registry without using Docker Hub at all.

**Step 7 — Understanding image digests and why they matter**

Throughout the push and pull operations, I had noticed the `digest: sha256:...` line appearing in output. I had glossed over it. I went back and looked at it properly.

A digest is a cryptographic hash of the image's content. Unlike a tag — which is a mutable pointer that can be updated to point to a different image — a digest is immutable. `myusername/backend@sha256:abc123...` will always refer to exactly one specific image. If the content changes, the digest changes. Two images with the same digest are byte-for-byte identical.

I pulled the image by digest instead of by tag:

```bash
docker pull myusername/backend@sha256:abc123...
```

This is the mechanism that makes deployments truly reproducible. A deployment script that references a digest is guaranteed to run the same image it ran last time, even if someone has pushed a new `v1` tag in the meantime. Tags are convenient for humans; digests are safe for automation.

I checked the digest of my local image:

```bash
docker inspect myusername/backend:v1 \
  --format='{{index .RepoDigests 0}}'
```

```
myusername/backend@sha256:abc123def456...
```

That string is the fully qualified, immutable reference to this exact image.



## 3. Why It Happens

A Docker registry is an HTTP server that implements the OCI Distribution Specification — a standard API for storing and retrieving container images. Docker Hub is an implementation of this spec. The private `registry:2` container is another. Amazon ECR, Google Artifact Registry, and GitHub Container Registry are others. They all speak the same protocol, so `docker push` and `docker pull` work identically against any of them.

Images are stored as a collection of layers plus a manifest. The manifest describes which layers make up the image and in what order. Each layer is a compressed archive of filesystem changes. When you push an image, Docker checks which layers already exist in the registry (by their content hash) and uploads only the ones that are missing. When you pull, Docker downloads only the layers it does not already have locally.

This layer deduplication is why pushes to Docker Hub are fast after the first push — the Python base image layers are already there. It is also why pulling a new version of an image is fast if the base layers are unchanged — only the top layers (application code, installed packages) need to download.

Tags are pointers in the registry's metadata store. They are mutable — anyone with push access can move a tag to point to a different image at any time. The `latest` tag is moved on every push by convention. This is why relying on `latest` in production is risky: the image behind `latest` may have changed since the last deployment. Versioned tags (`v1`, `v1.2.3`) are more stable but still mutable. Digests are the only truly immutable reference.



## 4. Solution

The complete workflow for building, tagging, pushing, and pulling images for this stack:

**Tagging for Docker Hub:**

```bash
# Tag with your Docker Hub username
docker tag backend:v1 myusername/backend:v1
docker tag backend:v1 myusername/backend:latest
docker tag frontend:latest myusername/frontend:v1
docker tag frontend:latest myusername/frontend:latest
```

**Pushing to Docker Hub:**

```bash
docker login
docker push myusername/backend:v1
docker push myusername/backend:latest
docker push myusername/frontend:v1
docker push myusername/frontend:latest
```

**Pulling on another machine:**

```bash
docker pull myusername/backend:v1
docker pull myusername/frontend:v1
```

**Running a private registry:**

```bash
docker run -d \
  --name local-registry \
  -p 5005:5000 \
  --restart unless-stopped \
  -v registry-data:/var/lib/registry \
  registry:2
```

**Tagging and pushing to private registry:**

```bash
docker tag backend:v1 localhost:5005/backend:v1
docker push localhost:5005/backend:v1
```

**Updating docker-compose.yml to pull from registry instead of building locally:**

```yaml
services:
  backend:
    image: myusername/backend:v1   # pull from registry
    # build: ./backend             # commented out — no build on this machine
    restart: on-failure
    # ... rest of config

  frontend:
    image: myusername/frontend:v1
    # build: ./frontend
    restart: on-failure
    # ... rest of config
```

When `image` is specified without `build`, `docker compose up` pulls the image from the registry instead of building locally. This is the deployment model: the server has no source code and no build tooling — it only runs `docker compose up` with a compose file that references registry images.



## 5. Deep Understanding

### The Registry API — What Happens During Push and Pull

When you run `docker push myusername/backend:v1`, Docker performs this sequence:

1. Calculates the digest of each layer in the image
2. Sends a `HEAD` request to the registry for each layer: "do you already have this?"
3. For layers the registry already has: skips the upload (`Layer already exists`)
4. For layers the registry does not have: uploads them in chunks via `POST` and `PUT`
5. Uploads the image manifest — the JSON document that lists which layers make up this image
6. Updates the tag to point to the new manifest

`docker pull` is the reverse: fetch the manifest for the tag, check which layers you already have locally, download the ones you are missing.

This protocol is why Docker is efficient with bandwidth. A 500MB image with a 450MB base layer only transfers 50MB on the second push, because the base layer is already in the registry.

### Tagging Conventions That Teams Actually Use

Tags are arbitrary strings. Docker places no constraints on what a tag can contain (beyond basic character restrictions). The conventions that work in practice:

`latest` — the most recently built image. Updated on every push to the main branch. Used for development environments that want the newest code without caring about a specific version.

`v1`, `v2` — major version tags. Moved forward when breaking changes are introduced. Stable enough for production but not immutable.

`v1.2.3` — semantic version tags. Each deployment gets a unique tag. Never moved after creation. The right choice for production deployments where you want to know exactly what is running and roll back to a specific version.

`git-abc1234` — commit hash tags. Every commit that passes CI gets its own tag derived from the git commit SHA. Provides a direct link between the running image and the source code that produced it. Used by teams that want to trace every deployed image back to a specific commit.

For this stack, a practical convention: `v1.2.3` for releases, `git-$(git rev-parse --short HEAD)` for CI builds, `latest` always pointing to the most recent main branch build.

### Public vs Private Registries

Docker Hub has a free tier that allows one private repository and unlimited public repositories. Public images are visible to anyone. For open source projects, this is fine. For proprietary applications, anything in a public repository is visible to the world — including the application code baked into the image.

For private images, the options are:

**Docker Hub private repositories** — simplest, requires a paid plan for multiple private repos.

**GitHub Container Registry (ghcr.io)** — free for public, included with GitHub for private. Integrates naturally with GitHub Actions CI/CD.

**Amazon ECR / Google Artifact Registry / Azure Container Registry** — cloud provider registries, integrated with their respective cloud platforms. The right choice when the deployment target is ECS, GKE, or AKS.

**Self-hosted (`registry:2`)** — full control, no per-image cost, but requires maintenance. Suitable for air-gapped environments or teams that cannot use external registries due to compliance requirements.

The push/pull workflow is identical across all of them. The differences are in authentication, pricing, access control, and integration with CI/CD platforms.

### Image Layers and the Build Cache Relationship

The layers in a registry image correspond directly to the layers in the local build cache. When you pull an image, Docker stores the layers in the same local cache that the build system uses. A `docker pull python:3.11.9-slim` followed by a `docker build` that uses that base image will reuse the pulled layers as a cache — the `FROM python:3.11.9-slim` step shows `CACHED` because the layer is already local.

This is how teams share build caches across machines. By pushing intermediate build stages to the registry and using `--cache-from` on subsequent builds, you can pre-warm the build cache on CI machines that have never built the image before:

```bash
docker build \
  --cache-from myusername/backend:latest \
  -t myusername/backend:v1.2.3 \
  ./backend
```

On a fresh CI runner with no local cache, `--cache-from` pulls the layers from the registry and uses them as cache. Builds that would have taken 3 minutes without a cache complete in 30 seconds.

### The Difference Between `build` and `image` in Compose

`docker-compose.yml` supports two modes for getting an image into a container:

```yaml
# Mode 1 — build locally
backend:
  build: ./backend
  image: backend:v1

# Mode 2 — pull from registry
backend:
  image: myusername/backend:v1
```

When both `build` and `image` are specified (as in step 14), `build` defines how to build the image and `image` defines what to name it. Running `docker compose build` builds and names the image. Running `docker compose up` uses the named image if it exists locally, builds it if it does not.

When only `image` is specified with no `build`, `docker compose up` attempts to pull the image from the registry if it is not in the local cache. This is the deployment model: a `docker-compose.yml` on the production server with no source code next to it, referencing fully qualified registry image names.

The practical implication: the compose file in the development repository references `build: ./backend`. The compose file used in production (or in CI deployment) replaces that with `image: myusername/backend:v1.2.3`. Many teams maintain a separate `docker-compose.prod.yml` for this reason, or use environment variable substitution to switch between build and image modes.



## 6. Commands

```bash
# ── Tagging Images ─────────────────────────────────────────────────────────

docker tag backend:v1 myusername/backend:v1          # tag for Docker Hub
docker tag backend:v1 myusername/backend:latest       # also tag as latest
docker tag backend:v1 localhost:5005/backend:v1       # tag for private registry

# ── Authenticating ─────────────────────────────────────────────────────────

docker login                                          # Docker Hub (prompts for credentials)
docker login ghcr.io                                  # GitHub Container Registry
docker login localhost:5005                           # private registry
docker logout                                         # remove stored credentials

# ── Pushing Images ─────────────────────────────────────────────────────────

docker push myusername/backend:v1
docker push myusername/backend:latest
docker push localhost:5005/backend:v1

# ── Pulling Images ─────────────────────────────────────────────────────────

docker pull myusername/backend:v1
docker pull myusername/backend:latest
docker pull myusername/backend@sha256:abc123...       # pull by immutable digest

# ── Inspecting Registry Contents ──────────────────────────────────────────

# List tags in a private registry repository
curl http://localhost:5005/v2/backend/tags/list

# List all repositories in a private registry
curl http://localhost:5005/v2/_catalog

# Get the manifest for a specific tag
curl http://localhost:5005/v2/backend/manifests/v1

# ── Working With Digests ───────────────────────────────────────────────────

# Get the digest of a local image
docker inspect myusername/backend:v1 \
  --format='{{index .RepoDigests 0}}'

# Get digest from the registry directly (Docker Hub)
docker manifest inspect myusername/backend:v1 | grep digest | head -1

# ── Running a Private Registry ─────────────────────────────────────────────

docker run -d \
  --name local-registry \
  -p 5005:5000 \
  --restart unless-stopped \
  -v registry-data:/var/lib/registry \
  registry:2

# Check registry health
curl http://localhost:5005/v2/

# Stop and remove
docker stop local-registry && docker rm local-registry

# ── Cleaning Up Local Images ───────────────────────────────────────────────

docker rmi myusername/backend:v1                      # remove a specific tag
docker image prune                                    # remove dangling images
docker image prune -a                                 # remove all unused images (careful)
```



## 7. Real-World Notes

The registry is the boundary between building and deploying. Everything before the registry push is development. Everything after the registry pull is operations. The image in the registry is the artifact that travels through CI/CD, gets promoted from staging to production, gets rolled back when something goes wrong, and gets audited when someone asks "what exactly was running on Tuesday at 3pm?" The answer is always a digest.

In real CI/CD pipelines, the build step almost always includes three pushes: a commit-specific tag (`git-abc1234`), a branch tag (`main`), and a version tag if this is a release commit (`v1.2.3`). The commit tag is immutable and provides traceability. The branch tag is mutable and provides "latest from this branch" for development environments. The version tag is reserved for production deployments.

Private registries in production almost always require authentication. The `docker login` credentials need to be available on every machine that runs `docker compose up` — including CI servers, staging machines, and production servers. Managing these credentials is part of the deployment infrastructure. Most teams use the cloud provider's registry (ECR, GCR, ACR) specifically because authentication is handled automatically by the cloud platform's IAM system — no credentials to rotate or distribute.

The `registry:2` container is powerful for local team use but ships with no authentication and no TLS by default. On a local machine or a private network, this is acceptable. Exposed to the public internet, it is not — anyone who can reach the registry can push and pull any image. Adding TLS and authentication to `registry:2` requires a reverse proxy (nginx or Traefik) and a TLS certificate. For production private registries with a public IP, using a managed service is almost always the right choice over self-hosting `registry:2`.

One workflow trap to be aware of: `docker compose up --build` on a machine that has both `build` and `image` specified will rebuild the image locally and overwrite the tag. If a production server has source code present and someone runs `docker compose up --build`, they may inadvertently replace the tested registry image with a locally built one. Production servers should not have source code present, and `docker compose up` on a production server should never be run with `--build`. The registry image is the contract; rebuilding locally breaks it.



## 8. Exercises

**Exercise 1 — Examine what is in your local image cache**

Run:

```bash
docker image ls
docker image ls --digests
```

Note the difference between the two outputs. The second shows the digest for images that have been pushed to or pulled from a registry. Images that were only built locally show `<none>` in the digest column — they have no registry identity yet. This is the visual distinction between a local build and a registry-backed image.

**Exercise 2 — Tag and push your backend image to Docker Hub**

Create a Docker Hub account if you do not have one. Create a repository called `backend`. Then:

```bash
docker tag backend:v1 yourusername/backend:v1
docker tag backend:v1 yourusername/backend:latest
docker login
docker push yourusername/backend:v1
docker push yourusername/backend:latest
```

Open Docker Hub in a browser and confirm the image is visible in your repository. Check the `Tags` tab — both tags should be listed with the same digest. This confirms that `latest` and `v1` point to the same underlying image.

**Exercise 3 — Simulate pulling on a fresh machine**

Remove the images you just pushed from your local cache:

```bash
docker rmi yourusername/backend:v1 yourusername/backend:latest
docker image ls | grep backend
```

The `yourusername/backend` images should be gone (the `backend:v1` local image remains). Now pull from the registry:

```bash
docker pull yourusername/backend:v1
```

Watch the layer download output. Notice which layers are downloaded versus which are already local. Run the pulled image:

```bash
docker run --rm yourusername/backend:v1 python --version
```

The image runs without building from source. This is the production pull model.

**Exercise 4 — Start a private registry and use it**

Start the private registry container:

```bash
docker run -d \
  --name local-registry \
  -p 5005:5000 \
  --restart unless-stopped \
  -v registry-data:/var/lib/registry \
  registry:2
```

Verify it is running:

```bash
curl http://localhost:5005/v2/
```

Tag and push the backend image:

```bash
docker tag backend:v1 localhost:5005/backend:v1
docker push localhost:5005/backend:v1
```

Confirm it arrived:

```bash
curl http://localhost:5005/v2/backend/tags/list
```

Remove the local tag and pull from the private registry:

```bash
docker rmi localhost:5005/backend:v1
docker pull localhost:5005/backend:v1
```

The private registry served the image. No Docker Hub account required, no external network access.

**Exercise 5 — Explore layer deduplication**

Push the backend image to the private registry:

```bash
docker push localhost:5005/backend:v1
```

Note which layers show `Pushed` and how long the push took. Now make a small change to `app.py` — add a comment — rebuild, and push again:

```bash
docker compose build backend
docker tag backend:v1 localhost:5005/backend:v2
docker push localhost:5005/backend:v2
```

Note which layers show `Layer already exists` and how much faster the second push is. The base image layers, the dependency layer, and every layer that did not change were skipped. Only the final `COPY . .` layer — the application code — was re-uploaded. This is layer deduplication in action.

**Exercise 6 — Pull by digest instead of tag**

Get the digest of your pushed image:

```bash
docker inspect yourusername/backend:v1 \
  --format='{{index .RepoDigests 0}}'
```

Copy the full `yourusername/backend@sha256:...` string. Remove the local image:

```bash
docker rmi yourusername/backend:v1
```

Now pull by digest:

```bash
docker pull yourusername/backend@sha256:<your-digest-here>
```

The image is pulled. It has no tag — check with `docker image ls`. It shows `<none>` in the tag column. The image exists but has no mutable name. Re-tag it:

```bash
docker tag yourusername/backend@sha256:<your-digest> yourusername/backend:v1
```

This exercise makes the difference between tags and digests concrete: the digest identified the exact image even after the tag was removed.

**Exercise 7 — Update docker-compose.yml to use registry images**

Create a copy of `docker-compose.yml` called `docker-compose.deploy.yml`. In this file, replace the `build` directive for backend and frontend with `image` directives pointing to your registry:

```yaml
backend:
  image: yourusername/backend:v1
  # build: ./backend  ← removed

frontend:
  image: yourusername/frontend:v1
  # build: ./frontend  ← removed
```

Remove the local backend and frontend images:

```bash
docker rmi backend:v1 frontend:latest
```

Start the stack using the deploy compose file:

```bash
docker compose -f docker-compose.deploy.yml up -d
```

Docker should pull both images from the registry and start the stack without building anything locally. Verify it works:

```bash
docker compose -f docker-compose.deploy.yml ps
curl http://localhost:5000/health
```

This is the production deployment model: a compose file with registry image references, no source code required, no build step on the deployment machine