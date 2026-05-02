# 24 — Version Tagging



## 0. Goal of This Step

Understand how to tag Docker images in a way that makes deployments traceable, rollbacks possible, and the history of every image readable — not just as a naming convention, but as a system that connects images to the code and the time that produced them.



## 1. What Problem It Solves

In step 23, we pushed `backend:v1` to the registry. That tag works for a single deployment. But the moment you make a change, build a new image, and push it — what happens to `v1`? If you push `backend:v1` again after the change, you have overwritten the previous `v1`. The tag now points to the new image. The previous image still exists in the registry by its digest, but `v1` no longer means what it meant yesterday.

This creates two problems that only appear under pressure. The first is rollback. If the new deployment breaks something and you need to roll back, what do you deploy? If `v1` was overwritten, rolling back to `v1` deploys the broken version. You would need to remember the digest of the previous image — which no one wrote down. The second is auditability. If something went wrong last Tuesday, what image was running? If tags were reused, you cannot reconstruct the answer from the registry alone.

The solution is a tagging strategy — a deliberate system for assigning tags that makes every image uniquely identifiable, connects the image to the code that produced it, and preserves the ability to redeploy any previous version.

Beyond rollback and auditability, there is a subtler problem: multiple environments. The backend image running in development is not the same build that should run in staging, which is not the same build that should run in production. A tagging strategy is how you track which image is in which environment and how images are promoted from one to the next.



## 2. What Happened (Experience)

After pushing `backend:v1` to the registry in step 23, I made a small change to `app.py` — added a log statement — and rebuilt the image. Without thinking, I tagged it `backend:v1` again and pushed.

```bash
docker push myusername/backend:v1
```

```
The push refers to repository [docker.io/myusername/backend]
f9e8d7c6: Pushed
v1: digest: sha256:xyz789...   ← different digest than before
```

A different digest. `v1` now pointed to the new image. The previous image — the one that had been running on the staging server — was still in the registry by its old digest, but `v1` no longer referred to it. I had silently overwritten a tag that something was depending on.

I realised I needed to think about tagging properly before pushing anything else.

**Step 1 — Understanding what information a tag can carry**

A tag is a string. Docker places almost no constraints on its content. The question is not what Docker allows — it is what information is useful to encode in the tag so that anyone reading `docker image ls` or the registry's tag list can understand what they are looking at.

I thought about the questions I would want a tag to answer:

- What version of the application is this?
- When was this built?
- Which commit in the repository produced this image?
- Is this a release build or a development build?

A tag like `v1` answers the first question approximately. It answers none of the others. I started designing a tagging scheme that answered all four.

**Step 2 — Semantic versioning for release images**

Semantic versioning — `MAJOR.MINOR.PATCH` — is the most widely understood versioning scheme for software. For Docker images, it maps naturally: `v1.0.0` is an initial release, `v1.0.1` is a patch, `v1.1.0` is a minor feature addition, `v2.0.0` is a breaking change.

I rebuilt the backend image and tagged it properly:

```bash
docker build -t myusername/backend:v1.0.0 ./backend
```

Then I also tagged it with floating convenience references:

```bash
docker tag myusername/backend:v1.0.0 myusername/backend:v1.0
docker tag myusername/backend:v1.0.0 myusername/backend:v1
docker tag myusername/backend:v1.0.0 myusername/backend:latest
```

Four tags, one image. The relationship between them:

`v1.0.0` — immutable. Never moved. Points to this exact build forever.
`v1.0` — moves forward when `v1.0.1`, `v1.0.2` are released. Always the latest patch in the 1.0 series.
`v1` — moves forward when `v1.1.0`, `v1.2.0` are released. Always the latest minor in the v1 major.
`latest` — moves forward on every release. Always the newest stable build.

The convention: production deployments reference `v1.0.0`. Development environments reference `latest`. Staging references a specific minor version like `v1.0`. This way a production deployment is always pinned to a specific immutable tag.

**Step 3 — Git commit tags for CI builds**

