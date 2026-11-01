import json
import httpx
from fastapi import FastAPI, Response
from pydantic import BaseModel, Field
from common.config import Settings
from common.health import add_health
from common.models import Record
from sqlalchemy import select


class Outgoing(BaseModel):
    host: str = Field(
        default="external-service.external.svc.cluster.local",
        pattern=r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$",
    )
    data: dict
    media: str = Field(default="application/json", max_length=100)


def send_request(cfg, data, client):
    body = json.dumps(
        data.data, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return client.post(
        f"{cfg.gateway_url}/ext/{data.host}/echo",
        content=body,
        headers={
            "Content-Type": data.media,
            "Content-Length": str(len(body)),
        },
    )


def create_app(cfg=None, sessions=None):
    cfg = cfg or Settings()
    sessions = sessions or cfg.sessions()
    app = FastAPI(title="app-service")
    add_health(app, sessions)

    @app.get("/api/v1/records")
    def records():
        with sessions() as db:
            return [
                {"id": r.id, "value": r.value}
                for r in db.scalars(select(Record)).all()
            ]

    @app.post("/api/v1/send")
    def send(data: Outgoing):
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = send_request(cfg, data, client)
        return Response(
            response.content,
            status_code=response.status_code,
            media_type=response.headers.get(
                "content-type", "application/json"
            ),
        )

    return app
