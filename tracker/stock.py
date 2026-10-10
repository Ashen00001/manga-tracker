import time
from datetime import datetime, timezone

from . import sources
from .models import SourceResult

FRESH_SECONDS = 10 * 60
_recent: dict[str, tuple[float, list[SourceResult]]] = {}


def lookup(isbn: str, max_age: float = FRESH_SECONDS) -> tuple[list[SourceResult], float]:
    """Stock at every source for one ISBN. Reuses a lookup from the last `max_age` seconds."""
    cached = _recent.get(isbn)
    if cached and time.time() - cached[0] < max_age:
        return cached[1], cached[0]
    results = sources.lookup_all(isbn)
    stamp = time.time()
    _recent[isbn] = (stamp, results)
    return results, stamp


def to_payload(isbn: str, results: list[SourceResult], stamp: float) -> dict:
    stores = sorted(
        (s for r in results for s in r.stores),
        key=lambda s: (not s.available, s.distance_mi if s.distance_mi is not None else 999),
    )
    return {
        "isbn": isbn,
        "checked_at": datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds"),
        "in_stock_count": sum(1 for s in stores if s.available),
        "stores": [s.to_dict() for s in stores],
        "sources": [
            {"source": r.source, "chain": r.chain, "ok": r.ok, "carried": r.carried, "error": r.error, "stores": len(r.stores)}
            for r in results
        ],
    }
