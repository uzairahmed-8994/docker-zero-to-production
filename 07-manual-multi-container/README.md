# 07 — Manual Multi-Container


## 0. Goal of This Step

Run two separate containers - a frontend and a backend and make them talk to each other manually, without any orchestration tool. Understand why this is hard to do correctly, what breaks along the way, and why this pain is exactly what motivates Docker Compose in the next step.


## 1. What Problem It Solves

Every real application is more than one service. You have a frontend, a backend, a database, maybe a cache. Each one runs in its own container. The question is: how do they find and talk to each other?

Up to step 6 we ran a single container in isolation. That was enough to learn the basics. But a single container is never a real application. This step introduces what it actually takes to wire multiple containers together by hand so that when Docker Compose does it automatically in the next step, we understand exactly what it is doing and why.


## 2. What Happened (Experience)

We now have two Flask apps:

- **Backend** — runs on port `5000`, exposes `/` and `/api/data`
- **Frontend** — runs on port `5001`, calls the backend at `http://backend:5000/api/data`

The frontend already has the backend URL hardcoded as an environment variable with a sensible default:

```python
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")
```

This means the frontend expects to find a container named `backend` on the network. Let's first do it the naive way and see what breaks.


**Step 1 — Build both images**

```bash
# From the 07-manual-multi-container directory
docker build -t frontend:v1 ./frontend
docker build -t backend:v1 ./backend
```

Confirm both images exist:

```bash
docker images
# REPOSITORY   TAG   IMAGE ID       CREATED
# frontend     v1    ...            ...
# backend      v1    ...            ...
```


**Step 2 — Run both containers the naive way**

```bash
docker run -d -p 5001:5001 --name frontend frontend:v1
docker run -d -p 5000:5000 --name backend backend:v1
```

Both are running:

```bash
docker ps
# frontend   Up ...   0.0.0.0:5001->5001/tcp
# backend    Up ...   0.0.0.0:5000->5000/tcp
```

Backend works fine:

```bash
curl http://localhost:5000/api/data
# {"data": "This is data from backend service"}
```

Now hit the frontend's `/api` route which calls the backend:

```bash
curl http://localhost:5001/api
```

```json
{
  "error": "HTTPConnectionPool(host='backend', port=5000): Max retries exceeded",
  "frontend": "error"
}
```

The frontend is running. The backend is running. But the frontend cannot reach the backend.

This is the exact same problem from step 5  (containers on the default bridge network have no DNS). The frontend is trying to resolve the hostname `backend` and failing because there is no DNS to look it up.



**Step 3 — Try to fix it by adding a custom network**

We learned in step 5 that custom networks give us DNS. So let's create one:

```bash
docker network create app-network
```

But our containers are already running on the default bridge. We need to connect them to the new network:

```bash
docker network connect app-network frontend
docker network connect app-network backend
```

Now try again:

```bash
curl http://localhost:5001/api
# {"backend_response": {"data": "This is data from backend service"}, "frontend": "ok"}
```

It works. The frontend resolved `backend` by name, called `/api/data`, got the response, and returned it.



**Step 4 — Realize the correct order matters**

Now clean everything up and try to do it properly from scratch {network first, then containers):

```bash
docker rm -f frontend backend
docker network rm app-network

# Create the network first
docker network create app-network

# Run both containers on the network from the start
docker run -d -p 5000:5000 --name backend --network app-network backend:v1
docker run -d -p 5001:5001 --name frontend --network app-network frontend:v1
```

Test:

```bash
curl http://localhost:5001/api
# {"backend_response": {"data": "This is data from backend service"}, "frontend": "ok"}
```

This is the correct way. Network first, then containers attached to it at startup.



## 3. Why It Happens

The frontend code does this:

```python
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")
response = requests.get(f"{BACKEND_URL}/api/data")
```

It constructs a URL with the hostname `backend`. When Python's `requests` library tries to open that connection, the OS needs to resolve `backend` to an IP address. It asks the DNS server configured in `/etc/resolv.conf`.

On the default bridge network, that DNS server knows nothing about your containers. Resolution fails. The HTTP request never even starts.

