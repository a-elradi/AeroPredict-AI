"""
Per-client rate limiting for expensive endpoints (each call costs API money).
In-memory, so limits are per server process — fine for a single Render instance.
"""
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import jsonify, request

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def client_ip() -> str:
    # Render's proxy appends the real client IP to X-Forwarded-For. Earlier
    # entries are supplied by the client and can be faked, so take the last one.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.remote_addr or "unknown"


def rate_limit(name: str, max_calls: int, per_seconds: int):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            key = f"{name}:{client_ip()}"
            now = time.time()
            with _lock:
                window = _hits[key]
                while window and window[0] <= now - per_seconds:
                    window.popleft()
                if len(window) >= max_calls:
                    retry_after = int(window[0] + per_seconds - now) + 1
                    resp = jsonify({
                        "error": "Too many requests. Please try again later.",
                        "retry_after_seconds": retry_after,
                    })
                    resp.status_code = 429
                    resp.headers["Retry-After"] = str(retry_after)
                    return resp
                window.append(now)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def reset() -> None:
    with _lock:
        _hits.clear()
