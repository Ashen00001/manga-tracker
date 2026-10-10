"""Barnes & Noble via its Shopify Storefront API (public token from barnesandnoble.com).

Storefront's `storeAvailability` gives per-store pickup availability near a point,
which is B&N's own "available in store" signal. Quantities are masked, so it's in/out only.
"""
import re
import threading

from ..config import settings
from ..http import Browser
from ..models import SourceResult, StoreStock, miles_between

NAME = "barnesnoble"
CHAIN = "Barnes & Noble"

_DEFAULTS = {
    "domain": "e9c22f-3.myshopify.com",
    "token": "9406acb91af33078542b8dcf088685a8",
    "version": "2025-07",
}
_config = dict(_DEFAULTS)
_config_lock = threading.Lock()
_browser = Browser()


class StorefrontError(Exception):
    pass


def _discover() -> None:
    """Re-read the Storefront domain/token/version from B&N's site (they rotate occasionally)."""
    html = Browser().get("https://www.barnesandnoble.com/").text
    found = {
        "domain": re.search(r'publicStoreDomain\\?",\\?"([a-z0-9\-]+\.myshopify\.com)', html),
        "token": re.search(r'storefrontAccessToken\\?",\\?"([0-9a-f]{32})', html),
        "version": re.search(r'publicStorefrontApiVersion\\?",\\?"(\d{4}-\d{2})', html),
    }
    with _config_lock:
        for key, match in found.items():
            if match:
                _config[key] = match.group(1)


def graphql(query: str, variables: dict, _retry: bool = True) -> dict:
    url = f"https://{_config['domain']}/api/{_config['version']}/graphql.json"
    response = _browser.post(
        url,
        json={"query": query, "variables": variables},
        headers={"X-Shopify-Storefront-Access-Token": _config["token"], "Content-Type": "application/json"},
    )
    if response.status_code in (401, 404) and _retry:
        _discover()
        return graphql(query, variables, _retry=False)
    if response.status_code != 200:
        raise StorefrontError(f"B&N storefront HTTP {response.status_code}")
    payload = response.json()
    if payload.get("errors"):
        raise StorefrontError(str(payload["errors"])[:300])
    return payload["data"]


def stock_query(products: int, variants: int, stores: int) -> str:
    return f"""
query($q: String!, $lat: Float!, $lon: Float!) {{
  products(first: {products}, query: $q) {{
    nodes {{
      variants(first: {variants}) {{
        nodes {{
          barcode
          price {{ amount }}
          storeAvailability(first: {stores}, near: {{latitude: $lat, longitude: $lon}}) {{
            nodes {{
              available
              location {{ name address {{ formatted latitude longitude phone }} }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""


STOCK_QUERY = stock_query(products=5, variants=3, stores=25)


def product_url(isbn: str) -> str:
    return f"https://www.barnesandnoble.com/s/{isbn}"


def _stores_from_variant(variant: dict, isbn: str) -> list[StoreStock]:
    price = float(variant["price"]["amount"]) if variant.get("price") else None
    stores = []
    for node in variant["storeAvailability"]["nodes"]:
        location = node["location"]
        address = location.get("address") or {}
        if address.get("latitude") is None or address.get("longitude") is None:
            continue  # virtual locations like "2-Pre" (preorders)
        distance = round(miles_between(settings.home_lat, settings.home_lon, address["latitude"], address["longitude"]), 1)
        if distance > settings.radius_miles:
            continue
        raw_name = location["name"]
        name, _, store_number = raw_name.rpartition("-")
        if not (name and store_number.isdigit()):
            name, store_number = raw_name, raw_name
        formatted = address.get("formatted") or []
        stores.append(StoreStock(
            source=NAME,
            chain=CHAIN,
            store_id=store_number,
            store_name=name.strip(),
            status="in_stock" if node["available"] else "out",
            address=", ".join(formatted[:2]) if formatted else None,
            phone=address.get("phone"),
            distance_mi=distance,
            price=price,
            url=product_url(isbn),
        ))
    return stores


def parse_stock(data: dict, isbns: list[str]) -> dict[str, SourceResult]:
    found: dict[str, SourceResult] = {}
    for product in data["products"]["nodes"]:
        for variant in product["variants"]["nodes"]:
            isbn = variant.get("barcode")
            if isbn in isbns and isbn not in found:
                found[isbn] = SourceResult(NAME, CHAIN, ok=True, stores=_stores_from_variant(variant, isbn))
    return {isbn: found.get(isbn) or SourceResult(NAME, CHAIN, ok=True, carried=False) for isbn in isbns}


def stock(isbn: str) -> SourceResult:
    try:
        data = graphql(STOCK_QUERY, {"q": isbn, "lat": settings.home_lat, "lon": settings.home_lon})
        return parse_stock(data, [isbn])[isbn]
    except Exception as exc:
        return SourceResult(NAME, CHAIN, ok=False, error=str(exc))


def stock_many(isbns: list[str], chunk: int = 25) -> dict[str, SourceResult]:
    """Same as stock() for a whole series at once: one request per 25 volumes."""
    out: dict[str, SourceResult] = {}
    for i in range(0, len(isbns), chunk):
        part = isbns[i:i + chunk]
        try:
            data = graphql(stock_query(products=len(part) * 2, variants=1, stores=15), {
                "q": " OR ".join(part), "lat": settings.home_lat, "lon": settings.home_lon,
            })
            out.update(parse_stock(data, part))
        except Exception as exc:
            out.update({isbn: SourceResult(NAME, CHAIN, ok=False, error=str(exc)) for isbn in part})
    return out
