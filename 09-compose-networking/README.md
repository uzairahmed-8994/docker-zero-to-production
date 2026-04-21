# 09 — Compose Networking


## 0. Goal of This Step

Understand how Docker Compose handles networking, move beyond the auto-created default network, define networks explicitly in your Compose file, and learn how to isolate services from each other — building directly on what you learned in step 05.



## 1. What Problem It Solves

In step 08 you ran `docker compose up` and it just worked — frontend talked to backend with no network setup on your part. Compose created a network silently and everything connected automatically.

That is fine for simple two-service apps. But real applications are more complex:

- You might have a frontend, a backend, and a database and you want the frontend to *not* be able to talk directly to the database
- You might have multiple Compose projects running on the same machine that need to share a network
- You might want meaningful network names instead of `08-docker-compose_default`
- You might need to control exactly which services can reach which other services

This step teaches you to take control of networking in Compose instead of relying on the default behavior.


## 2. What Happened (Experience)

Starting from the step 08 setup, everything works with zero network configuration. Let's first understand exactly what Compose did automatically, then break it deliberately, then take control of it properly.


**Step 1 — Understand what the default network actually is**

Run the app:

```bash
docker compose up -d
```

Now look at what network was created:

```bash
docker network ls
# NETWORK ID     NAME                        DRIVER
# a1b2c3d4e5f6   09-compose-networking_default   bridge
# ...
```

Inspect it:

```bash
docker network inspect 09-compose-networking_default
```

You will see both containers listed as members with their service names as DNS aliases. Compose created a standard custom bridge network, exactly what you created manually in step 05 with `docker network create`. The only difference is Compose named it and managed it for you.



**Step 2 — Prove services talk by service name**

Exec into the frontend and resolve the backend by name.


```bash
# Minimal images don’t include tools like wget or ping, so install them:
apt-get update && apt-get install -y wget iputils-ping

docker compose exec frontend /bin/sh

# inside:
wget -qO- http://backend:5000/api/data
# {"data": "This is data from backend service"}
```

Now try to reach the backend by a random name:

```bash
wget -qO- http://randomname:5000/api/data
# wget: bad address 'randomname'
```

Only service names from the same Compose file resolve. This is Docker's embedded DNS at `127.0.0.11` — the same thing you found in step 05, now managed automatically by Compose.



**Step 3 — Add a database service and expose the problem**

Let's extend the `docker-compose.yml` to add a Postgres database (we will use it properly in step 13 — for now it just demonstrates network isolation):

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

  database:
    image: postgres:15
    environment:
      - POSTGRES_PASSWORD=secret
```

Bring it up:

```bash
docker compose up -d
```

Now exec into the frontend and try to reach the database:

```bash
docker compose exec frontend /bin/sh
# inside:
ping database
# 64 bytes from database: ...
```

The frontend can reach the database directly. In a real application this is a security problem — the frontend should never talk to the database. Only the backend should. But on the default network, every service can reach every other service with no restrictions.


**Step 4 — Fix it with explicit networks**

Now rewrite the `docker-compose.yml` with two networks — one for frontend-to-backend communication, one for backend-to-database communication:

```yaml
services:
  frontend:
    build: ./frontend
    ports:
      - "5001:5001"
    environment:
      - BACKEND_URL=http://backend:5000
    networks:
      - frontend-network
    depends_on:
      - backend

  backend:
    build: ./backend
    ports:
      - "5000:5000"
    networks:
      - frontend-network
      - backend-network

  database:
    image: postgres:15
    environment:
      - POSTGRES_PASSWORD=secret
    networks:
      - backend-network

networks:
  frontend-network:
  backend-network:
```

Bring it down and back up:

```bash
docker compose down
docker compose up -d
```

Now test isolation:

```bash
# Frontend can reach backend — same network
docker compose exec frontend ping backend
# works

# Frontend cannot reach database — different network
docker compose exec frontend ping database
# ping: bad address 'database'

