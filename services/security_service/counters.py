def reset_window(rule, now):
    if now - rule.window_start >= rule.window_seconds:
        rule.window_start = now
        rule.used_bytes = 0
        rule.used_requests = 0