On a custom network, Docker's embedded DNS at `127.0.0.11` knows every container on that network by name. It resolves `backend` to the backend container's IP, the request goes through, and the frontend gets its response.

The container name is the hostname. That is why we named it `--name backend` because the frontend's code references `backend` as the hostname. The name in `docker run` and the hostname in the URL must match.



## 4. Solution

The correct procedure for running multiple containers that need to communicate:

**1. Create the network first**

```bash
docker network create app-network
```

**2. Run containers attached to that network**

```bash
docker run -d \
  -p 5000:5000 \
  --name backend \
  --network app-network \
  backend:v1

docker run -d \
  -p 5001:5001 \
  --name frontend \
  --network app-network \
  frontend:v1
```

**3. Verify connectivity**

```bash
# Backend directly
curl http://localhost:5000/
curl http://localhost:5000/api/data

# Frontend calling backend internally
curl http://localhost:5001/api
```

**4. Override the backend URL if needed**

The frontend reads `BACKEND_URL` from the environment. You can override it at runtime:

```bash
docker run -d \
  -p 5001:5001 \
  --name frontend \
  --network app-network \
  -e BACKEND_URL=http://backend:5000 \
  frontend:v1
```

This is useful when the backend has a different name or port in different environments.



## 5. Deep Understanding

### Container Name Is the Hostname

On a custom Docker network, the container's `--name` becomes its DNS hostname. When the frontend does `requests.get("http://backend:5000/api/data")`, Docker's DNS resolves `backend` to the IP of whichever container is currently running with that name.

This means naming is not cosmetic, it is functional. If you run your backend with `--name my-backend` but the frontend's code references `http://backend:5000`, it will fail. The name in your code and the name in `docker run` must match.

You can also set an explicit hostname separately from the container name:

```bash
docker run -d --name my-backend --hostname backend --network app-network backend:v1
```

Now the container is called `my-backend` in `docker ps` but resolves as `backend` on the network. In practice most people keep them the same to avoid confusion.

### Environment Variables as Configuration

The frontend uses:

```python
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:5000")
```

This is the correct pattern for containerized apps. You never hardcode URLs, ports, or credentials directly in code. You read them from environment variables with sensible defaults for local development. At runtime, Docker injects whatever values you pass with `-e`.

This pattern becomes essential when the same image is deployed to different environments — development, staging, production each with different backend addresses. The image stays the same. Only the environment variables change.

### Service Startup Order Is Not Guaranteed

When you run two containers manually, they start roughly at the same time. There is no guarantee the backend is fully ready before the frontend tries to call it. In our case this is fine because the frontend only calls the backend on incoming HTTP requests, not at startup. But in applications that connect to a database at startup (establishing a connection pool), if the database isn't ready yet, the app crashes.

This is called the **startup order problem**. Docker Compose has a `depends_on` option to control order, but even that only waits for the container to start not for the service inside to be ready. The real solution is building retry logic into your application or using a health check, which we will cover in step 19.

### Port Mapping vs Internal Communication

There are two separate concerns here that are easy to mix up:

**Port mapping** (`-p 5000:5000`) is for **host → container** access. It lets your browser or `curl` on your laptop reach the container. It has nothing to do with container-to-container communication.

**Custom networks** are for **container → container** access. Containers on the same custom network reach each other using their container names and internal ports, no port mapping involved.

```
Your laptop
│
├── curl localhost:5001  →  [port mapping]  →  frontend container (port 5001)
│                                                      │
│                                               [custom network]
│                                                      │
│                                              backend container (port 5000)
│                                               ↑ no -p needed for this
└── curl localhost:5000  →  [port mapping]  →  backend container (port 5000)
```

Notice: the backend does not need port mapping at all for the frontend to reach it. The frontend reaches it through the custom network on port `5000` directly. Port mapping on the backend is only there so *you* can test it directly from your laptop.

In a production setup, you would typically only expose the frontend (or a reverse proxy) to the outside world and keep the backend reachable only on the internal network.

### What `socket.gethostname()` Returns

The backend returns `socket.gethostname()` in its response. Inside a container, the hostname is the container ID by default (a random hex string like `64f8f3e32888`). You can override this with `--hostname`:

