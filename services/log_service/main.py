import hmac
from uuid import UUID
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from common.models import Event
from common.config import Settings
from common.tokens import verify_token
from common.health import add_health

class EventData(BaseModel):
    id: UUID
    created: int = Field(ge=0)
    service: str = Field(max_length=80)
    host: str = Field(max_length=253)
    bytes: int = Field(ge=0)
    decision: str = Field(pattern="^(ALLOW|DENY)$")
    rule: str = Field(pattern="^(ALLOW|R[1-5])$")

def store_event(sessions, data):
    payload = data.model_dump(mode="json")
    try:
        with sessions.begin() as db:
            db.add(Event(id=str(data.id), created=data.created, service=data.service,
                         decision=data.decision, rule=data.rule, payload=payload))
    except IntegrityError:
        with sessions() as db:
            previous = db.get(Event, str(data.id))
            if previous is None or previous.payload != payload:
                raise HTTPException(409, "Event identity conflict")
        return False
    return True

def create_app(cfg=None, sessions=None):
    cfg = cfg or Settings()
    sessions = sessions or cfg.sessions()
    app = FastAPI(title="log-service")
    add_health(app, sessions)

    @app.post("/api/v1/events")
    def ingest(data: EventData, x_log_key: str = Header(default="")):
        if not cfg.log_key or not hmac.compare_digest(x_log_key, cfg.log_key):
            raise HTTPException(403, "Trusted producer required")
        return {"stored": store_event(sessions, data), "id": str(data.id)}

    @app.get("/api/v1/events")
    def events(authorization: str = Header(default=""), limit: int = 50):
        verify_token(authorization, cfg.key("jwt-public.pem"))
        with sessions() as db:
            rows = db.scalars(select(Event).order_by(Event.created.desc(), Event.id)
                              .limit(min(max(limit, 1), 200))).all()
            return [row.payload for row in rows]
    return app
