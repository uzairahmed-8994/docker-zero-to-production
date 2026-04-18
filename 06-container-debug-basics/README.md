# 06 — Container Debug Basics


## 0. Goal of This Step

Learn how to see what is actually happening inside a running container, read logs when something breaks, get a shell inside a container to investigate, and figure out why a container crashed or won't start — using the same Flask app we have been working with.


## 1. What Problem It Solves

In the previous steps, we were able to run containers successfully.

But that only showed the happy path.

As soon as something breaks, Docker becomes confusing — because everything runs inside an isolated environment.

A container starts. Or it doesn't. Or it starts and dies 3 seconds later with no explanation.

Without debugging skills, Docker is a black box. You run `docker run` and either it works or it doesn't — and you have no idea why. You can't just open a file manager or click around. Everything is inside an isolated environment.

This step gives you the tools to answer:

- Is my container actually running?
- What is it doing right now?
- Why did it crash?
- Is my code even inside the container?
- What error happened at startup?

Debugging is what separates someone who *uses* Docker from someone who actually *understands* it.



## 2. What Happened (Experience)

We have the Flask container running from the previous steps. Everything looks fine on the surface:

```bash
docker run -d -p 5000:5000 --name flask-app flask-app:v1

docker ps
# CONTAINER ID   IMAGE          COMMAND           STATUS         PORTS
# 64f8f3e32888   flask-app:v1   "python app.py"   Up 2 seconds   0.0.0.0:5000->5000/tcp
```

But now let's simulate what actually happens in real life — things breaking.



**Scenario 1 — Container exits immediately after starting**

Let's break the app intentionally. Open `app.py` and introduce a syntax error:

```python
from flask import Flask

app = Flask(__name__)

@app.route("/")
def home()  # missing colon — syntax error
    return "Docker Zero to Production - Step 1"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

Rebuild and run:

```bash
docker build -t flask-app:v1 .
docker run -d -p 5000:5000 --name flask-broken flask-app:v1
```

Then check running containers:

```bash
docker ps
# (flask-broken is not here)
```

It's gone. No error message. No warning. It just disappeared.

This is the most common beginner panic in Docker. The container ran, crashed, and stopped — all in under a second.



**Scenario 2 — You can't tell what went wrong**

You run `docker ps` again hoping to see the container. Nothing. You rebuild, run again. Same thing. The container just won't stay alive and Docker gives you no feedback at all.

Docker did capture the error — but it did not show it in the terminal because the container exited quickly.

The error exists inside the container logs, but you have to explicitly ask Docker to show it.




**Scenario 3 — Old code still showing after a rebuild**

You fix the syntax error, rebuild the image, run the container — and the old broken behavior is still happening. You're confused. Did the build fail? Is Docker using a cached version? Why is nothing changing?



## 3. Why It Happens

**Scenario 1 — Container exits immediately:**

A container is not a virtual machine running in the background. A container runs **exactly one process**. The moment that process stops — for any reason, including a crash — the container stops with it. There is no restart, no retry, no warning. Docker just reports it as stopped.

In our case, Python fails to parse `app.py` because of the syntax error, exits with an error code, and the container stops immediately. From the outside it looks like nothing happened.

**Scenario 2 — No feedback:**

`docker ps` only shows *running* containers. A crashed container is stopped, so it doesn't appear. The crash output — the actual Python traceback — was printed to the container's stdout right before it died. Docker captured it. But you never asked to see it.

**Scenario 3 — Stale behavior after rebuild:**

You rebuilt the image but are still running a container that was created from the *old* image. `docker run` creates a brand new container from the current image — but if a container with that name already exists (even stopped), the old one is still there. The image changed but the container was never recreated from the new image.



## 4. Solution

### Fix Scenario 1 & 2 — Read the logs

The first thing to do when a container disappears is check all containers, not just running ones:

```bash
docker ps -a
# CONTAINER ID   IMAGE          COMMAND           STATUS                     NAMES
# 3f2a1c9d8b7e   flask-app:v1   "python app.py"   Exited (1) 4 seconds ago   flask-broken
```

`-a` means "all" — shows all containers (running, stopped, crashed). Now you can see it exited with code `1` which means the process crashed.

Now read what it printed before dying:

```bash
docker logs flask-broken
```

```
  File "/app/app.py", line 6
    def home()  # missing colon
              ^
SyntaxError: invalid syntax
```

There it is. The full Python traceback, exactly as if you had run the app locally. Docker captured everything the process printed and saved it — you just had to ask.

### Fix Scenario 3 — Remove the old container, run fresh

A container is created from an image at a specific point in time.

When you rebuild an image, existing containers are **not** updated automatically.

This means:

- the image may be new
- but the container may still be running the old version

To use the updated image, you must remove the old container and create a new one.


```bash
# Remove the old stopped container
docker rm flask-broken

# Rebuild the image
docker build -t flask-app:v1 .