Semantic versions are for releases. But between releases, the CI pipeline builds images on every commit. Those builds need tags too — and the natural tag for a CI build is the git commit hash that produced it.

I introduced this into the build process:

```bash
GIT_SHA=$(git rev-parse --short HEAD)
docker build -t myusername/backend:git-${GIT_SHA} ./backend
```

```bash
GIT_SHA=$(git rev-parse --short HEAD)
echo $GIT_SHA
# a1b2c3d
```

The image was now tagged `myusername/backend:git-a1b2c3d`. Every commit that goes through CI produces a uniquely tagged image. The tag is immutable by design — the git commit SHA never changes, so the tag never needs to move.

I pushed it:

```bash
docker push myusername/backend:git-a1b2c3d
```

Now I could answer "what code is in this image" by looking at the tag. `git show a1b2c3d` showed exactly what changed in that commit. The image and the source code were linked.

**Step 4 — Build timestamps for operational clarity**

Git SHAs are precise but not human-readable at a glance. When scanning a list of images in the registry, `git-a1b2c3d` does not immediately tell you whether this image is from this morning or from three weeks ago. I added a timestamp tag alongside the git tag:

```bash
BUILD_TIME=$(date -u +%Y%m%d-%H%M%S)
GIT_SHA=$(git rev-parse --short HEAD)

docker tag myusername/backend:git-${GIT_SHA} myusername/backend:${BUILD_TIME}-${GIT_SHA}
```

The resulting tag: `myusername/backend:20260428-103022-a1b2c3d`. Ugly, but immediately informative. From this tag alone I could tell the image was built on April 28th 2026 at 10:30 UTC from commit `a1b2c3d`. No registry UI, no metadata lookup needed.

I checked the full tag list in the registry:

```bash
curl http://localhost:5005/v2/backend/tags/list
```

```json
{
  "name": "backend",
  "tags": [
    "v1.0.0",
    "v1.0",
    "v1",
    "latest",
    "git-a1b2c3d",
    "20260428-103022-a1b2c3d"
  ]
}
```

Six tags, one image. Each tag served a different audience: `v1.0.0` for deployment scripts that need a stable pinned reference, `git-a1b2c3d` for developers tracing a bug back to a commit, `20260428-103022-a1b2c3d` for operators scanning the registry for stale images.

**Step 5 — Building the tagging into a script**

Running all these tag commands manually was error-prone. I missed the timestamp on one build and got the git SHA wrong on another. The tagging needed to be scripted.

I created a `build.sh` in the project root:

```bash
#!/bin/bash
set -e

IMAGE_NAME="myusername/backend"
GIT_SHA=$(git rev-parse --short HEAD)
BUILD_TIME=$(date -u +%Y%m%d-%H%M%S)
VERSION=${1:-"dev"}   # pass version as first argument, default to "dev"

echo "Building ${IMAGE_NAME}:${VERSION}"
echo "Git SHA: ${GIT_SHA}"
echo "Build time: ${BUILD_TIME}"

# Build once
docker build -t ${IMAGE_NAME}:${VERSION} ./backend

# Apply all tags
docker tag ${IMAGE_NAME}:${VERSION} ${IMAGE_NAME}:git-${GIT_SHA}
docker tag ${IMAGE_NAME}:${VERSION} ${IMAGE_NAME}:${BUILD_TIME}-${GIT_SHA}

# If this is a real version (not dev), also tag latest
if [ "${VERSION}" != "dev" ]; then
  docker tag ${IMAGE_NAME}:${VERSION} ${IMAGE_NAME}:latest
fi

echo "Tags applied:"
docker image ls ${IMAGE_NAME}

# Push all tags
docker push ${IMAGE_NAME}:${VERSION}
docker push ${IMAGE_NAME}:git-${GIT_SHA}
docker push ${IMAGE_NAME}:${BUILD_TIME}-${GIT_SHA}

if [ "${VERSION}" != "dev" ]; then
  docker push ${IMAGE_NAME}:latest
fi

echo "Done. Image: ${IMAGE_NAME}:${VERSION}"
echo "Digest: $(docker inspect ${IMAGE_NAME}:${VERSION} --format='{{index .RepoDigests 0}}')"
```

