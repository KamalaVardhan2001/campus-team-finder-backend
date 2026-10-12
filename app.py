#Import
import os
import uuid
from pathlib import Path
from datetime import date, datetime
from functools import wraps

import jwt
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request
from flask_cors import CORS
from neo4j import GraphDatabase
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv(
    dotenv_path=Path(__file__).resolve().parent / ".env",
    override=True,
)

NEO4J_URI = os.getenv("NEO4J_URI", "").strip()
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "").strip()
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j").strip()
JWT_SECRET = os.getenv("JWT_SECRET", "")
TOKEN_HOURS = int(os.getenv("TOKEN_HOURS", "12"))
FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]

app = Flask(__name__)
CORS(
    app,
    resources={r"/api/*": {"origins": FRONTEND_ORIGINS}},
    allow_headers=["Content-Type", "Authorization", "X-Student-Id"],
    methods=["GET", "POST", "PATCH", "OPTIONS"],
)


print("URI inside app.py:", repr(NEO4J_URI))
print("Username configured:", bool(NEO4J_USERNAME))
print("Password configured:", bool(NEO4J_PASSWORD))

driver = None
if NEO4J_URI and NEO4J_USERNAME and NEO4J_PASSWORD:
    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USERNAME, NEO4J_PASSWORD),
    )


def error(message, status):
    return jsonify({"error": message}), status


def serial(value):
    """Convert Neo4j temporal values and nested structures into JSON-safe values."""
    if isinstance(value, dict):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "iso_format"):
        return value.iso_format()
    return value


def query(cypher, **params):
    if driver is None:
        raise RuntimeError("Neo4j environment variables are not configured")
    with driver.session(database=NEO4J_DATABASE) as session:
        return [serial(record.data()) for record in session.run(cypher, **params)]


def write_transaction(work):
    if driver is None:
        raise RuntimeError("Neo4j environment variables are not configured")
    with driver.session(database=NEO4J_DATABASE) as session:
        return session.execute_write(work)


def new_id(prefix):
    return prefix + uuid.uuid4().hex[:12].upper()


def iso_day(value):
    if value is None:
        return ""
    if hasattr(value, "iso_format"):
        value = value.iso_format()
    elif isinstance(value, (datetime, date)):
        value = value.isoformat()
    return str(value)[:10]


def normal_status(value):
    return str(value or "").strip().lower()


def project_mapper(row):
    return {
        "id": str(row.get("id") or ""),
        "ownerId": str(row.get("ownerId") or ""),
        "ownerName": row.get("ownerName") or "",
        "title": row.get("title") or "",
        "category": row.get("category") or "Other",
        "description": row.get("description") or "",
        "requiredSkills": row.get("requiredSkills") or [],
        "teamSize": int(row.get("teamSize") or 0),
        "memberIds": [str(x) for x in (row.get("memberIds") or []) if x],
        "status": normal_status(row.get("status")),
        "createdAt": iso_day(row.get("createdAt")),
    }


PROJECT_QUERY = """
MATCH (p:Project)
OPTIONAL MATCH (owner:Student)-[:OWNS]->(p)
WITH p, collect(DISTINCT owner) AS owners
OPTIONAL MATCH (member:Student)-[:MEMBER_OF]->(p)
WITH p, owners, collect(DISTINCT member.studentId) AS memberIds
RETURN
    p.projectId AS id,
    head([o IN owners WHERE o IS NOT NULL | o.studentId]) AS ownerId,
    head([o IN owners WHERE o IS NOT NULL | o.name]) AS ownerName,
    p.title AS title,
    p.category AS category,
    p.summary AS description,
    [(p)-[:REQUIRES]->(sk:Skill) | sk.name] AS requiredSkills,
    p.maxMembers AS teamSize,
    memberIds AS memberIds,
    p.status AS status,
    p.createdAt AS createdAt
ORDER BY p.createdAt DESC
"""


def get_project(project_id):
    rows = query(
        PROJECT_QUERY.replace(
            "MATCH (p:Project)",
            "MATCH (p:Project {projectId: $project_id})",
            1
        ),
        project_id=project_id,
    )
    return project_mapper(rows[0]) if rows else None


