from sqlalchemy import String, Integer, BigInteger, Text, JSON, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Rule(Base):
    __tablename__ = "rules"
    service: Mapped[str] = mapped_column(String(80), primary_key=True)
    hosts: Mapped[list] = mapped_column(JSON)
    types: Mapped[list] = mapped_column(JSON)
    max_bytes: Mapped[int] = mapped_column(BigInteger)
    max_requests: Mapped[int] = mapped_column(Integer)
    window_seconds: Mapped[int] = mapped_column(Integer)
    block_seconds: Mapped[int] = mapped_column(Integer)
    window_start: Mapped[int] = mapped_column(BigInteger, default=0)
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    used_requests: Mapped[int] = mapped_column(Integer, default=0)
    blocked_until: Mapped[int] = mapped_column(BigInteger, default=0)

class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)

class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created: Mapped[int] = mapped_column(BigInteger, index=True)
    service: Mapped[str] = mapped_column(String(80))
    decision: Mapped[str] = mapped_column(String(10))
    rule: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON)

class User(Base):
    __tablename__ = "users"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16))
    failures: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[int] = mapped_column(BigInteger, default=0)

class AppBase(DeclarativeBase):
    pass

class Record(AppBase):
    __tablename__ = "records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
