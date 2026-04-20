# 08 — Docker Compose


## 0. Goal of This Step

Replace everything you did manually in step 07 — building images, creating networks, running containers with the right flags — with a single file and a single command. Understand what Docker Compose actually is, what it does under the hood, and why it exists.



## 1. What Problem It Solves

In step 07, what we actually did was **manual orchestration**.

We were not just running containers, we were coordinating a system:

- Creating a network
- Ensuring correct container names for DNS
- Passing environment variables
- Starting services in the right order
- Rebuilding and recreating containers on every change

This worked for two services. But imagine doing this for 5 services or 10 and this approach breaks down.

The problem is not just “too many commands”. The real problems are:

- No single source of truth for the system
- High chance of human error (wrong name, wrong network, wrong order)
- No reproducibility (someone else cannot easily recreate your setup)
- Cognitive overload, you are remembering infrastructure in your head

We were effectively managing an application, but without a way to **define the application itself**.

Docker Compose solves this by introducing a **declarative way to define your entire system in one place**, services, networks, configuration and then running it as a unit.


## 2. What Happened (Experience)

Coming from step 07, we had this entire sequence just to get two containers running and talking:

```bash
docker build -t backend:v1 ./backend
docker build -t frontend:v1 ./frontend
docker network create app-network
docker run -d -p 5000:5000 --name backend --network app-network backend:v1
docker run -d -p 5001:5001 --name frontend --network app-network frontend:v1
```

Five commands. And if anything went wrong or you changed code, you had to tear it all down and do it again.

What changed here is deeper than fewer commands.

Before (step 07), we were executing infrastructure step-by-step:

```bash
docker network create ...
docker run ...
docker run ...
```
Now, we define the system once:

```yaml
services:
  backend:
  frontend:
```

This is the shift from imperative execution to declarative system definition.

Instead of telling Docker how to run things, we describe what the system should look like — and Docker handles the rest.

At this point, we are shifting from **running containers** to **defining an application**.

Instead of executing a sequence of commands, we describe the desired end state - what services exist, how they connect, and how they should run and let Docker handle the execution.

Now let's create a `docker-compose.yml` file in the `08-docker-compose` directory (same frontend and backend apps):

```yaml
services:
  backend:
    build: ./backend
    ports:
      - "5000:5000"

  frontend:
    build: ./frontend
    ports:
      - "5001:5001"
    environment:
      - BACKEND_URL=http://backend:5000
```

That's it. Now start everything:

```bash
docker compose up
```

Output:

```
[+] Building 2/2
 ✔ backend Built
 ✔ frontend Built
[+] Running 3/3
 ✔ Network 08-docker-compose_default  Created
 ✔ Container 08-docker-compose-backend-1   Started
 ✔ Container 08-docker-compose-frontend-1  Started
```

Three things happened automatically:
- Both images were built
- A custom network was created
- Both containers were started on that network

Test it:

```bash
curl http://localhost:5001/api
# {"backend_response": {"data": "This is data from backend service"}, "frontend": "ok"}
```

Works. First try. One command instead of five.

Now run it in the background (detached mode):

```bash
docker compose up -d
```

Check what's running:

```bash
docker compose ps
# NAME                           STATUS    PORTS
# 08-docker-compose-backend-1    running   0.0.0.0:5000->5000/tcp
# 08-docker-compose-frontend-1   running   0.0.0.0:5001->5001/tcp
```

Stop and remove everything:

```bash
docker compose down
```

```
[+] Running 3/3
 ✔ Container 08-docker-compose-frontend-1  Removed
 ✔ Container 08-docker-compose-backend-1   Removed
 ✔ Network 08-docker-compose_default       Removed
```

Containers gone. Network gone. One command.


## 3. Why It Happens

Docker Compose is not a separate runtime. It is an **orchestration layer on top of the Docker API**.

In step 07, you were manually issuing low-level commands (`docker run`, `docker network create`, etc.). Compose takes a higher-level definition (`docker-compose.yml`) and translates it into those same API calls — consistently and deterministically.

The key difference is not what gets executed, but **how it is defined**.

- Before: imperative (step-by-step commands)
- Now: declarative (desired system state)

When you run `docker compose up`, Compose:

