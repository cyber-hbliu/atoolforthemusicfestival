# Making Time ∞ 2026 — the bill, sorted

The Making Time ∞ 2026 lineup (Fort Mifflin, Sep 18–20) as posted on the
official site, sorted into
six groups — techno · electro · dnb/jungle/breaks · house ·
ambient/experimental/idm · alternative/indie bands — with where each act is
based, their label, genre tags, and direct links to each artist's Bandcamp
(or their own channel where no Bandcamp exists) — links come from the
official lineup page itself.
Inspired by the [Berghain Klubnacht Database](https://berghain.ravers.workers.dev/).

## Files

| file | what it is |
|---|---|
| `index.html` | the whole tool — Explore and Timetable tabs (loads data.js) |
| `data.js` | the dataset on its own (`MT_DATA`) |
| `build_data.py` | source of truth: lineup + metadata + labels → regenerates `data.js` |
| `scrape_ra.py` | RA GraphQL scraper (run locally): lineup + artist bios + labels |
| `bandcamp_tags.py` | finds each artist's Bandcamp page, collects release tags |
| `classify_bios.py` | merges bios + tags → group/genre suggestions + review CSV |
| `fetch_genres.py` | tagged genres from Spotify + Discogs + Bandcamp → direct group mapping |
| `overrides.json` | (optional, yours) corrections — wins over everything |

## Deploy on GitHub Pages

Repo → add `index.html` and `data.js` (both in the root)
→ Settings → Pages → deploy from branch → done. After any data refresh, just
rerun `build_data.py` and commit the new `data.js` — the HTML never changes.
No build step, no backend.

## Refreshing / grounding the categories

All scrapers run on YOUR machine (RA and Bandcamp block cloud IPs; both respond
to a normal browser user-agent):

```bash
pip install requests beautifulsoup4
python scrape_ra.py            # RA lineup + biographies + labels → ra_dump.json
python bandcamp_tags.py        # Bandcamp pages + release tags → bandcamp_tags.json

# best signal — tagged genres via APIs (either or both, 2-min free signups):
export SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=...   # developer.spotify.com
export DISCOGS_TOKEN=...                                 # discogs.com → Settings → Developers
python fetch_genres.py         # → genre_classification.json + review CSV

python classify_bios.py        # prose-bio fallback → bio_classification.json
python build_data.py           # merge → data.js
```

Precedence: `overrides.json` > hand metadata > tagged genres > prose bios.

Precedence: `overrides.json` > hand metadata in `build_data.py` > bio/tag
classification. Review `bio_review.csv` and `bandcamp_review.csv`, pin fixes:

```json
{"Artist Name": {"groups": "TJ", "label": "Some Label", "city": "Berlin", "cc": "DE",
                 "bc": "https://artist.bandcamp.com"}}
```

Labels shown are editorial where filled; blank means unknown, not none.
If the official page changes, add/remove names in `LINEUP`, `META`, and
`LINKS` in `build_data.py` and rerun the chain. When set times are published
(usually the week of the festival), paste them into `SETTIMES` in
`build_data.py` — the timetable builder then switches from genre blocks to
chronological days with overlap warnings.

## Sources

- lineup + artist links — https://makingtimeisrad.com/lineup/
- RA listing — https://ra.co/events/2395557

Unaffiliated with Making Time, Resident Advisor, and Bandcamp.
