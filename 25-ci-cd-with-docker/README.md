# 25 — CI/CD with Docker



## 0. Goal of This Step

Understand how a CI/CD pipeline takes the manual steps built across the previous steps — build, test, tag, push, deploy — and runs them automatically on every code change, so that the path from a commit to a running deployment is consistent, auditable, and requires no human intervention.



## 1. What Problem It Solves

Across steps 14 through 24, the workflow for getting a code change into production looked like this: edit `app.py`, run `docker compose build`, run `./build.sh v1.0.x`, update the tag in `docker-compose.yml`, push the compose file change, SSH into the server, run `docker compose pull && docker compose up -d`, verify the health check. Every step was manual, run from a local machine, dependent on the developer having the right credentials, and entirely undocumented unless someone wrote it down.

This works when there is one developer and one server. It breaks in several ways as the project grows. A second developer does not know which steps to run or in which order. A deployment at 11pm means someone has to be at their laptop. A mistake in the build step — wrong tag, skipped vulnerability scan, untested image — goes straight to production with nothing catching it. And there is no record of what was deployed, when, or by whom, beyond whatever the developer remembers to write in a commit message.

CI/CD — Continuous Integration and Continuous Deployment — is the practice of automating this entire path. The pipeline is the written-down version of every manual step. It runs the same way on every push, by every developer, at any hour, and it produces a log of exactly what happened. The question "what exactly ran when we deployed v1.0.4 last Thursday?" has a precise answer: the pipeline log.

For Docker specifically, CI/CD solves one more problem that the previous steps left open: the `build.sh` script runs on a developer's laptop, with that developer's local Docker cache, that developer's version of the source code, and that developer's credentials. Two developers running `build.sh` on the same commit can produce different images if their local environments differ in any way. A CI pipeline runs in a clean, controlled environment on every push — the build is reproducible because the environment is reproducible.



## 2. What Happened (Experience)

The project had a working `build.sh`, a proper tagging scheme, a registry, and a server running the stack. The last manual step was the deployment itself. I had been SSHing into the server and running `docker compose pull && docker compose up -d` by hand. I started thinking about what it would take to make that happen automatically every time a change was pushed to the main branch.

I chose GitHub Actions as the CI/CD platform — it is free for public repositories, requires no separate server, and the workflow files live in the repository alongside the code they build. The concepts transfer directly to GitLab CI, Jenkins, CircleCI, or any other platform.

**Step 1 — Understanding what a pipeline is**

Before writing any workflow file, I thought about what the pipeline needed to do and in what order:

1. Something triggers the pipeline — a push to `main`, a pull request, a version tag
2. The code is checked out on a fresh runner machine
3. The Docker image is built from that code
4. The image is tested — at minimum, verify it starts and the health check passes
5. The image is scanned for vulnerabilities
6. The image is tagged and pushed to the registry
7. The deployment target is updated to use the new image

Each of these was a step I had already done manually. The pipeline was the same steps, in the same order, written as code instead of memorised procedure.

**Step 2 — Creating the workflow file**

GitHub Actions workflows live in `.github/workflows/`. I created the directory and a workflow file:

```bash
mkdir -p .github/workflows
touch .github/workflows/docker-build-push.yml
```

I started with the trigger and the basic structure:

```yaml
name: Build and Push Docker Image

on:
  push:
    branches:
      - main
  pull_request:
    branches:
      - main

jobs:
  build:
    runs-on: ubuntu-latest
```

`on.push.branches: [main]` — run this pipeline whenever code is pushed to the main branch.
`on.pull_request.branches: [main]` — also run it on pull requests targeting main, but without the push step (the image is built and tested but not pushed to the registry until the PR is merged).
`runs-on: ubuntu-latest` — run the job on a fresh Ubuntu virtual machine provided by GitHub.

**Step 3 — Adding the build steps**

I added the first set of steps — checking out the code, setting up Docker, and building the image:

```yaml
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build backend image
        run: |
          docker build -t backend:test ./backend
```

`actions/checkout@v4` checks out the repository onto the runner. Without this, the runner has no source code.

`docker/setup-buildx-action@v3` configures Docker Buildx — the extended build toolkit that supports build caching, multi-platform builds, and other features. Even for a basic build, using Buildx enables the cache features in a later step.

