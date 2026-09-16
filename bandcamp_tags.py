# -*- coding: utf-8 -*-
"""bandcamp_tags.py — collect artist-assigned Bandcamp tags for the lineup.

Run locally next to data.js:
    python3 bandcamp_tags.py
Writes bandcamp_tags.json  {name: {"url", "matched", "tags", "releases"}}
Then run python3 build_data.py so the tags land in data.js tooltips.

Strategy: artists whose data.js url is already a Bandcamp page are fetched
directly (an /album/ or /track/ link means exactly that release's tags,
the artist's own words for that record). Only artists without an official
Bandcamp link go through the flaky search.
"""
import json, os, re, time, urllib.parse, urllib.request

os.chdir(os.path.dirname(os.path.abspath(__file__)))

SLEEP = 1.2
MAX_RELEASES = 3
UA = {"User-Agent": "Mozilla/5.0 (festival tool; personal use)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")


def page_tags(url):
    html = get(url)
    return re.findall(r'class="tag"[^>]*>\s*([^<]+?)\s*<', html)


def release_urls(band_root):
    for path in ("/music", ""):
        try:
            html = get(band_root + path)
        except Exception:
            continue
        hrefs = re.findall(r'href="(/(?:album|track)/[^"#?]+)"', html)
        if hrefs:
            seen, out = set(), []
            for hr in hrefs:
                if hr not in seen:
                    seen.add(hr)
                    out.append(band_root + hr)
                if len(out) >= MAX_RELEASES:
                    break
            return out
    return []


def search_artist(name):
    q = urllib.parse.quote(name)
    try:
        html = get(f"https://bandcamp.com/search?q={q}&item_type=b")
    except Exception:
        return None, None
    m = re.search(r'class="heading">\s*<a href="(https://[^"/]+\.bandcamp\.com)[^"]*"[^>]*>\s*([^<]+?)\s*<', html)
    if not m:
        return None, None
    url, label = m.group(1), m.group(2)
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    if norm(label) != norm(name):
        return None, None            # no confident match — skip, never guess
    return url, label


def main():
    raw = open("data.js", encoding="utf-8").read()
    data = json.loads(re.sub(r"^(//[^\n]*\n)+const MT_DATA = ", "", raw).rstrip(";\n"))
    out, rows = {}, []
    for a in data["artists"]:
        name = a["name"]
        try:
            official = a.get("url") or ""
            if "bandcamp.com" in official:
                url, matched = official.split("?")[0].rstrip("/"), "official link"
            else:
                url, matched = search_artist(name)
                time.sleep(SLEEP)
            if not url:
                rows.append([name, "", "no confident match"])
                continue
            if "/album/" in url or "/track/" in url:
                rels = [url]
            else:
                root = re.match(r"https?://[^/]+", url).group(0)
                rels = release_urls(root)
                time.sleep(SLEEP)
            tags = []
            for r in rels:
                try:
                    for t in page_tags(r):
                        if t not in tags:
                            tags.append(t)
                except Exception:
                    pass
                time.sleep(SLEEP)
            if tags:
                out[name] = {"url": url, "matched": matched,
                             "tags": tags[:12], "releases": len(rels)}
                rows.append([name, url, ", ".join(tags[:6])])
            else:
                rows.append([name, url, "no tags found"])
        except Exception as e:
            rows.append([name, "", f"error: {e}"])
    with open("bandcamp_tags.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"{len(out)} artists with tags -> bandcamp_tags.json")
    for r in rows:
        print(" | ".join(r))


if __name__ == "__main__":
    main()
