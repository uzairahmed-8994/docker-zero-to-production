# 06 - Container Debug Basics

## 0. Goal of This Step

Learn how to debug containers when they don’t behave as expected.

By the end of this step, you should be able to:

- Understand what a container is actually doing
- Investigate failures instead of guessing
- Enter a running container and verify its state
- Identify root causes of common issues

---

## 1. What Problem It Solves

Up to now, running containers felt simple.

But real issues start appearing very quickly:

- Container runs but app is not accessible
- Container exits immediately
- Code changes are not reflected
- Application behaves differently inside container

At this point, Docker becomes confusing.

> “Container is running… but nothing works.”

Without debugging skills, you are stuck.

---

## 2. What Happened (Real Scenarios)

Instead of one issue, I faced multiple small problems.

---

### Scenario 1 — Container exits immediately

```bash
docker run ubuntu
```

Output:

> Nothing… container just exits

Confusion:

- Did it fail?
- Did it even start?

### Scenario 2 — App is running but not accessible
```bash
docker run flask-app:v1
```
Logs:

> Running on http://0.0.0.0:5000

Browser:
```bash
localhost:5000 → not working
```
This happens because the container is not exposing its port to the host.

Even though Flask is running on port 5000 inside the container, it is not accessible from the browser unless we map the port using -p.


### Scenario 3 — Code updated but container shows old behavior
- Modified app.py
- Rebuilt image
- Ran container

Still seeing old response
This creates confusion:

- Did the image rebuild correctly?
- Is Docker using cache?
- Am I running an old container?

### Scenario 4 — App crashes inside container
- Introduced error (wrong import)
- Container starts and stops quickly
No visible error in terminal

In all cases, the real issue was:

>I could not see what was happening inside the container

### Reproducing Issues (Optional)

You can intentionally create failures to practice debugging:

- Change Flask port (e.g., 8000 instead of 5000)
- Add a wrong import to crash the app
- Change response text and rebuild image

This helps in understanding how debugging tools work in real scenarios.

## 3. Why It Happens

### Containers are isolated systems

A container is not your local machine.

It has:

- Its own filesystem
- Its own runtime
- Its own environment

So when something breaks:

You don’t see it unless you inspect it

### Containers run a single main process

A container lives as long as its main process runs.

Example:
```bash
docker run ubuntu
```
Why does it exit?

Because:
There is no long-running process

### Important concept — PID 1

Inside a container, your application becomes:

>Process ID 1 (PID 1)

This is important because:

- If PID 1 exits → container stops
- If PID 1 crashes → container dies immediately
Container lifecycle = application lifecycle

### Logs are not automatically visible

If container runs in background or crashes:

You won’t see errors unless you explicitly check logs

### Docker caching can hide changes

When rebuilding images:

- Docker may reuse cached layers
- Old code might still exist in image
- Old container might still be running

You think code changed, but runtime didn’t

## 4. Solution

To debug containers, we use three core tools.

### 1. docker logs → See what actually happened
```bash
docker logs <container_id>
```

This shows:

- Application output
- Errors
- Crash messages

Example:
>ImportError: module not found

Immediately explains crash

### 2. docker exec -it → Go inside the container
```bash
docker exec -it <container_id> sh
```

Now you can:

- Inspect files
- Run commands
- Verify environment

### What does docker exec -it actually mean?

Break it down:

- exec → run a command inside a running container
- -i → interactive (keeps input open)
- -t → terminal (gives shell experience)

So:
```bash
docker exec -it container sh
```

means:
“Give me a terminal inside this running container”

### Why not SSH?

Containers are NOT virtual machines.

They:

- do not run SSH server
- do not expose SSH ports
- are just processes
docker exec replaces the need for SSH in containers

### 3. docker inspect → Check container configuration
```bash
docker inspect <container_id>
```

Used when:

- Behavior doesn’t match expectation
- Need to verify environment / networking

### Debugging container that exits immediately

If container exits too fast, you cannot use docker exec.

In that case, override the command:

```bash
docker run -it --entrypoint sh flask-app:v1
```
This starts the container with a shell instead of your app.

Now you can manually run:

```bash
python app.py
```

### Fixing the scenarios
- Scenario 1 → Understand main process (container exits)
- Scenario 2 → Use port mapping (-p)
- Scenario 3 → Rebuild properly and remove old containers
- Scenario 4 → Use logs to find error


## 5. Deep Understanding

### Debugging is about truth, not assumptions

Every issue comes down to:

> Expected behavior vs Actual behavior

Your job is to find the difference.

### What docker logs actually captures

Docker only captures:

STDOUT (normal output)
STDERR (errors)

If your app logs to files:
docker logs will NOT show it

### What happens when you run docker exec

You are NOT entering a new system.

Docker:

- attaches your terminal to the container
- starts a new process inside it
- shares the same environment

You are inside the same namespace as the app

This means:

- You see the same files as the application
- You share the same network environment
- You are interacting with the same running container

You are not entering a different system — you are accessing the same one from inside.

### OverlayFS (filesystem behavior)

Docker images are layered.

When a container runs:

- Base image layers → read-only
- Container adds → writable layer

If you create a file inside container:
```bash
echo "test" > file.txt
```
It exists only in that container.

If container is removed:

File is gone

This is why:

Containers are ephemeral (temporary)

### Debugging workflow (mental model)

When something fails:

>1. Is container running? (docker ps)
>2. What happened? (docker logs)
>3. Verify inside container (docker exec)
>4. Check configuration (docker inspect)


### Why this matters for next steps

In multi-container systems:

- multiple containers
- multiple logs
- multiple failure points

Without debugging:

You cannot identify where the issue is

## 6. Commands
```bash
docker ps
docker ps -a
docker logs <container_id>
docker logs -f <container_id>
docker exec -it <container_id> sh
docker inspect <container_id>
docker run -d flask-app:v1
```

## 7. Real-World Notes
- Always check logs first
- Containers fail silently if not inspected
- Do not treat containers like servers
- Avoid debugging blindly — always verify

## 8. Exercises
- Run ubuntu container and understand why it exits
- Break Flask app and debug using logs
- Enter container and explore filesystem
- Create file inside container → remove container → verify it’s gone
- Modify app → rebuild → ensure changes reflect

## Key Takeaways
- A running container does not mean a working application
- Container lifecycle depends on main process (PID 1)
- docker logs shows actual behavior
- docker exec -it allows inspection inside container
- Containers are temporary (OverlayFS behavior)


## Final Note

Debugging is not about memorizing commands.

It is about learning how to:

- observe systems
- verify assumptions
- identify root causes
