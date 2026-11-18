#!/usr/bin/env python3
"""Buduje statyczną wersję aplikacji (GitHub Pages) do katalogu site/.

    python3 build_site.py              # pobierz ogłoszenia + zbuduj stronę
    python3 build_site.py --no-scrape  # tylko przebuduj z bazy
"""
import json
import os
import shutil
import sys

import details as D
import tracker as t
import webapp

OUT = os.path.join(t.BASE, "site")


def write_analyses(con, cfg):
    """Pełna analiza każdego aktywnego ogłoszenia -> site/analysis/<id>.json.
    Podsumowanie AI dla najlepszych `ai_top_n` ogłoszeń, jeśli jest klucz ANTHROPIC_API_KEY
    (zapisywane w bazie, więc płacisz tylko raz za ogłoszenie, chyba że zmieni się cena)."""
    D.ensure_tables(con)
    rows, rate = D.ranked(con, cfg)
    out = os.path.join(OUT, "analysis")
    os.makedirs(out, exist_ok=True)
    use_ai = D.ai_available() and cfg.get("ai_top_n", 0) > 0
    made = 0
    for r in rows:
        a, row = D.full_analysis(con, cfg, r["id"], rows, rate, fetch=False)
        if use_ai and r["rank"] <= cfg["ai_top_n"] and a["ai"] is None:
            try:
                a["ai"] = D.make_ai(con, a, row, cfg)
                made += 1
            except Exception as e:  # noqa: BLE001 - analiza regułowa i tak jest
                t.log("AI %s: %s" % (r["id"], e))
        with open(os.path.join(out, r["id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False, separators=(",", ":"))
    t.log("Analizy: %d ogłoszeń, nowych podsumowań AI: %d%s"
          % (len(rows), made, "" if use_ai else " (brak klucza ANTHROPIC_API_KEY – AI wyłączone)"))


def main():
    cfg = t.load_config()
    con = t.db()
    if "--no-scrape" not in sys.argv:
        info = t.scrape_all(con, cfg)
        if not any(info["counts"].values()):
            t.log("UWAGA: żaden portal nie zwrócił ogłoszeń: %s" % info["errors"])
        D.fetch_missing(con, cfg)
    write_analyses(con, cfg)
    data = webapp.api_listings()
    data["ai_enabled"] = D.ai_available() and cfg.get("ai_top_n", 0) > 0
    ids = {r["id"] for r in data["rows"]}
    hist = {}
    for lid, day, price in t.db().execute("SELECT id, day, price FROM price_history ORDER BY day"):
        if lid in ids:
            hist.setdefault(lid, []).append([day, price])
    data["history"] = hist

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    site_cfg = {
        "actions": "https://github.com/%s/actions/workflows/daily.yml" % repo,
        "config": "https://github.com/%s/edit/main/config.json" % repo,
        "repo": repo,
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(t.BASE, "static", "index.html"), encoding="utf-8") as f:
        page = f.read()
    inject = "<script>window.STATIC_SITE=%s;</script>\n<script>" % json.dumps(site_cfg)
    page = page.replace("<script>", inject, 1)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    # PWA: manifest, service worker i ikony (instalacja na telefonie/komputerze, praca offline)
    for name in ("manifest.webmanifest", "sw.js"):
        shutil.copy(os.path.join(t.BASE, "static", name), os.path.join(OUT, name))
    shutil.copytree(os.path.join(t.BASE, "static", "icons"), os.path.join(OUT, "icons"), dirs_exist_ok=True)
    open(os.path.join(OUT, ".nojekyll"), "w").close()
    t.log("Strona zbudowana: %s (%d ogłoszeń)" % (OUT, len(data["rows"])))


if __name__ == "__main__":
    main()
