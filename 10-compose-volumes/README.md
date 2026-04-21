# 10 — Compose Volumes


## 0. Goal of This Step

Understand what Docker volumes are, why they exist, how to declare and use them in a Compose file, and what problem they solve that you have already been running into without fully realizing it — using the same frontend/backend setup as before.



## 1. What Problem It Solves

In step 06 you learned that a container's filesystem is ephemeral — anything written inside a container disappears when the container is removed. You proved this by creating a file inside a running container, removing the container, and finding the file gone.

That was fine for a stateless Flask app. But now consider:

- A database that stores its data inside the container — every `docker compose down` wipes your entire database
- An app that writes uploaded files inside the container — every restart loses all uploads


All these problems have the same root cause: container storage is tied to the container's lifetime. Volumes decouple storage from the container. Data lives in the volume, not in the container. The container can be removed, recreated, or replaced — the data stays.

This step focuses on volumes in the context of Compose. Step 11 goes deeper on persistence behavior, and step 12 covers bind mounts specifically.



## 2. What Happened (Experience)

Let's make the problem real before solving it.


**Step 1 — Prove the data loss problem**

Add a simple file-writing route to the backend so we can see data loss. Update `backend/app.py`:

```python
from flask import Flask, jsonify, request
import socket
import os

app = Flask(__name__)

NOTES_FILE = "/app/data/notes.txt"

@app.route("/")
def home():
    return jsonify({
        "message": "Hello from Backend",
        "hostname": socket.gethostname()
    })

@app.route("/api/data")
def data():
    return jsonify({
        "data": "This is data from backend service"
    })

@app.route("/notes", methods=["GET"])
def get_notes():
    if not os.path.exists(NOTES_FILE):
        return jsonify({"notes": []})
    with open(NOTES_FILE) as f:
        notes = f.read().splitlines()
    return jsonify({"notes": notes})

@app.route("/notes", methods=["POST"])
def add_note():
    os.makedirs(os.path.dirname(NOTES_FILE), exist_ok=True)
    note = request.json.get("note", "")
    with open(NOTES_FILE, "a") as f:
        f.write(note + "\n")
    return jsonify({"saved": note})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

Update `backend/requirements.txt` to add nothing new — Flask handles this already.

Rebuild and start:

```bash
docker compose up -d --build
```

Add a note:

```bash
curl -X POST http://localhost:5000/notes \
  -H "Content-Type: application/json" \
  -d '{"note": "this note will disappear"}'
# {"saved": "this note will disappear"}
```

Read it back:

```bash
curl http://localhost:5000/notes
# {"notes": ["this note will disappear"]}
```

Now do what you would normally do after finishing work — bring the stack down:

```bash
docker compose down
docker compose up -d
```

Read the notes again:

```bash
curl http://localhost:5000/notes
# {"notes": []}
```

Gone. The container was recreated from the image. The `/app/data/notes.txt` file existed only in the old container's writable layer, which `docker compose down` deleted.



**Step 2 — Fix it with a named volume**

Update `docker-compose.yml` to declare a volume and mount it into the backend:

```yaml
services:
  frontend:
    build: ./frontend
    ports:
      - "5001:5001"
    environment:
      - BACKEND_URL=http://backend:5000
    depends_on:
      - backend

  backend:
    build: ./backend
    ports:
      - "5000:5000"
    volumes:
      - backend-data:/app/data

volumes:
  backend-data:
```

Bring it up:

```bash
docker compose down
docker compose up -d
```

Add a note again:

```bash
curl -X POST http://localhost:5000/notes \
  -H "Content-Type: application/json" \
  -d '{"note": "this note will survive"}'
```

Now bring the stack all the way down and back up:

```bash
docker compose down
docker compose up -d
```

Read the notes:

```bash
curl http://localhost:5000/notes
# {"notes": ["this note will survive"]}
```

The container was destroyed and recreated. The note survived because it was written to the volume, not to the container's filesystem.



## 3. Why It Happens

Without a volume, when you write to `/app/data/notes.txt`, the file goes into the container's **writable layer** — a thin layer that sits on top of the image layers. This layer belongs to that specific container. When `docker compose down` removes the container, it removes the writable layer with it.

When you declare a volume and mount it at `/app/data`, Docker creates a managed storage location on your host machine (under `/var/lib/docker/volumes/` on Linux). When the container writes to `/app/data/notes.txt`, it is actually writing to that host location — not to the container's writable layer. The container is just a window into that storage.

When a volume is mounted at a path, it completely replaces that path inside the container. The container no longer writes to its own filesystem at that location — it writes directly to the volume.

When you run `docker compose down`, it removes the container. But the volume still exists — it is a separate object from the container. When you run `docker compose up` again, the new container mounts the same volume at the same path and finds all the data exactly where it was left.

```
Container (temporary)
│
└── /app/data  ──── [mounted from] ────  Volume: backend-data
                                         (lives on host, survives container removal)