1. Reads the YAML file
2. Builds any images that have a `build:` key (or pulls them if they have `image:`)
3. Creates a custom network named `<project>_default` automatically
4. Starts each service as a container on that network
5. Names each container `<project>_<service>_<number>`

The project name defaults to the directory name. That is why the network is called `08-docker-compose_default` and containers are named `08-docker-compose-backend-1`.

Because Compose creates a custom network automatically, DNS works immediately. The frontend can reach `http://backend:5000` because the service name `backend` in the YAML becomes the DNS hostname on that network — exactly like `--name backend` did in step 07, just handled for you.


## 4. Solution

**The `docker-compose.yml` file for our frontend/backend app:**

```yaml
services:
  backend:
    build: ./backend
    ports:
      - "5000:5000"

  frontend:
    build: ./frontend
    ports:
      - "5001:5001"
    environment:
      - BACKEND_URL=http://backend:5000
    depends_on:
      - backend
```

`depends_on` tells Compose to start the backend container before the frontend. It does not wait for the backend app to be *ready* — just for the container to start. For our simple case this is enough.

**The workflow from here on:**

```bash
# Start everything (build if needed)
docker compose up -d

# Check status
docker compose ps

# Read logs
docker compose logs
docker compose logs frontend   # just one service

# Rebuild after code changes
docker compose up -d --build

# Stop and remove containers + network
docker compose down
```

That is the full day-to-day workflow. Everything else is variations on this.



## 5. Deep Understanding

### Service vs Container

This is the most important shift in this step.

- A **container** is a running instance of an image (what you worked with in step 07)
- A **service** is a definition of how that container should run

In Compose, you do not directly manage containers. You define services, and Compose creates and manages containers for you.

Example:

```yaml
services:
  backend:
    build: ./backend
```

### The YAML File Structure

A `docker-compose.yml` has a few top-level keys. The most important is `services`. Each key under `services` is one container:

```yaml
services:
  <service-name>:       # this becomes the DNS hostname on the network
    build: ./path       # build image from this Dockerfile
    image: nginx        # OR use an existing image (not both)
    ports:
      - "host:container"
    environment:
      - KEY=VALUE
    depends_on:
      - other-service
```


### Service Name = DNS Hostname

The service name in your YAML (`backend`, `frontend`) is exactly the hostname other containers use to reach it. This is why the frontend's environment variable is `BACKEND_URL=http://backend:5000` — because the service is named `backend` in the YAML.

If you renamed the service to `api`, you would update the URL to `http://api:5000`. The name in the YAML and the name in the URL must match. This is the same principle as `--name` in step 07, just managed by Compose.

### What `docker compose down` Does vs `docker compose stop`

These are different and the difference matters:

`docker compose stop` — stops the containers but leaves them and the network in place. You can `docker compose start` to bring them back without recreating anything.

`docker compose down` — stops containers, removes them, and removes the network. Everything is gone. Next `up` creates fresh containers from scratch.

`docker compose down --volumes` — same as above but also deletes any named volumes. Use this when you want a completely clean slate including any stored data.

For development, you almost always want `down`. For temporarily pausing work, `stop` is faster.

### Build vs Image

In our file we use `build:` which tells Compose to build the image from a Dockerfile. You can also use `image:` to pull an existing image from Docker Hub:

```yaml
services:
  backend:
    build: ./backend    # build from local Dockerfile

  database:
    image: postgres:15  # pull this image, don't build it
```

You will use this pattern in step 13 when we add a real Postgres database. The database has no Dockerfile — you just reference the official image. Your own services have Dockerfiles.

### Rebuild Behavior

`docker compose up -d` does **not** rebuild images automatically if they already exist. If you change your code and run `up -d` again, it uses the cached image. You need to explicitly tell it to rebuild:

```bash
docker compose up -d --build
```

This is a common source of confusion — you change code, run `up`, and the old behavior is still there. Always use `--build` after code changes.

Alternatively, rebuild without starting:

```bash
docker compose build           # rebuild all services
docker compose build backend   # rebuild one service only
```

### Container Naming

Compose names containers as `<project>_<service>_<number>`. The project name is the directory name by default. You can override it:

```bash
docker compose -p myapp up -d
# containers: myapp-backend-1, myapp-frontend-1
# network: myapp_default
```

Or set it in a `.env` file:

```
COMPOSE_PROJECT_NAME=myapp
```

