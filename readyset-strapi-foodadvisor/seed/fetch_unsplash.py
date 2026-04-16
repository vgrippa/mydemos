"""Fetch food photos from Unsplash, falling back to the committed baseline bundle.

If UNSPLASH_ACCESS_KEY is unset, this script is a no-op that just prints a
status message — the baseline bundle in data/images/baseline/ is already
enough for the demo.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import time
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
BASELINE = DATA / "images" / "baseline"
EXTRA = DATA / "images" / "unsplash"
ATTR = DATA / "images" / "ATTRIBUTIONS.md"

QUERIES = [
    "pizza", "burger", "sushi", "pasta", "ramen", "taco", "salad",
    "steak", "dessert", "breakfast", "curry", "noodles", "bbq",
    "sandwich", "soup",
]
PER_QUERY = 10  # 15 * 10 = 150 images


def unsplash_search(key: str, q: str, per_page: int) -> list[dict]:
    params = urllib.parse.urlencode({"query": q, "per_page": per_page, "orientation": "landscape"})
    url = f"https://api.unsplash.com/search/photos?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Client-ID {key}",
            "Accept-Version": "v1",
            "User-Agent": "readyset-foodadvisor-demo/0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["results"]


def download(url: str, dest: pathlib.Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "readyset-foodadvisor-demo/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def main() -> None:
    key = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
    if not key:
        print("UNSPLASH_ACCESS_KEY not set — skipping Unsplash fetch.", file=sys.stderr)
        print(f"Using baseline images in {BASELINE} ({sum(1 for _ in BASELINE.glob('*.jpg'))} files).", file=sys.stderr)
        return

    EXTRA.mkdir(parents=True, exist_ok=True)
    attributions: list[str] = []

    for q in QUERIES:
        try:
            results = unsplash_search(key, q, PER_QUERY)
        except Exception as e:
            print(f"  unsplash search failed for {q}: {e}", file=sys.stderr)
            continue
        for i, r in enumerate(results):
            img_url = r["urls"]["regular"]
            fname = f"{q}-{i+1}.jpg"
            out = EXTRA / fname
            if out.exists():
                continue
            try:
                download(img_url, out)
                attributions.append(
                    f"- `{fname}` — Photo by [{r['user']['name']}]({r['user']['links']['html']}) on Unsplash"
                )
                time.sleep(0.3)  # be polite
            except Exception as e:
                print(f"  failed {fname}: {e}", file=sys.stderr)

    existing = ATTR.read_text() if ATTR.exists() else ""
    header = "# Image Attributions\n\n## Unsplash\n\n"
    ATTR.write_text(existing + header + "\n".join(attributions) + "\n")
    print(f"Downloaded {len(attributions)} Unsplash images -> {EXTRA}", file=sys.stderr)


if __name__ == "__main__":
    main()