# Run a fresh container from the new image
docker run -d -p 5000:5000 --name flask-app flask-app:v1
```

Rule to remember: rebuilding an image does not change containers that already exist. A container must be recreated to use the new image.



### Getting Inside a Running Container

Once the container is running normally, you can open a shell inside it:

```bash
docker exec -it flask-app /bin/sh
```

You are now inside the container's filesystem. Try these:

```bash
ls /app
# app.py  requirements.txt

cat /app/app.py
# your actual source code

ps aux
# PID   USER     COMMAND
# 1     root     python app.py
```

Type `exit` to leave. The container keeps running — you just closed your shell session inside it.

This is how you verify that your files are in the right place, your process is running as expected, and the environment looks the way you think it does.



## 5. Deep Understanding

### Where Do `docker logs` Come From?

Docker captures anything your process writes to **stdout** and **stderr**. That is it — nothing else.

If your app writes logs to a file inside the container (`/var/log/app.log` for example), `docker logs` will not show them. They exist only inside the container's filesystem, invisible to Docker's logging system.

This is why the Docker convention is to always log to stdout. Flask does this by default. In production apps you configure your logger explicitly to write to stdout so the container runtime can collect it, forward it to a centralized system, or display it with `docker logs`.

Useful variations:

```bash
docker logs flask-app            # all logs since the container started
docker logs -f flask-app         # follow live output (like tail -f)
docker logs --tail 50 flask-app  # last 50 lines only
docker logs -t flask-app         # include timestamps on every line
```

### Exit Codes Tell You What Happened

When a container stops, it leaves behind an exit code — the same code the process returned when it exited. This is your first clue when something goes wrong:

| Exit Code | Meaning |
|-----------|---------|
| 0 | Clean exit — process finished normally |
| 1 | General error — application crashed |
| 127 | Command not found — CMD points to something that doesn't exist |
| 137 | Container was killed — OOM or `docker stop` timed out and sent SIGKILL |
| 143 | Received SIGTERM — graceful shutdown was requested |

Exit code `1` with a Python traceback in the logs means your app crashed.
Exit code `127` means your Dockerfile CMD is pointing to a wrong path or a binary that isn't installed.
Exit code `137` means something killed the container forcefully from outside.

You can read the exit code directly:

```bash
docker inspect flask-broken --format='{{.State.ExitCode}}'
# 1
```

### `docker exec` — What It Actually Does

When you run `docker exec -it flask-app /bin/sh` you are not SSH-ing into a separate machine. You are joining the **same Linux namespaces** as the already-running container process. Same filesystem, same network, same process tree — you are literally entering the same isolated environment.

This means:
- If the container stops while you are inside it, your shell session dies too
- Changes you make inside (creating files, installing packages) affect the running container immediately
- But those changes live in the container's writable layer and are lost when the container is removed — the image stays untouched

The `-i` flag keeps stdin open so you can type. The `-t` flag allocates a proper terminal so output formats correctly. Together `-it` gives you an interactive shell.

Without those flags, `exec` is used for running a single command and capturing its output:

```bash
docker exec flask-app cat /app/app.py
docker exec flask-app env
docker exec flask-app ps aux
```

### Debugging a Container That Won't Start

Sometimes the container crashes so fast you cannot exec into it. The trick is to override the startup command at runtime:

```bash
docker run -it --entrypoint /bin/sh flask-app:v1
```

Instead of running your app, Docker opens a shell. You are now inside the image environment — same files, same Python installation, same everything — but your application is not running. You have full control:

```bash
ls /app               # are the files actually here?
python --version      # is Python what you expected?
python app.py         # run it manually and see the exact error
```

This is the most powerful technique for debugging images that refuse to start. You get to investigate the exact environment the container would run in, interactively, with no time pressure.

### Every Container Has a Writable Layer That Disappears

When a container runs, Docker adds a thin writable layer on top of the read-only image layers. Any files you create or modify inside the container go into this layer only. When you remove the container, this layer is permanently deleted. The image underneath is completely untouched.

```bash
# Create a file inside the running container
docker exec flask-app sh -c "echo 'i will disappear' > /app/test.txt"
docker exec flask-app cat /app/test.txt
# i will disappear

# Remove and recreate the container
docker rm -f flask-app
docker run -d -p 5000:5000 --name flask-app flask-app:v1

# The file is gone — it lived in the old container's writable layer
docker exec flask-app cat /app/test.txt
# cat: /app/test.txt: No such file or directory
```

This is not a bug. This is intentional design. Containers are meant to be stateless and disposable. Anything that needs to survive a container restart must be stored outside the container — in a volume, which we cover in a later step.

### `docker inspect` — The Full Picture

Every container has a complete JSON record of its configuration and current state. `docker inspect` exposes everything:

```bash
docker inspect flask-app
```

The raw output is very long. Use `--format` to extract exactly what you need:

```bash
# What IP does this container have?
docker inspect flask-app --format='{{.NetworkSettings.IPAddress}}'