def get_user(student_id):
    rows = query(
        """
        MATCH (s:Student {studentId: $sid})
        RETURN s.studentId AS id, s.name AS name, s.email AS email,
               s.department AS department, s.year AS year, s.bio AS bio,
               [(s)-[:HAS_SKILL]->(sk:Skill) | sk.name] AS skills
        """,
        sid=student_id,
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "id": str(row.get("id") or ""),
        "name": row.get("name") or "",
        "email": row.get("email") or "",
        "department": row.get("department") or "",
        "year": row.get("year") or "",
        "bio": row.get("bio") or "",
        "skills": sorted(set(row.get("skills") or []), key=str.lower),
    }


def token_required(fn):
    """
    Temporary demo identity only; this is NOT authentication.
    The frontend sends X-Student-Id with the ID of a seeded Student node.
    Remove this mechanism and implement real authentication before production.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        student_id = request.headers.get("X-Student-Id", "").strip()
        if not student_id:
            return error(
                "For this demo, send the X-Student-Id header with an existing Student ID.",
                401,
            )
        user = get_user(student_id)
        if not user:
            return error("Student ID was not found.", 401)
        g.current_user = user
        return fn(*args, **kwargs)
    return wrapper


def body_json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    return data


def text_field(data, key, min_len=1, max_len=500, required=False):
    value = data.get(key)
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, f"{key} must be text."
    value = value.strip()
    if required and not value:
        return None, f"{key} is required."
    if value and len(value) < min_len:
        return None, f"{key} must be at least {min_len} characters."
    if len(value) > max_len:
        return None, f"{key} must be at most {max_len} characters."
    return value, None


def valid_skills(skill_names, require_one=False):
    if not isinstance(skill_names, list) or any(not isinstance(x, str) for x in skill_names):
        return None, "requiredSkills/skills must be an array of skill names."
    names = list(dict.fromkeys(x.strip() for x in skill_names if x.strip()))
    if require_one and not names:
        return None, "Select at least one required skill."
    if not names:
        return [], None
    rows = query(
        """
        MATCH (sk:Skill)
        WHERE toLower(sk.name) IN $names
        RETURN collect(DISTINCT sk.name) AS names
        """,
        names=[x.lower() for x in names],
    )
    found = {name.lower(): name for name in (rows[0]["names"] if rows else [])}
    missing = [name for name in names if name.lower() not in found]
    if missing:
        return None, "Unknown skill(s): " + ", ".join(missing)
    return [found[name.lower()] for name in names], None


def set_project_skills_tx(tx, project_id, skills):
    tx.run(
        """
        MATCH (p:Project {projectId: $pid})
        OPTIONAL MATCH (p)-[r:REQUIRES]->()
        DELETE r
        """,
        pid=project_id,
    ).consume()
    for skill in skills:
        tx.run(
            """
            MATCH (p:Project {projectId: $pid}), (sk:Skill {name: $skill})
            MERGE (p)-[:REQUIRES]->(sk)
            """,
            pid=project_id,
            skill=skill,
        ).consume()


@app.get("/")
def home():
    return jsonify({
        "name": "Campus Team Finder API",
        "status": "running",
        "docs": "Use the endpoint list in the project handoff; this Flask API does not provide Swagger UI by default.",
    })


@app.get("/api/health")
def health():
    if driver is None:
        return error("Neo4j environment variables are not configured.", 503)
    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            session.run("RETURN 1").single()
        return jsonify({"api": "ok", "database": "connected"})
    except Exception:
        app.logger.exception("Neo4j health check failed")
        return error("Neo4j is unreachable or credentials are invalid.", 503)


@app.get("/api/auth/me")
@token_required
def auth_me():
    return jsonify(get_user(g.current_user["id"]))


@app.get("/api/projects")
@token_required
def projects():
    rows = query(PROJECT_QUERY)
    return jsonify([project_mapper(row) for row in rows])


@app.get("/api/applications/mine")
@token_required
def applications_mine():
    rows = query(
        """
        MATCH (s:Student {studentId: $sid})-[:SUBMITTED]->(a:Application)
              -[:FOR_PROJECT]->(p:Project)
        RETURN a.applicationId AS id, p.projectId AS projectId,
               p.title AS projectTitle, s.studentId AS applicantId,
               s.name AS applicantName,
               coalesce(a.fullName, s.name) AS fullName,
               coalesce(a.email, s.email) AS email,
               a.status AS status, a.appliedAt AS createdAt
        ORDER BY a.appliedAt DESC
        """,
        sid=g.current_user["id"],
    )
    return jsonify([application_mapper(row) for row in rows])


@app.get("/api/applications/received")
@token_required
def applications_received():
    rows = query(
        """
        MATCH (owner:Student {studentId: $sid})-[:OWNS]->(p:Project)
              <-[:FOR_PROJECT]-(a:Application)<-[:SUBMITTED]-(s:Student)
        RETURN a.applicationId AS id, p.projectId AS projectId,
               p.title AS projectTitle, s.studentId AS applicantId,
               s.name AS applicantName,
               coalesce(a.fullName, s.name) AS fullName,
               coalesce(a.email, s.email) AS email,
               a.status AS status, a.appliedAt AS createdAt
        ORDER BY a.appliedAt DESC
        """,
        sid=g.current_user["id"],
    )
    return jsonify([application_mapper(row) for row in rows])


def application_mapper(row):
    return {
        "id": str(row.get("id") or ""),
        "projectId": str(row.get("projectId") or ""),
        "projectTitle": row.get("projectTitle") or "",
        "applicantId": str(row.get("applicantId") or ""),
        "applicantName": row.get("applicantName") or "",
        "fullName": row.get("fullName") or row.get("applicantName") or "",
        "email": row.get("email") or "",
        "status": normal_status(row.get("status")),
        "createdAt": iso_day(row.get("createdAt")),
    }


@app.get("/api/skills")
@token_required
def skills():
    rows = query(
        "MATCH (sk:Skill) RETURN DISTINCT sk.name AS name ORDER BY name"
    )
    return jsonify([row["name"] for row in rows if row.get("name")])


@app.post("/api/projects")
@token_required
def create_project():
    data = body_json()
    if data is None:
        return error("Expected a JSON request body.", 400)

    title, err = text_field(data, "title", 1, 100, True)
    if err:
        return error(err, 400)
    description, err = text_field(data, "description", 20, 750, True)
    if err:
        return error(err, 400)
    category, err = text_field(data, "category", 1, 100, True)
    if err:
        return error(err, 400)

    team_size = data.get("teamSize")
    if isinstance(team_size, bool) or not isinstance(team_size, int) or not 2 <= team_size <= 30:
        return error("teamSize must be an integer between 2 and 30.", 400)

    skill_names, err = valid_skills(data.get("requiredSkills"), require_one=True)
    if err:
        return error(err, 400)

    pid = new_id("P")
    sid = g.current_user["id"]

    def work(tx):
        result = tx.run(
            """
            MATCH (s:Student {studentId: $sid})
            CREATE (p:Project {
                projectId: $pid, title: $title, summary: $description,
                category: $category, createdAt: date(),
                maxMembers: $team_size, status: 'open'
            })
            CREATE (s)-[:OWNS]->(p)
            MERGE (s)-[:MEMBER_OF]->(p)
            RETURN p.projectId AS id
            """,
            sid=sid, pid=pid, title=title, description=description,
            category=category, team_size=team_size,
        ).single()
        if not result:
            raise ValueError("Authenticated student was not found.")
        for skill in skill_names:
            tx.run(
                """
                MATCH (p:Project {projectId: $pid}), (sk:Skill {name: $skill})
                MERGE (p)-[:REQUIRES]->(sk)
                """,
                pid=pid, skill=skill,
            ).consume()
        return result["id"]

    try:
        write_transaction(work)
        project = get_project(pid)
        return jsonify(project), 201
    except ValueError as exc:
        return error(str(exc), 404)


@app.patch("/api/projects/<project_id>")
@token_required
def patch_project(project_id):
    data = body_json()
    if data is None:
        return error("Expected a JSON request body.", 400)

    ownership = query(
        """
        MATCH (s:Student {studentId: $sid})-[:OWNS]->(p:Project {projectId: $pid})
        RETURN p.projectId AS id
        """,
        sid=g.current_user["id"], pid=project_id,
    )
    if not ownership:
        exists = query(
            "MATCH (p:Project {projectId: $pid}) RETURN p.projectId AS id",
            pid=project_id,
        )
        return error("Only the project owner can edit this project." if exists else "Project not found.", 403 if exists else 404)

    allowed = {"title", "description", "category", "requiredSkills", "teamSize", "status"}
    unknown = set(data) - allowed
    if unknown:
        return error("Unsupported field(s): " + ", ".join(sorted(unknown)), 400)

    fields = {}
    for key, min_len, max_len in [
        ("title", 1, 100), ("description", 20, 750), ("category", 1, 100)
    ]:
        if key in data:
            value, err = text_field(data, key, min_len, max_len, True)
            if err:
                return error(err, 400)
            fields[key] = value

    if "teamSize" in data:
        size = data["teamSize"]
        if isinstance(size, bool) or not isinstance(size, int) or not 2 <= size <= 30:
            return error("teamSize must be an integer between 2 and 30.", 400)
        count = query(
            """
            MATCH (p:Project {projectId: $pid})
            OPTIONAL MATCH (s:Student)-[:MEMBER_OF]->(p)
            RETURN count(DISTINCT s) AS members
            """,
            pid=project_id,
        )[0]["members"]
        if size < count:
            return error("teamSize cannot be lower than the current member count.", 409)
        fields["teamSize"] = size

    if "status" in data:
        status = normal_status(data["status"])
        if status not in {"open", "closed"}:
            return error("status must be 'open' or 'closed'.", 400)
        fields["status"] = status

    skills_changed = "requiredSkills" in data
    skill_names = []
    if skills_changed:
        skill_names, err = valid_skills(data["requiredSkills"], require_one=True)
        if err:
            return error(err, 400)

    def work(tx):
        tx.run(
            """
            MATCH (p:Project {projectId: $pid})
            SET p.title = coalesce($title, p.title),
                p.summary = coalesce($description, p.summary),
                p.category = coalesce($category, p.category),
                p.maxMembers = coalesce($team_size, p.maxMembers),
                p.status = coalesce($status, p.status)
            """,
            pid=project_id,
            title=fields.get("title"),
            description=fields.get("description"),
            category=fields.get("category"),
            team_size=fields.get("teamSize"),
            status=fields.get("status"),
        ).consume()
        if skills_changed:
            set_project_skills_tx(tx, project_id, skill_names)

    write_transaction(work)
    return jsonify(get_project(project_id))


@app.post("/api/projects/<project_id>/applications")
@token_required
def submit_application(project_id):
    data = body_json()
    if data is None:
        return error("Expected a JSON request body.", 400)

    full_name, err = text_field(
        data, "fullName", 1, 100, True
    )
    if err:
        return error(err, 400)
    email, err = text_field(data, "email", 5, 254, True)
    if err:
        return error(err, 400)
    if "@" not in email or "." not in email.split("@")[-1]:
        return error("Enter a valid email address.", 400)

    sid = g.current_user["id"]
    aid = new_id("A")

    def work(tx):
        row = tx.run(
            """
            MATCH (s:Student {studentId: $sid})
            MATCH (p:Project {projectId: $pid})
            OPTIONAL MATCH (owner:Student)-[:OWNS]->(p)
            WITH s, p, collect(DISTINCT owner.studentId) AS owners
            OPTIONAL MATCH (member:Student)-[:MEMBER_OF]->(p)
            WITH s, p, owners, collect(DISTINCT member.studentId) AS members
            OPTIONAL MATCH (s)-[:SUBMITTED]->(old:Application)-[:FOR_PROJECT]->(p)
            WITH s, p, owners, members,
                 collect(DISTINCT toLower(old.status)) AS oldStatuses
            RETURN s.studentId AS sid, p.status AS status,
                   p.maxMembers AS maximum, owners, members, oldStatuses,
                   CASE WHEN $sid IN owners THEN true ELSE false END AS isOwner
            """,
            sid=sid, pid=project_id,
        ).single()

        if not row:
            return ("not_found", None)
        if normal_status(row["status"]) != "open":
            return ("closed", None)
        if row["isOwner"]:
            return ("owner", None)
        if sid in (row["members"] or []):
            return ("member", None)
        if len(set(row["members"] or []) | set(row["owners"] or [])) >= int(row["maximum"] or 0):
            return ("full", None)
        if set(row["oldStatuses"] or []) & {"pending", "accepted"}:
            return ("duplicate", None)

        tx.run(
            """
            MATCH (s:Student {studentId: $sid}), (p:Project {projectId: $pid})
            CREATE (a:Application {
                applicationId: $aid, appliedAt: date(),
                status: 'pending', fullName: $full_name, email: $email
            })
            CREATE (s)-[:SUBMITTED]->(a)
            CREATE (a)-[:FOR_PROJECT]->(p)
            """,
            sid=sid, pid=project_id, aid=aid,
            full_name=full_name, email=email,
        ).consume()
        return ("ok", aid)

    result, application_id = write_transaction(work)
    errors = {
        "not_found": (404, "Project not found."),
        "closed": (409, "This project is archived or not open."),
        "owner": (409, "You cannot apply to your own project."),
        "member": (409, "You are already a member of this project."),
        "full": (409, "This project has no free team capacity."),
        "duplicate": (409, "You already have a pending or accepted application."),
    }
    if result in errors:
        code, message = errors[result]
        return error(message, code)
    rows = query(
        """
        MATCH (s:Student {studentId: $sid})-[:SUBMITTED]->(a:Application {applicationId: $aid})
              -[:FOR_PROJECT]->(p:Project)
        RETURN a.applicationId AS id, p.projectId AS projectId,
               p.title AS projectTitle, s.studentId AS applicantId,
               s.name AS applicantName, a.fullName AS fullName,
               a.email AS email, a.status AS status, a.appliedAt AS createdAt
        """,
        sid=sid, aid=application_id,
    )
    return jsonify(application_mapper(rows[0])), 201


@app.patch("/api/applications/<application_id>")
@token_required
def patch_application(application_id):
    data = body_json()
    if data is None or not isinstance(data.get("status"), str):
        return error("A status is required.", 400)
    new_status = normal_status(data["status"])
    if new_status not in {"accepted", "rejected", "withdrawn"}:
        return error("status must be accepted, rejected, or withdrawn.", 400)

    sid = g.current_user["id"]

    def work(tx):
        row = tx.run(
            """
            MATCH (applicant:Student)-[:SUBMITTED]->(a:Application {applicationId: $aid})
                  -[:FOR_PROJECT]->(p:Project)
            OPTIONAL MATCH (owner:Student)-[:OWNS]->(p)
            RETURN applicant.studentId AS applicantId, a.status AS status,
                   p.projectId AS projectId, p.maxMembers AS maximum,
                   collect(DISTINCT owner.studentId) AS owners
            """,
            aid=application_id,
        ).single()
        if not row:
            return ("not_found", None)
        status = normal_status(row["status"])
        owners = [x for x in (row["owners"] or []) if x]
        is_owner = sid in owners
        is_applicant = sid == row["applicantId"]

        if new_status == "withdrawn":
            if not is_applicant:
                return ("forbidden", None)
            if status != "pending":
                return ("not_pending", None)
        else:
            if not is_owner:
                return ("forbidden", None)
            if status != "pending":
                return ("not_pending", None)

        if new_status == "accepted":
            # A property write obtains a Neo4j write lock on this project.
            # The lock is held until this transaction commits.
            tx.run(
                """
                MATCH (p:Project {projectId: $pid})
                SET p._capacityLock = coalesce(p._capacityLock, 0) + 1
                """,
                pid=row["projectId"],
            ).consume()
            capacity = tx.run(
                """
                MATCH (p:Project {projectId: $pid})
                OPTIONAL MATCH (m:Student)-[:MEMBER_OF]->(p)
                WITH p, collect(DISTINCT m.studentId) AS members
                RETURN p.maxMembers AS maximum, size(members) AS memberCount
                """,
                pid=row["projectId"],
            ).single()
            if not capacity or int(capacity["memberCount"] or 0) >= int(capacity["maximum"] or 0):
                tx.run(
                    "MATCH (p:Project {projectId: $pid}) REMOVE p._capacityLock",
                    pid=row["projectId"],
                ).consume()
                return ("full", None)

        tx.run(
            """
            MATCH (a:Application {applicationId: $aid})
            SET a.status = $status,
                a.decidedAt = CASE WHEN $status = 'withdrawn'
                    THEN a.decidedAt ELSE datetime() END
            """,
            aid=application_id, status=new_status,
        ).consume()

        if new_status == "accepted":
            tx.run(
                """
                MATCH (s:Student)-[:SUBMITTED]->(a:Application {applicationId: $aid})
                      -[:FOR_PROJECT]->(p:Project)
                MERGE (s)-[:MEMBER_OF]->(p)
                """,
                aid=application_id,
            ).consume()

        if new_status == "accepted":
            tx.run(
                """
                MATCH (p:Project {projectId: $pid})
                REMOVE p._capacityLock
                """,
                pid=row["projectId"],
            ).consume()
        return ("ok", row["projectId"])

    outcome, _ = write_transaction(work)
    errors = {
        "not_found": (404, "Application not found."),
        "forbidden": (403, "You are not allowed to perform this action."),
        "not_pending": (409, "Only pending applications can be changed."),
        "full": (409, "The project has no free capacity."),
    }
    if outcome in errors:
        code, message = errors[outcome]
        return error(message, code)

    rows = query(
        """
        MATCH (s:Student)-[:SUBMITTED]->(a:Application {applicationId: $aid})
              -[:FOR_PROJECT]->(p:Project)
        RETURN a.applicationId AS id, p.projectId AS projectId,
               p.title AS projectTitle, s.studentId AS applicantId,
               s.name AS applicantName,
               coalesce(a.fullName, s.name) AS fullName,
               coalesce(a.email, s.email) AS email,
               a.status AS status, a.appliedAt AS createdAt
        """,
        aid=application_id,
    )
    return jsonify(application_mapper(rows[0]))


@app.patch("/api/users/me")
@token_required
def patch_profile():
    data = body_json()
    if data is None:
        return error("Expected a JSON request body.", 400)

    allowed = {"name", "department", "year", "bio", "skills"}
    unknown = set(data) - allowed
    if unknown:
        return error("Unsupported field(s): " + ", ".join(sorted(unknown)), 400)

    fields = {}
    for key, max_len in [("name", 100), ("department", 100), ("year", 80), ("bio", 1000)]:
        if key in data:
            value, err = text_field(data, key, 1, max_len, True)
            if err:
                return error(err, 400)
            fields[key] = value

    skills_changed = "skills" in data
    if skills_changed:
        skill_names, err = valid_skills(data["skills"], require_one=False)
        if err:
            return error(err, 400)
    else:
        skill_names = []

    sid = g.current_user["id"]

    def work(tx):
        tx.run(
            """
            MATCH (s:Student {studentId: $sid})
            SET s.name = coalesce($name, s.name),
                s.department = coalesce($department, s.department),
                s.year = coalesce($year, s.year),
                s.bio = coalesce($bio, s.bio)
            """,
            sid=sid, name=fields.get("name"),
            department=fields.get("department"),
            year=fields.get("year"), bio=fields.get("bio"),
        ).consume()
        if skills_changed:
            tx.run(
                """
                MATCH (s:Student {studentId: $sid})
                OPTIONAL MATCH (s)-[r:HAS_SKILL]->()
                DELETE r
                """,
                sid=sid,
            ).consume()
            for skill in skill_names:
                tx.run(
                    """
                    MATCH (s:Student {studentId: $sid}), (sk:Skill {name: $skill})
                    MERGE (s)-[:HAS_SKILL]->(sk)
                    """,
                    sid=sid, skill=skill,
                ).consume()

    write_transaction(work)
    return jsonify(get_user(sid))


@app.errorhandler(404)
def not_found(_):
    return error("Route not found.", 404)


@app.errorhandler(405)
def method_not_allowed(_):
    return error("Method not allowed.", 405)


@app.errorhandler(Exception)
def unhandled_error(exc):
    app.logger.exception("Unhandled backend error: %s", exc)
    return error("Internal server error.", 500)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)