The number at the end (`-1`) exists because Compose supports running multiple instances of the same service — scaling — which we will see in later steps.

### Logs in Compose

```bash
docker compose logs            # all services, all logs
docker compose logs -f         # follow live
docker compose logs backend    # one service only
docker compose logs -f --tail 20 frontend  # follow, last 20 lines
```

Compose interleaves logs from all services with color coding and service name prefixes so you can tell which container produced which line. This is much more convenient than running `docker logs` on each container separately.


## 6. Commands

```bash
# ── Core Workflow ──────────────────────────────────────────────────────────

docker compose up              # start everything, stream logs
docker compose up -d           # start in background (detached)
docker compose up -d --build   # rebuild images then start
docker compose down            # stop and remove containers + network
docker compose down --volumes  # also remove volumes

# ── Status and Logs ────────────────────────────────────────────────────────

docker compose ps              # status of all services
docker compose logs            # all logs
docker compose logs -f         # follow live
docker compose logs <service>  # one service only

# ── Individual Service Control ─────────────────────────────────────────────

docker compose start           # start stopped services (no recreate)
docker compose stop            # stop without removing
docker compose restart         # restart all services
docker compose restart backend # restart one service

# ── Building ───────────────────────────────────────────────────────────────

docker compose build           # build all service images
docker compose build backend   # build one service image

# ── Running Commands Inside Services ──────────────────────────────────────

docker compose exec backend /bin/sh    # shell into running service
docker compose exec backend env        # run single command

# ── Cleanup ────────────────────────────────────────────────────────────────

docker compose down --rmi all  # also remove built images
```


## 7. Real-World Notes

In real projects, `docker-compose.yml` lives at the root of the repository and is committed to version control. It is the single source of truth for how the application runs locally. A new developer clones the repo and runs `docker compose up -d` — that is the entire local setup. No README with ten manual steps, no "works on my machine" problems.

You will often see two Compose files in production-grade projects: `docker-compose.yml` for base configuration and `docker-compose.override.yml` for local development overrides (like bind mounts for live code reloading). Compose merges these automatically. We cover this in a later step.

Docker Compose is primarily used for **local development and testing environments**. It runs everything on a single machine. For distributed, multi-node production systems, orchestration tools like Kubernetes are used instead.

The `depends_on` key controls start order but not readiness. If your backend takes 5 seconds to initialize and the frontend tries to connect immediately, it can fail even with `depends_on`. The production solution is health checks combined with `depends_on: condition: service_healthy` — covered in step 19.


## 8. Exercises

**Exercise 1 — Feel the difference from step 07**
Write the `docker-compose.yml` for the frontend and backend. Run `docker compose up -d`. Time how long it takes and count how many commands you typed. Compare to the five-command sequence from step 07. Then run `docker compose down`. Everything is gone in one command.

**Exercise 2 — Break and fix with `--build`**
Change the backend's response message in `app.py`. Run `docker compose up -d` without `--build`. Hit the endpoint — old message is still there. Now run `docker compose up -d --build`. Hit the endpoint — new message appears. This is the most common Compose mistake as a beginners.

**Exercise 3 — Explore what Compose created**
After `docker compose up -d`, run `docker network ls` and find the auto-created network. Run `docker ps` and see the container names. Run `docker network inspect <network-name>` and confirm both containers are on it with their service names as DNS entries. Compose did all of this — you just described what you wanted.

**Exercise 4 — Use `docker compose logs`**
With both services running, hit `http://localhost:5001/api` a few times. Then run `docker compose logs`. See both services' logs interleaved. Now run `docker compose logs frontend` to filter to just one. Then try `docker compose logs -f` and hit the endpoint again in another terminal — watch the logs appear live.

**Exercise 5 — `down` vs `stop`**
Run `docker compose stop`. Check `docker ps -a` — containers exist but are stopped. Run `docker compose start` — they come back without rebuilding. Now run `docker compose down`. Check `docker ps -a` again — containers are gone. Check `docker network ls` — the network is also gone. Understand exactly what each command cleans up.

**Exercise 6 — Add `depends_on` and remove it**
Add `depends_on: - backend` to the frontend service. Bring everything up and notice Compose starts backend first. Remove it and bring up again — both start simultaneously. For our app it doesn't matter because the frontend only calls the backend on request, not at startup. Think about when it *would* matter (hint: a service that connects to a database on startup).