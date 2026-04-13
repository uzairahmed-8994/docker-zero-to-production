# 01 - Basic Flask App

## 1. What Problem It Solves

Before Dockerizing an application, we first need a working application.
This step creates a minimal Flask application that will evolve throughout this repository.

This ensures:

* Application works locally
* Dependencies are defined
* Base application exists for containerization



## 2(a). Explanation

This is a simple Flask web server that:

* Runs on port 5000
* Listens on 0.0.0.0
* Returns a basic HTTP response

Why `0.0.0.0`?

Because later Docker containers require the app to listen on all interfaces, not just localhost.

## 2(b) Environment Setup (Virtual Environment - venv)

This project uses a Python virtual environment (`venv`) to isolate dependencies.

### Why this is needed

Without `venv`, installing Python packages globally causes problems like:

* Different projects requiring different Flask versions
* Breaking system Python packages
* Dependency conflicts between projects



### Real Problem Example (Without venv)

Imagine:

Project A needs:

* Flask 2.0

Project B needs:

* Flask 3.0

If installed globally:

```bash
pip install flask==2.0
```

Then:

```bash
pip install flask==3.0
```

Project A breaks.



### How venv solves this

Each project gets its own isolated environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Now:

* Flask installed in Project A does NOT affect Project B
* Each project has its own dependencies



### Simple Analogy

Think of `venv` like:

> A separate toolbox for each project
> Instead of mixing all tools in one box



### When to use venv

* Python projects
* Local development
* Before Docker containerization

Note: In Docker, this is not required because containers already isolate environments.



## 3. Documentation Links

* Flask Documentation
  https://flask.palletsprojects.com/

* Python Virtual Environment
  https://docs.python.org/3/library/venv.html



## 4. Simple Example

Run locally:

```bash
python app.py
```

Open browser:

```
http://localhost:5000
```

Output:

```
Docker Zero to Production - Step 1
```



## 5. Common Mistakes

Common beginner mistakes:

* Using `127.0.0.1` instead of `0.0.0.0`
* Not creating virtual environment
* Not pinning dependencies in requirements.txt



## 6. Real World Scenarios

This mirrors real-world workflow:

1. Build application
2. Test locally
3. Containerize
4. Deploy

This step covers **Step 1 — Build application**



## 7. Exercises

Try:

* Change response message
* Add another route `/health`
* Change port to 8000

Example:

```
http://localhost:5000/health
```



## 8. Practical Implementation

Files created:

```
01-basic-flask-app/
├── app.py
├── requirements.txt
└── venv/
```

Run commands:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```


## 9. Troubleshooting

Problem:

```
ModuleNotFoundError: No module named flask
```

Solution:

```bash
pip install -r requirements.txt
```



## 10. Production Best Practices

* Always use virtual environments
* Pin dependencies
* Test locally before containerizing
* Keep application minimal initially
