# 05 — Docker Networking
 
## Goal of This Step
 
Understand how containers talk to each other, why they sometimes can't, and how Docker's networking model actually works, by breaking things first and then fixing them properly.

 
## 1. What Problem It Solves
 
You have two containers running. Your frontend needs to talk to your backend. Your app needs to reach your database. How do containers find each other?
 
On your laptop, two apps can reach each other on `localhost`. 
Inside Docker, each container is isolated — it has its own network stack, its own IP, its own localhost. 
So how do you connect them? And more importantly, in a real system you don't want to hardcode IP addresses. You want to say "talk to the database" not "talk to 172.17.0.3" — because that IP can change every time a container restarts.
  
In the previous step, we learned how to access a container from the browser using port mapping.

But that only solves communication between host → container.

It does NOT solve container → container communication.
 
## 2. What Happened (Experience)
 
I had the Flask app from the previous steps. I wanted to run two instances and have them reach each other — simulating what it would feel like for two services to communicate.
 
I ran both containers:
 
```bash
docker run -d --name app1 flask-app:v1
docker run -d --name app2 flask-app:v1
```
 
Both were running fine. Then I tried to ping `app2` from inside `app1`:
 
```bash
docker exec -it app1 /bin/sh
ping app2
```
 
```
ping: app2: Name or service not known
```
<details>Ping is used here as a simple way to test basic network connectivity.

If ping works:
- the containers can reach each other over the network

If it fails:
- either networking or name resolution is broken

This does NOT mean the application itself is working — only that the network path exists. </details>


The container is named `app2`. It's running. Why can't `app1` find it by name?
 
At this point, this was confusing.

The containers were clearly running. 
```bash
docker ps
CONTAINER ID   IMAGE          COMMAND           CREATED              STATUS              PORTS      NAMES
1aee52c13b5a   flask-app:v1   "python app.py"   3 seconds ago        Up 2 seconds        5000/tcp   app2
0a90c841739e   flask-app:v1   "python app.py"   About a minute ago   Up About a minute   5000/tcp   app1
```

So either:
- Docker networking was broken
- or I misunderstood how container naming works

To verify whether networking itself was working, I checked the container IPs:

```bash
docker network inspect bridge
#           "Name": "app1", IPv4Address: 172.17.0.2
#           "Name": "app2", IPv4Address: 172.17.0.3
```
 
Then I tried using the IP directly:

```bash
ping 172.17.0.3
# 64 bytes from 172.17.0.3: seq=0 ttl=64 time=0.091 ms
```
 
It worked.

So the containers can reach each other — just not by name.

That made the situation more confusing:

- the network is working
- but name-based communication is not


**What’s the difference between using a name and using an IP?** Why does one work and the other fail?

 
## 3. Why It Happens
 
When you run a container without specifying a network, Docker attaches it to a built-in network called the **bridge** network (also called `docker0`). This is the default.
 
The default bridge network is basic. It gives containers IP addresses and lets them reach each other by IP — but it has **no DNS**. There is no name resolution. Containers on the default bridge are essentially strangers who happen to live on the same subnet. They can communicate if you know the exact address, but nobody knows anyone's name.
 
When you create a **custom bridge network**, Docker does something different. It runs an internal DNS server for that network. Every container you attach to a custom network gets registered by its name in that DNS server. So when one container tries to reach another by name, Docker’s internal DNS resolves it to the correct IP automatically — and keeps it updated even if the container restarts and gets a new IP.
 
This is not a small detail. It's the entire foundation of how multi-container apps work in Docker.
 
So the failure was not because containers cannot communicate.

It was because:

- communication by IP works
- communication by name requires DNS
- and DNS only exists in custom networks


## 4. Solution

Since the problem is lack of DNS in the default bridge network, the solution is to use a network where Docker provides automatic name resolution.

The default bridge network is fine for simple cases, but it lacks DNS-based name resolution.

For any scenario where containers need to discover each other reliably, a custom network should be used. 
 
```bash
# Create a network
docker network create my-network
 
# Run containers inside that network:
docker run -d --name app3 --network my-network flask-app:v1
docker run -d --name app4 --network my-network flask-app:v1
 
# Now containers can resolve each other by name:
docker exec -it app3 ping app4
# 64 bytes from app4 (172.18.0.3): seq=0 ttl=64 time=0.078 ms
```
 
You can also attach an already-running container to a network:
 
```bash
docker network connect my-network app1
```
 
And detach it:
 
```bash
docker network disconnect my-network app1
```
 
 
## 5. Deep Understanding
 
### Every Container Gets Its Own Network Stack
 
When Docker creates a container, it creates a completely isolated network namespace for it. This means the container has:
 
