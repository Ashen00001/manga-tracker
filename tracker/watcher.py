"""Background checks: restocks for watched volumes, new volumes for watched series."""
import asyncio
import json
import logging
import socket
import sys
import time
from contextlib import contextmanager

from . import catalog, db, notify, stock
from .config import settings
from .models import AVAILABLE, Book, SourceResult, StoreStock

log = logging.getLogger("tracker.watcher")


def find_restocks(results: list[SourceResult], previous: dict[tuple[str, str], str]) -> list[StoreStock]:
    """Stores that are available now but weren't at the last check.

    The first check of a book only records a baseline, and failed sources are skipped
    so a store that drops out for one check doesn't fire a fake "restock" later.
    """
    if not previous:
        return []
    restocked = []
    for result in results:
        if not result.ok:
            continue
        for s in result.stores:
            if s.available and previous.get((s.source, s.store_id)) not in AVAILABLE:
                restocked.append(s)
    return restocked


def _book_for(watch: dict) -> Book:
    if watch.get("book_json"):
        data = json.loads(watch["book_json"])
        data.pop("label", None)
        return Book(**data)
    return catalog.lookup_isbn(watch["isbn"]) or Book(watch["isbn"], watch["title"], watch["title"], None, "Standard")


def check_volume(watch: dict, send: bool = True) -> list[StoreStock]:
    isbn = watch["isbn"]
    results, _ = stock.lookup(isbn, max_age=60)
    restocked = find_restocks(results, db.stock_state(isbn))
    for result in results:
        if result.ok:
            db.save_stock_state(isbn, result.stores)
    db.touch_watch(watch["id"])

    if restocked:
        book = _book_for(watch)
        names = ", ".join(f"{s.chain} {s.store_name}" for s in restocked[:4])
        db.log_event("restock", f"{book.label} in stock: {names}", isbn)
        if send and settings.discord_ready:
            try:
                notify.send(embeds=[notify.restock_embed(book, restocked)])
            except notify.DiscordError as exc:
                db.log_event("error", f"Discord: {exc}", isbn)
    return restocked


def check_series(watch: dict, send: bool = True) -> list[Book]:
    volumes = catalog.series_volumes(watch["series"], watch["edition"])
    known = db.known_volumes(watch["id"])
    fresh = [b for b in volumes if b.isbn not in known]
    db.add_known_volumes(watch["id"], [b.isbn for b in volumes])
    db.touch_watch(watch["id"])
    if not known:
        return []  # first run: just learn what exists

    for book in fresh:
        db.add_watch("volume", book.label, isbn=book.isbn, cover_url=book.cover_url, book=book.to_dict())
        db.log_event("new_volume", f"New volume listed: {book.label}", book.isbn)
        if send and settings.discord_ready:
            try:
                notify.send(embeds=[notify.new_volume_embed(book)])
            except notify.DiscordError as exc:
                db.log_event("error", f"Discord: {exc}", book.isbn)
    return fresh


@contextmanager
def keep_awake():
    """Hold off Windows sleep for the duration of a check.

    After a wake timer fires, Windows goes back to sleep ~2 minutes later unless
    something asks it not to, which could cut a slow Target lookup off mid-check.
    """
    if sys.platform != "win32":
        yield
        return
    import ctypes
    es_continuous, es_system_required = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)
    try:
        yield
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous)


def wait_for_network(timeout: float = 90, host: str = "www.barnesandnoble.com") -> bool:
    """Right after a wake the network (and VPN) can take a bit to come back."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            socket.create_connection((host, 443), timeout=5).close()
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(3)


def check_all(send: bool = True) -> dict:
    with keep_awake():
        return _check_all(send)


def _check_all(send: bool) -> dict:
    summary = {"volumes": 0, "series": 0, "restocks": 0, "new_volumes": 0, "errors": 0}
    for watch in db.list_watches("series"):
        try:
            summary["new_volumes"] += len(check_series(watch, send))
            summary["series"] += 1
        except Exception as exc:
            summary["errors"] += 1
            db.log_event("error", f"Series check failed for {watch['title']}: {exc}")
    for watch in db.list_watches("volume"):
        try:
            summary["restocks"] += len(check_volume(watch, send))
            summary["volumes"] += 1
        except Exception as exc:
            summary["errors"] += 1
            db.log_event("error", f"Stock check failed for {watch['title']}: {exc}", watch["isbn"])
    db.log_event("check", "Checked {volumes} volumes and {series} series: {restocks} restocks, {new_volumes} new volumes".format(**summary))
    return summary


POLL_SECONDS = 300
DUE_SLACK_SECONDS = 15 * 60  # so a scheduled wake at 12:00 isn't skipped because the last check ran at 8:05


def min_gap_seconds() -> float:
    return max(settings.check_interval_hours * 3600 - DUE_SLACK_SECONDS, 60)


def is_due() -> bool:
    last = db.last_run("watchlist")
    return last is None or time.time() - last >= min_gap_seconds()


def run_if_due(send: bool = True) -> dict | None:
    """Run the watchlist check unless one ran recently. Returns None when skipped.

    Uses wall-clock time stored in the DB rather than a sleeping timer, so it behaves
    when the machine sleeps, and so the server and the wake task never both check.
    """
    if not is_due():
        return None
    if not wait_for_network():
        db.log_event("error", "Skipped the watchlist check: no network")
        return None
    if not db.claim_run("watchlist", min_gap_seconds()):
        return None
    return check_all(send)


async def run_forever() -> None:
    await asyncio.sleep(30)  # let the server finish starting
    while True:
        try:
            await asyncio.to_thread(run_if_due)
        except Exception:
            log.exception("watchlist check crashed")
        await asyncio.sleep(POLL_SECONDS)
