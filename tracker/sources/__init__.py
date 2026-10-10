from concurrent.futures import ThreadPoolExecutor

from ..models import SourceResult
from . import barnesnoble, target

# Add new stores here. Each module exposes NAME, CHAIN and stock(isbn) -> SourceResult.
SOURCES = [barnesnoble, target]


def lookup_all(isbn: str) -> list[SourceResult]:
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        return list(pool.map(lambda source: source.stock(isbn), SOURCES))
