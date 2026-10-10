import json
import os
import tempfile
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="manga-tracker-test-")
os.environ["HOME_LAT"], os.environ["HOME_LON"], os.environ["RADIUS_MILES"] = "47.6062", "-122.3321", "20"

import pytest  # noqa: E402

from tracker import catalog, db, notify, watcher  # noqa: E402
from tracker.models import Book, SourceResult, StoreStock  # noqa: E402
from tracker.sources import barnesnoble, target  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- catalog parsing -------------------------------------------------------

@pytest.mark.parametrize("title, series, volume", [
    ("Chainsaw Man, Vol. 17", "Chainsaw Man", 17),
    ("One Piece (Omnibus Edition), Vol. 10: Includes vols. 28, 29 & 30", "One Piece", 10),
    ("One Piece (Omnibus Edition), Vol. 1: East Blue Vols. 1-2-3", "One Piece", 1),
    ("Berserk Deluxe, Volume 8", "Berserk", 8),
    ("Battle Angel Alita Deluxe 5 (Contains Vol. 9 & Ashen Victor)", "Battle Angel Alita", 5),
    ("Chainsaw Man 23", "Chainsaw Man", 23),
    ("Kaiju No. 8, Vol. 3", "Kaiju No. 8", 3),
    ("Battle Angel Alita Deluxe Complete Series Box Set", "Battle Angel Alita", None),
    ("Frieren: Beyond Journey's End, Vol. 1", "Frieren: Beyond Journey's End", 1),
    ("JoJo's Bizarre Adventure: Part 4--Diamond Is Unbreakable, Vol. 2", "JoJo's Bizarre Adventure: Part 4--Diamond Is Unbreakable", 2),
])
def test_parse_title(title, series, volume):
    assert catalog.parse_title(title) == (series, volume)


@pytest.mark.parametrize("title, product_type, isbn, edition", [
    ("Chainsaw Man, Vol. 17", "Paperback", "9781974752690", "Standard"),
    ("Chainsaw Man 23", "Paperback", "9784088847788", "Japanese edition"),
    ("One Piece (Omnibus Edition), Vol. 10", "Paperback", "9781421555041", "Omnibus"),
    ("Naruto (3-in-1 Edition), Vol. 1", "Paperback", "9781421539898", "Omnibus"),
    ("Berserk Deluxe, Volume 8", "Hardcover", "9781506717913", "Deluxe"),
    ("Chainsaw Man Box Set", "Paperback", "9781974000000", "Box set"),
    ("Vagabond (VIZBIG Edition), Vol. 1", "Hardcover", "9781421522777", "Hardcover"),
])
def test_classify_edition(title, product_type, isbn, edition):
    assert catalog.classify_edition(title, product_type, isbn) == edition


def test_is_manga():
    assert catalog.is_manga("VIZ Media LLC", "9781974752690")
    assert catalog.is_manga("Some Random Press", "9784088847788")  # Japanese ISBN
    assert not catalog.is_manga("Tamashii Nations", "4573102649515")


def test_group_series_orders_editions_and_picks_cover():
    books = [
        Book("1", "A, Vol. 1", "A", 1, "Standard", cover_url="std1"),
        Book("2", "A, Vol. 2", "A", 2, "Standard"),
        Book("3", "A (Omnibus Edition), Vol. 1", "A", 1, "Omnibus", cover_url="omni"),
        Book("4", "B, Vol. 1", "B", 1, "Standard"),
    ]
    groups = catalog.group_series(books)
    assert [g["series"] for g in groups] == ["A", "B"]
    assert [e["edition"] for e in groups[0]["editions"]] == ["Standard", "Omnibus"]
    assert groups[0]["cover_url"] == "std1"
    assert groups[0]["volume_count"] == 3


# --- sources ----------------------------------------------------------------

def test_barnesnoble_parse_stock():
    isbn = "9781974752690"
    result = barnesnoble.parse_stock(fixture("bn_stock.json"), [isbn])[isbn]
    assert result.ok and result.carried
    assert result.stores, "expected nearby stores"
    for store in result.stores:
        assert store.chain == "Barnes & Noble"
        assert store.distance_mi is not None and store.distance_mi <= 20
        assert store.store_id.isdigit() and not store.store_name.endswith(store.store_id)
        assert store.status in ("in_stock", "out")
        assert store.price == 11.99


def test_barnesnoble_unknown_isbn_is_not_carried():
    result = barnesnoble.parse_stock(fixture("bn_stock.json"), ["9780000000000"])["9780000000000"]
    assert result.ok and not result.carried and not result.stores


def test_target_parse_search_and_fiats():
    match = target.parse_search(fixture("target_search.json"), "9781974752690")
    assert match and match["tcin"] and match["url"].startswith("https://www.target.com/")
    stores = target.parse_fiats(fixture("target_fiats.json"), match)
    assert stores
    for store in stores:
        assert store.chain == "Target"
        assert store.status in ("in_stock", "limited", "out")
        assert store.distance_mi is not None and store.distance_mi <= 20
        assert store.address and store.phone