I ran it for a release:

```bash
chmod +x build.sh
./build.sh v1.0.1
```

```
Building myusername/backend:v1.0.1
Git SHA: b2c3d4e
Build time: 20260428-114500
Tags applied:
REPOSITORY           TAG                       IMAGE ID       SIZE
myusername/backend   v1.0.1                    e5f6a7b8c9d0   195MB
myusername/backend   git-b2c3d4e               e5f6a7b8c9d0   195MB
myusername/backend   20260428-114500-b2c3d4e   e5f6a7b8c9d0   195MB
myusername/backend   latest                    e5f6a7b8c9d0   195MB
Done. Image: myusername/backend:v1.0.1
Digest: myusername/backend@sha256:newdigest...
```

One command, consistent tagging, all pushes handled, digest printed at the end for logging.

**Step 6 — Updating docker-compose.yml to use the versioned tag**

With a proper versioning scheme in place, I updated `docker-compose.yml` to reference the exact version instead of a mutable tag. On the production server, the compose file referenced:

```yaml
backend:
  image: myusername/backend:v1.0.1
```

To deploy a new version, I changed the tag in the compose file to `v1.0.2`, committed the change, and ran `docker compose pull && docker compose up -d`. The change to the compose file was the deployment record — git history showed exactly when each version was deployed and who changed it.

**Step 7 — Verifying what actually ran**

After deployment, I verified which image was running by checking the digest:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{index .RepoDigests 0}}'
```

```
myusername/backend@sha256:newdigest...
```

And cross-referenced it against the registry:

```bash
docker manifest inspect myusername/backend:v1.0.1 | grep digest | head -1
```

Same digest. The container running on the server was exactly the image I had pushed. Not a rebuild, not a different version — the exact same bytes.



## 3. Why It Happens

Docker tags are mutable references stored in the registry's metadata. When you push a tag, the registry updates a pointer: this tag name now refers to this manifest digest. The old manifest is not deleted — it is orphaned. It still exists in the registry and can still be referenced by digest, but the tag no longer points to it.

This mutability is by design. Tags are meant to be movable — `latest` moves forward on every build, `v1.0` moves forward on every patch release. The immutability guarantee comes from digests, not from tags. A deployment that needs guaranteed reproducibility should reference a digest, not a tag.

The git commit SHA tagging convention works because git commits are immutable by design. The SHA is computed from the content of the commit — if anything changes, the SHA changes. A Docker tag derived from a git SHA inherits that immutability in practice: there is no reason to push a different image under the same git SHA tag, and doing so would be immediately visible as a discrepancy between the tag and the code.

Semantic versioning provides a human-readable layer on top of this. It communicates intent: `v1.0.1` is a patch on `v1.0.0`, compatible by convention, safe to upgrade to. `v2.0.0` signals a breaking change. Container deployments can read these signals and make automated decisions — "auto-update to the latest patch release" is a policy that semantic versioning makes expressible.



## 4. Solution

The complete tagging approach for this stack, encoded in a build script:

**`build.sh` in the project root:**

```bash
#!/bin/bash
set -e

IMAGE_NAME="${REGISTRY:-myusername}/backend"
GIT_SHA=$(git rev-parse --short HEAD)
BUILD_TIME=$(date -u +%Y%m%d-%H%M%S)
VERSION=${1:-"dev"}

echo "Building ${IMAGE_NAME}:${VERSION} from commit ${GIT_SHA}"

docker build -t ${IMAGE_NAME}:${VERSION} ./backend
docker tag ${IMAGE_NAME}:${VERSION} ${IMAGE_NAME}:git-${GIT_SHA}
docker tag ${IMAGE_NAME}:${VERSION} ${IMAGE_NAME}:${BUILD_TIME}-${GIT_SHA}

