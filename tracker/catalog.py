"""Find manga volumes (and their ISBNs) from free-text queries.

B&N's catalog is the main source: it has nearly every English manga edition with ISBN,
cover and list price. Open Library is the fallback for anything B&N doesn't list.
"""
import re

from curl_cffi import requests

from . import db
from .models import Book
from .sources import barnesnoble

MANGA_PUBLISHERS = (
    "viz", "kodansha", "yen press", "yen on", "seven seas", "dark horse", "square enix", "vertical",
    "tokyopop", "ghost ship", "airship", "j-novel", "udon", "denpa", "one peace", "kaiten", "titan",
    "ize press", "sublime", "inklore", "shueisha", "shogakukan", "kadokawa", "hakusensha", "akita",
    "futabasha", "ichijinsha", "square enix manga",
)
EXCLUDED_TYPES = ("eBook", "Audiobook", "Other Format", "Other Form", "CD", "Vinyl LP", "DVD", "Blu-ray")

VOLUME_RE = re.compile(r"(?:\bvol(?:ume)?\.?|#)\s*(\d+(?:\.\d+)?)\b", re.I)
TRAILING_NUMBER_RE = re.compile(r"\s(\d{1,3}(?:\.\d)?)$")
QUERY_VOLUME_RE = re.compile(r"^(.*?)(?:,?\s*(?:vol(?:ume)?\.?|v\.?|#))?\s*(\d{1,3})$", re.I)
EDITION_WORDS_RE = re.compile(
    r"\b(deluxe|omnibus|edition|collector'?s|hardcover|box set|complete series|special|limited|\d-in-\d)\b",
    re.I,
)

SEARCH_QUERY = """
query($q: String!, $after: String) {
  products(first: 100, query: $q, after: $after, sortKey: TITLE) {
    pageInfo { hasNextPage endCursor }
    nodes {
      title productType vendor
      featuredImage { url }
      variants(first: 1) { nodes { barcode price { amount } } }
    }
  }
}
"""


def classify_edition(title: str, product_type: str | None, isbn: str) -> str:
    t = title.lower()
    if isbn.startswith("9784"):
        return "Japanese edition"
    if "box set" in t:
        return "Box set"
    if "omnibus" in t or re.search(r"\b\d-in-\d\b", t):
        return "Omnibus"
    if "deluxe" in t:
        return "Deluxe"
    if "collector" in t:
        return "Collector's edition"
    if "special edition" in t or "limited edition" in t:
        return "Special edition"
    if (product_type or "").lower() == "hardcover":
        return "Hardcover"
    return "Standard"


def parse_title(title: str) -> tuple[str, float | None]:
    """'One Piece (Omnibus Edition), Vol. 10: Includes vols. 28, 29 & 30' -> ('One Piece', 10)"""
    base = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", title).strip()
    volume = None
    match = VOLUME_RE.search(base)
    if match:
        volume = float(match.group(1))
        series = base[: match.start()]
    else:
        trailing = TRAILING_NUMBER_RE.search(base)
        if trailing:
            volume = float(trailing.group(1))
            series = base[: trailing.start()]
        else:
            series = base
    series = EDITION_WORDS_RE.sub("", series)
    series = re.sub(r"\s{2,}", " ", series).strip(" ,-–:")
    return series or title, volume


def is_manga(vendor: str | None, isbn: str) -> bool:
    v = (vendor or "").lower()
    return isbn.startswith("9784") or any(p in v for p in MANGA_PUBLISHERS)


def _book_from_node(node: dict) -> Book | None:
    if node.get("productType") in EXCLUDED_TYPES:
        return None
    variants = node["variants"]["nodes"]
    if not variants or not variants[0].get("barcode"):
        return None
    isbn = variants[0]["barcode"]
    if not re.fullmatch(r"97[89]\d{10}", isbn):
        return None
    series, volume = parse_title(node["title"])
    price = variants[0].get("price")
    return Book(
        isbn=isbn,
        title=node["title"],
        series=series,
        volume=volume,
        edition=classify_edition(node["title"], node.get("productType"), isbn),
        format=node.get("productType"),
        publisher=node.get("vendor"),
        cover_url=(node.get("featuredImage") or {}).get("url"),
        price=float(price["amount"]) if price else None,
        language="Japanese" if isbn.startswith("9784") else "English",
    )


def _bn_search(shopify_query: str, max_pages: int = 3) -> list[tuple[Book, str | None]]:
    out, after = [], None
    for _ in range(max_pages):
        data = barnesnoble.graphql(SEARCH_QUERY, {"q": shopify_query, "after": after})["products"]
        for node in data["nodes"]:
            book = _book_from_node(node)
            if book:
                out.append((book, node.get("vendor")))
        if not data["pageInfo"]["hasNextPage"]:
            break
        after = data["pageInfo"]["endCursor"]
    return out


