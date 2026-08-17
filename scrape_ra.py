#!/usr/bin/env python3
# -*- coding: utf-8 -*-
 
import argparse, json, re, sys, time
import requests

URL = "https://ra.co/graphql"
HEADERS = {
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"),
    "content-type": "application/json",
    "referer": "https://ra.co/",
}

# Fort Mifflin editions only
EVENT_IDS = {
    2026: "2395557",   # re-run when the full lineup is posted
}

EVENT_QUERY = """
query GET_EVENT($id: ID!) {
  event(id: $id) {
    id
    title
    date
    contentUrl
    artists { id name contentUrl }
  }
}
"""

# Artist fields are resolved at runtime: the script introspects the Artist
# type once, keeps the fields that exist, and expands objects properly
# (biography is an object on RA's schema: biography { blurb content }).
WANTED_SCALARS = ["id", "name", "contentUrl", "followerCount", "firstName",
                  "lastName", "countryName"]
WANTED_OBJECTS = {                      # field -> subselection to request
    "country":   "country { name urlCode }",
    "biography": "biography { blurb content }",
    "labels":    "labels { name }",
}

INTROSPECT_QUERY = """
query TypeFields($name: String!) {
  __type(name: $name) { name fields { name type { name kind ofType { name } } } }
}
"""

PROMOTER_ID = "37096"   # Making Time — ra.co/promoters/37096

# Filter shapes RA has used for listings; the script tries each until one works.
PROMOTER_FILTER_CANDIDATES = [
    {"promoters": {"eq": int(PROMOTER_ID)}},
    {"promoter":  {"eq": int(PROMOTER_ID)}},
    {"promoterId": {"eq": int(PROMOTER_ID)}},
]

LISTINGS_QUERY = """
query LISTINGS($filters: FilterInputDtoInput, $page: Int, $pageSize: Int) {
  eventListings(filters: $filters, page: $page, pageSize: $pageSize,
                sort: { listingDate: { priority: 1, order: ASCENDING } }) {
    data {
      id
      listingDate
      event {
        id title date contentUrl
        venue { id name }
        artists { id name contentUrl }
      }
    }
    totalResults
  }
}
"""


def fetch_promoter_history(date_from="2001-01-01", date_to="2026-12-31"):
    """Paginate every Making Time event RA has listed. Coverage caveat: RA's
    US archive is sparse before the late 2000s — the early Transit/PURE era
    (The Strokes 2001, Bloc Party 2004...) mostly is not on RA and needs the
    flyer archive instead."""
    for filt in PROMOTER_FILTER_CANDIDATES:
        filters = dict(filt)
        filters["listingDate"] = {"gte": date_from, "lte": date_to}
        try:
            first = gql(LISTINGS_QUERY, {"filters": filters, "page": 1, "pageSize": 50})
        except RuntimeError as e:
            print(f"  filter {list(filt)[0]!r} rejected ({str(e)[:80]}…), trying next")
            continue
        total = first["eventListings"]["totalResults"]
        print(f"promoter filter {list(filt)[0]!r} accepted — {total} listings")
        events, page = [], 1
        batch = first["eventListings"]["data"]
        while batch:
            for row in batch:
                ev = row["event"]
                events.append({
                    "id": ev["id"], "title": ev["title"], "date": ev["date"],
                    "url": "https://ra.co" + (ev.get("contentUrl") or ""),
                    "venue": (ev.get("venue") or {}).get("name"),
                    "lineup": [{"id": a["id"], "name": a["name"]}
                               for a in (ev.get("artists") or [])],
                })
            page += 1
            time.sleep(1)
            batch = gql(LISTINGS_QUERY,
                        {"filters": filters, "page": page, "pageSize": 50}
                        )["eventListings"]["data"]
        return events
    raise SystemExit("No promoter filter shape accepted — run --introspect and "
                     "check FilterInputDtoInput fields, then update "
                     "PROMOTER_FILTER_CANDIDATES.")


def gql(query, variables):
    r = requests.post(URL, headers=HEADERS,
                      json={"query": query, "variables": variables}, timeout=30)
    r.raise_for_status()
    out = r.json()
    if "errors" in out:
        raise RuntimeError(json.dumps(out["errors"])[:500])
    return out["data"]


def introspect():
    for t in ("Artist", "Event"):
        data = gql(INTROSPECT_QUERY, {"name": t})
        fields = [f["name"] for f in (data["__type"] or {}).get("fields", [])]
        print(f"\n{t} fields ({len(fields)}):\n  " + ", ".join(sorted(fields)))


