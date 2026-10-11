"""python -m tracker [--log FILE] <command>

  serve [--host H] [--port P]   run the web app + background checks
  search <text>                  search the catalog
  stock <isbn>                   stock at every store near you
  check [--if-due]               run the watchlist check once (--if-due: skip if one ran recently)
  test-discord                   send a test DM

--log appends all output to FILE, for running windowless under pythonw.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import catalog, notify, stock, watcher

LOG_MAX_BYTES = 5 * 1024 * 1024


def log_to(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
        try:
            path.replace(path.with_suffix(path.suffix + ".1"))
        except OSError:
            pass  # another process has it open; rotate next time
    out = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = out
    print(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}  {' '.join(sys.argv[1:])}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="tracker")
    parser.add_argument("--log", type=Path, help="append output to this file")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8787)
    sub.add_parser("search").add_argument("text", nargs="+")
    sub.add_parser("stock").add_argument("isbn")
    sub.add_parser("check").add_argument("--if-due", action="store_true")
    sub.add_parser("test-discord")
    args = parser.parse_args()
    if args.log:
        log_to(args.log)

    if args.command == "serve":
        import uvicorn
        uvicorn.run("tracker.web:app", host=args.host, port=args.port)
    elif args.command == "search":
        result = catalog.search(" ".join(args.text))
        for book in result["books"]:
            print(f"{book.isbn}  {book.label:<55}  {book.edition:<16} {book.publisher or ''}  ${book.price or 0:.2f}")
    elif args.command == "stock":
        results, stamp = stock.lookup(args.isbn, max_age=0)
        payload = stock.to_payload(args.isbn, results, stamp)
        for src in payload["sources"]:
            state = "ok" if src["ok"] else f"FAILED ({src['error']})"
            print(f"[{src['chain']}] {state}, {'carried' if src['carried'] else 'not carried'}, {src['stores']} stores")
        for s in payload["stores"]:
            mark = {"in_stock": "✅", "limited": "🟡", "out": "  ", "unknown": "❔"}[s["status"]]
            qty = f" x{s['quantity']}" if s["quantity"] is not None else ""
            dist = f"{s['distance_mi']:>5} mi" if s["distance_mi"] is not None else "     ? "
            print(f"  {mark} {dist}  {s['chain']:<15} {s['store_name']:<28}{qty}  ${s['price'] or 0:.2f}")
    elif args.command == "check":
        summary = watcher.run_if_due() if args.if_due else watcher.check_all()
        print(json.dumps(summary, indent=2) if summary is not None else "not due, skipped")
    elif args.command == "test-discord":
        notify.send("👋 Manga Tracker is connected. Restock alerts will show up here.")
        print("sent")


if __name__ == "__main__":
    main()
