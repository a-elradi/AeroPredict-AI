"""Small in-memory TTL cache, used to avoid repeating paid API calls."""
import time
from typing import Any, Callable

_store: dict[str, tuple[float, Any]] = {}
_MAX_ENTRIES = 2000


def get(key: str) -> Any:
    hit = _store.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def put(key: str, value: Any, ttl_seconds: int) -> None:
    now = time.time()
    if len(_store) >= _MAX_ENTRIES:
        for k in [k for k, (exp, _) in _store.items() if exp <= now]:
            _store.pop(k, None)
        if len(_store) >= _MAX_ENTRIES:
            _store.clear()
    _store[key] = (now + ttl_seconds, value)


def cached(key: str, ttl_seconds: int, fetch: Callable[[], Any]) -> Any:
    value = get(key)
    if value is not None:
        return value
    value = fetch()
    if value is not None:
        put(key, value, ttl_seconds)
    return value


def clear() -> None:
    _store.clear()
