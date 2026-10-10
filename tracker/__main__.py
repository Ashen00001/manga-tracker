"""python -m tracker <command>

  serve [--host H] [--port P]   run the web app + background checks
  search <text>                  search the catalog
  stock <isbn>                   stock at every store near you
  check                          run the watchlist check once
  test-discord                   send a test DM
"""
import argparse
import json

from . import catalog, notify, stock, watcher


def main() -> None:
    parser = argparse.ArgumentParser(prog="tracker")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8787)
    sub.add_parser("search").add_argument("text", nargs="+")
    sub.add_parser("stock").add_argument("isbn")
    sub.add_parser("check")
    sub.add_parser("test-discord")
    args = parser.parse_args()

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
        print(json.dumps(watcher.check_all(), indent=2))
    elif args.command == "test-discord":
        notify.send("👋 Manga Tracker is connected. Restock alerts will show up here.")
        print("sent")


if __name__ == "__main__":
    main()
