# 12 — Bind Mounts



## 0. Goal of This Step

Understand what bind mounts are, how they differ from named volumes, when to use one over the other, and how bind mounts unlock a development workflow where code changes reflect inside the container instantly — without rebuilding the image.


## 1. What Problem It Solves

Named volumes solve the data persistence problem — data survives container restarts. But they introduce a different friction during development: every time you change your code, you have to rebuild the image and recreate the container to see the change.

```bash
# Current dev workflow without bind mounts:
# 1. Edit app.py
# 2. docker compose up -d --build   (wait for build)
# 3. Test the change
# 4. Edit app.py again
# 5. docker compose up -d --build   (wait again)
# 6. Repeat forever
```

For a small Flask app this takes a few seconds. For a larger application with many dependencies it can take minutes. Multiply that by dozens of code changes per hour and the friction adds up fast.

Bind mounts solve this by mounting a directory from your host machine directly into the container. When you edit a file on your laptop, the container sees the change immediately because it is reading from your laptop's filesystem, not from a copy baked into the image.


## 2. What Happened (Experience)

Starting with the same backend Flask app. Currently the workflow is: edit code → rebuild image → recreate container → test. Let's feel how slow this is and then fix it with a bind mount.


**Step 1 — Feel the rebuild friction**

Make a small change to `backend/app.py` — change the message in the `/` route:

```python
@app.route("/")
def home():
    return jsonify({
        "message": "Hello from Backend - version 2",  # changed
        "hostname": socket.gethostname()
    })
```

Apply it:

```bash
docker compose up -d --build
```

Watch it rebuild the entire image just for a one-line change. Test it:

```bash
curl http://localhost:5000/
# {"hostname": "...", "message": "Hello from Backend - version 2"}
```

Works — but slow. Now imagine doing this twenty times in an afternoon.



**Step 2 — Add a bind mount**

Update `docker-compose.yml` to mount the local `backend/` directory into the container:

```yaml
services:
  backend:
    build: ./backend
    ports:
      - "5000:5000"
    volumes:
      - ./backend:/app          # bind mount: host path : container path
      - backend-data:/app/data  # named volume still used for persistent data

volumes:
  backend-data:
```

Bring it up:

```bash
docker compose up -d
```

Test it:

```bash
curl http://localhost:5000/
# {"hostname": "...", "message": "Hello from Backend - version 2"}
```

Now make another change to `app.py`. Change the message again:

```python
"message": "Hello from Backend - live reload",
```

Do NOT rebuild. Just wait a moment and test again:

```bash
curl http://localhost:5000/
```

So still shows the old message. The file changed on disk but Flask is not picking it up because it does not watch for file changes by default.



**Step 3 — Enable Flask's reloader**

Flask has a built-in development server with auto-reload. Update the `CMD` in `backend/Dockerfile` — or better, pass the environment variable through Compose:

```yaml
services:
  backend:
    build: ./backend
    ports:
      - "5000:5000"
    environment:
      - FLASK_ENV=development
      - FLASK_DEBUG=1
    volumes:
      - ./backend:/app
      - backend-data:/app/data

volumes:
  backend-data:
```

Rebuild once to apply the environment change:

```bash
docker compose up -d --build
```

Now change the message in `app.py` again:

```python
"message": "Hello from Backend - instant",
```

Save the file. Check the container logs:

```bash
docker compose logs -f backend
# * Detected change in '/app/app.py', reloading
# * Restarting with stat
# * Debugger is active!
```

Test immediately:

```bash
curl http://localhost:5000/
# {"hostname": "...", "message": "Hello from Backend - instant"}
```

The change appeared without any rebuild. You edited a file on your laptop and the running container picked it up in under a second. This is the bind mount development workflow.

---

**Step 4 — Understand what is actually happening**

The bind mount `./backend:/app` means: take the `backend/` directory from your host machine and make it appear inside the container at `/app`. It is not a copy — it is the same files, accessed through the container's filesystem. When you save `app.py` in your editor, the container immediately sees the new version because it is reading the same file from your disk.

This also means the bind mount **overrides** whatever was in the image at `/app`. The image built the files into `/app` — but the bind mount shadows that completely. The container now reads from your host filesystem instead.

---

## 3. Why It Happens

Named volumes are Docker-managed storage. Docker decides where the data lives on the host (under `/var/lib/docker/volumes/`). You do not control the path.

Bind mounts are the opposite — you specify an exact path on your host machine, and Docker mounts it directly into the container at the path you choose. No copying, no Docker-managed storage. Just a direct link between your host filesystem and the container's filesystem.

```
Named volume:
  Container /app/data  →  Docker managed  →  /var/lib/docker/volumes/project_backend-data/_data
  (Docker controls where it is on the host)

Bind mount:
  Container /app  →  directly linked  →  /home/uzair/devops/projects/.../backend/
  (You control exactly which host path it maps to)
```

