#!/usr/bin/env python3
"""Aplikacja webowa BMW 330i tracker.

    python3 webapp.py            -> http://localhost:8330

Serwer sam pobiera ogłoszenia raz dziennie (o godzinie `daily_run_hour` z config.json),
a przycisk "Odśwież dane" pozwala zrobić to od razu. Tylko biblioteka standardowa.
"""
import datetime as dt
import json
import os
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tracker as t

PORT = int(os.environ.get("PORT", "8330"))
STATIC = os.path.join(t.BASE, "static")
CONFIG_PATH = os.path.join(t.BASE, "config.json")
EDITABLE = {  # pola, które można zmienić z poziomu aplikacji
    "buyer_type": lambda v: v if v in ("private", "company") else None,
    "import_fixed_costs_pln": lambda v: max(0, int(float(v))),
    "excise_rate": lambda v: min(0.2, max(0.0, float(v))),
    "daily_run_hour": lambda v: min(23, max(0, int(v))),
    "autoscout24_countries": lambda v: ",".join(
        c for c in str(v).upper().replace(" ", "").split(",") if c.isalpha() and len(c) <= 2) or "D",
}

state = {"running": False, "message": "", "last_error": None, "lock": threading.Lock()}


def ensure_tables(con):
    con.execute("CREATE TABLE IF NOT EXISTS marks(id TEXT PRIMARY KEY, mark TEXT)")
    con.commit()


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def last_run(con):
    row = con.execute("SELECT MAX(day) FROM runs WHERE error IS NULL").fetchone()
    return row[0] if row else None


# ------------------------------------------------------------ scraping ----

def run_scrape():
    with state["lock"]:
        if state["running"]:
            return False
        state["running"], state["message"], state["last_error"] = True, "Start...", None

    def work():
        try:
            cfg = t.load_config()
            con = t.db()
            info = t.scrape_all(con, cfg, progress=lambda m: state.update(message=m))
            n_new = sum(info["new"].values())
            state["message"] = "Gotowe: %d ogłoszeń, nowych %d" % (sum(info["counts"].values()), n_new)
            if info["errors"]:
                state["last_error"] = "; ".join("%s: %s" % kv for kv in info["errors"].items())
            if cfg.get("notify_macos") and sys.platform == "darwin" and n_new:
                rows, _, _ = t.rank(con, cfg)
                if rows:
                    t.notify("BMW 330i: %d nowych ogłoszeń" % n_new,
                             "Nr 1: %s zł (%+.0f%% vs rynek)" % (t.fmt(rows[0]["cost_pln"]), rows[0]["deal_pct"]))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            state["last_error"] = str(e)
            state["message"] = "Błąd pobierania"
        finally:
            state["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return True


def scheduler():
    """Raz dziennie, po godzinie daily_run_hour, jeśli dziś jeszcze nie pobierano."""
    while True:
        try:
            cfg = t.load_config()
            con = t.db()
            now = dt.datetime.now()
            done_today = last_run(con) == now.date().isoformat()
            con.close()
            if not done_today and now.hour >= cfg.get("daily_run_hour", 8):
                t.log("Harmonogram: codzienne pobieranie")
                run_scrape()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(600)


# ------------------------------------------------------------------ API ----

def api_listings():
    cfg = t.load_config()
    con = t.db()
    ensure_tables(con)
    rows, beta, rate = t.rank(con, cfg)
    marks = dict(con.execute("SELECT id, mark FROM marks").fetchall())
    day = t.today().isoformat()
    keep = ("id", "source", "country", "url", "title", "year", "reg_month", "km", "power_hp",
            "price", "currency", "dealer", "location", "thumb", "xdrive", "touring", "m_sport",
            "first_seen", "first_price", "cost_pln", "cost_note", "expected_pln", "deal_pct",
            "vat_deductible")
    out = []
    for i, r in enumerate(rows, 1):
        o = {k: r.get(k) for k in keep}
        o["rank"] = i
        o["is_new"] = r["first_seen"] == day
        o["mark"] = marks.get(r["id"])
        o["cost_pln"], o["expected_pln"] = round(r["cost_pln"]), round(r["expected_pln"])
        o["deal_pct"] = round(r["deal_pct"], 1)
        out.append(o)
    model = {}
    if beta:
        import math
        b = dict(zip(t.FEATURES, beta))
        model = {k: round((math.exp(b[k]) - 1) * 100, 1) for k in ("km10k", "age_y", "xdrive", "touring", "m_sport")}
    errors = con.execute("SELECT source, error FROM runs WHERE day=(SELECT MAX(day) FROM runs) "
                         "AND error IS NOT NULL").fetchall()
    resp = {"rows": out, "model": model, "rate": rate, "last_run": last_run(con),
            "today": day, "config": {k: cfg.get(k) for k in EDITABLE},
            "min_year": cfg["min_year"], "errors": [{"source": s, "error": e} for s, e in errors]}
    con.close()
    return resp


def api_history(listing_id):
    con = t.db()
    rows = con.execute("SELECT day, price FROM price_history WHERE id=? ORDER BY day", (listing_id,)).fetchall()
    con.close()
    return [{"day": d, "price": p} for d, p in rows]


def api_mark(body):
    con = t.db()
    ensure_tables(con)
    if body.get("mark") in ("fav", "hidden"):
        con.execute("INSERT OR REPLACE INTO marks VALUES(?,?)", (body["id"], body["mark"]))
    else:
        con.execute("DELETE FROM marks WHERE id=?", (body["id"],))
    con.commit()
    con.close()
    return {"ok": True}


def api_config(body):
    cfg = t.load_config()
    for k, conv in EDITABLE.items():
        if k in body:
            try:
                v = conv(body[k])
            except (TypeError, ValueError):
                v = None
            if v is not None:
                cfg[k] = v
    save_config(cfg)
    return {k: cfg.get(k) for k in EDITABLE}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # cichszy log
        pass

    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path, ctype):
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self.send_file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")
            if u.path == "/api/listings":
                return self.send_json(api_listings())
            if u.path == "/api/history":
                return self.send_json(api_history(q.get("id", [""])[0]))
            if u.path == "/api/status":
                return self.send_json({k: state[k] for k in ("running", "message", "last_error")})
            self.send_json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        # proste zabezpieczenie: zapisy tylko z tej samej strony
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            return self.send_json({"error": "forbidden"}, 403)
        try:
            body = self.read_body()
            if u.path == "/api/run":
                return self.send_json({"started": run_scrape()})
            if u.path == "/api/mark":
                return self.send_json(api_mark(body))
            if u.path == "/api/config":
                return self.send_json(api_config(body))
            self.send_json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.send_json({"error": str(e)}, 500)


def main():
    cfg = t.load_config()
    if "daily_run_hour" not in cfg:
        cfg["daily_run_hour"] = 8
        save_config(cfg)
    con = t.db()
    ensure_tables(con)
    con.close()
    threading.Thread(target=scheduler, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    t.log("BMW 330i tracker: http://localhost:%d" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