# What exit code did it die with?
docker inspect flask-broken --format='{{.State.ExitCode}}'

# What environment variables does it have?
docker inspect flask-app --format='{{.Config.Env}}'

# What image hash is this container actually running?
docker inspect flask-app --format='{{.Image}}'
```

The last one is useful for Scenario 3. If you rebuild an image and the container still shows old behavior, compare the image hash from `docker inspect` with the current hash from `docker images`. If they are different, the container was created from the old image and needs to be removed and recreated.

### Debugging Workflow

When something does not work, follow this order:

1. Check if container is running
   docker ps

2. Check all containers
   docker ps -a

3. Read logs
   docker logs <container>

4. Enter container if needed
   docker exec -it <container> sh

5. Inspect configuration
   docker inspect <container>

This structured approach avoids random guessing.


## 6. Commands

```bash
# ── Checking Container State ───────────────────────────────────────────────

docker ps                          # running containers only
docker ps -a                       # all containers including stopped ones
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# ── Reading Logs ───────────────────────────────────────────────────────────

docker logs <name>                 # all logs
docker logs -f <name>              # follow live
docker logs --tail 50 <name>       # last 50 lines
docker logs -t <name>              # with timestamps

# ── Getting Inside a Container ─────────────────────────────────────────────

docker exec -it <name> /bin/sh     # interactive shell (slim images)
docker exec -it <name> /bin/bash   # if bash is available
docker exec <name> cat /app/app.py # run single command, no shell

# ── Debugging Images That Won't Start ─────────────────────────────────────

docker run -it --entrypoint /bin/sh flask-app:v1

# ── Inspecting State ───────────────────────────────────────────────────────

docker inspect <name>                                       # full JSON
docker inspect <name> --format='{{.State.ExitCode}}'
docker inspect <name> --format='{{.NetworkSettings.IPAddress}}'
docker inspect <name> --format='{{.Config.Env}}'
docker inspect <name> --format='{{.Image}}'

# ── Cleanup ────────────────────────────────────────────────────────────────

docker rm <name>                   # remove a stopped container
docker rm -f <name>                # force remove a running container
docker container prune             # remove all stopped containers
```


## 7. Real-World Notes

In production you almost never `exec` into a running container to fix something. If you are making live fixes inside a running container, something is wrong with your process. Containers are meant to be immutable — when something breaks, you fix the image, rebuild, and redeploy. Use `exec` for investigation only, never for making changes that need to persist.

`docker logs` works fine for a single container on a single machine. In production with dozens or hundreds of containers across multiple servers, you need a centralized logging system. Tools like Datadog, the ELK stack, or AWS CloudWatch collect stdout from every container automatically and put it in one searchable place. This only works because containers log to stdout. That convention exists precisely to make centralized log collection simple.

Exit codes matter in automated pipelines. A CI/CD system (GitHub Actions, Jenkins, GitLab CI) runs your container and checks the exit code. Code `0` means success, anything else means failure and the pipeline stops. This is why your app should exit cleanly with `0` on a graceful shutdown and with a non-zero code on actual failure — the pipeline depends on it.



## 8. Exercises

**Exercise 1 — Reproduce the crash, find it in logs**
Introduce a syntax error in `app.py` (remove a colon from a function definition). Rebuild the image. Run the container in detached mode. Watch it not appear in `docker ps`. Find it with `docker ps -a`. Read the exact Python error with `docker logs`. Fix the code, rebuild, confirm it works again.

**Exercise 2 — Explore the container from inside**
Start the flask container normally. Exec into it with `/bin/sh`. Confirm your source files are at `/app`. Run `ps aux` and find `python app.py` listed as PID 1. Run `env` to see what environment variables exist inside the container. Run `cat /etc/resolv.conf` — you should recognize `127.0.0.11` from the networking step if you ran this on a custom network.

**Exercise 3 — The ephemeral filesystem**
Exec into a running container and create a file: `echo "i will disappear" > /app/test.txt`. Confirm it exists with `cat /app/test.txt`. Now remove the container with `docker rm -f` and run a fresh one from the same image. Try to read the file again. It is gone. The image is untouched. This is the most important mental model in Docker.

**Exercise 4 — Debug a container that won't start**
Change your Dockerfile CMD to point to a file that does not exist:
```dockerfile
CMD ["python", "doesnotexist.py"]
```
Build and run. It exits immediately. Check the exit code with `docker ps -a`. Read the error with `docker logs`. Then use `--entrypoint /bin/sh` to get inside the image and look around — confirm the file is genuinely missing. Fix the Dockerfile, rebuild, confirm it works.

**Exercise 5 — Understand exit codes**
Run a container normally then stop it with `docker stop`. Check the exit code with `docker inspect --format='{{.State.ExitCode}}'`. It should be `0` or `143`. Now run a container with a broken app and check its exit code — it will be `1`. Notice the difference: `0` means clean stop, anything else means something went wrong. This is the exact signal that CI/CD pipelines use to decide if your deployment succeeded.