Because a bind mount is a direct link, any change on either side — host or container — is instantly visible on the other side. Edit a file in VS Code on your laptop, the container sees it immediately. If the container writes a file to `/app`, you will find it in your `backend/` folder.

---

## 4. Solution

**The bind mount development setup:**

```yaml
services:
  backend:
    build: ./backend
    ports:
      - "5000:5000"
    environment:
      - FLASK_DEBUG=1
    volumes:
      - ./backend:/app           # bind mount for live code
      - backend-data:/app/data   # named volume for persistent data

  frontend:
    build: ./frontend
    ports:
      - "5001:5001"
    environment:
      - BACKEND_URL=http://backend:5000
      - FLASK_DEBUG=1
    volumes:
      - ./frontend:/app          # bind mount for frontend code too

volumes:
  backend-data:
```

**The syntax difference between bind mounts and named volumes:**

```yaml
volumes:
  - ./backend:/app         # bind mount  — starts with . or /  (a path)
  - backend-data:/app/data # named volume — starts with a name  (no slash)
```

Compose tells them apart by the left side of the colon. If it starts with `.` or `/`, it is a bind mount to a host path. If it starts with a plain name, it is a named volume.

---

## 5. Deep Understanding

### Bind Mounts Override Image Content

This is the most important thing to understand about bind mounts and it catches people off guard.

Your Dockerfile copies files into the image:

```dockerfile
COPY . .    # copies backend/ into /app in the image
```

Then your Compose file mounts a bind mount at the same path:

```yaml
volumes:
  - ./backend:/app
```

The bind mount **completely replaces** what the image has at `/app`. The files that were baked into the image are hidden. The container only sees what is on your host filesystem at `./backend`.

This is actually what you want for development — your live code replaces the baked-in code. But it means if your host directory is missing something (like a compiled artifact or generated file), the container will not find it either.

It also means the bind mount must have everything the app needs. If your image installs dependencies into `/app/node_modules` or `/app/venv` and you mount a bind mount at `/app`, those installed dependencies are gone — hidden by the bind mount. You need to handle this carefully (see the node_modules problem in a moment).

### The Dependency Override Problem

Consider a Node.js app (or any app with installed packages). The Dockerfile installs dependencies inside the image:

```dockerfile
COPY package.json .
RUN npm install       # installs into /app/node_modules
COPY . .
```

Then you add a bind mount:

```yaml
volumes:
  - .:/app             # mounts your local directory, which has no node_modules
```

Your local directory probably has no `node_modules` (or a different version). The bind mount hides the image's `node_modules`. The app crashes — can't find its dependencies.

The fix is to use an **anonymous volume to protect the dependencies directory**:

```yaml
volumes:
  - .:/app                 # bind mount your code
  - /app/node_modules      # anonymous volume protects node_modules from being hidden
```

The second line tells Docker: "at `/app/node_modules` specifically, use Docker-managed storage (from the image), not the bind mount." The anonymous volume takes precedence over the bind mount at that specific path.

For Python with Flask this is less of an issue because Flask's dependencies are installed in the system Python, not inside `/app`. But if you ever use a virtualenv inside your project directory, you would need the same pattern.

### Read-Only Bind Mounts

You can mount a bind mount as read-only — the container can read files from it but cannot write back to your host:

```yaml
volumes:
  - ./backend:/app:ro    # :ro makes it read-only
```

This is useful for configuration files, secrets files, or SSL certificates that the app needs to read but should never modify. It also prevents a bug in your containerized app from accidentally corrupting your source code on the host.

### Bind Mounts Are Not for Production

Bind mounts are a **development tool**. They depend on the host machine's filesystem — a specific path that exists on your laptop. In production:

- There is no developer laptop — code runs on a server or in a cloud environment
- The image should be the single source of truth — self-contained, portable, reproducible
- You want the exact code that was tested to run in production — not "whatever is in this directory right now"

In production, you build the image with code baked in (`COPY . .` in the Dockerfile) and deploy the image. No bind mounts. The development workflow with bind mounts is a shortcut that only makes sense locally.

### Named Volume vs Bind Mount — When to Use Which

| Situation | Use |
|-----------|-----|
| Development — live code reloading | Bind mount |
| Database data, uploaded files | Named volume |
| Config files the container should read | Bind mount (read-only) |
| Sharing data between containers | Named volume |
| Production deployments | Neither — code in image, data in named volume |
| Logs you want to read on the host | Bind mount |

The short version: bind mounts are for **development workflow** and **reading host files**. Named volumes are for **persistent data** that the container owns.

### Absolute vs Relative Paths in Compose

In a Compose file, bind mount paths are relative to the `docker-compose.yml` file's location:

```yaml
volumes:
  - ./backend:/app        # relative — resolves from where docker-compose.yml is
  - /home/uzair/certs:/certs  # absolute — exact path on the host
```

Relative paths are almost always preferred — they make the project portable. Anyone who clones the repo has the right directory structure automatically. Absolute paths break when someone else runs the project or when you move the project to a different location.

### WSL2-Specific Note

