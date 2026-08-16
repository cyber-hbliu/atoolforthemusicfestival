#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_genres.py — classify the bill from TAGGED genres instead of prose bios.

Sources (use any subset; more sources = better votes):
  * Spotify   — set env vars SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET
                (free: developer.spotify.com -> Create app -> copy both;
                 no user login needed, this uses client-credentials)
  * Discogs   — set env var DISCOGS_TOKEN
                (free: discogs.com -> Settings -> Developers -> Generate token)
  * Bandcamp  — reads bandcamp_tags.json if you've run bandcamp_tags.py

Run:
    export SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... DISCOGS_TOKEN=...
    python fetch_genres.py            # all artists in data.js
    python fetch_genres.py --limit 10 # smoke test

Writes genre_classification.json + genre_review.csv.
Then: python build_data.py  (it prefers this file over bio_classification.json).

Unlike classify_bios.py, there is NO keyword scanning here: every tag passes
through an explicit TAG_MAP with weights, and groups are decided by votes
across sources. Unknown tags are ignored (and listed in the review CSV so the
map can grow). Spotify note: their API has been shedding metadata since 2024;
if artist.genres comes back empty the source is skipped for that artist.
"""
import argparse, csv, json, os, re, sys, time, unicodedata
import requests

UA = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"}

# ---------------------------------------------------------------- tag map ---
# group letters: T techno · E electro · J dnb/jungle/breaks · H house ·
#                A ambient/experimental (DJs/producers) ·
#                M ambient/folk/vocal (musicians) · B alternative/indie bands
# weight 2 = decisive tag, 1 = supporting. Substring match on normalized tag.
TAG_MAP = [
    # electro before techno so "electro" doesn't fall through
    ("electroclash", "E", 2), ("ghettotech", "E", 2), ("miami bass", "E", 2),
    ("electro", "E", 2), ("drexciya", "E", 2), ("booty", "E", 1), ("italo body", "E", 1),
    ("dub techno", "T", 2), ("techno", "T", 2), ("acid", "T", 1), ("trance", "T", 1),
    ("industrial", "T", 1), ("gqom", "T", 2), ("gabber", "T", 2), ("hardcore techno", "T", 2),
    ("hard drum", "T", 2), ("rave", "T", 1), ("ebm", "T", 1), ("minimal", "T", 1),
    ("jungle", "J", 2), ("drum and bass", "J", 2), ("drum n bass", "J", 2), ("dnb", "J", 2),
    ("breakbeat", "J", 2), ("breaks", "J", 2), ("footwork", "J", 2), ("juke", "J", 2),
    ("uk garage", "J", 2), ("2-step", "J", 2), ("2 step", "J", 2), ("dubstep", "J", 2),
    ("grime", "J", 2), ("bassline", "J", 2), ("jersey club", "J", 2), ("baltimore club", "J", 2),
    ("hardcore continuum", "J", 2), ("140", "J", 1),
    ("deep house", "H", 2), ("acid house", "H", 2), ("tech house", "H", 2),
    ("house", "H", 2), ("disco", "H", 2), ("boogie", "H", 1), ("balearic", "H", 1),
    ("funk", "H", 1), ("soul", "H", 1), ("amapiano", "H", 2), ("garage house", "H", 2),
    ("idm", "A", 2), ("braindance", "A", 2), ("intelligent dance", "A", 2),
    ("musique concrete", "A", 2), ("field record", "A", 2), ("sound art", "A", 2),
    ("dub", "A", 1), ("downtempo", "A", 1), ("electronica", "A", 1), ("leftfield", "A", 1),
    ("fourth world", "A", 2), ("experimental electronic", "A", 2),
    ("experimental", "A", 1), ("ambient", "A", 1), ("drone", "A", 1), ("noise", "A", 1),
    ("freak folk", "M", 2), ("folk", "M", 2), ("singer-songwriter", "M", 2),
    ("songwriter", "M", 2), ("americana", "M", 2), ("country", "M", 2),
    ("harp", "M", 2), ("choral", "M", 2), ("chant", "M", 2), ("a cappella", "M", 2),
    ("vocal", "M", 1), ("free jazz", "M", 2), ("spiritual jazz", "M", 2), ("jazz", "M", 1),
    ("modern classical", "M", 2), ("classical", "M", 1), ("chamber", "M", 2),
    ("new age", "M", 1), ("guitar", "M", 1), ("saxophone", "M", 1), ("cello", "M", 1),
    ("percussion", "M", 1), ("piano", "M", 1), ("flute", "M", 1),
    ("post-punk", "B", 2), ("post punk", "B", 2), ("shoegaze", "B", 2),
    ("dream pop", "B", 2), ("indie rock", "B", 2), ("noise rock", "B", 2),
    ("art rock", "B", 2), ("art pop", "B", 2), ("punk", "B", 2), ("no wave", "B", 2),
    ("coldwave", "B", 2), ("darkwave", "B", 2), ("synth-punk", "B", 2), ("synthpop", "B", 1),
    ("indie", "B", 1), ("rock", "B", 1), ("band", "B", 1), ("pop", "B", 1),
]
GROUP_NAMES = {"T": "techno", "E": "electro", "J": "dnb/jungle/breaks", "H": "house",
               "A": "ambient/experimental", "M": "ambient/folk/vocal",
               "B": "alternative/indie bands"}
MIN_SCORE, MAX_GROUPS = 2, 2


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def map_tags(tags):
    """tags (raw strings) -> (groups_string, matched, unknown)."""
    scores, matched, unknown = {}, [], []
    for raw in tags:
        t = norm(raw)
        hit = None
        for key, g, w in TAG_MAP:
            if key in t:
                hit = (g, w)
                break
        if hit:
            scores[hit[0]] = scores.get(hit[0], 0) + hit[1]
            matched.append(raw)
        else:
            unknown.append(raw)
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    top = ranked[0][1] if ranked else 0
    groups = "".join(g for g, s in ranked[:MAX_GROUPS] if s >= MIN_SCORE and s * 2 >= top)
    return groups, matched, unknown


# ---------------------------------------------------------------- sources ---
def spotify_token():
    cid, sec = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not (cid and sec):
        return None
    r = requests.post("https://accounts.spotify.com/api/token",
                      data={"grant_type": "client_credentials"},
                      auth=(cid, sec), timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def spotify_genres(name, token):
    r = requests.get("https://api.spotify.com/v1/search",
                     params={"q": name, "type": "artist", "limit": 5},
                     headers={"authorization": "Bearer " + token}, timeout=30)
    r.raise_for_status()
    q = norm(name)
    for item in r.json().get("artists", {}).get("items", []):
        if norm(item["name"]) == q:
            return item.get("genres") or []
    return []


def discogs_genres(name, token):
    """Styles from the artist's top search results (styles are Discogs'
    fine-grained electronic taxonomy; genres are the coarse level)."""
    r = requests.get("https://api.discogs.com/database/search",
                     params={"q": name, "type": "release", "artist": name,
                             "per_page": 5, "token": token},
                     headers=UA, timeout=30)
    r.raise_for_status()
    tags = []
    q = norm(name)
    for res in r.json().get("results", []):
        title = norm(res.get("title", ""))
        if q not in title:          # release titles are "Artist - Title"
            continue
        for t in (res.get("style") or []) + (res.get("genre") or []):
            if t not in tags:
                tags.append(t)
    return tags


# MusicBrainz REQUIRES a user-agent with contact info and 503s anything
# anonymous. Put YOUR email or a URL here (https://musicbrainz.org/doc/
# MusicBrainz_API/Rate_Limiting):
MB_CONTACT = "your-email@example.com"        # <-- EDIT THIS
MB_UA = {"user-agent": f"makingtime-2026-tool/1.0 ( {MB_CONTACT} )"}
MB_FAILS = {"n": 0}                          # circuit breaker


def mb_get(url, params):
    """GET with one backoff retry on 503; trips the breaker after 5 fails."""
    for attempt in (1, 2):
        r = requests.get(url, params=params, headers=MB_UA, timeout=30)
        if r.status_code == 503 and attempt == 1:
            time.sleep(5)
            continue
        r.raise_for_status()
        MB_FAILS["n"] = 0
        return r
    r.raise_for_status()

def musicbrainz_genres(name):
    """Open crowd-tag database — the legally queryable cousin of RYM's
    taxonomy. Two calls: search for exact artist match, then lookup with
    genres+tags included."""
    r = mb_get("https://musicbrainz.org/ws/2/artist/",
               {"query": 'artist:"%s"' % name, "fmt": "json", "limit": 5})
    q = norm(name)
    mbid = None
    for a in r.json().get("artists", []):
        names = [a.get("name", "")] + [al.get("name", "") for al in a.get("aliases", [])]
        if any(norm(n) == q for n in names):   # primary name OR alias (per MB docs,
            mbid = a["id"]                      # search results include aliases)
            break
    if not mbid:
        return []
    time.sleep(1.1)
    r = mb_get("https://musicbrainz.org/ws/2/artist/" + mbid,
               {"inc": "genres+tags", "fmt": "json"})
    j = r.json()
    tags = [g["name"] for g in j.get("genres", []) if g.get("count", 0) > 0]
    tags += [t["name"] for t in j.get("tags", [])
             if t.get("count", 0) > 0 and t["name"] not in tags]
    return tags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data.js")
    ap.add_argument("--limit", type=int, help="only first N artists (smoke test)")
    args = ap.parse_args()

    raw = open(args.data, encoding="utf-8").read()
    d = json.loads(re.sub(r"^(//[^\n]*\n)+const MT_DATA = ", "", raw).rstrip(";\n"))
    artists = d["artists"][: args.limit] if args.limit else d["artists"]

    sp = None
    try:
        sp = spotify_token()
    except Exception as e:
        print("Spotify auth failed:", e)
    dg = os.environ.get("DISCOGS_TOKEN")
    bc = {}
    if os.path.exists("bandcamp_tags.json"):
        bc = json.load(open("bandcamp_tags.json", encoding="utf-8"))
    print("sources: musicbrainz",
          "spotify" if sp else "-", "discogs" if dg else "-",
          "bandcamp(%d)" % len(bc) if bc else "-")

    out, rows = {}, []
    for i, a in enumerate(artists, 1):
        name = a["name"]
        tags, srcs = [], []
        if name in bc and bc[name].get("tags"):
            tags += [t for t in bc[name]["tags"] if t not in tags]
            srcs.append("bandcamp")
        if sp:
            try:
                g = spotify_genres(name, sp)
                if g:
                    tags += [t for t in g if t not in tags]
                    srcs.append("spotify")
                time.sleep(.3)
            except Exception as e:
                print(f"  ! spotify {name}: {e}")
        if MB_FAILS["n"] < 5:
            try:
                g = musicbrainz_genres(name)
                if g:
                    tags += [t for t in g if t not in tags]
                    srcs.append("musicbrainz")
                time.sleep(1.1)          # musicbrainz: 1 req/s
            except Exception as e:
                MB_FAILS["n"] += 1
                print(f"  ! musicbrainz {name}: {e}")
                if MB_FAILS["n"] >= 5:
                    print("  (musicbrainz disabled for this run — 5 straight "
                          "failures; set MB_CONTACT at the top of this file "
                          "to your email/URL, MB 503s anonymous clients)")
        if dg:
            try:
                g = discogs_genres(name, dg)
                if g:
                    tags += [t for t in g if t not in tags]
                    srcs.append("discogs")
                time.sleep(1.1)          # discogs: 60 req/min
            except Exception as e:
                print(f"  ! discogs {name}: {e}")
        if not tags:
            rows.append([name, "", "", "", "no tagged genres found"])
            continue
        groups, matched, unknown = map_tags(tags)
        out[name] = {"groups": groups,
                     "genres": matched[:6],
                     "sources": srcs}
        if name in bc:
            out[name]["bc"] = bc[name]["url"]
        rows.append([name,
                     "+".join(GROUP_NAMES[g] for g in groups) or "(below threshold)",
                     ", ".join(matched[:8]),
                     ", ".join(srcs),
                     ("unmapped: " + ", ".join(unknown[:6])) if unknown else ""])
        if i % 20 == 0:
            print(f"  {i}/{len(artists)}")

    with open("genre_classification.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open("genre_review.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["artist", "assigned groups", "matched tags", "sources", "notes"])
        w.writerows(sorted(rows))
    print(f"\n{len(out)}/{len(artists)} classified from tagged genres")
    print("wrote genre_classification.json + genre_review.csv")
    print("Skim the 'unmapped' column — recurring unknown tags belong in TAG_MAP.")
    print("Then: python build_data.py (precedence: overrides > hand > this > bios)")


if __name__ == "__main__":
    main()
