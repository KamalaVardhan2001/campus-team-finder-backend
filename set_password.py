# Optional future helper; not needed while login is disabled.
import os
import getpass
from dotenv import load_dotenv
from neo4j import GraphDatabase
from werkzeug.security import generate_password_hash

load_dotenv()

uri = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")
database = os.getenv("NEO4J_DATABASE", "neo4j")

if not all([uri, username, password]):
    raise SystemExit("Configure NEO4J_URI, NEO4J_USERNAME and NEO4J_PASSWORD in .env first.")

student_id = input("Existing Student ID (for example S001): ").strip()
new_password = getpass.getpass("New password (minimum 8 characters): ")
confirm = getpass.getpass("Confirm password: ")

if len(new_password) < 8:
    raise SystemExit("Password must be at least 8 characters.")
if new_password != confirm:
    raise SystemExit("Passwords do not match.")

hashed = generate_password_hash(new_password)

with GraphDatabase.driver(uri, auth=(username, password)) as driver:
    with driver.session(database=database) as session:
        result = session.run(
            """
            MATCH (s:Student {studentId: $sid})
            SET s.passwordHash = $password_hash
            RETURN s.studentId AS id, s.email AS email
            """,
            sid=student_id,
            password_hash=hashed,
        ).single()

if not result:
    raise SystemExit("Student ID was not found. No account was changed.")

print(f"Password provisioned for {result['id']} ({result['email']}).")
print("Do not share the password or commit it to source control.")
