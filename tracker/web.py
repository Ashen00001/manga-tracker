import asyncio
import hashlib
import hmac
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import catalog, db, notify, sources, stock, watcher
from .sources import barnesnoble
from .config import ROOT, settings

STATIC = ROOT / "static"
COOKIE = "mt_session"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.conn()
    task = asyncio.create_task(watcher.run_forever())
    yield
    task.cancel()


app = FastAPI(title="Manga Tracker", lifespan=lifespan)


# --- optional password ---------------------------------------------------

def _session_token() -> str:
    return hmac.new((settings.app_password or "").encode(), b"manga-tracker-session", hashlib.sha256).hexdigest()


@app.middleware("http")
async def require_login(request: Request, call_next):
    if not settings.app_password or request.url.path in ("/login", "/manifest.webmanifest", "/icon.png"):
        return await call_next(request)
    if hmac.compare_digest(request.cookies.get(COOKIE, ""), _session_token()):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "login required"}, status_code=401)
    return RedirectResponse("/login")


LOGIN_PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Manga Tracker</title><link rel="stylesheet" href="/static/style.css"></head>
<body class="login"><form method="post" action="/login"><h1>Manga Tracker</h1>
<input type="password" name="password" placeholder="Password" autofocus>{error}<button>Unlock</button></form></body></html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return LOGIN_PAGE.format(error="")


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    if not settings.app_password or not hmac.compare_digest(str(form.get("password", "")), settings.app_password):
        return HTMLResponse(LOGIN_PAGE.format(error='<p class="error">Wrong password</p>'), status_code=401)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE, _session_token(), max_age=60 * 60 * 24 * 180, httponly=True, samesite="lax")
    return response


# --- pages ---------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/icon.png")
async def icon():
    return FileResponse(STATIC / "icon.png")


# --- API -----------------------------------------------------------------

@app.get("/api/search")
async def api_search(q: str):
    if len(q.strip()) < 2:
        raise HTTPException(400, "Type at least 2 characters")
    try:
        result = await asyncio.to_thread(catalog.search, q)
    except Exception as exc:
        raise HTTPException(502, f"Search failed: {exc}")
    watched = {w["isbn"] for w in db.list_watches("volume")}
    return {
        "query": result["series_query"],
        "volume": result["volume"],
        "series": catalog.group_series(result["books"]),
        "books": [b.to_dict() | {"watched": b.isbn in watched} for b in result["books"]],
    }


@app.get("/api/series")
async def api_series(name: str):
    detail = await asyncio.to_thread(catalog.series_detail, name)
    if not detail:
        raise HTTPException(404, "Series not found")
    watched_series = {w["key"] for w in db.list_watches("series")}
    detail["watched_editions"] = [
        e["edition"] for e in detail["editions"] if db.watch_key("series", series=detail["series"], edition=e["edition"]) in watched_series
    ]
    return detail


_availability_cache: dict[str, tuple[float, dict]] = {}


@app.get("/api/availability")
async def api_availability(isbns: str):
    """Quick in-stock summary for many volumes at once (series pages). Uses B&N's batch lookup."""
    wanted = [i for i in isbns.split(",") if i][:100]
    now = time.time()
    missing = [i for i in wanted if i not in _availability_cache or now - _availability_cache[i][0] > stock.FRESH_SECONDS]
    if missing:
        results = await asyncio.to_thread(barnesnoble.stock_many, missing)
        for isbn, result in results.items():
            available = sorted((s for s in result.stores if s.available), key=lambda s: s.distance_mi or 999)
            _availability_cache[isbn] = (now, {
                "ok": result.ok,
                "carried": result.carried,
                "in_stock": len(available),
                "nearest": available[0].store_name if available else None,
                "nearest_chain": available[0].chain if available else None,
                "nearest_mi": available[0].distance_mi if available else None,
            })
    return {isbn: _availability_cache[isbn][1] for isbn in wanted if isbn in _availability_cache}


@app.get("/api/book/{isbn}")
async def api_book(isbn: str):
    book = await asyncio.to_thread(catalog.lookup_isbn, isbn)
    if not book:
        raise HTTPException(404, "Book not found")
    watched = any(w["isbn"] == isbn for w in db.list_watches("volume"))
    return book.to_dict() | {"watched": watched}


@app.get("/api/stock/{isbn}")
async def api_stock(isbn: str, refresh: bool = False):
    results, stamp = await asyncio.to_thread(stock.lookup, isbn, 0 if refresh else stock.FRESH_SECONDS)
    return stock.to_payload(isbn, results, stamp)


class WatchIn(BaseModel):
    kind: str
    isbn: str | None = None
    series: str | None = None
    edition: str | None = None


@app.get("/api/watches")
async def api_watches():
    return {"watches": db.list_watches()}


@app.post("/api/watches")
async def api_add_watch(body: WatchIn):
    if body.kind == "volume":
        if not body.isbn:
            raise HTTPException(400, "isbn required")
        book = await asyncio.to_thread(catalog.lookup_isbn, body.isbn)
        if not book:
            raise HTTPException(404, "Book not found")
        watch = db.add_watch("volume", book.label, isbn=book.isbn, cover_url=book.cover_url, book=book.to_dict())
        await asyncio.to_thread(watcher.check_volume, watch, False)  # baseline so the next check can spot restocks
    elif body.kind == "series":
        if not (body.series and body.edition):
            raise HTTPException(400, "series and edition required")
        volumes = await asyncio.to_thread(catalog.series_volumes, body.series, body.edition)
        cover = next((b.cover_url for b in reversed(volumes) if b.cover_url), None)
        label = body.series + ("" if body.edition == "Standard" else f" ({body.edition})")
        watch = db.add_watch("series", label, series=body.series, edition=body.edition, cover_url=cover)
        await asyncio.to_thread(watcher.check_series, watch, False)
    else:
        raise HTTPException(400, "kind must be volume or series")
    db.log_event("watch", f"Watching {watch['title']}", watch.get("isbn"))
    return watch


@app.delete("/api/watches/{watch_id}")
async def api_delete_watch(watch_id: int):
    watch = db.get_watch(watch_id)
    if not watch:
        raise HTTPException(404, "Not watching that")
    db.delete_watch(watch_id)
    db.log_event("unwatch", f"Stopped watching {watch['title']}", watch.get("isbn"))
    return {"ok": True}


@app.post("/api/check")
async def api_check():
    return await asyncio.to_thread(watcher.check_all)


@app.get("/api/events")
async def api_events():
    return {"events": db.recent_events()}


@app.get("/api/status")
async def api_status():
    return {
        "discord": settings.discord_ready,
        "radius_miles": settings.radius_miles,
        "home_zip": settings.home_zip,
        "check_interval_hours": settings.check_interval_hours,
        "sources": [{"source": s.NAME, "chain": s.CHAIN} for s in sources.SOURCES],
    }


@app.post("/api/test-discord")
async def api_test_discord():
    try:
        await asyncio.to_thread(notify.send, "👋 Manga Tracker is connected. Restock alerts will show up here.")
    except notify.DiscordError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}
