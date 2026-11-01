import time
from uuid import uuid4
from sqlalchemy import select
import httpx
from common.models import Outbox

def enqueue_event(db, service, host, size, decision):
    event_id = str(uuid4())
    db.add(Outbox(id=event_id, payload={
        "id": event_id, "created": int(time.time()), "service": service,
        "host": host, "bytes": size, "decision": "ALLOW" if decision.allowed else "DENY",
        "rule": decision.rule}))

def flush_outbox(sessions, client, log_url, log_key):
    delivered = 0
    with sessions.begin() as db:
        batch = db.scalars(select(Outbox).order_by(Outbox.id)
                           .with_for_update(skip_locked=True).limit(25)).all()
        for item in batch:
            response = client.post(log_url + "/api/v1/events", json=item.payload,
                                   headers={"X-Log-Key": log_key})
            response.raise_for_status()
            db.delete(item)
            delivered += 1
    return delivered

def outbox_loop(stop, sessions, cfg):
    with httpx.Client(timeout=3, trust_env=False) as client:
        while not stop.wait(1):
            try:
                flush_outbox(sessions, client, cfg.log_url, cfg.log_key)
            except Exception:
                # Transaction rollback retains events for the next attempt.
                continue
