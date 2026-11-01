from fastapi import HTTPException
from sqlalchemy import text

def add_health(app, sessions):
    @app.get("/healthz")
    def live():
        return {"status": "alive"}

    @app.get("/readyz")
    def ready():
        try:
            with sessions() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            raise HTTPException(503, "Database unavailable")
        return {"status": "ready"}
