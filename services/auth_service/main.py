import time
import bcrypt
import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from common.config import Settings
from common.models import User
from common.tokens import issue_token, verify_token
from common.health import add_health
from services.security_service.main import Policy

class Login(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=72)

def authenticate(sessions, login, now):
    password = login.password.encode("utf-8")
    if len(password) > 72:
        raise HTTPException(401, "Invalid credentials")
    identity = None
    with sessions.begin() as db:
        user = db.scalar(select(User).where(User.name == login.username).with_for_update())
        if user is not None and now < user.locked_until:
            raise HTTPException(429, "Account temporarily locked")
        # A fixed valid hash also makes unknown-user verification expensive.
        encoded = user.password_hash.encode() if user else bcrypt.hashpw(b"unused", bcrypt.gensalt(rounds=12))
        valid = bcrypt.checkpw(password, encoded)
        if user is not None and valid:
            user.failures = 0
            identity = (user.name, user.role)
        elif user is not None:
            user.failures += 1
            if user.failures >= 5:
                user.locked_until = now + 60
                user.failures = 0
    if identity is None:
        raise HTTPException(401, "Invalid credentials")
    return identity

def create_app(cfg=None, sessions=None):
    cfg = cfg or Settings()
    sessions = sessions or cfg.sessions()
    app = FastAPI(title="auth-service")
    add_health(app, sessions)

    @app.post("/api/v1/token")
    def token(login: Login):
        name, role = authenticate(sessions, login, int(time.time()))
        return {"access_token": issue_token(name, role, cfg.key("jwt-private.pem")),
                "token_type": "bearer", "expires_in": 900}

    @app.put("/api/v1/rules/{service}")
    def update(service: str, policy: Policy, authorization: str = Header(default="")):
        verify_token(authorization, cfg.key("jwt-public.pem"), ("admin",))
        with httpx.Client(timeout=5, trust_env=False) as client:
            response = client.put(cfg.security_url + "/api/v1/rules/" + service,
                                  json=policy.model_dump(), headers={"Authorization": authorization})
        if response.status_code != 200:
            raise HTTPException(response.status_code, "Policy update rejected")
        return response.json()
    return app
