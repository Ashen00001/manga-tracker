"""Discord DMs through the bot REST API (no gateway connection needed).
The bot has to share a server with you for Discord to allow the DM."""
from curl_cffi import requests

from . import db
from .config import settings
from .models import Book, StoreStock

API = "https://discord.com/api/v10"
GREEN, BLUE = 0x2ECC71, 0x5865F2


class DiscordError(Exception):
    pass


def _headers() -> dict:
    return {"Authorization": f"Bot {settings.discord_token}", "Content-Type": "application/json"}


def _dm_channel() -> str:
    cached = db.cache_get("discord:dm_channel")
    if cached:
        return cached
    r = requests.post(f"{API}/users/@me/channels", json={"recipient_id": settings.discord_user_id}, headers=_headers(), timeout=20)
    if r.status_code != 200:
        raise DiscordError(f"Couldn't open a DM ({r.status_code}): {r.text[:200]}")
    channel_id = r.json()["id"]
    db.cache_set("discord:dm_channel", channel_id, ttl_hours=24 * 365)
    return channel_id


def send(content: str | None = None, embeds: list[dict] | None = None) -> None:
    if not settings.discord_ready:
        raise DiscordError("DISCORD_BOT_TOKEN and DISCORD_USER_ID aren't set")
    r = requests.post(
        f"{API}/channels/{_dm_channel()}/messages",
        json={"content": content, "embeds": embeds or []},
        headers=_headers(),
        timeout=20,
    )
    if r.status_code not in (200, 201):
        raise DiscordError(f"Discord said {r.status_code}: {r.text[:200]}")


def _app_link(isbn: str) -> str | None:
    return f"{settings.public_url.rstrip('/')}/#/book/{isbn}" if settings.public_url else None


def store_line(s: StoreStock) -> str:
    bits = [s.chain + (" (used)" if s.condition == "used" else "")]
    if s.distance_mi is not None:
        bits.append(f"{s.distance_mi:g} mi")
    if s.status == "limited":
        bits.append("limited" + (f", {s.quantity} left" if s.quantity else ""))
    elif s.quantity and s.quantity < 50:
        bits.append(f"{s.quantity} in stock")
    if s.price:
        bits.append(f"${s.price:.2f}")
    return f"**{s.store_name}** · " + " · ".join(bits)


def restock_embed(book: Book, stores: list[StoreStock]) -> dict:
    lines = [store_line(s) for s in sorted(stores, key=lambda s: s.distance_mi or 999)[:12]]
    embed = {
        "title": f"📚 {book.label} is in stock nearby",
        "description": "\n".join(lines),
        "color": GREEN,
        "footer": {"text": f"ISBN {book.isbn}"},
    }
    if url := _app_link(book.isbn) or (stores[0].url if stores else None):
        embed["url"] = url
    if book.cover_url:
        embed["thumbnail"] = {"url": book.cover_url}
    return embed


def new_volume_embed(book: Book) -> dict:
    embed = {
        "title": f"🆕 New volume listed: {book.label}",
        "description": "Added to your watchlist. You'll get a DM when it's on a shelf near you.",
        "color": BLUE,
        "footer": {"text": f"ISBN {book.isbn}"},
    }
    if url := _app_link(book.isbn):
        embed["url"] = url
    if book.cover_url:
        embed["thumbnail"] = {"url": book.cover_url}
    return embed
