# Campus Team Finder — Flask + Neo4j backend

This backend follows the frontend/Neo4j handoff contract supplied by the project team. It maps the existing graph schema to the JSON fields expected by React.

## Endpoints

All endpoints use the `/api` prefix.

- `GET /api/auth/me` (temporary demo identity; no login)
- `GET /api/projects`
- `GET /api/applications/mine`
- `GET /api/applications/received`
- `GET /api/skills`
- `POST /api/projects`
- `PATCH /api/projects/<id>`
- `POST /api/projects/<id>/applications`
- `PATCH /api/applications/<id>`
- `PATCH /api/users/me`
- `GET /api/health` (deployment check)

Login is intentionally disabled for now. For the classroom demo only, endpoints that need a current student expect `X-Student-Id: S001` (use an existing Student ID). This is not secure authentication: anyone who knows a student ID can impersonate that student. Do not use this mode for real users or public production data. User/project/application IDs are returned as strings, status values as lowercase strings, and dates as `YYYY-MM-DD`.

## 1. Requirements

- Python 3.10 or newer
- A reachable Neo4j database with read/write permissions
- VS Code (recommended)

## 2. Install and run on Windows

Open PowerShell in this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and enter the actual Neo4j URI, username, password, database name, and a generated JWT secret. Never commit `.env`.

Generate a JWT secret:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Run the API:

```powershell
python -m flask --app app run --host 0.0.0.0 --port 5000
```

Open `http://127.0.0.1:5000/api/health`. The Flask app does not provide Swagger `/docs`; test endpoints with Postman or the frontend.

## 3. Login is postponed

The login endpoint and password provisioning are intentionally left out for now, because the existing Student nodes have no password hashes. For a temporary classroom demo, send an existing student ID in the `X-Student-Id` header to endpoints that require a current student. This is only a development shortcut and is not secure authentication. Anyone who knows another student's ID could impersonate them. Restore proper password hashing and token authentication before using real users or exposing sensitive data.

## 4. React configuration

The handoff document says the frontend adapter expects:

```dotenv
VITE_API_MODE=flask
VITE_API_BASE_URL=http://127.0.0.1:5000/api
```

Add these to the React project's `.env` (not this backend's `.env`) and restart the React dev server. For this temporary no-login demo, frontend requests to protected endpoints must include `X-Student-Id: S001` (or another existing Student ID) rather than a Bearer token. Adjust the frontend API adapter to add that header. The `/api/auth/login` endpoint is not available until proper login is implemented.

For a deployed backend, replace the base URL with the actual Render URL plus `/api`, for example:

```dotenv
VITE_API_MODE=flask
VITE_API_BASE_URL=https://YOUR-RENDER-SERVICE.onrender.com/api
```

## 5. Deploy to Render

1. Push `app.py`, `requirements.txt`, `set_password.py`, `.env.example`, `.gitignore`, and this README to a **private** GitHub repository.
2. In Render, create a Web Service connected to that repository.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Add environment variables in Render's Environment settings:
   - `NEO4J_URI`
   - `NEO4J_USERNAME`
   - `NEO4J_PASSWORD`
   - `NEO4J_DATABASE`
   - `JWT_SECRET` (generate a separate secret for the deployed service)
   - `TOKEN_HOURS` (optional, defaults to 12)
   - `FRONTEND_ORIGINS` (comma-separated local and deployed frontend origins)
6. Deploy, then test `https://YOUR-RENDER-SERVICE.onrender.com/api/health`.
7. Set the React `VITE_API_BASE_URL` to `https://YOUR-RENDER-SERVICE.onrender.com/api`.

For the temporary no-login demo, the frontend must send `X-Student-Id` on student-specific requests. This is not secure for real users. The service needs outbound connectivity to the database. A Neo4j Sandbox may expire or may not permit native Bolt connections from Render; test connectivity before depending on it for the demo. If it cannot be reached, use a persistent Neo4j deployment with a supported remote connection method.

## 6. Data and security notes

- This project uses bearer tokens and localStorage-compatible frontend integration because the handoff specifies that contract. This is a student-project prototype, not a security audit.
- Do not expose Neo4j credentials to React, GitHub, or public screenshots.
- Do not commit `.env`.
- Only existing Skill names are accepted.
- Project owners are both `OWNS` and `MEMBER_OF` when creating projects.
- Accepting an application adds `MEMBER_OF` and checks capacity within a Neo4j write transaction.
- Application history is retained; archiving does not delete projects.
- Before real production use, add database uniqueness constraints, rate limiting, email verification/account recovery, robust concurrent-application testing, and operational monitoring.