You are on WSL2. Bind mounts work correctly when your project files live inside the WSL2 filesystem (under `~/` in your Linux home). They work but are **significantly slower** if your files live in the Windows filesystem (under `/mnt/c/`). This is a known WSL2 limitation — accessing Windows filesystem from Linux goes through a translation layer.

Keep your Docker projects inside WSL2's native filesystem (`~/devops/...`) as you are already doing. Never put Docker project files under `/mnt/c/Users/...` if you care about bind mount performance.

---

## 6. Commands

```bash
# ── Compose With Bind Mounts ───────────────────────────────────────────────

docker compose up -d              # bind mounts are applied at startup
docker compose up -d --build      # rebuild image (only needed first time or for dep changes)

# ── Verifying the Bind Mount ──────────────────────────────────────────────

# Check what is mounted in a running container
docker inspect <container> --format='{{json .Mounts}}' | python3 -m json.tool

# Confirm bind mount is live — edit a file on host, check inside container
docker compose exec backend cat /app/app.py

# ── Checking Logs for Flask Reloader ──────────────────────────────────────

docker compose logs -f backend    # watch for "Detected change" messages

# ── Read-Only Mount Syntax ─────────────────────────────────────────────────

# In docker-compose.yml:
# volumes:
#   - ./backend:/app:ro

# ── Syntax Reference ──────────────────────────────────────────────────────

# Bind mount (host path → container path):
# - ./local/path:/container/path
# - ./local/path:/container/path:ro   (read-only)

# Named volume (Docker managed):
# - volume-name:/container/path

# Anonymous volume (protect a path from being hidden by bind mount):
# - /container/path
```

---

## 7. Real-World Notes

The bind mount development workflow — live code in the container without rebuilding — is standard practice across all containerized development, not just Python/Flask. The same pattern works for Node.js (with nodemon), Ruby on Rails, Go (with air), and any language with a file watcher. The specific tool changes but the Docker side is always the same: bind mount your code directory, run the server in watch mode.

Docker Compose has a `watch` feature (introduced in Compose v2.22) that formalizes this pattern with more control — you can define which file changes trigger a sync, which trigger a rebuild, and which trigger a restart. It is worth knowing exists, but the bind mount approach you learned here is simpler, more widely understood, and works everywhere.

In team environments, bind mounts are part of the local development contract. The `docker-compose.yml` typically has bind mounts configured for development. Some teams maintain a separate `docker-compose.prod.yml` or `docker-compose.override.yml` that removes the bind mounts and environment-specific settings for production use. We will see this pattern in a later step.

Never commit files like `.env` or SSL certificate files to git — but you can absolutely bind-mount them into containers for local development. A pattern you will see often: a `.env.example` file is committed to git, developers copy it to `.env` and fill in their local values, and the Compose file mounts or reads from `.env`. The actual `.env` with real values is in `.gitignore`.

---

## 8. Exercises

**Exercise 1 — Feel the difference**
Without bind mounts, make three small code changes to `app.py`, rebuilding after each one. Note how long each rebuild takes. Then add the bind mount with Flask debug mode. Make three more changes — no rebuilds needed. The difference in workflow speed is the entire motivation for this step.

**Exercise 2 — Verify the mount is live**
With the bind mount running, exec into the container and run `cat /app/app.py`. Then edit `app.py` on your host. Exec in again and run `cat /app/app.py` — the change is there immediately. You are reading the same file from two different perspectives: your editor on the host, and the container's filesystem.

**Exercise 3 — Test read-only**
Add `:ro` to your bind mount. Bring it up. Try to write a file from inside the container:
```bash
docker compose exec backend sh -c "echo test > /app/test.txt"
# Read-only file system error
```
Remove `:ro` and try again — it works. Now check your `backend/` folder on the host — `test.txt` is there. The container wrote directly to your host filesystem.

**Exercise 4 — Observe the image override**
Build your image and run it **without** a bind mount. Exec in and check `/app/app.py` — it shows the version baked into the image. Now add the bind mount and bring it up again. Edit `app.py` on your host, exec in again and check `/app/app.py` — it shows your live version. The bind mount completely replaced the image's files at that path.

**Exercise 5 — Break it with a missing dependency (simulated)**
Temporarily rename `requirements.txt` to `requirements.txt.bak` so it is missing from your `backend/` directory. With the bind mount active, the container's `/app/requirements.txt` also disappears — because the bind mount reflects your host directory exactly. This demonstrates that the bind mount is a live mirror, not a one-time copy. Rename it back.

**Exercise 6 — Both volumes together**
Make sure your Compose file has both the bind mount (`./backend:/app`) and the named volume (`backend-data:/app/data`) simultaneously. Add a note via the `/notes` endpoint. Edit `app.py` and watch the reloader trigger. Verify the note is still there after the reload — the named volume at `/app/data` is not affected by the bind mount at `/app` because it is mounted at a more specific path that takes precedence. Two volume types, coexisting, each doing their job.