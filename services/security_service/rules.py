from dataclasses import dataclass
from .counters import reset_window
from .blocks import block_source


@dataclass(frozen=True)
class Decision:
    allowed: bool
    rule: str


def evaluate(rule, host, size, content_type, now):
    if now < rule.blocked_until:
        return Decision(False, "R1")
    if host not in rule.hosts:
        return Decision(False, "R2")
    reset_window(rule, now)
    if rule.used_bytes + size > rule.max_bytes:
        block_source(rule, now)
        return Decision(False, "R3")
    if rule.used_requests + 1 > rule.max_requests:
        block_source(rule, now)
        return Decision(False, "R4")
    media = content_type.split(";", 1)[0].strip().lower()
    if media not in rule.types:
        return Decision(False, "R5")
    rule.used_bytes += size
    rule.used_requests += 1
    return Decision(True, "ALLOW")
