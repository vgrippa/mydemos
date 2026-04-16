#!/usr/bin/env bash
#
# Readyset · Strapi FoodAdvisor demo orchestrator.
#
#   ./run_demo.sh up           spin everything up
#   ./run_demo.sh seed         load ~573 real restaurants + images into Strapi
#   ./run_demo.sh drop-caches  DROP CACHE for every cached query (forces upstream)
#   ./run_demo.sh warm         run k6 against the uncached (warm-up) endpoints
#   ./run_demo.sh cache        run SHOW PROXIED QUERIES + create caches
#   ./run_demo.sh report       re-run k6 after caching and print before/after
#   ./run_demo.sh status       show CACHES + PROXIED QUERIES
#   ./run_demo.sh down         stop + remove containers & volumes
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

CREATE_CACHES_SH="$HERE/../scripts/cache_user_queries_pg.sh"

cmd=${1:-help}

compose() { docker compose -f "$HERE/docker-compose.yml" "$@"; }

psql_rs() { PGPASSWORD=readyset psql -h 127.0.0.1 -p 5433 -U readyset -d foodadvisor -P pager=off "$@"; }
psql_up() { PGPASSWORD=readyset psql -h 127.0.0.1 -p 5432 -U readyset -d foodadvisor -P pager=off "$@"; }

ensure_k6() {
  command -v k6 >/dev/null || { echo "k6 not found.  brew install k6 (or see https://k6.io/docs/get-started/installation/)"; exit 1; }
}

case "$cmd" in
  up)
    compose up -d --build
    echo "Waiting for Strapi /_health ..."
    for i in $(seq 1 60); do
      if curl -fs http://127.0.0.1:1337/_health >/dev/null; then echo "  ok"; break; fi
      sleep 5
      [[ $i -eq 60 ]] && { echo "Strapi never became healthy"; compose logs strapi | tail -60; exit 1; }
    done
    echo
    echo "Strapi:     http://127.0.0.1:1337/admin  (admin@foodadvisor.demo / Demo12345!)"
    echo "Grafana:    http://127.0.0.1:4001       (anonymous admin)"
    echo "Prometheus: http://127.0.0.1:9091"
    echo "Readyset:   psql -h 127.0.0.1 -p 5433 -U readyset -d foodadvisor"
    ;;

  seed)
    "$HERE/seed/run_seed.sh" "${@:2}"
    ;;

  warm)
    ensure_k6
    DURATION=${DURATION:-60s} VUS=${VUS:-20} \
      k6 run --out json="$HERE/.cache/k6-warm.json" "$HERE/scripts/k6_load.js" | tee "$HERE/.cache/warm.txt"
    ;;

  cache)
    echo "==> Running $CREATE_CACHES_SH"
    RS_HOST=127.0.0.1 RS_PORT=5433 RS_USER=readyset RS_DB=foodadvisor PGPASSWORD=readyset \
      bash "$CREATE_CACHES_SH"
    echo
    n="$(psql_rs --csv -c 'SHOW CACHES' 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')"
    echo "==> Total caches registered: ${n}"
    ;;

  report)
    ensure_k6
    DURATION=${DURATION:-60s} VUS=${VUS:-20} \
      k6 run --out json="$HERE/.cache/k6-cached.json" "$HERE/scripts/k6_load.js" | tee "$HERE/.cache/cached.txt"
    echo
    echo "==================== BEFORE (warm) ===================="
    grep -E "restaurants|articles|categories|http_req|rps" "$HERE/.cache/warm.txt" 2>/dev/null || echo "  (run './run_demo.sh warm' first)"
    echo "==================== AFTER  (cached) =================="
    grep -E "restaurants|articles|categories|http_req|rps" "$HERE/.cache/cached.txt" 2>/dev/null || echo "  (no cached run)"
    ;;

  status)
    compose ps
    echo
    psql_rs -c "SHOW CACHES" || true
    echo
    psql_rs -c "SHOW PROXIED QUERIES" | head -30 || true
    ;;

  drop-caches)
    psql_rs --csv -c "SHOW CACHES" | tail -n +2 | awk -F, '{print $1}' | while IFS= read -r qid; do
      [[ -z "$qid" ]] && continue
      psql_rs -c "DROP CACHE $qid" >/dev/null 2>&1 && echo "  dropped $qid"
    done
    echo
    echo "Remaining caches:"
    psql_rs -c "SHOW CACHES"
    ;;

  down)
    compose down -v
    ;;

  *)
    sed -n '2,14p' "$0"
    ;;
esac
