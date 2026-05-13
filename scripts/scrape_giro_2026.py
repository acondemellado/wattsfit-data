#!/usr/bin/env python3
"""
Extrae los GPX oficiales de cada etapa del Giro d'Italia 2026 desde
giroditalia.it. El slug del GPX en el CDN está randomizado por etapa, por
lo que hay que parsearlo de cada página individual.

Imprime un diccionario {stage: url} listo para inyectar en fetch_routes.py.
"""
from __future__ import annotations

import re
import ssl
import sys
import urllib.request
import urllib.error
from pathlib import Path

import certifi

SSL_CTX = ssl.create_default_context(cafile=certifi.where())
UA = "Mozilla/5.0 (Wattsfit catalog builder)"
TIMEOUT = 20

INDEX_URL = "https://www.giroditalia.it/en/the-route/"
STAGE_REGEX = re.compile(
    r'href="(https://www\.giroditalia\.it/en/tappe/stage-(\d+)-of-the-giro-ditalia-2026-[^"]+/)"'
)
GPX_REGEX = re.compile(
    r'href="(https://[a-z0-9.-]*giroditalia\.it[^"]*\.gpx[^"]*)"',
    re.IGNORECASE,
)


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def main() -> int:
    idx_html = fetch_text(INDEX_URL)
    pages: dict[int, str] = {}
    for m in STAGE_REGEX.finditer(idx_html):
        stage = int(m.group(2))
        pages.setdefault(stage, m.group(1))
    print(f"Encontradas {len(pages)} páginas de etapa", file=sys.stderr)

    found: dict[int, str] = {}
    missing: list[int] = []
    for stage in sorted(pages):
        url = pages[stage]
        try:
            html = fetch_text(url)
        except urllib.error.HTTPError as e:
            print(f"  stage {stage}: HTTP {e.code}", file=sys.stderr)
            missing.append(stage)
            continue
        gpx_match = GPX_REGEX.search(html)
        if gpx_match is None:
            print(f"  stage {stage}: GPX no localizado", file=sys.stderr)
            missing.append(stage)
            continue
        found[stage] = gpx_match.group(1)
        print(f"  stage {stage}: {gpx_match.group(1)}", file=sys.stderr)

    print("# Pegar en fetch_routes.py")
    print("GIRO_2026_GPX = {")
    for stage in sorted(found):
        print(f"    {stage}: {found[stage]!r},")
    print("}")
    if missing:
        print(f"# missing stages: {missing}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