if [ "${VERSION}" != "dev" ]; then
  docker tag ${IMAGE_NAME}:${VERSION} ${IMAGE_NAME}:latest
fi

docker push ${IMAGE_NAME}:${VERSION}
docker push ${IMAGE_NAME}:git-${GIT_SHA}
docker push ${IMAGE_NAME}:${BUILD_TIME}-${GIT_SHA}

if [ "${VERSION}" != "dev" ]; then
  docker push ${IMAGE_NAME}:latest
fi

echo "Digest: $(docker inspect ${IMAGE_NAME}:${VERSION} --format='{{index .RepoDigests 0}}')"
```

**`docker-compose.yml` — pin to a specific version tag:**

```yaml
services:
  backend:
    image: myusername/backend:v1.0.1   # pinned — change this to deploy a new version
    # build: ./backend                  # commented out on production
    restart: on-failure
    # ... rest of config

  frontend:
    image: myusername/frontend:v1.0.1
    # build: ./frontend
    restart: on-failure
    # ... rest of config
```

**Deploying a new version:**

```bash
# 1. Build and push the new version
./build.sh v1.0.2

# 2. Update the image tag in docker-compose.yml
sed -i 's/backend:v1.0.1/backend:v1.0.2/' docker-compose.yml

# 3. Pull and redeploy
docker compose pull
docker compose up -d

# 4. Verify the correct image is running
docker inspect $(docker compose ps -q backend) \
  --format='{{index .RepoDigests 0}}'
```

**Rolling back:**

```bash
# Revert the image tag in docker-compose.yml to the previous version
sed -i 's/backend:v1.0.2/backend:v1.0.1/' docker-compose.yml

# Pull and redeploy the previous version
docker compose pull
docker compose up -d
```



## 5. Deep Understanding

### Tag Mutability — The Core Problem

Every versioning decision in this step flows from one fact: Docker tags are mutable. The implications are worth spelling out precisely.

When a tag is mutable, `docker pull myusername/backend:v1.0.0` is not guaranteed to return the same image on two consecutive days, even if the tag name suggests stability. If someone pushed a new image under `v1.0.0` between those two pulls, the second pull returns a different image. This is not theoretical — it happens when teams skip patch versioning and overwrite existing tags.

`docker pull myusername/backend@sha256:abc123...` is guaranteed. The registry will reject a push that tries to associate a different manifest with an existing digest. Digests are content-addressed — the digest is computed from the content, and the same digest always refers to the same content.

The practical takeaway: tag names are for human readability and operational convenience. Digests are the source of truth. Production deployments that matter should record the digest of what was deployed, even if they use a tag name for convenience during the pull.

### The Floating Tag Pattern

The four-tier tag structure — `v1.0.0`, `v1.0`, `v1`, `latest` — is a pattern borrowed from language runtime images. The official Python image uses exactly this: `python:3.11.9` is immutable, `python:3.11` floats to the latest 3.11.x, `python:3` floats to the latest Python 3.x.

The value of floating tags is that consumers can choose their own stability level. An automated testing environment that wants the latest code can pull `latest`. A staging environment that wants to test against the current minor version can pin to `v1.0`. A production environment that needs absolute stability pins to `v1.0.0`.

The risk of floating tags is exactly their value: they move. A deployment that pulled `v1.0` yesterday and pulls `v1.0` again today may get a different image. This is fine if that is the intent. It is a problem if it is not. The discipline is: know which tags in your system are floating and which are pinned, and ensure production environments only reference pinned tags.

### Git SHAs as the Link Between Code and Image

The `git rev-parse --short HEAD` pattern creates a traceable link between every image and the source code that produced it. This link is what makes post-incident analysis possible.

Given an image tag `git-a1b2c3d`:

```bash
# What code is in this image?
git show a1b2c3d

# What changed between two deployed images?
git log --oneline b2c3d4e..a1b2c3d

# When was this commit made?
git show -s --format="%ci" a1b2c3d

