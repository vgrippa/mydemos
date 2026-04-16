#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

if [[ -f ../.env ]]; then
  set -a; source ../.env; set +a
fi

echo "==> [1/4] Fetching OSM restaurants (cached after first run)"
python3 fetch_osm.py || echo "   (continuing with whatever is cached)"

echo "==> [2/4] Fetching Wikipedia cuisine summaries"
python3 fetch_wikipedia.py || true

echo "==> [3/4] Fetching Unsplash images (if key set)"
python3 fetch_unsplash.py || true

echo "==> [4/4] Loading into Strapi via REST"
python3 load_strapi.py "$@"
