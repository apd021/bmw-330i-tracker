#!/usr/bin/env python3
"""BMW 330i tracker: zbiera ogłoszenia z otomoto.pl i autoscout24.de,
liczy koszt zakupu w PLN (z importem z DE) i tworzy ranking opłacalności.

Uruchomienie:  python3 tracker.py            (pełny przebieg)
               python3 tracker.py --report   (tylko raport z bazy, bez pobierania)
Wymaga wyłącznie biblioteki standardowej Pythona 3.9+.
"""
import csv
import datetime as dt
import html
import json
import math
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "listings.db")
REPORT_DIR = os.path.join(BASE, "reports")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def today():
    return dt.date.today()


def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def load_config():
    with open(os.path.join(BASE, "config.json"), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- HTTP ----

def http_get(url, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pl-PL,pl;q=0.9,de;q=0.8,en;q=0.7",
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 404):
                break
        except Exception as e:  # noqa: BLE001 - sieć bywa kapryśna
            last = e
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("Nie udało się pobrać %s: %s" % (url, last))


def next_data(page):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise RuntimeError("Brak __NEXT_DATA__ na stronie (zmiana struktury portalu?)")
    return json.loads(m.group(1))


def polite_sleep(cfg):
    time.sleep(cfg["request_delay_seconds"] + random.random() * 2)


def eur_pln_rate(cfg):
    """Kurs EUR/PLN z dziennych danych ECB."""
    try:
        xml = http_get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml")
        m = re.search(r"currency='PLN' rate='([\d.]+)'", xml)
        if m:
            return float(m.group(1))
    except Exception as e:  # noqa: BLE001
        log("Kurs ECB niedostępny (%s), używam zapasowego" % e)
    return cfg["fallback_eur_pln"]


# ------------------------------------------------------------- helpers ----

def digits(s):
    d = re.sub(r"[^\d]", "", str(s or ""))
    return int(d) if d else None


# Aktywny tempomat (ACC / Stop&Go) - PL/DE/EN; używane w tytułach, wyposażeniu i opisach
ACC_RE = (r"\bacc\b|aktywny tempomat|adaptacyjny tempomat|tempomat adaptacyjny|tempomat aktywny|"
          r"kontrola odległości od poprzedzającego|abstandstempomat|aktive geschwindigkeits|"
          r"stop\s?&\s?go|distance control|active cruise|driving assistant prof")


def text_flags(text):
    t = (text or "").lower()
    return {
        "acc": bool(re.search(ACC_RE, t)),
        "xdrive": "xdrive" in t or "x-drive" in t,
        "touring": "touring" in t or "kombi" in t,
        "m_sport": bool(re.search(r"m[\s\-]?sport|m[\s\-]?pakiet|m[\s\-]?paket|m pro", t)),
    }


# -------------------------------------------------------------- otomoto ----

def scrape_otomoto(cfg):
    out = {}
    for page_no in range(1, cfg["max_pages_per_source"] + 1):
        params = {
            "search[filter_enum_fuel_type]": "petrol",
            "search[filter_enum_damaged]": "0",  # bez aut uszkodzonych (np. aukcje z USA)
            # 330i = 2.0 benzyna 245/258 KM; zakres mocy odcina 318i/320i i M340i
            "search[filter_float_engine_power:from]": "240",
            "search[filter_float_engine_power:to]": "265",
            "search[order]": "created_at_first:desc",
            "page": str(page_no),
        }
        url = ("https://www.otomoto.pl/osobowe/bmw/seria-3/od-%d?" % cfg["min_year"]
               + urllib.parse.urlencode(params))
        data = next_data(http_get(url))
        search = None
        for v in data["props"]["pageProps"].get("urqlState", {}).values():
            try:
                d = json.loads(v.get("data") or "{}")
            except ValueError:
                continue
            if d.get("advertSearch"):
                search = d["advertSearch"]
        if not search or not search.get("edges"):
            break
        for e in search["edges"]:
            n = e["node"]
            p = {x["key"]: x["value"] for x in n.get("parameters", [])}
            title = ("%s %s" % (n.get("title", ""), n.get("shortDescription", ""))).strip()
            year = digits(p.get("year"))
            if not year or year < cfg["min_year"]:
                continue
            low = title.lower()
            if re.search(r"m340|330e|330d|320|318", low) and "330i" not in low:
                continue
            if re.search(r"uszkodz|run ?& ?drive|copart|iaai|aukcj", low):
                continue
            price = n["price"]["amount"]
            gross = n["price"].get("isGross", True)
            badges = n["price"].get("badges") or []
            seller = n.get("seller", {}).get("__typename", "")
            loc = n.get("location") or {}
            out["oto-" + n["id"]] = dict(
                id="oto-" + n["id"], source="otomoto", country="PL",
                url=n["url"], title=title, year=year, reg_month=None,
                km=digits(p.get("mileage")) or 0, power_hp=digits(p.get("engine_power")),
                price=float(price["units"]), currency=price["currencyCode"],
                price_is_gross=bool(gross),
                vat_deductible=("INVOICE_ISSUED" in badges),
                dealer=(seller == "ProfessionalSeller"),
                location="%s, %s" % ((loc.get("city") or {}).get("name", ""),
                                     (loc.get("region") or {}).get("name", "")),
                thumb=(n.get("thumbnail") or {}).get("x1"),
                **text_flags(title))
        info = search.get("pageInfo") or {}
        if (info.get("currentOffset", 0) + info.get("pageSize", 32)) >= search.get("totalCount", 0):
            break
        polite_sleep(cfg)
    return list(out.values())


# ---------------------------------------------------------- autoscout24 ----

def scrape_autoscout24(cfg):
    out = {}
    for page_no in range(1, cfg["max_pages_per_source"] + 1):
        params = {
            "fregfrom": str(cfg["min_year"]), "atype": "C",
            "cy": cfg["autoscout24_countries"], "fuel": "B",
            "sort": "age", "desc": "1", "page": str(page_no),
        }
        url = "https://www.autoscout24.de/lst/bmw/330?" + urllib.parse.urlencode(params)
        pp = next_data(http_get(url))["props"]["pageProps"]
        listings = pp.get("listings") or []
        for l in listings:
            v = l.get("vehicle", {})
            if (v.get("motorTypeName") or "").lower() != "330i" or v.get("isCurrentlyDamaged"):
                continue
            tr = l.get("tracking", {})
            reg = tr.get("firstRegistration") or ""
            m = re.match(r"(\d{2})-(\d{4})", reg)
            year, month = (int(m.group(2)), int(m.group(1))) if m else (today().year, today().month)
            if year < cfg["min_year"]:
                continue
            price = l.get("price", {})
            vat_label = (price.get("vatLabel") or "").lower()
            loc = l.get("location", {})
            title = "BMW 330%s %s" % (v.get("modelVersionInput") or "", v.get("variant") or "")
            flags = text_flags(title)
            flags["touring"] = flags["touring"] or (v.get("variant") == "Touring")
            u = l.get("url", "")
            out["as24-" + l["id"]] = dict(
                id="as24-" + l["id"], source="autoscout24", country=loc.get("countryCode", "DE"),
                url=u if u.startswith("http") else "https://www.autoscout24.de" + u,
                title=title.strip(), year=year, reg_month=month,
                km=digits(tr.get("mileage") or v.get("mileageInKm")) or 0, power_hp=None,
                price=float(price.get("priceRaw") or digits(tr.get("price")) or 0), currency="EUR",
                price_is_gross=True,
                vat_deductible=("inkl" in vat_label and "nicht" not in vat_label),
                dealer=(l.get("seller", {}).get("type") == "Dealer"),
                location="%s %s" % (loc.get("zip", ""), loc.get("city", "")),
                thumb=(l.get("images") or l.get("ocsImagesA") or [None])[0],
                **flags)
        if page_no >= (pp.get("numberOfPages") or 1) or not listings:
            break
        polite_sleep(cfg)
    return [x for x in out.values() if x["price"] > 0]


SCRAPERS = {"otomoto": scrape_otomoto, "autoscout24": scrape_autoscout24}


# ------------------------------------------------------- koszt w Polsce ----

def age_months(l):
    month = l["reg_month"] or 7  # otomoto podaje tylko rok - zakładamy połowę roku
    return max(0, (today().year - l["year"]) * 12 + today().month - month)


def landed_cost_pln(l, cfg, rate):
    """Szacunkowy łączny koszt zakupu auta w Polsce (PLN).

    private: osoba prywatna, liczy się cena brutto.
    company: firma (VAT odliczalny) - liczy się cena netto, gdy jest faktura VAT.
    Import z DE: akcyza (3,1% dla silników do 2,0 l) + koszty stałe (transport,
    tłumaczenia, przegląd, rejestracja). Auto do 6 mies. lub do 6000 km to
    'nowy środek transportu' - VAT płaci się w Polsce (23%) od ceny netto.
    """
    pl_vat, de_vat = cfg["pl_vat"], cfg["de_vat"]
    company = cfg["buyer_type"] == "company"
    if l["source"] == "otomoto":
        gross = l["price"] if l["price_is_gross"] else l["price"] * (1 + pl_vat)
        if company and l["vat_deductible"]:
            return gross / (1 + pl_vat), "netto (FV 23%)"
        return gross, "brutto"
    # Niemcy
    price_pln = l["price"] * rate
    net = price_pln / (1 + de_vat) if l["vat_deductible"] else price_pln
    nst = l["km"] <= 6000 or age_months(l) <= 6
    if company and l["vat_deductible"]:
        base, note = net, "WNT netto"
    elif nst and l["vat_deductible"]:
        base, note = net * (1 + pl_vat), "NŚT: netto + 23% VAT PL"
    else:
        base, note = price_pln, "brutto DE"
    excise = (net if l["vat_deductible"] else price_pln) * cfg["excise_rate"]
    return base + excise + cfg["import_fixed_costs_pln"], note + " + akcyza + opłaty"


# ----------------------------------------------- model wartości rynkowej ----

FEATURES = ["const", "km10k", "age_y", "xdrive", "touring", "m_sport"]


def feature_vec(l):
    return [1.0, l["km"] / 10000.0, age_months(l) / 12.0,
            float(l["xdrive"]), float(l["touring"]), float(l["m_sport"])]


def solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def fit_model(rows, ridge=0.5):
    """Regresja log(koszt) na przebieg, wiek i wyposażenie (z lekką regularyzacją).
    Dwa przebiegi: drugi pomija skrajne odstające ogłoszenia (błędne ceny itp.)."""
    def fit(sample):
        k = len(FEATURES)
        xtx = [[0.0] * k for _ in range(k)]
        xty = [0.0] * k
        for x, y in sample:
            for i in range(k):
                xty[i] += x[i] * y
                for j in range(k):
                    xtx[i][j] += x[i] * x[j]
        for i in range(1, k):
            xtx[i][i] += ridge
        return solve(xtx, xty)

    data = [(feature_vec(r), math.log(r["cost_pln"])) for r in rows]
    beta = fit(data)
    resid = [y - sum(b * xi for b, xi in zip(beta, x)) for x, y in data]
    sd = (sum(e * e for e in resid) / max(1, len(resid))) ** 0.5
    trimmed = [d for d, e in zip(data, resid) if abs(e) <= 2.5 * sd]
    if len(trimmed) >= len(FEATURES) + 3:
        beta = fit(trimmed)
    return beta


def score(rows):
    if len(rows) < len(FEATURES) + 3:
        for r in rows:
            r["expected_pln"], r["deal_pct"] = r["cost_pln"], 0.0
        return rows, None
    beta = fit_model(rows)
    for r in rows:
        exp = math.exp(sum(b * x for b, x in zip(beta, feature_vec(r))))
        r["expected_pln"] = exp
        r["deal_pct"] = (exp - r["cost_pln"]) / exp * 100.0
    rows.sort(key=lambda r: r["deal_pct"], reverse=True)
    return rows, beta


# ------------------------------------------------------------- database ----

def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS listings(
        id TEXT PRIMARY KEY, source TEXT, data TEXT,
        first_seen TEXT, last_seen TEXT, first_price REAL, last_price REAL,
        active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS price_history(
        id TEXT, day TEXT, price REAL, PRIMARY KEY(id, day));
    CREATE TABLE IF NOT EXISTS runs(
        day TEXT, source TEXT, count INTEGER, error TEXT);
    """)
    return con


def store(con, source, items):
    day = today().isoformat()
    new = 0
    for l in items:
        row = con.execute("SELECT first_seen FROM listings WHERE id=?", (l["id"],)).fetchone()
        if row is None:
            new += 1
            con.execute("INSERT INTO listings VALUES(?,?,?,?,?,?,?,1)",
                        (l["id"], source, json.dumps(l, ensure_ascii=False), day, day,
                         l["price"], l["price"]))
        else:
            con.execute("UPDATE listings SET data=?, last_seen=?, last_price=?, active=1 WHERE id=?",
                        (json.dumps(l, ensure_ascii=False), day, l["price"], l["id"]))
        con.execute("INSERT OR REPLACE INTO price_history VALUES(?,?,?)", (l["id"], day, l["price"]))
    # ogłoszenia z tego portalu, których dziś nie było -> nieaktywne
    ids = [l["id"] for l in items]
    if ids:
        con.execute("UPDATE listings SET active=0 WHERE source=? AND id NOT IN (%s)"
                    % ",".join("?" * len(ids)), [source] + ids)
    con.commit()
    return new


def active_listings(con):
    rows = []
    for data, first_seen, first_price in con.execute(
            "SELECT data, first_seen, first_price FROM listings WHERE active=1"):
        l = json.loads(data)
        l["first_seen"], l["first_price"] = first_seen, first_price
        rows.append(l)
    return rows


# --------------------------------------------------------------- report ----

def fmt(n):
    return "{:,.0f}".format(n).replace(",", " ")


def write_report(rows, beta, cfg, rate, run_info):
    os.makedirs(REPORT_DIR, exist_ok=True)
    day = today().isoformat()
    top = rows[:cfg["top_n_in_report"]]
    new_ids = {r["id"] for r in rows if r["first_seen"] == day}

    def badge(r):
        b = []
        if r["id"] in new_ids:
            b.append('<span class="b new">NOWE</span>')
        if r["price"] < r["first_price"]:
            b.append('<span class="b drop">obniżka %s%%</span>'
                     % fmt((r["first_price"] - r["price"]) / r["first_price"] * 100))
        if r["deal_pct"] > 25:
            b.append('<span class="b sus" title="Bardzo tanio – sprawdź, czy nie jest uszkodzone, '
                     'z importu lub czy cena jest pełna">sprawdź!</span>')
        return " ".join(b)

    trs = []
    for i, r in enumerate(top, 1):
        flag = "🇵🇱" if r["source"] == "otomoto" else "🇩🇪"
        orig = "%s %s" % (fmt(r["price"]), "zł" if r["currency"] == "PLN" else "€")
        tags = ", ".join(t for t, k in (("xDrive", "xdrive"), ("Touring", "touring"),
                                        ("M Sport", "m_sport")) if r[k])
        cls = "good" if r["deal_pct"] > 3 else ("bad" if r["deal_pct"] < -3 else "")
        img = ('<img src="%s" loading="lazy">' % html.escape(r["thumb"])) if r.get("thumb") else ""
        trs.append("""<tr>
<td class="rank">%d</td><td class="img">%s</td>
<td><a href="%s" target="_blank">%s</a><div class="sub">%s %s · %s · %s %s</div></td>
<td>%s%s</td><td class="num">%s km</td><td class="num">%s</td>
<td class="num"><b>%s zł</b><div class="sub">%s</div></td>
<td class="num">%s zł</td><td class="num %s"><b>%+.1f%%</b><div class="sub">%s zł</div></td></tr>"""
                   % (i, img, html.escape(r["url"]), html.escape(r["title"][:90]),
                      flag, html.escape(r["location"]), html.escape(tags) or "—",
                      "dealer" if r["dealer"] else "prywatny", badge(r),
                      r["year"], ("/%02d" % r["reg_month"]) if r["reg_month"] else "",
                      fmt(r["km"]), orig, fmt(r["cost_pln"]), html.escape(r["cost_note"]),
                      fmt(r["expected_pln"]), cls, r["deal_pct"],
                      fmt(r["expected_pln"] - r["cost_pln"])))

    by_src = {}
    for r in rows:
        by_src.setdefault(r["source"], []).append(r["cost_pln"])
    src_stats = " · ".join("%s: %d ogł., mediana %s zł" % (s, len(v), fmt(sorted(v)[len(v) // 2]))
                           for s, v in sorted(by_src.items()))
    model_txt = ""
    if beta:
        b = dict(zip(FEATURES, beta))
        model_txt = ("Model rynku: każde 10 000 km ≈ %+.1f%% ceny, każdy rok wieku ≈ %+.1f%%, "
                     "xDrive ≈ %+.1f%%, Touring ≈ %+.1f%%."
                     % tuple((math.exp(b[k]) - 1) * 100 for k in ("km10k", "age_y", "xdrive", "touring")))
    errors = "".join('<div class="err">⚠ %s: %s</div>' % (html.escape(s), html.escape(e))
                     for s, e in run_info.get("errors", {}).items())

    page = """<!DOCTYPE html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BMW 330i – ranking %s</title><style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:24px;color:#1c1c1e;background:#f5f5f7}
h1{margin:0 0 4px}.meta{color:#666;margin-bottom:16px;line-height:1.5}
table{border-collapse:collapse;width:100%%;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px #0001}
th,td{padding:8px 10px;border-bottom:1px solid #eee;vertical-align:middle;font-size:14px;text-align:left}
th{background:#1c69d4;color:#fff;font-weight:600}td.num{text-align:right;white-space:nowrap}
td.rank{font-size:20px;font-weight:700;color:#1c69d4;text-align:center}
td.img img{width:120px;border-radius:6px;display:block}a{color:#1c1c1e;font-weight:600;text-decoration:none}
a:hover{text-decoration:underline}.sub{color:#888;font-size:12px;font-weight:400}
.good{color:#1a7f37}.bad{color:#c62828}.b{font-size:11px;padding:2px 6px;border-radius:8px;margin-left:4px;color:#fff}
.new{background:#1a7f37}.sus{background:#c62828}.drop{background:#e67e22}.err{background:#fdecea;color:#b71c1c;padding:8px;border-radius:8px;margin:6px 0}
footer{color:#888;font-size:12px;margin-top:16px;line-height:1.5}
</style></head><body>
<h1>BMW 330i (od %d) – ranking opłacalności</h1>
<div class="meta">%s · aktywnych ogłoszeń: <b>%d</b> · nowych dziś: <b>%d</b> · kurs EUR/PLN (ECB): %.4f · tryb: %s<br>%s<br>%s</div>
%s
<table><tr><th>#</th><th></th><th>Ogłoszenie</th><th>Rok</th><th>Przebieg</th><th>Cena</th>
<th>Koszt w PL</th><th>Wartość rynkowa</th><th>Okazja</th></tr>%s</table>
<footer>„Okazja” = o ile koszt zakupu w Polsce jest niższy od wartości oczekiwanej dla auta o takim przebiegu,
wieku i wyposażeniu (model dopasowany do wszystkich bieżących ogłoszeń PL+DE).
Koszt importu z DE: akcyza %.1f%%, opłaty stałe %s zł; auta do 6 mies./6000 km liczone jako nowy środek transportu (VAT 23%% w PL).
Szacunki orientacyjne – sprawdź stan, historię i wyposażenie przed zakupem.</footer>
</body></html>""" % (day, cfg["min_year"], day, len(rows), len(new_ids), rate,
                     "firma (netto)" if cfg["buyer_type"] == "company" else "osoba prywatna (brutto)",
                     src_stats, model_txt, errors, "\n".join(trs),
                     cfg["excise_rate"] * 100, fmt(cfg["import_fixed_costs_pln"]))

    path = os.path.join(REPORT_DIR, "ranking-%s.html" % day)
    for p in (path, os.path.join(REPORT_DIR, "latest.html")):
        with open(p, "w", encoding="utf-8") as f:
            f.write(page)
    with open(os.path.join(REPORT_DIR, "ranking-%s.csv" % day), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "source", "title", "year", "reg_month", "km", "price", "currency",
                    "cost_pln", "expected_pln", "deal_pct", "new_today", "url"])
        for i, r in enumerate(rows, 1):
            w.writerow([i, r["source"], r["title"], r["year"], r["reg_month"] or "", r["km"],
                        r["price"], r["currency"], round(r["cost_pln"]), round(r["expected_pln"]),
                        round(r["deal_pct"], 1), int(r["id"] in new_ids), r["url"]])
    return path, len(new_ids)


def notify(title, msg):
    try:
        subprocess.run(["osascript", "-e", 'display notification %s with title %s'
                        % (json.dumps(msg, ensure_ascii=False), json.dumps(title, ensure_ascii=False))], check=False, timeout=10)
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------------- main ----

def scrape_all(con, cfg, progress=None):
    """Pobiera wszystkie portale; błąd jednego nie zatrzymuje pozostałych."""
    run_info = {"errors": {}, "counts": {}, "new": {}}
    for name in cfg["sources"]:
        try:
            log("Pobieram %s..." % name)
            if progress:
                progress("Pobieram %s..." % name)
            items = SCRAPERS[name](cfg)
            new = store(con, name, items)
            run_info["counts"][name], run_info["new"][name] = len(items), new
            log("%s: %d ogłoszeń 330i (nowych: %d)" % (name, len(items), new))
            con.execute("INSERT INTO runs VALUES(?,?,?,NULL)", (today().isoformat(), name, len(items)))
        except Exception as e:  # noqa: BLE001 - jeden portal nie blokuje reszty
            log("BŁĄD %s: %s" % (name, e))
            run_info["errors"][name] = str(e)
            con.execute("INSERT INTO runs VALUES(?,?,0,?)", (today().isoformat(), name, str(e)))
        con.commit()
    return run_info


_rate_cache = {}


def cached_rate(cfg):
    day = today().isoformat()
    if day not in _rate_cache:
        _rate_cache.clear()
        _rate_cache[day] = eur_pln_rate(cfg)
    return _rate_cache[day]


def rank(con, cfg):
    """Zwraca (wiersze posortowane wg okazji, współczynniki modelu, kurs EUR/PLN)."""
    rate = cached_rate(cfg)
    rows = active_listings(con)
    for r in rows:
        r["cost_pln"], r["cost_note"] = landed_cost_pln(r, cfg, rate)
    rows, beta = score(rows) if rows else (rows, None)
    # Ranking = okazja cenowa + premia za preferowane wyposażenie (aktywny tempomat)
    bonus = cfg.get("acc_bonus_pct", 5)
    for r in rows:
        if "acc" not in r:  # starsze wpisy w bazie - wykryj z tytułu
            r["acc"] = bool(re.search(ACC_RE, (r.get("title") or "").lower()))
        r["acc_bonus"] = bonus if r.get("acc") else 0
        r["score"] = r["deal_pct"] + r["acc_bonus"]
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows, beta, rate


def main():
    cfg = load_config()
    con = db()
    run_info = {"errors": {}}
    if "--report" not in sys.argv:
        run_info = scrape_all(con, cfg)
    rows, beta, rate = rank(con, cfg)
    if not rows:
        log("Brak ogłoszeń w bazie.")
        return 1
    path, n_new = write_report(rows, beta, cfg, rate, run_info)
    log("Raport: %s" % path)
    for i, r in enumerate(rows[:5], 1):
        log("  %d. %+.1f%%  %s zł  %s %s km  %s" % (i, r["deal_pct"], fmt(r["cost_pln"]),
                                                   r["year"], fmt(r["km"]), r["url"]))
    if cfg.get("notify_macos") and sys.platform == "darwin":
        best = rows[0]
        notify("BMW 330i: %d nowych ogłoszeń" % n_new,
               "Nr 1: %s zł (%+.0f%% vs rynek), %s km" % (fmt(best["cost_pln"]), best["deal_pct"], fmt(best["km"])))
    if cfg.get("open_report_after_run") and sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
