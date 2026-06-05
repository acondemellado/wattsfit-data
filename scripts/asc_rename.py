#!/usr/bin/env python3
"""
Cambia el NOMBRE de la app en App Store Connect (metadatos, misma app/Bundle
ID) usando la API key. No sube app nueva: el nombre se aplica con versiones.

Uso:
  python3 asc_rename.py            # solo inspecciona (estado actual)
  python3 asc_rename.py Velotactic # cambia el nombre en las localizaciones editables
"""
import json
import sys
import time
import ssl
import urllib.request
from pathlib import Path

import jwt
import certifi

# Credenciales locales (NO en el repo público): el Key ID se deduce del nombre
# del .p8 y el Issuer ID del env, igual que release_testflight.sh.
_KEYDIR = Path.home() / ".appstoreconnect" / "private_keys"
P8 = next(_KEYDIR.glob("AuthKey_*.p8"))
KEY_ID = P8.stem.replace("AuthKey_", "")
_ENV = Path.home() / ".appstoreconnect" / "wattsfit_ids.env"
ISSUER = next((l.split("=", 1)[1].strip() for l in _ENV.read_text().splitlines()
               if l.startswith("ASC_ISSUER_ID=")), "") if _ENV.exists() else ""
BUNDLE = "com.wattsfit.wattsfit"
BASE = "https://api.appstoreconnect.apple.com/v1"
CTX = ssl.create_default_context(cafile=certifi.where())


def token():
    now = int(time.time())
    return jwt.encode(
        {"iss": ISSUER, "iat": now, "exp": now + 1000, "aud": "appstoreconnect-v1"},
        P8.read_text(),
        algorithm="ES256",
        headers={"kid": KEY_ID, "typ": "JWT"},
    )


def call(method, path, body=None):
    url = path if path.startswith("http") else f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:600]}")
        raise


def main():
    new_name = sys.argv[1] if len(sys.argv) > 1 else None
    apps = call("GET", f"/apps?filter[bundleId]={BUNDLE}")
    if not apps.get("data"):
        print("No se encontró la app con ese Bundle ID")
        return
    app = apps["data"][0]
    app_id = app["id"]
    print(f"App: {app['attributes'].get('name')} (id {app_id})")

    infos = call("GET", f"/apps/{app_id}/appInfos")
    for info in infos["data"]:
        state = info["attributes"].get("appStoreState") or info["attributes"].get("state")
        info_id = info["id"]
        editable = state not in ("READY_FOR_SALE", "DEVELOPER_REMOVED_FROM_SALE",
                                  "REMOVED_FROM_SALE", "REJECTED")
        locs = call("GET", f"/appInfos/{info_id}/appInfoLocalizations")
        for loc in locs["data"]:
            a = loc["attributes"]
            print(f"  appInfo {info_id} [{state}] · {a.get('locale')}: name='{a.get('name')}' "
                  f"subtitle='{a.get('subtitle')}'  (editable={editable})")
            if new_name and editable:
                patch = {"data": {"type": "appInfoLocalizations", "id": loc["id"],
                                  "attributes": {"name": new_name}}}
                call("PATCH", f"/appInfoLocalizations/{loc['id']}", patch)
                print(f"    -> renombrado a '{new_name}'")


if __name__ == "__main__":
    main()
