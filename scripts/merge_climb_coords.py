#!/usr/bin/env python3
"""
Mergea coordenadas (summitLat/summitLon) y opcionalmente proRecord en
climbs.json desde scripts/data/climb_coords_tour2026.json.

Mismo algoritmo de matching que merge_climb_records.py.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIMBS_FILE = REPO_ROOT / "climbs.json"
COORDS_FILE = REPO_ROOT / "scripts" / "data" / "climb_coords_tour2026.json"


def normalize(s: str, keep_parens: bool = False) -> str:
    s = s.lower()
    if not keep_parens:
        s = re.sub(r"\([^)]*\)", "", s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def main() -> int:
    climbs_data = json.loads(CLIMBS_FILE.read_text())
    coords = json.loads(COORDS_FILE.read_text())
    climbs = climbs_data["climbs"]

    by_full: dict[str, list[dict]] = {}
    by_base: dict[str, list[dict]] = {}
    for c in climbs:
        by_full.setdefault(normalize(c["name"], keep_parens=True), []).append(c)
        by_base.setdefault(normalize(c["name"]), []).append(c)

    updated_coords = 0
    updated_records = 0
    not_found: list[str] = []
    ambiguous: list[str] = []

    for entry in coords:
        match_str = entry["match"]
        has_parens = "(" in match_str
        if has_parens:
            candidates = by_full.get(normalize(match_str, keep_parens=True), [])
        else:
            candidates = by_base.get(normalize(match_str), [])
            if len(candidates) > 1:
                no_parens = [c for c in candidates if "(" not in c["name"]]
                if len(no_parens) == 1:
                    candidates = no_parens

        if len(candidates) == 1:
            tgt = candidates[0]
            if "summitLat" in entry and "summitLon" in entry:
                tgt["summitLat"] = entry["summitLat"]
                tgt["summitLon"] = entry["summitLon"]
                updated_coords += 1
            if "pro_record" in entry and "proRecord" not in tgt:
                tgt["proRecord"] = entry["pro_record"]
                updated_records += 1
            print(f"  ✓ {match_str} → {tgt['name']}")
        elif not candidates:
            not_found.append(match_str)
        else:
            ambiguous.append(
                f"{match_str}: {[c['name'] for c in candidates]}"
            )

    CLIMBS_FILE.write_text(
        json.dumps(climbs_data, indent=2, ensure_ascii=False) + "\n"
    )

    print()
    print(f"Coords actualizadas: {updated_coords}")
    print(f"Records añadidos:    {updated_records}")
    if not_found:
        print("No match:")
        for n in not_found:
            print(f"  - {n}")
    if ambiguous:
        print("Ambiguos:")
        for a in ambiguous:
            print(f"  - {a}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
