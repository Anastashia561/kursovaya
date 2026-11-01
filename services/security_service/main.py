import hmac
import time
import threading
import re
import httpx
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Header, HTTPException
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from common.config import Settings
from common.models import Rule
from common.health import add_health
from common.tokens import verify_token
from .identity import KubernetesIdentity
from .rules import evaluate
from .events import enqueue_event, outbox_loop

class Policy(BaseModel):
    hosts: list[str] = Field(min_length=1, max_length=10)
    types: list[str] = Field(min_length=1, max_length=10)
    max_bytes: int = Field(ge=1, le=10_000_000)
    max_requests: int = Field(ge=1, le=10000)
    window_seconds: int = Field(ge=1, le=86400)
    block_seconds: int = Field(ge=1, le=86400)

def check_request(sessions, service, host, size, media, now):
    with sessions.begin() as db:
        rule = db.scalar(select(Rule).where(Rule.service == service).with_for_update())
        if rule is None:
            raise HTTPException(403, "Unknown source")
        decision = evaluate(rule, host, size, media, now)
        enqueue_event(db, service, host, size, decision)
    return decision

def create_app(cfg=None, sessions=None, identity=None):
    cfg = cfg or Settings()
    sessions = sessions or cfg.sessions()
    if identity is None and os.environ.get("SERVICE_MODE") == "local":
        identity = lambda ip: "app-service" if ip == "127.0.0.1" else None
    identity = identity or KubernetesIdentity()
    registry = CollectorRegistry()
    requests = Counter("egress_requests_total", "Egress decisions", ["service"], registry=registry)
    blocked = Counter("egress_blocked_total", "Denied requests", ["service", "rule"], registry=registry)
    byte_count = Counter("egress_bytes_total", "Reserved allowed body bytes", ["service"], registry=registry)
    duration = Histogram("egress_check_seconds", "Decision duration", registry=registry)

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        thread = threading.Thread(target=outbox_loop, args=(stop, sessions, cfg), daemon=True)
        if cfg.worker:
            thread.start()
        yield
        stop.set()
        if cfg.worker:
            thread.join(timeout=5)

    app = FastAPI(title="security-service", lifespan=lifespan)
    add_health(app, sessions)

    @app.get("/api/v1/check")
    def check(request: Request):
        if not cfg.gateway_key or not hmac.compare_digest(
                request.headers.get("x-gateway-key", ""), cfg.gateway_key):
            raise HTTPException(403, "Gateway identity required")
        headers = request.headers
        try:
            uri = headers.get("x-original-uri", "")
            match = re.fullmatch(r"/ext/([a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?)/echo", uri)
            length = headers.get("x-body-size", "")
            if not match or not re.fullmatch(r"[0-9]{1,7}", length):
                raise ValueError("Invalid envelope")
            size = int(length)
            if headers.get("x-original-method") != "POST" or not 1 <= size <= 1048576:
                raise ValueError("Unsupported request")
            service = identity(headers.get("x-source-ip", ""))
            if service is None:
                raise HTTPException(403, "Unknown source")
            with duration.time():
                decision = check_request(sessions, service, match[1], size,
                                         headers.get("x-body-type", ""), int(time.time()))
        except HTTPException:
            raise
        except ValueError:
            raise HTTPException(403, "Invalid envelope")
        except (SQLAlchemyError, httpx.HTTPError, OSError, KeyError):
            raise HTTPException(503, "Security dependency unavailable")
        requests.labels(service).inc()
        if decision.allowed:
            byte_count.labels(service).inc(size)
        else:
            blocked.labels(service, decision.rule).inc()
        return Response(status_code=200 if decision.allowed else 403,
                        headers={"X-Decision-Rule": decision.rule})

    @app.put("/api/v1/rules/{service}")
    def update_policy(service: str, policy: Policy, authorization: str = Header(default="")):
        verify_token(authorization, cfg.key("jwt-public.pem"), ("admin",))
        if policy.hosts != [cfg.allowed_host] or any(t not in {"application/json", "text/plain"} for t in policy.types):
            raise HTTPException(422, "Target or media is outside the deployed routing contract")
        with sessions.begin() as db:
            rule = db.scalar(select(Rule).where(Rule.service == service).with_for_update())
            if rule is None:
                raise HTTPException(404, "Unknown service")
            for key, value in policy.model_dump().items():
                setattr(rule, key, value)
        return {"updated": service}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(registry), headers={"Content-Type": CONTENT_TYPE_LATEST})

    return app

