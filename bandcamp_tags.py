#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bandcamp_tags.py — find each artist's Bandcamp page and collect the genre
tags from their most recent releases. Run locally:

    pip install requests beautifulsoup4
    python bandcamp_tags.py              # reads artist names from data.js
    python bandcamp_tags.py --year 2026  # only the 2026 bill

Writes bandcamp_tags.json  {name: {url, matched, tags, releases}}
and bandcamp_review.csv    for eyeballing the matches.

Bandcamp has no public API; this walks the public search page and release
pages politely (1 req/s). Tags on Bandcamp are artist-assigned, which makes
them the most honest genre signal available — better than press bios.
classify_bios.py merges these tags into the group assignment, and
build_data.py picks up the page URL for each artist card.

Matching is fuzzy-but-conservative: it takes a search hit only when the
normalized band name equals or contains the query (so "Nothing" won't match
"Nothing But Thieves"). Everything below that confidence lands in the CSV
as "no confident match" for you to resolve by hand in overrides.json:
    {"Artist Name": {"bc": "https://artistname.bandcamp.com"}}
"""
import argparse, csv, json, re, sys, time, unicodedata
import requests
from bs4 import BeautifulSoup

UA = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"}
SEARCH = "https://bandcamp.com/search?q={}&item_type=b"
MAX_RELEASES = 3          # release pages to sample per artist
SLEEP = 1.0


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def get(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def search_artist(name):
    """Return (band_url, matched_name) or (None, None).
    The search URL already filters to artists (item_type=b), so every result
    heading is a band — no per-result type check needed."""
    soup = get(SEARCH.format(requests.utils.quote(name)))
    q = norm(name)
    headings = soup.select(".result-info .heading a") or soup.select(".searchresult .heading a")
    for a in headings:
        cand = a.get_text(strip=True)
        n = norm(cand)
        if n == q or (len(q) >= 6 and (q in n or n in q)):
            m = re.match(r"(https?://[^/]+)", a.get("href", "").strip())
            if m and "bandcamp.com" in m.group(1):
                return m.group(1), cand
    return None, None


def release_urls(band_url):
    """Recent release pages from the /music grid (or homepage fallback)."""
    urls = []
    for path in ("/music", ""):
        try:
            soup = get(band_url + path)
        except Exception:
            continue
        for a in soup.select("#music-grid a[href], .music-grid a[href]"):
            href = a["href"].split("?")[0]
            if href.startswith("/album/") or href.startswith("/track/"):
                u = band_url + href
                if u not in urls:
                    urls.append(u)
        if urls:
            break
    return urls[:MAX_RELEASES]


def release_tags(url):
    soup = get(url)
    return [a.get_text(strip=True).lower() for a in soup.select("a.tag")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, help="limit to artists on this year's bill")
    ap.add_argument("--data", default="data.js", help="path to data.js")
    args = ap.parse_args()

    raw = open(args.data, encoding="utf-8").read()
    d = json.loads(re.sub(r"^(//[^\n]*\n)+const MT_DATA = ", "", raw).rstrip(";\n"))
    artists = d["artists"]
    if args.year:
        artists = [a for a in artists if args.year in a["years"]]
    print(f"{len(artists)} artists to look up")

    out, rows = {}, []
    for i, a in enumerate(artists, 1):
        name = a["name"]
        try:
            # official lineup links 28 artists' Bandcamp pages — use those
            # directly; only fall back to search for everyone else
            official = (a.get("url") or "")
            if "bandcamp.com" in official:
                url, matched = official.split("?")[0], "official lineup link"
            else:
                url, matched = search_artist(name)
                time.sleep(SLEEP)
            if not url:
                rows.append([name, "", "", "no confident match"])
                continue
            if "/album/" in url or "/track/" in url:
                rels = [url]                      # album link -> that release
            else:
                root = re.match(r"https?://[^/]+", url).group(0)
                rels = release_urls(root)
                time.sleep(SLEEP)
            tags = []
            for r in rels:
                try:
                    for t in release_tags(r):
                        if t not in tags:
                            tags.append(t)
                except Exception:
                    pass
                time.sleep(SLEEP)
            # drop pure location tags bandcamp appends (e.g. "philadelphia")
            loc = {norm(a.get("city", "")), norm(a.get("country", ""))}
            tags = [t for t in tags if norm(t) not in loc][:12]
            out[name] = {"url": url, "matched": matched, "tags": tags,
                         "releases": len(rels)}
            rows.append([name, url, ", ".join(tags), f"matched: {matched}"])
        except Exception as e:
            print(f"  ! {name}: {type(e).__name__}: {e}")
            rows.append([name, "", "", f"error: {e}"])
        if i % 20 == 0:
            print(f"  {i}/{len(artists)}")

    with open("bandcamp_tags.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open("bandcamp_review.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["artist", "bandcamp url", "tags", "note"])
        w.writerows(rows)
    n_err = sum(1 for r in rows if r[3].startswith("error"))
    n_miss = sum(1 for r in rows if r[3] == "no confident match")
    print(f"\nmatched {len(out)}/{len(artists)} "
          f"(no confident match: {n_miss}, errors: {n_err}) — "
          "wrote bandcamp_tags.json + bandcamp_review.csv")
    if not out and artists:
        try:
            html = requests.get(SEARCH.format(requests.utils.quote(artists[0]["name"])),
                                headers=UA, timeout=30).text
            open("bandcamp_debug.html", "w", encoding="utf-8").write(html)
            print("Zero matches — wrote bandcamp_debug.html (the raw search page "
                  "for the first artist). If it's a captcha/consent page, Bandcamp "
                  "is rate-limiting your IP; wait and retry. If it's normal search "
                  "results, their markup changed — send me this file.")
        except Exception:
            pass
    print("Next: python classify_bios.py  (merges these tags with RA bios), "
          "then python build_data.py")


if __name__ == "__main__":
    main()
