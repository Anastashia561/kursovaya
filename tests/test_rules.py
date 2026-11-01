from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select, func
from common.models import Rule, Outbox
from services.security_service.main import check_request

HOST = "external-service.external.svc.cluster.local"


def decision(store, host=HOST, size=10, media="application/json", now=1001):
    return check_request(store, "app-service", host, size, media, now)


def test_allow_reserves_and_enqueues_atomically(store):
    assert decision(store).allowed
    with store() as db:
        r = db.get(Rule, "app-service")
        assert (r.used_bytes, r.used_requests) == (10, 1)
        assert db.scalar(select(func.count()).select_from(Outbox)) == 1


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example",
        HOST + ".attacker.example",
        "127.0.0.1",
        HOST.upper(),
    ],
)
def test_r2_exact_target(store, host):
    assert decision(store, host=host).rule == "R2"


def test_byte_limit_and_block_expiration(store):
    assert decision(store, size=100).allowed
    assert decision(store, size=1).rule == "R3"
    assert decision(store, now=1002).rule == "R1"
    assert decision(store, now=1061).allowed


def test_request_limit(store):
    for _ in range(3):
        assert decision(store).allowed
    assert decision(store).rule == "R4"
    assert decision(store).rule == "R1"


def test_r5_and_no_budget_on_denial(store):
    assert decision(store, media="application/octet-stream").rule == "R5"
    assert decision(
        store, size=100, media="Application/JSON; charset=utf-8"
    ).allowed


def test_two_instances_share_budget(store):
    for _ in range(2):
        assert decision(store).allowed
    assert check_request(
        store, "app-service", HOST, 10, "application/json", 1001
    ).allowed
    assert decision(store).rule == "R4"


def test_transaction_rollback_does_not_reserve(store, monkeypatch):
    import services.security_service.main as module

    def fail(*args):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(module, "enqueue_event", fail)
    with pytest.raises(RuntimeError):
        decision(store)
    with store() as db:
        assert db.get(Rule, "app-service").used_requests == 0


def test_postgres_parallel_limit(store):
    if store.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL row locking; executed by CI")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: decision(store), range(12)))
    assert sum(d.allowed for d in results) == 3
