#!/usr/bin/env python3
"""Buduje statyczną wersję aplikacji (GitHub Pages) do katalogu site/.

    python3 build_site.py              # pobierz ogłoszenia + zbuduj stronę
    python3 build_site.py --no-scrape  # tylko przebuduj z bazy
"""
import json
import os
import shutil
import sys

import tracker as t
import webapp

OUT = os.path.join(t.BASE, "site")


def main():
    cfg = t.load_config()
    con = t.db()
    if "--no-scrape" not in sys.argv:
        info = t.scrape_all(con, cfg)
        if not any(info["counts"].values()):
            t.log("UWAGA: żaden portal nie zwrócił ogłoszeń: %s" % info["errors"])
    data = webapp.api_listings()
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
    open(os.path.join(OUT, ".nojekyll"), "w").close()
    t.log("Strona zbudowana: %s (%d ogłoszeń)" % (OUT, len(data["rows"])))


if __name__ == "__main__":
    main()
