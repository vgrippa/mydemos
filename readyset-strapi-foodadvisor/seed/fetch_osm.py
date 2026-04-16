"""Pull real restaurants from OpenStreetMap via the Overpass API.

Writes seed/data/restaurants.json. Re-runnable; respects an on-disk cache so
the demo doesn't hammer Overpass.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
CACHE = HERE / ".cache"
CACHE.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# (city, bounding box south,west,north,east)
CITIES = [
    ("New York",      "40.70,-74.02,40.82,-73.93"),
    ("San Francisco", "37.73,-122.52,37.81,-122.38"),
    ("London",        "51.49,-0.18,51.55,-0.08"),
    ("Paris",         "48.82,2.27,48.90,2.41"),
    ("Tokyo",         "35.65,139.68,35.72,139.78"),
]

def query(bbox: str) -> str:
    return (
        "[out:json][timeout:60];"
        f"(node[\"amenity\"=\"restaurant\"][\"name\"]({bbox});"
        f" way[\"amenity\"=\"restaurant\"][\"name\"]({bbox}););"
        "out center tags 150;"
    )

def fetch_city(city: str, bbox: str) -> list[dict]:
    cache_file = CACHE / f"osm_{city.replace(' ', '_').lower()}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    body = urllib.parse.urlencode({"data": query(bbox)}).encode()
    last_err: Exception | None = None
    for url in ENDPOINTS:
        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"User-Agent": "readyset-foodadvisor-demo/0.1"},
            )
            with urllib.request.urlopen(req, timeout=75) as resp:
                data = json.loads(resp.read().decode())
            cache_file.write_text(json.dumps(data))
            return data
        except Exception as e:
            last_err = e
            print(f"  overpass failed on {url}: {e}", file=sys.stderr)
            time.sleep(2)
    raise RuntimeError(f"all overpass endpoints failed: {last_err}")

def normalize(city: str, raw: dict) -> list[dict]:
    out = []
    for el in raw.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        cuisine = (tags.get("cuisine") or "").split(";")[0].replace("_", " ").title() or "Other"
        addr_parts = [
            tags.get("addr:housenumber"),
            tags.get("addr:street"),
            tags.get("addr:city") or city,
        ]
        address = " ".join(p for p in addr_parts if p).strip()
        out.append({
            "name": name,
            "city": city,
            "cuisine": cuisine,
            "address": address,
            "lat": lat,
            "lon": lon,
            "website": tags.get("website") or tags.get("contact:website") or "",
            "phone": tags.get("phone") or tags.get("contact:phone") or "",
            "osm_id": el.get("id"),
            "osm_type": el.get("type"),
        })
    return out

def main() -> None:
    out_path = DATA / "restaurants.json"
    if out_path.exists() and "--force" not in sys.argv:
        count = len(json.loads(out_path.read_text()))
        print(f"Using committed {out_path.name} ({count} restaurants). "
              f"Pass --force to refetch.", file=sys.stderr)
        return

    all_rows: list[dict] = []
    for city, bbox in CITIES:
        print(f"Fetching {city} ...", file=sys.stderr)
        try:
            raw = fetch_city(city, bbox)
            rows = normalize(city, raw)
            print(f"  {len(rows)} restaurants", file=sys.stderr)
            all_rows.extend(rows)
        except Exception as e:
            print(f"  ! skipping {city}: {e}", file=sys.stderr)

    # Deduplicate by (name, city)
    seen: set[tuple[str, str]] = set()
    dedup: list[dict] = []
    for r in all_rows:
        key = (r["name"], r["city"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)

    out_path.write_text(json.dumps(dedup, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(dedup)} unique restaurants -> {out_path}", file=sys.stderr)

if __name__ == "__main__":
    main()
