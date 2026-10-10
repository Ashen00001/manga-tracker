import math
from dataclasses import asdict, dataclass, field
from typing import Literal

Status = Literal["in_stock", "limited", "out", "unknown"]
AVAILABLE: tuple[Status, ...] = ("in_stock", "limited")


@dataclass
class Book:
    isbn: str
    title: str
    series: str
    volume: float | None
    edition: str
    format: str | None = None
    publisher: str | None = None
    cover_url: str | None = None
    price: float | None = None
    language: str = "English"

    @property
    def label(self) -> str:
        if self.volume is None:
            return self.title
        vol = int(self.volume) if float(self.volume).is_integer() else self.volume
        extra = "" if self.edition == "Standard" else f" ({self.edition})"
        return f"{self.series} Vol. {vol}{extra}"

    def to_dict(self) -> dict:
        return asdict(self) | {"label": self.label}


@dataclass
class StoreStock:
    source: str
    chain: str
    store_id: str
    store_name: str
    status: Status
    condition: Literal["new", "used"] = "new"
    address: str | None = None
    phone: str | None = None
    distance_mi: float | None = None
    quantity: int | None = None
    price: float | None = None
    url: str | None = None

    @property
    def available(self) -> bool:
        return self.status in AVAILABLE

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SourceResult:
    source: str
    chain: str
    ok: bool
    stores: list[StoreStock] = field(default_factory=list)
    carried: bool = True
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "chain": self.chain,
            "ok": self.ok,
            "carried": self.carried,
            "error": self.error,
            "stores": [s.to_dict() for s in self.stores],
        }


def miles_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