def test_target_status_mapping():
    payload = {"data": {"fulfillment_fiats": {"locations": [
        {"distance": 2.0, "location_available_to_promise_quantity": 3.0, "in_store_only": {"availability_status": "LIMITED_STOCK"},
         "store": {"store_id": "1", "location_name": "A", "mailing_address": {}}},
        {"distance": 3.0, "location_available_to_promise_quantity": 9.0, "in_store_only": {"availability_status": "IN_STOCK"},
         "store": {"store_id": "2", "location_name": "B", "mailing_address": {}}},
        {"distance": 99.0, "in_store_only": {"availability_status": "IN_STOCK"}, "store": {"store_id": "3", "location_name": "Far"}},
    ]}}}
    stores = target.parse_fiats(payload, {"price": 8.99, "url": "u"})
    assert [(s.store_name, s.status, s.quantity) for s in stores] == [("A", "limited", 3), ("B", "in_stock", 9)]


# --- restock detection --------------------------------------------------------

def store(store_id: str, status: str, source: str = "target") -> StoreStock:
    return StoreStock(source=source, chain=source, store_id=store_id, store_name=f"Store {store_id}", status=status)


def test_first_check_is_only_a_baseline():
    results = [SourceResult("target", "Target", ok=True, stores=[store("1", "in_stock")])]
    assert watcher.find_restocks(results, previous={}) == []


def test_restock_detected_only_for_newly_available_stores():
    previous = {("target", "1"): "out", ("target", "2"): "in_stock", ("target", "3"): "out"}
    results = [SourceResult("target", "Target", ok=True, stores=[
        store("1", "in_stock"),   # restocked
        store("2", "in_stock"),   # was already in stock
        store("3", "out"),        # still out
        store("4", "limited"),    # new store, limited counts as available
    ])]
    assert [s.store_id for s in watcher.find_restocks(results, previous)] == ["1", "4"]


def test_failed_source_never_triggers_restock():
    previous = {("target", "1"): "out"}
    results = [SourceResult("target", "Target", ok=False, error="blocked", stores=[store("1", "in_stock")])]
    assert watcher.find_restocks(results, previous) == []


def test_check_volume_records_baseline_then_alerts(monkeypatch):
    sent = []
    responses = iter([
        [SourceResult("target", "Target", ok=True, stores=[store("1", "out")])],
        [SourceResult("target", "Target", ok=True, stores=[store("1", "in_stock")])],
    ])
    monkeypatch.setattr(watcher.stock, "lookup", lambda isbn, max_age=0: (next(responses), 0))
    monkeypatch.setattr(watcher.notify, "send", lambda content=None, embeds=None: sent.append(embeds))
    monkeypatch.setattr(watcher.settings.__class__, "discord_ready", property(lambda self: True))

    book = Book("9781974752690", "Chainsaw Man, Vol. 17", "Chainsaw Man", 17, "Standard")
    watch = db.add_watch("volume", book.label, isbn=book.isbn, book=book.to_dict())
    assert watcher.check_volume(watch) == []  # baseline
    restocked = watcher.check_volume(watch)
    assert [s.store_id for s in restocked] == ["1"]
    assert sent and "Chainsaw Man Vol. 17" in sent[0][0]["title"]


def test_series_watch_learns_then_reports_new_volumes(monkeypatch):
    vol = lambda n: Book(f"97800000000{n:02d}", f"X, Vol. {n}", "X", n, "Standard")
    listings = iter([[vol(1), vol(2)], [vol(1), vol(2), vol(3)]])
    monkeypatch.setattr(watcher.catalog, "series_volumes", lambda series, edition: next(listings))
    watch = db.add_watch("series", "X", series="X", edition="Standard")
    assert watcher.check_series(watch, send=False) == []
    new = watcher.check_series(watch, send=False)
    assert [b.volume for b in new] == [3]
    assert any(w["isbn"] == vol(3).isbn for w in db.list_watches("volume"))  # auto-watched for stock


# --- notifications -------------------------------------------------------------

def test_restock_embed():
    book = Book("9781974752690", "Chainsaw Man, Vol. 17", "Chainsaw Man", 17, "Standard", cover_url="https://img")
    stores = [
        StoreStock("target", "Target", "1", "Woodinville", "limited", distance_mi=3.5, quantity=2, price=8.99),
        StoreStock("barnesnoble", "Barnes & Noble", "2", "Northgate Station", "in_stock", distance_mi=3.9, price=11.99),
    ]
    embed = notify.restock_embed(book, stores)
    assert embed["title"] == "📚 Chainsaw Man Vol. 17 is in stock nearby"
    assert embed["description"].splitlines()[0] == "**Woodinville** · Target · 3.5 mi · limited, 2 left · $8.99"
    assert embed["thumbnail"]["url"] == "https://img"
