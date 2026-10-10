"""Target via redsky (the API target.com itself calls). Behind PerimeterX, so: browser
impersonation, retries, and the ISBN -> TCIN lookup is cached so the guarded search
endpoint is hit once per book."""
from .. import db
from ..config import settings
from ..http import Browser
from ..models import SourceResult, StoreStock

NAME = "target"
CHAIN = "Target"
KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
API = "https://redsky.target.com/redsky_aggregations/v1/web"
HEADERS = {"Referer": "https://www.target.com/", "Origin": "https://www.target.com"}

_browser = Browser(warmup_url="https://www.target.com/")

STATUS_MAP = {
    "IN_STOCK": "in_stock",
    "LIMITED_STOCK": "limited",
    "OUT_OF_STOCK": "out",
    "UNAVAILABLE": "out",
    "NOT_SOLD_IN_STORE": "out",
}


def find_tcin(isbn: str) -> dict | None:
    """Returns {"tcin", "price", "url"} or None if Target doesn't sell this ISBN."""
    cached = db.cache_get(f"target:tcin:{isbn}")
    if cached is not None:
        return cached or None

    response = _browser.get(f"{API}/plp_search_v2", headers=HEADERS | {"Referer": f"https://www.target.com/s?searchTerm={isbn}"}, params={
        "key": KEY, "channel": "WEB", "count": 5, "keyword": isbn, "offset": 0,
        "page": f"/s/{isbn}", "platform": "desktop", "pricing_store_id": "1288",
        "visitor_id": "0192F3E3A1B20201A9C7E0C6C9A2B3C4", "zip": settings.home_zip,
    })
    match = parse_search(response.json(), isbn)
    db.cache_set(f"target:tcin:{isbn}", match or {}, ttl_hours=24 * 30 if match else 24 * 3)
    return match


def parse_search(payload: dict, isbn: str) -> dict | None:
    products = ((payload.get("data") or {}).get("search") or {}).get("products") or []
    for product in products:
        item = product.get("item") or {}
        barcode = item.get("primary_barcode")
        if barcode and barcode.lstrip("0") != isbn.lstrip("0"):
            continue
        price = (product.get("price") or {}).get("current_retail")
        return {
            "tcin": product["tcin"],
            "price": price,
            "url": (item.get("enrichment") or {}).get("buy_url") or f"https://www.target.com/p/-/A-{product['tcin']}",
        }
    return None


def parse_fiats(payload: dict, match: dict) -> list[StoreStock]:
    locations = ((payload.get("data") or {}).get("fulfillment_fiats") or {}).get("locations") or []
    stores = []
    for loc in locations:
        store = loc.get("store") or {}
        distance = loc.get("distance")
        if distance is not None and distance > settings.radius_miles:
            continue
        raw = (loc.get("in_store_only") or {}).get("availability_status", "")
        quantity = loc.get("location_available_to_promise_quantity")
        address = store.get("mailing_address") or {}
        stores.append(StoreStock(
            source=NAME,
            chain=CHAIN,
            store_id=str(store.get("store_id") or loc.get("location_id")),
            store_name=store.get("location_name") or "Target",
            status=STATUS_MAP.get(raw, "unknown"),
            address=", ".join(x for x in [address.get("address_line1"), address.get("city")] if x) or None,
            phone=store.get("main_voice_phone_number"),
            distance_mi=round(distance, 1) if distance is not None else None,
            quantity=int(quantity) if quantity is not None else None,
            price=match.get("price"),
            url=match.get("url"),
        ))
    return stores


def stock(isbn: str) -> SourceResult:
    try:
        match = find_tcin(isbn)
        if not match:
            return SourceResult(NAME, CHAIN, ok=True, carried=False)
        response = _browser.get(f"{API}/fiats_v1", headers=HEADERS, params={
            "key": KEY, "tcin": match["tcin"], "nearby": settings.home_zip,
            "radius": str(int(settings.radius_miles)), "limit": "20",
            "include_only_available_stores": "false", "requested_quantity": "1",
        })
        return SourceResult(NAME, CHAIN, ok=True, stores=parse_fiats(response.json(), match))
    except Exception as exc:
        return SourceResult(NAME, CHAIN, ok=False, error=str(exc))