```bash
docker run -d --name backend --hostname backend-service --network app-network backend:v1
```

This is useful when you need the hostname to be predictable, for example, in application logs where you want to know which service produced the log entry.



## 6. Commands

```bash
# ── Building Images ────────────────────────────────────────────────────────

docker build -t backend:v1 ./backend
docker build -t frontend:v1 ./frontend

# ── Network Setup ──────────────────────────────────────────────────────────

docker network create app-network
docker network ls
docker network inspect app-network

# ── Running Containers ─────────────────────────────────────────────────────

docker run -d -p 5000:5000 --name backend --network app-network backend:v1
docker run -d -p 5001:5001 --name frontend --network app-network frontend:v1

# With environment variable override:
docker run -d -p 5001:5001 --name frontend --network app-network \
  -e BACKEND_URL=http://backend:5000 frontend:v1

# ── Testing ────────────────────────────────────────────────────────────────

curl http://localhost:5000/             # backend home
curl http://localhost:5000/api/data     # backend data endpoint
curl http://localhost:5001/             # frontend home
curl http://localhost:5001/api          # frontend calling backend

# ── Verifying Network Connectivity ────────────────────────────────────────

docker exec -it frontend ping backend   # DNS resolution check
docker exec -it frontend /bin/sh
# inside: wget -qO- http://backend:5000/api/data

# ── Cleanup ────────────────────────────────────────────────────────────────

docker rm -f frontend backend
docker network rm app-network
```



## 7. Real-World Notes

What you just did manually (create a network, run containers on it, wire them by name) is exactly what Docker Compose does automatically. In real projects nobody wires multi-container apps by hand. But having done it manually means you understand *what* Compose is doing when it creates networks and assigns service names, instead of treating it as magic.

The pattern of reading configuration from environment variables (`os.getenv`) is standard practice in containerized applications. It is formalized in a methodology called **12-factor app** (a set of principles for building software that runs well in containers and cloud environments). Factor 3 of 12 is specifically "store config in the environment". Every serious backend you will ever work with follows this.

Only expose what needs to be exposed. In our setup both containers have port mappings (`-p`) because we want to test both from our laptop. In a real deployment, only the service that receives external traffic, the frontend or more commonly a reverse proxy like Nginx, gets a port mapping. Backend services, databases, and caches stay on the internal network only. This reduces your attack surface significantly.



## 8. Exercises

**Exercise 1 — Reproduce the DNS failure**
Run both containers without `--network app-network`. Try `curl http://localhost:5001/api`. Confirm it returns the connection error. Then run `docker exec -it frontend ping backend` and confirm "Name or service not known". This connects back to what you learned in step 5.

**Exercise 2 — Fix it the right way**
Clean up, create `app-network`, run both containers on it from the start. Confirm `curl http://localhost:5001/api` returns the backend's data. Then exec into the frontend and run `cat /etc/resolv.conf` — confirm Docker's DNS at `127.0.0.11` is there.

**Exercise 3 — Override the backend URL**
Run the frontend with `-e BACKEND_URL=http://wrong-name:5000`. Hit `/api` and see the error. Now change it to the correct name. This demonstrates how environment variables control behavior without changing the image.

**Exercise 4 — Internal vs external ports**
Remove the `-p 5000:5000` mapping from the backend. Run it with no port mapping. Confirm you cannot reach `localhost:5000` from your laptop. But confirm the frontend can still reach it internally via `curl http://localhost:5001/api`. The backend is now only accessible inside the Docker network, which is exactly how production setups work.

**Exercise 5 — See the hostname**
Hit `curl http://localhost:5000/` and look at the `hostname` field in the response. It will be a random container ID. Now recreate the backend with `--hostname backend-service` and hit the same endpoint. The hostname is now predictable. Think about why this matters for logging in a system with many containers.

**Exercise 6 — Feel the pain of doing this manually**
Make a small change to the backend (change the response message). Now go through the full process: rebuild the image, remove the old container, recreate the network if needed, run the new container, verify. Count how many commands that took. This is the exact pain Docker Compose eliminates and you'll appreciate it much more having felt it first.