```



## 4. Solution

**Declaring volumes in Compose has two parts — always both:**

**Part 1** — Mount the volume into a service:

```yaml
services:
  backend:
    volumes:
      - backend-data:/app/data   # volume-name:container-path
```

**Part 2** — Declare the volume at the top level:

```yaml
volumes:
  backend-data:
```

If you skip part 2, Compose will error. The top-level declaration tells Compose this is a named volume it should manage. Without it, Compose doesn't know whether `backend-data` is a named volume or a path on your host (which is a bind mount — covered in step 12).

**The full pattern:**

```yaml
services:
  backend:
    build: ./backend
    ports:
      - "5000:5000"
    volumes:
      - backend-data:/app/data

volumes:
  backend-data:
```

**To keep data when bringing down:**

```bash
docker compose down          # removes containers and network, keeps volumes
```

**To also delete volumes:**

```bash
docker compose down --volumes  # removes everything including volumes
```



## 5. Deep Understanding

### Named Volumes vs Anonymous Volumes

There are two kinds of volumes Docker can create:

**Named volumes** — you give them a name (`backend-data`). They persist after `docker compose down`. You can inspect them, back them up, and share them between services. This is what you declared above.

**Anonymous volumes** — created when a Dockerfile has a `VOLUME` instruction or when you mount without a name:

```yaml
volumes:
  - /app/data    # no name — anonymous volume
```

Anonymous volumes get a random ID as their name. They are hard to manage, hard to find, and pile up over time. Avoid them in Compose. Always use named volumes.

### Where Volumes Actually Live

On Linux (and in WSL2), Docker stores named volume data at:

```
/var/lib/docker/volumes/<volume-name>/_data
```

You can look directly at the data:

```bash
# Find the volume name
docker volume ls
# DRIVER    VOLUME NAME
# local     10-compose-volumes_backend-data

# Inspect it
docker volume inspect 10-compose-volumes_backend-data
# shows Mountpoint: /var/lib/docker/volumes/10-compose-volumes_backend-data/_data

# On Linux you can read it directly (requires sudo)
sudo ls /var/lib/docker/volumes/10-compose-volumes_backend-data/_data
# notes.txt
```

The volume exists independently of any container. Even if zero containers are using it, the data is still there.

### Volume Naming in Compose

Like networks, Compose prefixes volume names with the project name. If your project is `10-compose-volumes` and you declare a volume named `backend-data`, the actual Docker volume is `10-compose-volumes_backend-data`.

Inside the Compose file you always use the short name. Outside (in `docker volume ls`, `docker volume inspect`) you use the full prefixed name.

### Sharing a Volume Between Services

A volume can be mounted by multiple services simultaneously:

```yaml
services:
  backend:
    volumes:
      - shared-data:/app/data

  worker:
    volumes:
      - shared-data:/worker/data

volumes:
  shared-data:
```

Both services read and write to the same underlying storage. This is useful for a worker process that processes files uploaded through the backend. Be careful with concurrent writes — Docker volumes have no built-in locking. Your application is responsible for managing concurrent access.

### The `external` Volume

Just like external networks, you can declare an external volume — one that already exists and was not created by this Compose file:

```yaml
volumes:
  existing-data:
    external: true
```

Compose will not create or delete this volume. It just mounts it. If the volume doesn't exist when you run `docker compose up`, it fails with a clear error. This is used when you want to share a volume across multiple Compose projects or when the volume was pre-populated with data before the app ran.

### Volume Driver

The default volume driver is `local` — data lives on the same machine Docker is running on. There are third-party drivers that store data on network storage (NFS, AWS EBS etc.):

```yaml
volumes:
  backend-data:
    driver: local          # default, data on this machine