The build step runs `docker build` exactly as it was run locally — the same Dockerfile, the same context. The tag `backend:test` is a local-only tag used for testing before the production push.

**Step 4 — Testing the image before pushing**

A pipeline that builds and immediately pushes without testing is only slightly better than no pipeline at all — it automates the mistake along with the correct deployment. I added a test step that verified the image actually worked:

```yaml
      - name: Test backend image
        run: |
          docker run -d \
            --name backend-test \
            -e DB_HOST=localhost \
            -e DB_PORT=5432 \
            -e DB_NAME=testdb \
            -e DB_USER=testuser \
            -e DB_PASSWORD=testpass \
            -p 5000:5000 \
            backend:test

          # Wait for the container to start
          sleep 5

          # Check the health endpoint
          docker run --rm --network host curlimages/curl:latest \
            curl -f http://localhost:5000/health || \
            (docker logs backend-test && exit 1)

          docker stop backend-test
          docker rm backend-test
```

This test did not connect to a real database — the backend would fail to initialise and the health endpoint would not respond if the database connection was the only thing checked. But it confirmed that the image built correctly, Gunicorn started, and the health endpoint was reachable. A failed health check would print the container logs and fail the pipeline step, stopping the workflow before the push.

For a more complete test, a `docker-compose.test.yml` file can bring up the full stack — backend, frontend, and a test database — run integration tests against it, and tear it down. That is the next step after the basic health check works.

**Step 5 — Adding the registry login and push**

The push step needed credentials. I added the Docker Hub username and password as GitHub repository secrets — values stored encrypted in GitHub that are injected into the pipeline environment at runtime, never written in the workflow file:

In GitHub: Settings → Secrets and variables → Actions → New repository secret:
- `DOCKERHUB_USERNAME` = `myusername`
- `DOCKERHUB_TOKEN` = a Docker Hub access token (not the account password — a token generated in Docker Hub settings under Security)

Then added the login and push steps to the workflow:

```yaml
      - name: Log in to Docker Hub
        if: github.event_name == 'push'
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      - name: Extract metadata for tags
        if: github.event_name == 'push'
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ secrets.DOCKERHUB_USERNAME }}/backend
          tags: |
            type=sha,prefix=git-
            type=raw,value=latest,enable={{is_default_branch}}

      - name: Build and push backend image
        if: github.event_name == 'push'
        uses: docker/build-push-action@v5
        with:
          context: ./backend
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

`if: github.event_name == 'push'` — this step only runs on pushes to main, not on pull requests. Pull requests build and test but do not push to the registry.

`docker/metadata-action@v5` generates tags automatically from the git context. `type=sha,prefix=git-` produces `git-a1b2c3d` from the commit SHA. `type=raw,value=latest` applies the `latest` tag when the push is to the default branch. This is the same tagging logic from `build.sh`, implemented as a pipeline action.

`cache-from: type=gha` and `cache-to: type=gha,mode=max` use GitHub Actions' built-in cache to persist Docker layer cache between pipeline runs. The first build on a cold runner takes the full time. Subsequent builds that have not changed the base image or the dependencies hit the cache and complete in seconds.

**Step 6 — Watching the first pipeline run**

I committed the workflow file and pushed to main:

```bash
git add .github/workflows/docker-build-push.yml
git commit -m "Add CI/CD pipeline for Docker build and push"
git push origin main
```

In the GitHub repository, I opened the Actions tab. The workflow appeared immediately — GitHub had detected the push and started the pipeline. I watched the steps execute in real time:

```
✓ Checkout code           (2s)
✓ Set up Docker Buildx    (3s)
✓ Build backend image     (47s)
✓ Test backend image      (8s)
✓ Log in to Docker Hub    (1s)
✓ Extract metadata        (1s)
✓ Build and push backend  (23s)
```

Total: 85 seconds from push to image in the registry. The image appeared on Docker Hub tagged with the commit SHA and as `latest`.

I made a small change to `app.py` and pushed again. This time:

```
✓ Checkout code           (2s)
✓ Set up Docker Buildx    (3s)
✓ Build backend image     (8s)   ← cache hit on base and dependency layers
✓ Test backend image      (6s)
✓ Log in to Docker Hub    (1s)
✓ Extract metadata        (1s)
✓ Build and push backend  (6s)   ← only changed layers uploaded
```

Total: 27 seconds. The build cache made the second run three times faster. Only the application code layer was rebuilt and pushed.

**Step 7 — Adding automated deployment**

The pipeline built and pushed the image. The deployment step — updating the server — was still manual. I extended the workflow to SSH into the server and run the deployment after a successful push:

```yaml
  deploy:
    needs: build
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'

    steps:
      - name: Deploy to server
        uses: appleboy/ssh-action@v1.0.0
        with:
          host: ${{ secrets.SERVER_HOST }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SERVER_SSH_KEY }}
          script: |
            cd /opt/myapp
            docker compose pull
            docker compose up -d
            docker compose ps