# Who authored this commit?
git show -s --format="%an <%ae>" a1b2c3d
```

Every question about a running image becomes answerable from the git history. The image tag is not just a name — it is an index into the source code history.

The `--short` flag produces a 7-character abbreviation of the full 40-character SHA. This is compact enough for tags while being unique enough for practical use — git guarantees short SHA uniqueness within a repository. For extra certainty, the full SHA can be used: `$(git rev-parse HEAD)`.

### Labels — Metadata Baked Into the Image

Beyond tags, Docker supports image labels — key-value metadata baked into the image at build time. Labels are accessible via `docker inspect` and are preserved through push and pull. They are the right place to store metadata that belongs with the image forever, not just as a tag name.

The OCI Image Specification defines standard label names:

```dockerfile
LABEL org.opencontainers.image.version="v1.0.1"
LABEL org.opencontainers.image.revision="a1b2c3d4e5f6"
LABEL org.opencontainers.image.created="2026-04-28T10:30:22Z"
LABEL org.opencontainers.image.source="https://github.com/myorg/myrepo"
LABEL org.opencontainers.image.title="Backend API"
```

These can be passed as build arguments to avoid hardcoding them in the Dockerfile:

```dockerfile
ARG VERSION
ARG GIT_SHA
ARG BUILD_TIME

LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.revision="${GIT_SHA}"
LABEL org.opencontainers.image.created="${BUILD_TIME}"
```

And passed during the build:

```bash
docker build \
  --build-arg VERSION=v1.0.1 \
  --build-arg GIT_SHA=$(git rev-parse HEAD) \
  --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  -t myusername/backend:v1.0.1 \
  ./backend
```

Reading the labels of a running container:

```bash
docker inspect $(docker compose ps -q backend) \
  --format='{{json .Config.Labels}}' | python -m json.tool
```

```json
{
  "org.opencontainers.image.version": "v1.0.1",
  "org.opencontainers.image.revision": "a1b2c3d4e5f6...",
  "org.opencontainers.image.created": "2026-04-28T10:30:22Z"
}
```

The metadata travels with the image. A container running on a server three months after the image was built still carries the exact version, commit, and build time in its labels.

### Image Retention and Tag Cleanup

Pushing many tags on every commit accumulates images in the registry. Left unchecked, a repository that has been building for a year with git SHA tags has thousands of images. Most registries provide lifecycle policies to clean up old images automatically.

The general rule: keep all semver release tags forever — storage is cheap relative to the ability to redeploy any release. Keep git SHA tags for 30–90 days — long enough to cover your incident response window. The `latest` tag is a pointer, not storage — moving it elsewhere costs nothing.

Docker Hub has automated retention features. AWS ECR and Google Artifact Registry have lifecycle policies that can delete images older than N days or keep only the N most recent images matching a pattern. Setting up a retention policy is part of the registry setup for any repository that receives frequent CI builds.



## 6. Commands

```bash
# ── Tagging ────────────────────────────────────────────────────────────────

# Semantic version tagging
docker tag backend:v1 myusername/backend:v1.0.0
docker tag backend:v1 myusername/backend:v1.0
docker tag backend:v1 myusername/backend:v1
docker tag backend:v1 myusername/backend:latest

# Git SHA tagging
GIT_SHA=$(git rev-parse --short HEAD)
docker tag backend:v1 myusername/backend:git-${GIT_SHA}

# Timestamp + SHA tagging
BUILD_TIME=$(date -u +%Y%m%d-%H%M%S)
docker tag backend:v1 myusername/backend:${BUILD_TIME}-${GIT_SHA}

# Build with multiple tags in one step
docker build \
  -t myusername/backend:v1.0.1 \
  -t myusername/backend:git-${GIT_SHA} \
  -t myusername/backend:latest \
  ./backend

# ── Inspecting Tags and Labels ─────────────────────────────────────────────

# List all local tags for a repository
docker image ls myusername/backend

# View labels on a local image
docker inspect myusername/backend:v1.0.1 \
  --format='{{json .Config.Labels}}' | python -m json.tool