```

In production on a cloud provider you would use a driver that backs the volume with a persistent network disk so that if the container moves to a different machine, the data follows it. This is a production concern, not a local development one. On your laptop, `local` is always correct.

### What `docker compose down --volumes` Actually Deletes

`--volumes` (or `-v`) removes only the volumes **declared in the Compose file**. It does not touch external volumes. It also removes anonymous volumes attached to containers. Named volumes from other Compose files on the same machine are not touched.

This flag is most useful in CI/CD pipelines or when you want a completely clean test environment — tear down everything including data, start completely fresh.



## 6. Commands

```bash
# ── Volume Management ──────────────────────────────────────────────────────

docker volume ls                          # list all volumes
docker volume inspect <volume-name>       # see location, metadata
docker volume rm <volume-name>            # delete a volume (must be unused)
docker volume prune                       # delete all unused volumes

# ── Compose Volume Operations ─────────────────────────────────────────────

docker compose up -d                      # volumes are created automatically
docker compose down                       # keeps volumes
docker compose down --volumes             # removes volumes too
docker compose down -v                    # shorthand for --volumes

# ── Inspecting Volume Data ─────────────────────────────────────────────────

# Find full volume name
docker volume ls | grep <project-name>

# See where data lives on host
docker volume inspect <full-volume-name>

# Read data directly (Linux/WSL2)
sudo ls /var/lib/docker/volumes/<full-volume-name>/_data

# ── Checking What Volumes a Container Has ─────────────────────────────────

docker inspect <container> --format='{{json .Mounts}}' | python3 -m json.tool
```



## 7. Real-World Notes

The most common use of volumes in real projects is for databases. Every database — Postgres, MySQL, MongoDB, Redis — stores its data files somewhere on disk. In Docker, you always mount a volume at the database's data directory so that your data survives container restarts and upgrades. Without a volume, every `docker compose down` is a complete database wipe. You will see this in step 13 when we add real Postgres.

Volumes are also used for shared file storage between services — an upload service writes files to a volume, a processing service reads from the same volume. This is common in media processing pipelines.

In production on a single machine, local volumes work fine. The problem arises when you scale to multiple machines — a volume on machine A is not accessible from machine B. This is where networked storage drivers come in, or where you move to object storage (S3, GCS) instead of filesystem volumes entirely. That is a cloud architecture concern, not a Docker concern.

Never store secrets (passwords, API keys) in volumes. Volumes are for data. Secrets have their own mechanism in Docker — environment variables for simple cases, Docker Secrets or Kubernetes Secrets for production.



## 8. Exercises

**Exercise 1 — Reproduce the data loss**
Use the updated backend with the `/notes` endpoints. Start the stack, add a note, verify you can read it. Run `docker compose down` then `docker compose up -d`. Try to read the note — it is gone. This is the problem volumes solve. Make sure you feel it before moving on.

**Exercise 2 — Add the volume and prove persistence**
Add the named volume to `docker-compose.yml` as shown in this step. Bring the stack down and back up. Add a note. Bring it down again. Bring it up again. The note survives. Run `docker volume ls` and find your volume listed as a separate object.

**Exercise 3 — Inspect the volume**
Run `docker volume inspect <full-volume-name>`. Find the `Mountpoint` path. On WSL2/Linux, navigate to that path with `sudo ls` and confirm your `notes.txt` file is sitting there directly on the host filesystem — outside any container.

**Exercise 4 — `down` vs `down --volumes`**
Add several notes. Run `docker compose down` — then `docker compose up -d` — confirm notes survive. Now run `docker compose down --volumes`. Run `docker volume ls` — the volume is gone. Bring it up again — notes are gone. This is the difference between `down` and `down --volumes`. Use `--volumes` only when you want a clean slate.

**Exercise 5 — Anonymous vs named volume**
Temporarily change your volume mount to `- /app/data` (no name). Bring up, add a note, bring down, bring up again. The note is gone — Anonymous volumes persist on disk, but Compose does not reliably reattach them on container recreation. This makes them difficult to manage and appear as if data is lost. This is why named volumes are preferred.. Run `docker volume ls` and notice the unnamed volume with a random ID hash. Then switch back to the named volume. Understand why named volumes are always preferred.

**Exercise 6 — External volume**
Create a volume manually: `docker volume create my-external-data`. Declare it as external in your Compose file. Bring the stack up — Compose uses the existing volume instead of creating a new one. Bring it down — `docker volume ls` still shows `my-external-data` because Compose does not manage or delete external volumes.