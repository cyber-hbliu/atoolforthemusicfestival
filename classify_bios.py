#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
classify_bios.py — scan RA artist biographies for genre keywords and sort
each artist into the five groups:

    T techno · E electro · J dnb/jungle/breakbeats · H house ·
    A ambient/experimental (DJs) · M ambient/folk/vocal (musicians) ·
    B alternative/indie bands

Pipeline:
    1. python scrape_ra.py                  # locally → ra_dump.json (with biography field)
    2. python classify_bios.py              # → bio_classification.json + bio_review.csv
    3. python build_data.py                 # merges everything → data.js

The classifier is deliberately conservative: it only assigns a group when the
bio gives real signal (>= MIN_SCORE), and it writes bio_review.csv so you can
eyeball every call and pin corrections in overrides.json. Precedence in
build_data.py is: overrides.json > GROUP_OVERRIDES > hand metadata > this file.
Keyword lexicons are ordered longest-match-first so "acid house" scores house,
not techno; "dub techno" scores techno, not ambient dub.
"""
import csv, json, re, sys, unicodedata
from collections import Counter

MIN_SCORE = 3          # minimum keyword score before a group is assigned
MAX_GROUPS = 2         # an artist gets at most two groups
MAX_GENRES = 5         # genre tags kept per artist

# (regex, group letter, canonical tag, weight) — checked in order, first match
# per position wins, so multi-word phrases must come before their substrings.
LEX = [
    # ---- disambiguating multi-word phrases first ----
    (r"\bacid house\b",            "H", "acid house", 2),
    (r"\bdub techno\b",            "T", "dub techno", 2),
    (r"\bambient (house|dub)\b",   "A", "ambient dub", 2),
    (r"\bdeep house\b",            "H", "deep house", 2),
    (r"\bgarage house\b",          "H", "garage house", 2),
    (r"\buk(?:\s|-)?garage\b|\bukg\b|\b2[- ]step\b", "H", "UK garage", 2),
    (r"\bghetto ?house\b",         "H", "ghetto house", 2),
    (r"\bjersey club\b|\bbaltimore club\b|\bbmore\b", "J", "club", 2),
    (r"\bpost[- ]punk\b",          "B", "post-punk", 2),
    (r"\bnew wave\b|\bminimal wave\b|\bsynth[- ]?punk\b|\bcold ?wave\b|\bdarkwave\b|\bebm\b", "B", "wave/EBM", 1),
    (r"\bnoise rock\b|\bart rock\b|\bindie rock\b", "B", "rock", 2),
    (r"\bfree jazz\b|\bspiritual jazz\b", "B", "jazz", 2),
    (r"\bfourth world\b",          "A", "fourth world", 2),
    (r"\bnew age\b",               "A", "new age", 2),
    (r"\bmusique concr", "A", "musique concrète", 2),
    (r"\bfield record",            "A", "field recording", 2),
    (r"\bsound art\b|\bsound design\b|\bsound bath\b", "A", "sound art", 2),
    (r"\bmodular synth|\bbuchla\b|\beurorack\b", "A", "modular", 2),

    # ---- dnb / jungle / breakbeats ----
    (r"\bjungle\b|\bjunglist\b",   "J", "jungle", 2),
    (r"\bdrum\s*(?:&|and|'?n'?)\s*bass\b|\bdnb\b|\bd&b\b", "J", "drum & bass", 2),
    (r"\bbreakbeat|\bbreaks\b",    "J", "breaks", 2),
    (r"\bfootwork\b|\bjuke\b|\b160\b", "J", "footwork", 2),
    (r"\bhardcore continuum\b|\brave nostalgia\b|\bpiano rave\b", "J", "rave", 1),
    (r"\bdubstep\b|\b140\b|\bbassline\b|\bgrime\b", "J", "dubstep/140", 1),

    # ---- techno / electro ----
    (r"\btechno\b",                "T", "techno", 2),
    (r"\belectro\b(?!nic)",        "E", "electro", 2),
    (r"\belectroclash\b",          "E", "electroclash", 2),
    (r"\bmiami bass\b|\bbooty\b",  "E", "electro bass", 2),
    (r"\bacid\b",                  "T", "acid", 1),
    (r"\bindustrial\b",            "T", "industrial", 1),
    (r"\btrance\b",                "T", "trance", 1),
    (r"\bghettotech\b",            "E", "ghettotech", 2),
    (r"\bgqom\b|\bgabber\b|\bhard drum\b|\bhard dance\b", "T", "hard/club", 2),
    (r"\bminimal\b(?! wave)",      "T", "minimal", 1),
    (r"\bwarehouse\b|\brave\b",    "T", "rave", 1),

    # ---- house ----
    (r"\bhouse\b",                 "H", "house", 2),
    (r"\bdisco\b",                 "H", "disco", 2),
    (r"\bboogie\b|\bfunk\b|\bsoul\b|\bedits?\b", "H", "funk/soul", 1),
    (r"\bbalearic\b",              "H", "balearic", 1),

    # ---- ambient / experimental / idm ----
    (r"\bambient\b",               "A", "ambient", 2),
    (r"\bexperimental\b",          "A", "experimental", 2),
    (r"\bidm\b|\bintelligent dance\b|\bbraindance\b", "A", "IDM", 2),
    (r"\bdrone\b",                 "A", "drone", 2),
    (r"\belectronica\b",           "A", "electronica", 1),
    (r"\bdowntempo\b|\bchill[- ]?out\b|\bleftfield\b", "A", "leftfield", 1),
    (r"\bdub\b(?!step)",           "A", "dub", 1),
    (r"\bcollage\b|\btape music\b|\bimprovis", "A", "improvised", 1),

    # ---- alternative / indie bands ----
    (r"\bband\b|\btrio\b|\bquartet\b|\bensemble\b", "B", "band", 2),
    (r"\bshoegaze\b|\bdream pop\b", "B", "shoegaze/dream pop", 2),
    (r"\bpunk\b",                  "B", "punk", 1),
    (r"\bsinger[- ]songwriter\b|\bsongwriter\b|\bfolk\b|\bamericana\b", "M", "songwriter/folk", 2),
    (r"\bguitarist\b|\bguitar\b|\bcellist\b|\bviolinist\b|\bharpist\b|\bharp\b|\bsaxophon|\bpercussionist\b|\bbassoon\b", "M", "instrumentalist", 2),
    (r"\bdrummer\b", "B", "drummer", 1),
    (r"\bvocalist\b|\bsinger\b(?! ?songwriter)", "M", "vocal", 2),
    (r"\bpop\b", "B", "pop", 1),
    (r"\bjazz\b",                  "M", "jazz", 1),
    (r"\blive (?:show|act|set|performance)s?\b", "B", "live", 1),
]
LEX = [(re.compile(p, re.I), g, tag, w) for p, g, tag, w in LEX]

GROUP_NAMES = {"T": "techno", "E": "electro", "J": "dnb/jungle/breaks", "H": "house", "M": "ambient/folk/vocal",
               "A": "ambient/experimental/idm", "B": "alternative/indie bands"}


def norm(text):
    return unicodedata.normalize("NFKC", text or "")


def classify(bio):
    """Return (groups_string, genre_tags, hit_counter) from a biography text."""
    text = norm(bio)
    scores, tags = Counter(), Counter()
    consumed = set()
    for rx, g, tag, w in LEX:
        for m in rx.finditer(text):
            span = (m.start(), m.end())
            if any(s < span[1] and span[0] < e for s, e in consumed):
                continue                     # earlier (longer) pattern owns this span
            consumed.add(span)
            scores[g] += w
            tags[tag] += w
    ranked = scores.most_common()
    top = ranked[0][1] if ranked else 0
    groups = [g for g, s in ranked[:MAX_GROUPS] if s >= MIN_SCORE and s * 2 >= top]
    genres = [t for t, _ in tags.most_common(MAX_GENRES)]
    return "".join(groups), genres, scores


def main():
    dump = {}
    try:
        with open("ra_dump.json", encoding="utf-8") as f:
            dump = json.load(f)
    except FileNotFoundError:
        print("ra_dump.json not found — classifying from Bandcamp tags only.")
    bc = {}
    try:
        with open("bandcamp_tags.json", encoding="utf-8") as f:
            bc = json.load(f)
    except FileNotFoundError:
        print("bandcamp_tags.json not found — classifying from RA bios only.")
    if not dump and not bc:
        sys.exit("Nothing to classify. Run scrape_ra.py and/or bandcamp_tags.py first.")

    # per-artist text = RA biography + Bandcamp tags (tags repeated to weight
    # them double — artist-assigned tags are the strongest signal available)
    texts, names = {}, {}
    if dump:
        names = {a["id"]: a["name"] for e in dump["events"].values() for a in e["lineup"]}
        for aid, rec in dump.get("artists", {}).items():
            name = rec.get("name") or names.get(aid, aid)
            texts[name] = rec.get("biography") or ""
    for name, rec in bc.items():
        tagtext = (" . ".join(rec.get("tags", [])) + " . ") * 2
        texts[name] = (texts.get(name, "") + " . " + tagtext).strip()

    out, rows = {}, []
    for name, text in texts.items():
        if not text.strip(" ."):
            rows.append([name, "", "", "no biography / tags"])
            continue
        groups, genres, scores = classify(text)
        out[name] = {"groups": groups, "genres": genres, "scores": dict(scores)}
        if name in bc:
            out[name]["bc"] = bc[name]["url"]
        rows.append([name,
                     "+".join(GROUP_NAMES[g] for g in groups) or "(below threshold)",
                     ", ".join(genres),
                     " ".join(f"{g}:{s}" for g, s in scores.most_common())])

    with open("bio_classification.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open("bio_review.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["artist", "assigned groups", "genre tags", "keyword scores"])
        w.writerows(sorted(rows))

    print(f"{len(texts)} artists with source text · {len(out)} classified")
    print("wrote bio_classification.json + bio_review.csv")
    print("Review the CSV; pin any corrections in overrides.json, then run "
          "build_data.py. Bio-derived groups only fill artists that have no "
          "hand-assigned group, so nothing you've curated gets clobbered.")


if __name__ == "__main__":
    main()