```

I added three more secrets to the repository: `SERVER_HOST` (the server's IP address), `SERVER_USER` (the SSH username), and `SERVER_SSH_KEY` (the private SSH key for that user on that server).

The `needs: build` directive made the deploy job wait for the build job to complete successfully before running. A failed build meant no deployment. A failed test meant no deployment. Only a fully successful build-test-push sequence triggered the deployment.

After the deploy job ran, the server was running the new image. The entire path — from `git push` on my laptop to the new image serving traffic on the server — was automated and took under two minutes.

**Step 8 — Understanding what changed about the workflow**

I compared the before and after:

Before: edit code → build locally → test manually → tag → push → SSH to server → pull → restart → verify.

After: edit code → push to git. Everything else happened automatically, in a documented, reproducible environment, with a log of every step.

The pipeline also introduced a gate that did not exist before: the test step. Locally, I had sometimes skipped the health check verification when in a hurry. The pipeline never skipped it. Every push was tested the same way.



## 3. Why It Happens

A CI/CD pipeline is a program that runs other programs. GitHub Actions executes workflow YAML files in response to git events. Each step in the workflow is either a shell command (`run:`) or a pre-built action (`uses:`). The runner machine is a fresh virtual machine for every pipeline run — nothing carries over from previous runs except what is explicitly cached.

This freshness is the key property. A developer's local machine accumulates state over months: a different version of a dependency installed manually, a build cache that includes an old layer, an environment variable set in `.bashrc` that the application accidentally depends on. The CI runner has none of this. It starts clean, it installs only what the workflow specifies, it builds in a controlled environment. If the build passes on CI, it built cleanly.

Docker's layer cache integrates with CI through the `cache-from` and `cache-to` build arguments. The GitHub Actions cache backend stores Docker layer tarballs between pipeline runs. When a new runner starts and pulls the cache, it downloads the previously cached layers and uses them as a build cache — achieving the same cache hit rate as a local developer machine, without the stale state.

Secrets in CI pipelines are the production-safe version of the `.env` file from step 22. They are stored encrypted, are never written to logs, and are scoped to the repository. The workflow file can be committed publicly; the secrets it references are never visible even to repository contributors.



## 4. Solution

The complete workflow file for building, testing, and deploying the backend image:

**`.github/workflows/docker-build-push.yml`:**

```yaml
name: Build, Test, and Deploy

on:
  push:
    branches:
      - main
  pull_request:
    branches:
      - main

jobs:
  build:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build backend image for testing
        uses: docker/build-push-action@v5
        with:
          context: ./backend
          load: true
          tags: backend:test
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Test backend image
        run: |
          docker run -d --name backend-test -p 5000:5000 \
            -e DB_HOST=localhost \
            -e DB_PORT=5432 \
            -e DB_NAME=testdb \
            -e DB_USER=testuser \
            -e DB_PASSWORD=testpass \
            backend:test
          sleep 5
          docker run --rm --network host curlimages/curl:latest \
            curl -f http://localhost:5000/health || \
            (docker logs backend-test && exit 1)
          docker stop backend-test && docker rm backend-test

      - name: Log in to Docker Hub
        if: github.event_name == 'push'
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      - name: Extract metadata for Docker tags
        if: github.event_name == 'push'
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ secrets.DOCKERHUB_USERNAME }}/backend
          tags: |
            type=sha,prefix=git-
            type=raw,value=latest,enable={{is_default_branch}}

      - name: Build and push backend image
        if: github.event_name == 'push'
        uses: docker/build-push-action@v5
        with:
          context: ./backend
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy:
    needs: build
    runs-on: ubuntu-latest
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'

    steps:
      - name: Deploy to server
        uses: appleboy/ssh-action@v1.0.0
        with:
          host: ${{ secrets.SERVER_HOST }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SERVER_SSH_KEY }}
          script: |
            cd /opt/myapp
            docker compose pull
            docker compose up -d
            docker compose ps
