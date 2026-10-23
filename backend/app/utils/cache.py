"""Small in-memory TTL cache, used to avoid repeating paid API calls."""
import time
from typing import Any, Callable

_store: dict[str, tuple[float, Any]] = {}
_MAX_ENTRIES = 2000


def cached(key: str, ttl_seconds: int, fetch: Callable[[], Any]) -> Any:
    now = time.time()
    hit = _store.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = fetch()
    if value is not None:
        if len(_store) >= _MAX_ENTRIES:
            for k in [k for k, (exp, _) in _store.items() if exp <= now]:
                _store.pop(k, None)
            if len(_store) >= _MAX_ENTRIES:
                _store.clear()
        _store[key] = (now + ttl_seconds, value)
    return value


def clear() -> None:
    _store.clear()