def artist_field_list():
    """Introspect the Artist type once and build the query field list."""
    data = gql(INTROSPECT_QUERY, {"name": "Artist"})
    have = {f["name"] for f in (data["__type"] or {}).get("fields", [])}
    fields = [f for f in WANTED_SCALARS if f in have]
    fields += [sel for name, sel in WANTED_OBJECTS.items() if name in have]
    skipped = [f for f in WANTED_SCALARS + list(WANTED_OBJECTS) if f not in have]
    if skipped:
        print("  (not in RA's Artist schema, skipping:", ", ".join(skipped) + ")")
    return fields


FIELD_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"')

def fetch_artist(artist_id, fields):
    q = "query A($id: ID!) { artist(id: $id) { %s } }" % " ".join(fields)
    return gql(q, {"id": artist_id})["artist"]


def fetch_artist_resilient(artist_id, fields):
    """Drop only fields the error names in quotes (never substring matches)."""
    fields = list(fields)
    while fields:
        try:
            return fetch_artist(artist_id, fields), fields
        except RuntimeError as e:
            named = set(FIELD_RE.findall(str(e)))
            drop = [f for f in fields if f.split(" ")[0].split("{")[0] in named]
            if not drop:
                raise
            for f in drop:
                fields.remove(f)
            print(f"    (schema rejected {drop}, retrying without)")
    return {"id": artist_id}, fields


def flatten_artist(rec):
    """Normalize nested fields so downstream files see flat keys."""
    if not rec:
        return rec
    bio = rec.get("biography")
    if isinstance(bio, dict):
        rec["biography"] = " ".join(x for x in [bio.get("blurb"), bio.get("content")] if x)
    labels = rec.get("labels")
    if isinstance(labels, list):
        rec["labels"] = [l.get("name") for l in labels if isinstance(l, dict)]
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--introspect", action="store_true",
                    help="print Artist/Event schema fields and exit")
    ap.add_argument("--skip-artists", action="store_true",
                    help="only fetch event lineups, skip per-artist detail calls")
    ap.add_argument("--promoter", action="store_true",
                    help="fetch ALL Making Time events on RA (full party history, "
                         "not just Fort Mifflin) -> promoter_history.json")
    args = ap.parse_args()

    if args.introspect:
        introspect()
        return

    if args.promoter:
        events = fetch_promoter_history()
        with open("promoter_history.json", "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=1)
        venues = {}
        for e in events:
            venues[e["venue"] or "?"] = venues.get(e["venue"] or "?", 0) + 1
        print(f"wrote promoter_history.json — {len(events)} events across "
              f"{len(venues)} venues")
        for v, n in sorted(venues.items(), key=lambda x: -x[1])[:15]:
            print(f"  {n:>4}  {v}")
        return

    dump = {"fetched": time.strftime("%Y-%m-%d"), "events": {}, "artists": {}}

    for year, eid in EVENT_IDS.items():
        print(f"[{year}] event {eid} ...")
        ev = gql(EVENT_QUERY, {"id": eid})["event"]
        dump["events"][year] = {
            "id": ev["id"], "title": ev["title"], "date": ev["date"],
            "url": "https://ra.co" + (ev.get("contentUrl") or ""),
            "lineup": [{"id": a["id"], "name": a["name"],
                        "url": "https://ra.co" + (a.get("contentUrl") or "")}
                       for a in (ev.get("artists") or [])],
        }
        print(f"  {len(dump['events'][year]['lineup'])} tagged artists")
        time.sleep(1)

    if not args.skip_artists:
        ids = {a["id"]: a["name"] for e in dump["events"].values() for a in e["lineup"]}
        print(f"\nfetching {len(ids)} artist profiles ...")
        fields = artist_field_list()
        for i, (aid, name) in enumerate(ids.items(), 1):
            try:
                rec, fields = fetch_artist_resilient(aid, fields)
                dump["artists"][aid] = flatten_artist(rec)
            except Exception as e:
                print(f"  ! {name}: {e}")
            if i % 25 == 0:
                print(f"  {i}/{len(ids)}")
            time.sleep(1)

    with open("ra_dump.json", "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=1)
    print("\nwrote ra_dump.json")
    print("Heads up: RA only tags artists that have RA profiles on the event "
          "page — locals without profiles (Phil Yeah, Hudson River etc.) live "
          "only in the flyer text, which is why build_data.py keeps its own "
          "hand-compiled lineup lists as the source of truth.")


if __name__ == "__main__":
    main()