# Backend can reach both — it is on both networks
docker compose exec backend ping frontend
# works
docker compose exec backend ping database
# works
```

The frontend is now completely isolated from the database. The backend acts as the only bridge between them — which is exactly the architecture you want.



## 3. Why It Happens

When you define explicit networks in Compose, each network becomes a separate custom bridge network on Docker. A container only joins the networks you assign it to. Docker's DNS on each network only knows about containers on *that* network.

So when the frontend tries to resolve `database`, it asks Docker's DNS server (`127.0.0.11`). That DNS server only knows about services on `frontend-network`. `database` is only on `backend-network`. The DNS lookup fails, not because of a firewall but because `database` simply doesn't exist as far as `frontend-network`'s DNS is concerned.

The backend is special — it is connected to *both* networks. It has two network interfaces, two DNS contexts, and can reach services on either network. This is the controlled bridge pattern you saw in step 05's Exercise 4.

```
Internet / Your laptop
        │
   [port 5001]
        │
   frontend  ──── frontend-network ────  backend
                                            │
                                     backend-network
                                            │
                                        database
```

The frontend can only go as far as the backend. The database is invisible to it.


## 4. Solution

**The correct pattern for multi-service apps with a database:**

```yaml
services:
  frontend:
    build: ./frontend
    ports:
      - "5001:5001"
    environment:
      - BACKEND_URL=http://backend:5000
    networks:
      - frontend-network
    depends_on:
      - backend

  backend:
    build: ./backend
    ports:
      - "5000:5000"
    networks:
      - frontend-network
      - backend-network

  database:
    image: postgres:15
    environment:
      - POSTGRES_PASSWORD=secret
    networks:
      - backend-network

networks:
  frontend-network:
  backend-network:
```

**Rules to follow:**

- Define all networks explicitly under the top-level `networks:` key
- Assign each service only the networks it actually needs
- Services that bridge layers (like backend) get assigned to multiple networks
- Never put the database on the same network as the frontend



## 5. Deep Understanding

### The Default Network Is Not Wrong — Just Unrestricted

The auto-created `_default` network is a perfectly valid custom bridge network with DNS. For simple apps with two or three services that all need to talk to each other, it is completely fine. You only need explicit networks when you want **isolation** — when certain services should not be able to reach certain other services.

Don't add complexity you don't need. A two-service frontend/backend app with no database is fine on the default network. Add explicit networks when you add a database or when your app grows to the point where unrestricted inter-service communication becomes a concern.

### Network Names in Compose

When you declare a network in Compose:

```yaml
networks:
  frontend-network:
```

The actual Docker network name is `<project>_frontend-network`. So if your project is `09-compose-networking`, the real network name is `09-compose-networking_frontend-network`. You can see this with `docker network ls`.

Inside the Compose file you always use the short name (`frontend-network`). The prefix is added automatically. This matters when you need to reference the network from outside Compose — for example, connecting a standalone container to a Compose network.

### External Networks

Sometimes you want a Compose project to connect to a network that already exists — created by another Compose project or manually. You declare it as `external`:

```yaml
networks:
  shared-network:
    external: true
```

Compose will not create this network — it expects it to already exist. If it doesn't, `docker compose up` will fail with a clear error. This is useful when two separate Compose projects need to share services — for example, a shared database used by multiple apps.

### Aliases — Giving a Service Multiple Names on a Network

A service can have additional DNS names on a specific network using `aliases`:

```yaml
services:
  backend:
    networks:
      frontend-network:
        aliases:
          - api
          - api-service
```

Now the backend can be reached as `backend`, `api`, or `api-service` on the `frontend-network`. This is useful when migrating service names — you can add the new name as an alias while old code still references the old name, then remove the alias once everything is updated.

### `ipam` — Controlling Subnets

By default Compose assigns subnets automatically (`172.18.0.0/16`, `172.19.0.0/16`, etc.). You can control this explicitly if needed:

```yaml
networks:
  backend-network:
    ipam:
      config:
        - subnet: 172.20.0.0/24