# View labels on a running container
docker inspect $(docker compose ps -q backend) \
  --format='{{json .Config.Labels}}' | python -m json.tool

# Get the digest of a tagged image
docker inspect myusername/backend:v1.0.1 \
  --format='{{index .RepoDigests 0}}'

# ── Build With Labels ──────────────────────────────────────────────────────

docker build \
  --build-arg VERSION=v1.0.1 \
  --build-arg GIT_SHA=$(git rev-parse HEAD) \
  --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  -t myusername/backend:v1.0.1 \
  ./backend

# ── Registry Tag Listing ───────────────────────────────────────────────────

# List all tags in a private registry
curl http://localhost:5005/v2/backend/tags/list | python -m json.tool

# ── Deployment Workflow ────────────────────────────────────────────────────

# Build and push a new version
./build.sh v1.0.2

# Pull and redeploy
docker compose pull
docker compose up -d

# Verify the running image digest
docker inspect $(docker compose ps -q backend) \
  --format='{{index .RepoDigests 0}}'

# Rollback by reverting the tag in docker-compose.yml then:
docker compose pull && docker compose up -d
```



## 7. Real-World Notes

Tagging discipline is one of those things that feels like overhead until the first production incident where you need to roll back at 2am. At that moment, having `v1.0.1` as a stable, immutable tag that maps to a known-good image is the difference between a 2-minute rollback and a 45-minute archaeology session trying to find the right digest in the registry history.

Most teams start with `latest` and `v1` style tags and graduate to proper semantic versioning after their first tagging-related incident. The moment described at the opening of this step — where `v1` was silently overwritten — is the exact incident that converts teams. Building the tagging system correctly from the beginning is cheaper than retrofitting it under pressure.

In GitHub Actions, the git SHA and tag information is available as environment variables (`$GITHUB_SHA`, `$GITHUB_REF_NAME`) that can be passed directly to the build command. A properly configured workflow for building and pushing Docker images tags with the commit SHA on every push to main, and additionally tags with the semantic version when a git tag is pushed. The workflow replaces `build.sh` — but the tagging logic is identical. The script in this step is a stepping stone to understanding what the CI pipeline is doing.

The OCI image labels (`org.opencontainers.image.*`) have become the standard for image metadata. Tools like `docker scout`, `trivy`, and most registry UIs know how to read these labels and display them. An image with proper OCI labels shows its version, creation time, and source repository in the registry UI without any additional configuration. The ten lines of `LABEL` instructions in the Dockerfile pay dividends every time someone looks at the image in the registry six months after it was built.

One trap specific to `docker-compose.yml`: `image: myusername/backend:latest` in a compose file looks harmless but means `docker compose pull` will silently update the image to whatever `latest` currently points to. On a development machine, this is convenient. On a server, it means an unattended `docker compose pull && docker compose up -d` cron job can deploy untested code automatically. Compose files for servers should reference explicit version tags. `latest` in a production compose file is a latent deployment automation bug waiting for the wrong moment.



## 8. Exercises

**Exercise 1 — Observe the tag overwrite problem**

Build the backend image and push it as `v1.0.0`:

```bash
docker tag backend:v1 myusername/backend:v1.0.0
docker push myusername/backend:v1.0.0
```

Note the digest printed at the end of the push. Now make a trivial change to `app.py` — add a comment — rebuild, retag as `v1.0.0`, and push again:

```bash
docker compose build backend
docker tag backend:v1 myusername/backend:v1.0.0
docker push myusername/backend:v1.0.0
```

Note the new digest. The tag `v1.0.0` now points to the new image. The previous image still exists in the registry by its old digest but the tag no longer reaches it. This is the exact problem a proper tagging scheme prevents.

**Exercise 2 — Build the semantic version tag hierarchy**

Build the backend image and apply all four tiers of the floating tag pattern:

```bash
docker tag backend:v1 myusername/backend:v1.0.1
docker tag backend:v1 myusername/backend:v1.0
docker tag backend:v1 myusername/backend:v1
docker tag backend:v1 myusername/backend:latest
```

Verify all four point to the same image ID:

```bash
docker image ls myusername/backend
```

All four rows should show the same `IMAGE ID`. Push all four tags. Open the Docker Hub repository in a browser and check the Tags tab — confirm all four are listed with identical digests. This is the floating tag hierarchy made visible in the registry.

**Exercise 3 — Add git SHA tagging to your build**

Tag the backend image with the current git commit SHA:

```bash
GIT_SHA=$(git rev-parse --short HEAD)
echo "Current SHA: $GIT_SHA"
docker tag backend:v1 myusername/backend:git-${GIT_SHA}
docker push myusername/backend:git-${GIT_SHA}
```

Now trace the tag back to the commit:

```bash
git show ${GIT_SHA}
```

Read the commit message and changed files. This is the link between the image in the registry and the code that produced it — the link that makes post-incident analysis possible.

**Exercise 4 — Add OCI labels to the Dockerfile**

Add these lines to the backend `Dockerfile`, after the `FROM` line:

```dockerfile
ARG VERSION=dev
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown

LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.revision="${GIT_SHA}"
LABEL org.opencontainers.image.created="${BUILD_TIME}"
LABEL org.opencontainers.image.title="Backend API"
```

Build with the arguments:

```bash
docker build \
  --build-arg VERSION=v1.0.2 \
  --build-arg GIT_SHA=$(git rev-parse HEAD) \
  --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  -t myusername/backend:v1.0.2 \
  ./backend
```

Inspect the labels on the built image:

```bash
docker inspect myusername/backend:v1.0.2 \
  --format='{{json .Config.Labels}}' | python -m json.tool
```

Confirm the version, revision, and creation time are present. Now start a container from this image and inspect the labels on the running container — they should be identical. The metadata travels with the image from build through deployment.

**Exercise 5 — Write and test the build script**

Create `build.sh` in the project root with the script from the Solution section. Make it executable:

```bash
chmod +x build.sh
```

Run it for a dev build:

```bash
./build.sh dev
```

Run it for a versioned release:

```bash
./build.sh v1.0.3
```

Compare the tags produced by each run:

```bash
docker image ls myusername/backend
```

The `dev` run should produce `dev`, `git-<sha>`, and a timestamp tag but not `latest`. The `v1.0.3` run should produce all of those plus `latest`. Confirm all versioned tags point to the same `IMAGE ID`.

**Exercise 6 — Simulate a rollback using version tags**

Update `docker-compose.yml` to use `v1.0.2`:

```yaml
backend:
  image: myusername/backend:v1.0.2
```

Apply it and verify which image is running:

```bash
docker compose pull
docker compose up -d
docker inspect $(docker compose ps -q backend) \
  --format='{{index .RepoDigests 0}}'
```

Note the digest. Now simulate a bad deployment — switch to `v1.0.3` in `docker-compose.yml`, apply it, and confirm the digest changes. Then roll back:

```yaml
backend:
  image: myusername/backend:v1.0.2
```

```bash
docker compose pull
docker compose up -d
docker inspect $(docker compose ps -q backend) \
  --format='{{index .RepoDigests 0}}'
```

Confirm the digest matches `v1.0.2` again. The entire rollback was a tag change in a text file followed by two commands. No rebuilding, no source code needed — just the registry tags waiting.

**Exercise 7 — Read the full tag list and categorise it**

After completing the previous exercises, list all tags currently in your registry for the backend:

```bash
# For private registry:
curl http://localhost:5005/v2/backend/tags/list | python -m json.tool

# For Docker Hub: check the Tags tab in the browser
```

For every tag in the list, answer two questions: is this tag effectively immutable or floating? Would it be safe to reference this tag in a production `docker-compose.yml`?

Then group the tags into three buckets: keep forever (stable release tags), keep for 30–90 days (git SHA and timestamp tags), move on every push (floating tags like `latest`, `v1`, `v1.0`). This exercise connects the tag list to a retention policy — the thinking that informs lifecycle rules in a real registry.