def _open_library(isbn: str) -> Book | None:
    r = requests.get(f"https://openlibrary.org/isbn/{isbn}.json", timeout=20, impersonate="chrome")
    if r.status_code != 200:
        return None
    data = r.json()
    series, volume = parse_title(data.get("title", isbn))
    covers = data.get("covers") or []
    return Book(
        isbn=isbn,
        title=data.get("title", isbn),
        series=series,
        volume=volume,
        edition=classify_edition(data.get("title", ""), None, isbn),
        publisher=(data.get("publishers") or [None])[0],
        cover_url=f"https://covers.openlibrary.org/b/id/{covers[0]}-M.jpg" if covers else None,
    )


def _sort_key(book: Book):
    return (book.series.lower(), book.edition != "Standard", book.edition, book.volume if book.volume is not None else 1e9, book.title)


def search(text: str) -> dict:
    """Returns {"books": [...], "series_query": str, "volume": float | None}."""
    text = text.strip()
    digits = re.sub(r"[\s\-]", "", text)
    if re.fullmatch(r"97[89]\d{10}", digits):
        book = lookup_isbn(digits)
        return {"books": [book] if book else [], "series_query": text, "volume": None}

    clean = text.replace('"', "").replace("\\", "")
    wanted_volume = None
    match = QUERY_VOLUME_RE.match(clean)
    if match and match.group(1).strip():
        clean, wanted_volume = match.group(1).strip(), float(match.group(2))

    exclusions = " ".join(f'-product_type:"{t}"' for t in EXCLUDED_TYPES)
    found = _bn_search(f'title:"{clean}" {exclusions}')
    if not found and wanted_volume is not None:
        # "Mob Psycho 100" style titles: the trailing number is part of the name
        clean, wanted_volume = text.replace('"', ""), None
        found = _bn_search(f'title:"{clean}" {exclusions}')
    manga = [b for b, vendor in found if is_manga(vendor, b.isbn)]
    books = manga or [b for b, _ in found]
    if wanted_volume is not None:
        same_volume = [b for b in books if b.volume == wanted_volume]
        books = same_volume or books

    unique = {b.isbn: b for b in books}
    for book in unique.values():
        db.cache_set(f"book:{book.isbn}", book.to_dict(), ttl_hours=24 * 7)
    return {"books": sorted(unique.values(), key=_sort_key), "series_query": clean, "volume": wanted_volume}


def lookup_isbn(isbn: str) -> Book | None:
    cached = db.cache_get(f"book:{isbn}")
    if cached:
        cached.pop("label", None)
        return Book(**cached)
    found = _bn_search(isbn, max_pages=1)
    book = next((b for b, _ in found if b.isbn == isbn), None) or _open_library(isbn)
    if book:
        db.cache_set(f"book:{isbn}", book.to_dict(), ttl_hours=24 * 7)
    return book


EDITION_ORDER = ["Standard", "Omnibus", "Deluxe", "Hardcover", "Collector's edition", "Special edition", "Box set", "Japanese edition"]


def group_series(books: list[Book]) -> list[dict]:
    """Collapse a flat book list into series -> editions -> volumes (books arrive pre-sorted)."""
    groups: dict[str, dict] = {}
    for book in books:
        group = groups.setdefault(book.series.lower(), {"series": book.series, "publisher": book.publisher, "editions": {}})
        group["editions"].setdefault(book.edition, []).append(book)
    out = []
    for group in groups.values():
        editions = sorted(group["editions"], key=lambda e: EDITION_ORDER.index(e) if e in EDITION_ORDER else 99)
        first = group["editions"][editions[0]]
        cover = next((b.cover_url for b in first if b.cover_url), None)
        out.append({
            "series": group["series"],
            "publisher": group["publisher"],
            "cover_url": cover,
            "volume_count": sum(len(v) for v in group["editions"].values()),
            "editions": [{"edition": e, "count": len(group["editions"][e])} for e in editions],
        })
    return sorted(out, key=lambda g: -g["volume_count"])


def series_detail(name: str) -> dict | None:
    books = [b for b in search(name)["books"] if b.series.lower() == name.lower()]
    if not books:
        return None
    summary = group_series(books)[0]
    by_edition: dict[str, list[dict]] = {}
    for book in books:
        by_edition.setdefault(book.edition, []).append(book.to_dict())
    summary["volumes"] = {e["edition"]: by_edition[e["edition"]] for e in summary["editions"]}
    return summary


def series_volumes(series: str, edition: str) -> list[Book]:
    """Every listed volume of a series in a given edition (used by series watches)."""
    result = search(series)
    return [b for b in result["books"] if b.series.lower() == series.lower() and b.edition == edition]
