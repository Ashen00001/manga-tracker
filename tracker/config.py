import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    home_lat: float
    home_lon: float
    home_zip: str
    radius_miles: float
    data_dir: Path
    check_interval_hours: float
    discord_token: str | None
    discord_user_id: str | None
    app_password: str | None
    public_url: str | None

    @property
    def discord_ready(self) -> bool:
        return bool(self.discord_token and self.discord_user_id)


def load() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        home_lat=float(os.environ.get("HOME_LAT", "47.6062")),
        home_lon=float(os.environ.get("HOME_LON", "-122.3321")),
        home_zip=os.environ.get("HOME_ZIP", "98101"),
        radius_miles=float(os.environ.get("RADIUS_MILES", "20")),
        data_dir=data_dir,
        check_interval_hours=float(os.environ.get("CHECK_INTERVAL_HOURS", "4")),
        discord_token=_optional("DISCORD_BOT_TOKEN"),
        discord_user_id=_optional("DISCORD_USER_ID"),
        app_password=_optional("APP_PASSWORD"),
        public_url=_optional("PUBLIC_URL"),
    )


settings = load()
