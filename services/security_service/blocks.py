def block_source(rule, now):
    rule.blocked_until = now + rule.block_seconds