```

You rarely need this in development. It becomes relevant when Docker's auto-assigned subnets conflict with your company's VPN or office network ranges — a surprisingly common production headache.

### The `driver` Key

Networks have a driver that controls their behavior. The default is `bridge` which is what we always use. Other options:

- `host` — removes isolation, container shares host network (Linux only)
- `overlay` — for Docker Swarm, spans multiple machines
- `none` — no networking

For single-machine Docker Compose, you will always use `bridge` (the default). You only need to specify it explicitly if you want to pass additional driver options:

```yaml
networks:
  frontend-network:
    driver: bridge
    driver_opts:
      com.docker.network.bridge.name: my-custom-bridge
```


## 6. Commands

```bash
# ── Inspecting Networks Created by Compose ────────────────────────────────

docker network ls                              # see all networks including Compose ones
docker network inspect <network-name>          # see members, IPs, config

# ── Testing Connectivity Between Services ─────────────────────────────────

docker compose exec <service> ping <other-service>
docker compose exec <service> /bin/sh
# inside: wget -qO- http://<service>:<port>/path

# ── Checking Which Networks a Container Is On ─────────────────────────────

docker inspect <container> --format='{{json .NetworkSettings.Networks}}' | python3 -m json.tool

# ── Working With External Networks ────────────────────────────────────────

docker network create shared-network           # create manually first
# then in docker-compose.yml mark it as external: true

# ── Full Workflow ──────────────────────────────────────────────────────────

docker compose up -d
docker compose ps
docker compose exec frontend ping backend      # verify connectivity
docker compose exec frontend ping database     # verify isolation
docker compose down
```



## 7. Real-World Notes

Network isolation is a security practice, not just an organisational one. In a production breach, if an attacker compromises the frontend container, network isolation stops them from directly querying your database. They would have to also compromise the backend to get there. Layers of isolation buy you time and limit blast radius.

The three-tier architecture you set up in this step — frontend network, backend network, database only on backend network — is the standard pattern for web applications. Frontend talks to backend, backend talks to database, frontend never touches database directly. You will see this in every serious Docker deployment.

In Kubernetes, the equivalent concept is Network Policies — explicit rules about which pods can communicate with which other pods. The mental model is identical to what you learned here, just with a different syntax and enforcement mechanism. Having understood Docker network isolation makes Kubernetes Network Policies immediately intuitive.



## 8. Exercises

**Exercise 1 — Inspect the default network**
Use the step 08 `docker-compose.yml` with no explicit networks. Bring it up and run `docker network ls`. Find the auto-created network. Run `docker network inspect` on it and find both service containers listed as members. Confirm `127.0.0.11` is the DNS server. You are looking at exactly what Compose created silently for you in step 08.

**Exercise 2 — Prove unrestricted default access**
With the three-service YAML (frontend, backend, database on default network), exec into the frontend and ping the database. Confirm it works. This is what you are about to fix.

**Exercise 3 — Implement network isolation**
Rewrite the YAML with `frontend-network` and `backend-network` as shown in this step. Bring it down and back up. Run three tests: frontend can reach backend, frontend cannot reach database, backend can reach both. All three should behave as expected. Run `docker network ls` and confirm two separate networks were created.

**Exercise 4 — Inspect a multi-network container**
After implementing isolation, run:
```bash
docker inspect 09-compose-networking-backend-1 --format='{{json .NetworkSettings.Networks}}' | python3 -m json.tool
```
You will see two network entries for the backend — one for each network it belongs to, each with a different IP address. This is what it looks like for a container to bridge two networks.

**Exercise 5 — External network**
Create a network manually: `docker network create shared-net`. Write a minimal `docker-compose.yml` with one service and mark `shared-net` as external. Bring it up. Confirm the service joined the externally created network. Then bring it down — confirm `docker network ls` still shows `shared-net` (Compose does not remove external networks on `down`).

**Exercise 6 — Break the alias, understand naming**
Add an alias to the backend on `frontend-network`:
```yaml
networks:
  frontend-network:
    aliases:
      - api
```
Exec into the frontend and run `ping api`. It resolves. Now run `ping backend` — it still resolves too. Both names work simultaneously. Remove the alias, bring it back up, confirm `ping api` now fails while `ping backend` still works.