```

**Required GitHub repository secrets:**

```
DOCKERHUB_USERNAME   — your Docker Hub username
DOCKERHUB_TOKEN      — Docker Hub access token (not account password)
SERVER_HOST          — deployment server IP or hostname
SERVER_USER          — SSH username on the server
SERVER_SSH_KEY       — private SSH key for the server user
```

**`docker-compose.yml` on the server — reference `latest` for auto-deployment:**

```yaml
backend:
  image: myusername/backend:latest   # pipeline updates latest on every main push
  restart: on-failure
  # ... rest of config
```



## 5. Deep Understanding

### The Pipeline as Documentation

A CI/CD workflow file is the executable documentation of the deployment process. Every step that was previously in someone's head, in a Notion page, or in a Slack message is now in a YAML file committed to the repository. New team members can read the workflow file and understand exactly how the application gets built and deployed — without asking anyone.

This is a deeper value than just automation. Documentation written in prose drifts — it describes how things were supposed to work, not necessarily how they do work. A workflow file cannot drift from reality: if it describes the wrong steps, the pipeline fails. The workflow file is always the accurate description of the current deployment process because it is the deployment process.

### Build Jobs vs Deploy Jobs — Why They Are Separate

The workflow in this step has two jobs: `build` and `deploy`. They could be combined into one job, but separating them is deliberate.

A build job runs on every push and every pull request. It builds, tests, and — on main branch pushes — pushes the image. It has no access to the server and no ability to affect the running production environment.

A deploy job runs only on successful main branch builds. It has access to server credentials. It runs after the build job succeeds, meaning it only runs when the image was built and tested successfully.

This separation means pull requests can be tested without any risk of accidentally deploying to production. It also means the deploy job can be restricted by branch protection rules — requiring code review approval before a push to main is allowed, which gates the deployment behind a human review.

### Cache Strategy — Why It Matters at Scale

Without build caching, every CI run installs the full base image, reinstalls all Python dependencies, and copies all application code from scratch. For this stack, that is 130MB of base image plus 54MB of packages on every single pipeline run.

With `cache-from: type=gha`, GitHub Actions stores each Docker layer as a separate cache entry. On subsequent runs, unchanged layers are restored from cache in seconds rather than rebuilt or re-downloaded. A pipeline that took 90 seconds cold takes 25 seconds warm — the difference is almost entirely the base image and dependency layers hitting the cache.

At scale, this matters economically. GitHub Actions bills by the minute for private repositories. A team pushing 20 times per day with a 90-second cold pipeline uses 30 minutes of CI time per day. The same team with a 25-second warm pipeline uses 8 minutes. The cache pays for itself quickly.

The `cache-to: type=gha,mode=max` setting saves all layers to cache, including intermediate layers from the build stage in multi-stage builds. `mode=max` is more aggressive than the default and produces better cache hit rates, at the cost of more cache storage.

### Pull Request Checks vs Main Branch Deployments

The `if: github.event_name == 'push'` condition on the push and deploy steps creates a meaningful distinction:

On a pull request: the pipeline builds and tests the image but does not push to the registry and does not deploy. The pull request author and reviewers can see that the build passed. The image is tested but not published.

On a merge to main: the pipeline builds, tests, pushes the image with the commit SHA and `latest` tags, and deploys to the server. The merge to main is the deployment decision.

This pattern enforces the principle that production deployments happen through the main branch, not from developer laptops. The only way to deploy is to push code that passes the pipeline.

### Version Tag Deployments

The workflow above deploys automatically on every push to main, always using `latest`. For teams that want more control — deploying specific versions rather than every commit — a second workflow trigger handles git version tags:

```yaml
on:
  push:
    tags:
      - 'v*.*.*'
