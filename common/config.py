import os
from dataclasses import dataclass, field
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", "")
    )
    secret_dir: str = field(
        default_factory=lambda: os.environ.get("SECRET_DIR", "/run/ubi193")
    )
    gateway_key: str = field(
        default_factory=lambda: os.environ.get("GATEWAY_KEY", "")
    )
    log_key: str = field(
        default_factory=lambda: os.environ.get("LOG_KEY", "")
    )
    log_url: str = field(
        default_factory=lambda: os.environ.get(
            "LOG_URL", "http://log-service.security.svc.cluster.local:8083"
        )
    )
    security_url: str = field(
        default_factory=lambda: os.environ.get(
            "SECURITY_URL",
            "http://security-service.security.svc.cluster.local:8081",
        )
    )
    gateway_url: str = field(
        default_factory=lambda: os.environ.get(
            "GATEWAY_URL",
            "http://gateway-service.security.svc.cluster.local:8080",
        )
    )
    allowed_host: str = "external-service.external.svc.cluster.local"
    worker: bool = True

    def key(self, name):
        return (Path(self.secret_dir) / name).read_bytes()

    def sessions(self):
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required")
        engine = create_engine(self.database_url, pool_pre_ping=True)
        return sessionmaker(engine, expire_on_commit=False)