- Its own network interfaces (you'll see `eth0` and `lo` inside it)
- Its own IP address
- Its own routing table
- Its own `localhost` — which means `localhost` inside a container refers to *that container only*, not your laptop
This is why you can run ten containers all listening on port 5000 — they don't conflict because each one's port 5000 lives in its own isolated namespace.
 
### The Default Bridge — What It Actually Is
 
When Docker installs, it creates a virtual network interface on your host called `docker0`. This is a Linux bridge — think of it as a virtual network switch. Every container on the default bridge gets a virtual ethernet pair: one end lives on the host and plugs into `docker0`, the other end lives inside the container as `eth0`.
 
```
Your Laptop
│
├── docker0 (172.17.0.1)  ← virtual switch
│     ├── vethXXXX ───── eth0 (172.17.0.2)  ← app1
│     └── vethYYYY ───── eth0 (172.17.0.3)  ← app2
```
 
They can ping each other by IP because they're on the same bridge. But there's no DNS — no way to say "I want to reach app2" and have it resolved to an IP automatically.
 
### Custom Networks and Docker's Embedded DNS
 
When you create a custom bridge network, Docker writes a special nameserver into every container on that network:
 
```bash
# Inside any container on a custom network:
cat /etc/resolv.conf
# nameserver 127.0.0.11
```
 
`127.0.0.11` is Docker's **embedded DNS server**. It runs inside the container's network namespace and knows about every other container on the same custom network. When `app3` pings `app4`, the OS queries `127.0.0.11`, Docker resolves `app4` to its current IP, and the connection goes through.
 
```
my-network
│
├── app3 (172.18.0.2)
│     └── /etc/resolv.conf → nameserver 127.0.0.11
│
└── app4 (172.18.0.3)
      └── /etc/resolv.conf → nameserver 127.0.0.11
```
 
The default bridge has no such DNS. That's the entire difference between your `app1`/`app2` failure and your `app3`/`app4` success.
 
### Why IP Addresses Are Unreliable
 
Container IPs are assigned at runtime and change every time a container is recreated. If your app hardcodes `172.17.0.3` as the database address and the database container restarts, it might come back as `172.17.0.4`. Your app breaks silently.
 
Name-based DNS on custom networks solves this permanently. The name `db` always resolves to whichever container is currently running with that name, regardless of its IP. This is exactly how Docker Compose works — it creates a custom network for your stack automatically and lets every service reach every other by service name.
 
### Network Isolation Between Networks
 
Different custom networks are completely isolated from each other. A container on `network-a` cannot reach a container on `network-b` even if both run on the same machine. This is intentional — it gives you security separation between different application stacks.
 
If a container needs to span two networks (like a reverse proxy routing to multiple apps), you attach it to both:
 
```bash
docker network connect network-b nginx-proxy
```
 
Now `nginx-proxy` can reach containers on both networks, but the two networks still can't reach each other directly.
 
### Host and None — The Other Modes
 
**`--network host`** removes all network isolation. The container shares your machine's network stack directly — port 5000 in the container is port 5000 on your host, no `-p` mapping needed. Linux only (doesn't work on Docker Desktop for Mac/Windows).
 
**`--network none`** gives the container no network at all. No interfaces, no internet, no communication with anything. Used for security-sensitive workloads.
 
---
 
## 6. Commands
 
```bash
# ── Managing Networks ──────────────────────────────────────────────────────
 
docker network ls                              # list all networks
docker network create my-network              # create a custom bridge network
docker network inspect my-network             # see containers, IPs, subnet
docker network rm my-network                  # delete a network
docker network prune                          # delete all unused networks
 
# ── Running Containers on a Network ───────────────────────────────────────
 
docker run -d --name app3 --network my-network flask-app:v1
docker network connect my-network app1        # attach a running container
docker network disconnect my-network app1     # detach a running container
 
# ── Verifying Connectivity ─────────────────────────────────────────────────
 
docker exec -it app3 ping app4                # name resolution (custom net)
docker exec -it app3 cat /etc/resolv.conf     # confirm 127.0.0.11 DNS
 
# ── Finding Container IPs ──────────────────────────────────────────────────
 
docker inspect app2 --format='{{.NetworkSettings.IPAddress}}'
docker inspect app3 --format='{{.NetworkSettings.Networks.my-network.IPAddress}}'
```
 
---
 
## 7. Real-World Notes
 
In real projects you almost never manage networks this manually. Docker Compose creates a custom network for your entire stack automatically — every service reaches every other by its service name. But knowing *why* that works means you'll never be confused when a connection fails.

For example, in a real application:

- frontend talks to backend using: http://backend:8000
- backend talks to database using: postgres://db:5432

These names (backend, db) are container names resolved by Docker's internal DNS.
 
Hardcoding container IPs in application config is a serious mistake in any containerized environment. Always use service names — `postgresql://db:5432/mydb`, `http://backend:8000`, etc. The name is stable. The IP is not.
 
When something can't connect in production, the first question is always "can it resolve the name?" — not "is the port open?". Most connection failures in containerized systems are DNS failures, not port or firewall issues.
 
---
 
## 8. Exercises
 
**Exercise 1 — Reproduce the failure**
Run `app1` and `app2` with no `--network` flag. Exec into `app1` and try `ping app2`. Confirm it fails with "Name or service not known". Then get `app2`'s IP with `docker inspect` and ping by IP. It works. Sit with that difference.
 
**Exercise 2 — Fix it with a custom network**
Create `my-network`. Run `app3` and `app4` on it. Exec into `app3` and ping `app4` by name. Then run `cat /etc/resolv.conf` inside the container. You'll see `nameserver 127.0.0.11`. That's Docker's embedded DNS — you just found it.
 
**Exercise 3 — Watch DNS survive a restart**
Run a container on a custom network and note its IP. Stop and remove it. Run a new container with the same name. Its IP will likely be different. Ping it by name from another container on the same network. It still resolves. The name is stable even though the IP changed.
 
**Exercise 4 — Network isolation**
Create `net-a` and `net-b`. Run `container-a` on `net-a` and `container-b` on `net-b`. Try to ping `container-b` from `container-a` — it should fail completely. Now run a third container attached to both networks. Can it reach both? This is the mental model for how a reverse proxy works in Docker.
 
**Exercise 5 — Inspect the default bridge**
Run `docker network inspect bridge`. Find your `app1` and `app2` containers listed with their IPs. Notice there's no DNS configuration. Now run `docker network inspect my-network` and compare. The difference in the configuration is exactly why one works and the other doesn't.