```

When a git tag like `v1.0.2` is pushed, this triggers a separate workflow that builds the image, tags it with the semver version (`v1.0.2`, `v1.0`, `v1`, `latest`), and deploys it. Day-to-day commits to main go through the automated pipeline without production deployment. Production deployments are triggered by intentionally pushing a version tag.

This two-pipeline pattern — continuous integration on every commit, continuous deployment on version tags — is the most common approach for teams that want automation without deploying every single commit to production.

### What the Deploy Step Actually Does

The deploy job SSHs into the server and runs three commands:

```bash
docker compose pull    # pull the new image(s) from the registry
docker compose up -d   # restart any containers whose image has changed
docker compose ps      # print the current state for the pipeline log
```

`docker compose up -d` only restarts containers whose image digest has changed. If the frontend image did not change in this commit, the frontend container is not restarted. Only the backend container is updated. This minimises downtime — only the changed services cycle.

The `docker compose ps` at the end prints the container status to the pipeline log. If a container fails to start after the update, the status shows `exited` rather than `running`, and that output is visible in the pipeline log. It is a basic sanity check that the deployment succeeded.



## 6. Commands

```bash
# ── Local Development Workflow (unchanged) ─────────────────────────────────

docker compose build
docker compose up -d
docker compose logs -f

# ── Triggering the Pipeline ────────────────────────────────────────────────

git add .
git commit -m "Your change description"
git push origin main                          # triggers the pipeline

# Creating a version tag (triggers version deployment pipeline if configured)
git tag v1.0.2
git push origin v1.0.2

# ── Watching Pipeline Output ───────────────────────────────────────────────

# Via GitHub UI: repository → Actions tab → click the running workflow
# Via GitHub CLI:
gh run list                                   # list recent pipeline runs
gh run watch                                  # watch the current run live
gh run view <run-id>                          # view a specific run's output
gh run view <run-id> --log                    # full log output

# ── Managing Secrets ───────────────────────────────────────────────────────

# Via GitHub CLI:
gh secret set DOCKERHUB_USERNAME
gh secret set DOCKERHUB_TOKEN
gh secret set SERVER_HOST
gh secret set SERVER_USER
gh secret set SERVER_SSH_KEY < ~/.ssh/id_rsa  # pipe private key from file

# List configured secrets (names only — values are never readable)
gh secret list

# ── Verifying the Deployed Image ───────────────────────────────────────────

# On the server — confirm which image is running
docker inspect $(docker compose ps -q backend) \
  --format='{{index .RepoDigests 0}}'

# Cross-reference with the pipeline's pushed tag
docker manifest inspect myusername/backend:latest | grep digest | head -1

# ── Debugging a Failed Pipeline ────────────────────────────────────────────

# Reproduce the failing step locally:
docker build -t backend:test ./backend
docker run -d --name backend-test -p 5000:5000 \
  -e DB_HOST=localhost -e DB_PORT=5432 \
  -e DB_NAME=testdb -e DB_USER=testuser -e DB_PASSWORD=testpass \
  backend:test
