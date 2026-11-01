import os
import time
import bcrypt
import pytest
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from common.models import Base, Rule, User
from common.config import Settings


@pytest.fixture
def store(tmp_path):
    url = os.environ.get(
        "TEST_DATABASE_URL", "sqlite:///" + str(tmp_path / "test.db")
    )
    engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as db:
        db.add(
            Rule(
                service="app-service",
                hosts=["external-service.external.svc.cluster.local"],
                types=["application/json"],
                max_bytes=100,
                max_requests=3,
                window_seconds=60,
                block_seconds=30,
                window_start=1000,
                used_bytes=0,
                used_requests=0,
                blocked_until=0,
            )
        )
        for role in ["admin", "auditor"]:
            db.add(
                User(
                    name=role,
                    role=role,
                    password_hash=bcrypt.hashpw(
                        b"test-password", bcrypt.gensalt(rounds=4)
                    ).decode(),
                    failures=0,
                    locked_until=0,
                )
            )
    yield sessions
    engine.dispose()


@pytest.fixture(scope="session")
def keys(tmp_path_factory):
    root = tmp_path_factory.mktemp("keys")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (root / "jwt-private.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (root / "jwt-public.pem").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return root


@pytest.fixture
def cfg(keys):
    return Settings(
        secret_dir=str(keys),
        gateway_key="test-gateway",
        log_key="test-log",
        worker=False,
    )
