#!/usr/bin/env python3
"""
Mergea scripts/data/climb_records.json en climbs.json añadiendo el campo
`proRecord` a cada puerto cuyo `name` coincida con el `match` del registro.

Estrategia de matching:
  - Normaliza nombres a minúsculas
  - Elimina paréntesis y su contenido
  - Compara con substring + igualdad exacta

Solo mergea cuando hay un único match, para evitar pisar entradas erróneas.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIMBS_FILE = REPO_ROOT / "climbs.json"
RECORDS_FILE = REPO_ROOT / "scripts" / "data" / "climb_records.json"


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
    if not CLIMBS_FILE.exists() or not RECORDS_FILE.exists():
        print(f"Faltan ficheros: {CLIMBS_FILE} o {RECORDS_FILE}", file=sys.stderr)
        return 1

    climbs_data = json.loads(CLIMBS_FILE.read_text())
    records = json.loads(RECORDS_FILE.read_text())
    climbs = climbs_data["climbs"]

    # Doble índice: por nombre completo (con paréntesis) y por nombre base
    by_full: dict[str, list[dict]] = {}
    by_base: dict[str, list[dict]] = {}
    for c in climbs:
        by_full.setdefault(normalize(c["name"], keep_parens=True), []).append(c)
        by_base.setdefault(normalize(c["name"]), []).append(c)

    merged = 0
    not_found: list[str] = []
    ambiguous: list[str] = []

    for rec in records:
        match_str = rec["match"]
        has_parens = "(" in match_str

        # Si el match incluye paréntesis, exige match exacto en by_full
        if has_parens:
            candidates = by_full.get(
                normalize(match_str, keep_parens=True), []
            )
        else:
            candidates = by_base.get(normalize(match_str), [])

        # Si hay ambigüedad sin paréntesis, prefiere el candidato cuyo
        # nombre tampoco tenga paréntesis (la versión "genérica").
        if len(candidates) > 1 and not has_parens:
            no_parens = [c for c in candidates if "(" not in c["name"]]
            if len(no_parens) == 1:
                candidates = no_parens

        if len(candidates) == 1:
            candidates[0]["proRecord"] = rec["pro_record"]
            merged += 1
            print(f"  ✓ {match_str} → {candidates[0]['name']}")
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
    print(f"Mergeados: {merged}/{len(records)}")
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