docker logs backend-test
curl -f http://localhost:5000/health
docker stop backend-test && docker rm backend-test
```



## 7. Real-World Notes

The gap between "automated build" and "automated deployment" is where most teams spend their first year of CI/CD. Building and pushing the image automatically is step one. Deploying it automatically — with confidence — requires enough test coverage that a passing pipeline is actually meaningful. A pipeline that builds the image, runs no tests, and deploys to production is faster than manual deployment and just as risky.

The test step in this workflow is minimal — a health check against a container without a database. In a real application, the tests would be more comprehensive: unit tests run inside the container, integration tests run against a docker-compose test environment with a real database, API tests against the full stack. The pipeline structure is the same; only the contents of the test step change.

Many teams start with GitHub Actions because it is free and requires no infrastructure. The workflow concepts — triggers, jobs, steps, secrets, caching — are nearly identical across GitLab CI (`.gitlab-ci.yml`), CircleCI, Jenkins, and every other major platform. Learning GitHub Actions is learning CI/CD; the syntax is the only thing that changes between platforms.

The `appleboy/ssh-action` for deployment is simple and works well for a single server. For multiple servers, blue-green deployments, or rolling updates, the deploy step becomes more complex — but it is still just shell commands that happen to run over SSH. The complexity of the deployment strategy lives in those shell commands, not in the CI platform.

The pipeline log is a deployment audit trail. Every run records who triggered it (the git commit author), what commit hash was built, which steps passed and failed, and how long each step took. When something goes wrong in production, the pipeline log for the last deployment is the first place to look. This audit trail is one of the most underappreciated benefits of CI/CD — not automation, but accountability.



## 8. Exercises

**Exercise 1 — Read a workflow file before writing one**

Find any open-source project on GitHub that uses Docker. Navigate to the `.github/workflows/` directory and read the workflow file for their Docker build. Identify: what event triggers the pipeline, how the image is tagged, whether there is a test step, and how credentials are handled. The patterns you find will almost certainly match what was built in this step. Reading someone else's working pipeline before writing your own builds intuition for the structure.

**Exercise 2 — Create the workflow file and trigger a first run**

Create `.github/workflows/docker-build-push.yml` with the workflow from the Solution section. Commit it and push to main:

```bash
git add .github/workflows/docker-build-push.yml
git commit -m "Add CI/CD pipeline"
git push origin main
```

Open the Actions tab in your GitHub repository and watch the pipeline run. For each step, note the time it took. Identify which step was slowest. After the run completes, check Docker Hub — the image should appear with the git SHA tag and `latest`.

**Exercise 3 — Observe the cache benefit**

After the first pipeline run completes, make a trivial change to `app.py` — add a comment — and push:

```bash
git add backend/app.py
git commit -m "Add comment to test cache"
git push origin main
```

Watch the second pipeline run. Compare the build step time to the first run. The base image and dependency layers should show cache hits, making the build significantly faster. The `Build and push backend image` step should also be faster — only the changed layer needs uploading. Record both times — the difference is the value of the layer cache.

**Exercise 4 — Test the pull request gate**

Create a new branch, make a change, and open a pull request:

```bash
git checkout -b test-pr-gate
echo "# test" >> backend/app.py
git add backend/app.py
git commit -m "Test PR pipeline gate"
git push origin test-pr-gate
```

Open a pull request on GitHub. The pipeline runs automatically. Check the Actions tab — the build and test steps should run, but the push and deploy steps should be skipped (because `if: github.event_name == 'push'` is false for a PR). The PR page shows the pipeline status. Merge the PR and observe the full pipeline run on the resulting push to main.

**Exercise 5 — Deliberately break the test step and watch it gate the deployment**

Add this to a route in `app.py` to make the health check return 500:

```python
@app.route("/health")
def health():
    return jsonify({"status": "error"}), 500
```

Commit and push. Watch the pipeline fail at the test step:

```
✗ Test backend image
  curl: (22) The requested URL returned error: 500
```

The pipeline stops. The push step does not run. The server is not updated. The broken image never reached the registry. Revert the health route and push again — the pipeline passes and the deployment proceeds. This exercise demonstrates the test step as a gate: breaking production by pushing broken code requires either bypassing the pipeline or having no tests at all.

**Exercise 6 — Add the deploy job and watch the full automated deployment**

Add the `deploy` job to the workflow file and configure the three server secrets (`SERVER_HOST`, `SERVER_USER`, `SERVER_SSH_KEY`). Push the updated workflow:

```bash
git add .github/workflows/docker-build-push.yml
git commit -m "Add automated deployment to pipeline"
git push origin main
```

Watch the full pipeline run in the Actions tab. After the build job completes, the deploy job should start automatically. Watch it connect to the server, pull the new image, and restart the containers. When it finishes, SSH into the server and verify the running image digest matches what the pipeline pushed.

**Exercise 7 — Read the pipeline as an audit trail**

After running several pipeline runs from the previous exercises, open the Actions tab in GitHub and look at the history. For each run, identify: what commit triggered it, which steps passed and failed, how long the total run took, and whether a deployment happened.

Now answer this question from the pipeline history alone: what was the last successful deployment, which commit did it deploy, and what time did it reach the server? This is the audit trail. In a production incident, this information determines whether the deployment was the cause of the problem and what would need to be rolled back. The pipeline log is the answer to "what changed and when" — without it, those questions are answered by memory, which is unreliable.