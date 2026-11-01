import os
import time
import bcrypt
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from common.models import Base, AppBase, Rule, User, Record
from scripts.prepare import HOST


def seed(engine, credentials):
    Base.metadata.create_all(engine)
    with Session(engine) as db, db.begin():
        if db.get(Rule, "app-service") is None:
            db.add(
                Rule(
                    service="app-service",
                    hosts=[HOST],
                    types=["application/json"],
                    max_bytes=4096,
                    max_requests=3,
                    window_seconds=10,
                    block_seconds=5,
                    window_start=int(time.time()),
                    used_bytes=0,
                    used_requests=0,
                    blocked_until=0,
                )
            )
        for role in ["admin", "auditor"]:
            if db.get(User, role) is None:
                db.add(
                    User(
                        name=role,
                        role=role,
                        password_hash=bcrypt.hashpw(
                            credentials[role + "_password"].encode(),
                            bcrypt.gensalt(rounds=12),
                        ).decode(),
                        failures=0,
                        locked_until=0,
                    )
                )


def seed_app(engine):
    AppBase.metadata.create_all(engine)
    with Session(engine) as db, db.begin():
        if db.get(Record, 1) is None:
            db.add(Record(id=1, value="synthetic-record-001"))


def main():
    credentials = {
        k: os.environ[k.upper()]
        for k in ["admin_password", "auditor_password"]
    }
    engine = create_engine(os.environ["BOOTSTRAP_DATABASE_URL"])
    seed(engine, credentials)
    roles = {
        "svc_security": ["rules", "outbox"],
        "svc_auth": ["users"],
        "svc_log": ["events"],
    }
    with engine.begin() as db:
        for role, tables in roles.items():
            exists = db.scalar(
                text("SELECT 1 FROM pg_roles WHERE rolname=:name"),
                {"name": role},
            )
            password = os.environ[role.upper() + "_PASSWORD"]
            if not exists:
                # Identifiers are fixed constants, password is quoted by psycopg.
                from psycopg import sql

                raw = db.connection.driver_connection
                with raw.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                            sql.Identifier(role), sql.Literal(password)
                        )
                    )
            db.execute(text(f"GRANT CONNECT ON DATABASE security TO {role}"))
            db.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            for table in tables:
                privileges = (
                    "SELECT, INSERT"
                    if role == "svc_log"
                    else (
                        "SELECT, UPDATE"
                        if role == "svc_auth"
                        else "SELECT, INSERT, UPDATE, DELETE"
                    )
                )
                db.execute(
                    text(f"GRANT {privileges} ON TABLE {table} TO {role}")
                )
    app = create_engine(os.environ["APP_DATABASE_URL"])
    seed_app(app)
    print("Schema, least-privilege roles and synthetic records initialized.")


if __name__ == "__main__":
    main()
