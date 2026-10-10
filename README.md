# Manga Tracker

Search a manga series, pick a volume, and see **which stores near you have it on the shelf right now**, with the cheapest one on top. Like an Amazon product page, but for stores you can drive to.

- **Search** a series ("frieren") or a specific volume ("chainsaw man 17", or an ISBN)
- **Series page:** every volume with its cover, grouped by edition (Standard / Omnibus / Deluxe / Box set / Japanese edition), each with an "In stock · 9 nearby" badge
- **Volume page:** best nearby price, then every store that has it, sorted by distance, with Directions / Call / View online buttons
- **Notify me:** get a Discord DM when a watched volume shows up in stock nearby, or when a new volume of a watched series is listed
- Works as a home-screen app on iPhone (Share → Add to Home Screen)

## Stores

| Store | How | Notes |
|---|---|---|
| Barnes & Noble | Official Shopify Storefront API (the token B&N's own site uses) | In/out per store, quantities hidden. Also powers the catalog search |
| Target | `redsky` API that target.com uses | In stock / limited / out, with quantity. Behind PerimeterX, so lookups retry and Target product IDs are cached |
| Kinokuniya Seattle, Third Place Books, Half Price Books (used) | *Planned* | Their sites block VPN/datacenter IPs; needs testing from a home connection |

Stores live in `tracker/sources/`. Each one is a module exposing `NAME`, `CHAIN` and `stock(isbn) -> SourceResult`; add it to `SOURCES` in `tracker/sources/__init__.py`. Used copies set `condition="used"` and show a **Used** badge.

## Run it locally

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
cp .env.example .env                          # then edit
.venv/bin/python -m tracker serve             # http://localhost:8787
```

Handy CLI:

```bash
python -m tracker search chainsaw man 17
python -m tracker stock 9781974752690
python -m tracker check          # run the watchlist check once
python -m tracker test-discord   # send a test DM
```

Tests: `pip install pytest && pytest` (parsers run against recorded real responses in `tests/fixtures`).

## Discord bot (for DMs)

1. <https://discord.com/developers/applications> → **New Application** ("Manga Tracker") → **Bot** → **Reset Token**, then copy it into `DISCORD_BOT_TOKEN`.
2. **OAuth2 → URL Generator**: tick the `bot` scope (no permissions needed). Open the URL and add the bot to any server you're in. Discord only lets bots DM people they share a server with.
3. Discord **Settings → Advanced → Developer Mode** on. Right-click your name, **Copy User ID**, and put it in `DISCORD_USER_ID`.
4. `python -m tracker test-discord`

## Deploy (Oracle Cloud free tier)

Works on the free Ampere (ARM) or AMD VMs.

```bash
# on the server
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
git clone <this repo> manga-tracker && cd manga-tracker
cp .env.example .env && nano .env
sudo docker compose up -d --build
```

**Reaching it from your phone:**

- **Tailscale (recommended):** install Tailscale on the VM, then open `http://<vm-tailscale-name>:8787`. Nothing is exposed to the internet, so no password is needed.
- **Public:** open port 8787 in the VCN security list and in the VM's iptables, **set `APP_PASSWORD`**, and ideally put Caddy in front for HTTPS.

Set `PUBLIC_URL` so Discord alerts open the right volume in the app.

## Notes

- Watchlist checks run every `CHECK_INTERVAL_HOURS` inside the server process. The first check of a new watch only records a baseline, so you're alerted on *changes*. If a store fails to load, it's skipped rather than treated as "out of stock", so you don't get fake restock alerts.
- Retailer APIs are unofficial (except B&N's Storefront API) and can change; each store is isolated in its own module.
- Data is a single SQLite file in `DATA_DIR` (`./data` by default).
