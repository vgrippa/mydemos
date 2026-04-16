#!/usr/bin/env bash
#
# One-shot end-to-end runner for the Strapi + Readyset demo.
#
#   ./demo.sh            full demo: up -> seed -> multiply -> bench
#   ./demo.sh bench      just re-run the before/after bench on an already-up stack
#   ./demo.sh down       teardown + drop volumes
#   ./demo.sh help       usage
#
# Only host requirement: Docker (+ Docker Compose v2, bundled in modern Docker).
#

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
mkdir -p "$HERE/.cache"

# Silences the "What's next: docker ai ..." Docker Desktop hint that floods
# output on every failed `compose exec` during the wait-for-Strapi loop.
export DOCKER_CLI_HINTS=false

GREEN=$'\033[32m'
YELLOW=$'\033[33m'
RED=$'\033[31m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
RESET=$'\033[0m'

section() {
  echo
  echo "${BOLD}${GREEN}==>${RESET}${BOLD} $*${RESET}"
}

hr() {
  printf '%.0s─' $(seq 1 70); echo
}

fail() {
  echo "${RED}✗${RESET} $*" >&2
  exit 1
}

require_docker() {
  command -v docker >/dev/null || fail "Docker not found. Install Docker Desktop or Docker Engine."
  docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 not found. Update Docker."
  docker info >/dev/null 2>&1 || fail "Docker daemon is not running. Start Docker."
}

compose()      { docker compose -f "$HERE/docker-compose.yml" "$@"; }
compose_tools(){ docker compose -f "$HERE/docker-compose.yml" --profile tools "$@"; }

run_tools() {
  compose_tools run --rm tools "$@"
}

run_k6() {
  local out="$1"; shift
  compose_tools run --rm k6 run "$@" --summary-export=/out/$(basename "$out" .txt).json | tee "$HERE/.cache/$(basename "$out")"
}

run_psql_rs() {
  # Runs psql against Readyset via the tools sidecar.
  run_tools psql -h cache -p 5433 -U readyset -d foodadvisor "$@"
}

wait_for_strapi() {
  section "Waiting for Strapi to be healthy (up to 3 minutes)"
  for i in $(seq 1 36); do
    # Probe via curl (which the strapi image installs) from inside the
    # container.  -s silences, -f => exit non-zero on 4xx/5xx, -o /dev/null
    # discards the body.  All stderr suppressed so Docker Desktop doesn't
    # print its noisy "What's next" hint on each failed iteration.
    if compose exec -T strapi curl -sf -o /dev/null http://127.0.0.1:1337/_health 2>/dev/null; then
      echo "${GREEN}✓${RESET} Strapi is up"
      return 0
    fi
    printf '.'
    sleep 5
  done
  echo
  compose logs --tail 30 strapi
  fail "Strapi never came up"
}

cmd_up() {
  section "Bringing the stack up (Postgres · Readyset · Strapi · Prometheus · Grafana)"
  compose up -d --build
  wait_for_strapi
}

cmd_seed() {
  section "Seeding Strapi with ~573 restaurants / 29 articles / 24 images"
  # load_strapi uses stdlib only; fetchers skip when committed JSON is present.
  run_tools bash -c 'cd seed && python3 fetch_osm.py && python3 fetch_wikipedia.py && python3 load_strapi.py --force'
}

cmd_multiply() {
  section "Scaling to 43,320 restaurants via direct SQL (${YELLOW}factor=30${RESET})"
  run_tools python3 scripts/multiply_restaurants.py --host postgres --port 5432 --factor 30 --yes
}

cmd_drop_caches() {
  section "Dropping existing Readyset caches (for a clean baseline)"
  run_psql_rs --csv -c 'SHOW CACHES' | tail -n +2 | awk -F, '{print $1}' | while IFS= read -r qid; do
    [[ -z "$qid" ]] && continue
    run_psql_rs -c "DROP CACHE $qid" >/dev/null 2>&1 && echo "  dropped $qid"
  done
}

cmd_warm() {
  section "k6 baseline (${BOLD}no caches${RESET}) — 30s @ 10 VUs"
  mkdir -p "$HERE/.cache"
  compose_tools run --rm -e VUS=10 -e DURATION=30s k6 run /scripts/k6_load.js \
    --summary-export=/out/k6-warm.json 2>&1 | tee "$HERE/.cache/warm.txt"
}

cmd_cache() {
  section "Caching all user queries Readyset has observed (skips pg_catalog / information_schema)"
  run_tools bash ./scripts/cache_user_queries_pg.sh
  n="$(run_psql_rs --csv -c 'SHOW CACHES' | tail -n +2 | wc -l | tr -d ' ')"
  echo "${GREEN}✓${RESET} ${n} caches now registered"
}

cmd_report() {
  section "k6 cached run — 30s @ 10 VUs"
  compose_tools run --rm -e VUS=10 -e DURATION=30s k6 run /scripts/k6_load.js \
    --summary-export=/out/k6-cached.json 2>&1 | tee "$HERE/.cache/cached.txt"

  echo
  hr
  echo "${BOLD}BEFORE (uncached)${RESET}"
  grep -E "^\s+/api|^\s+http_req|^\s+requests|^\s+rps" "$HERE/.cache/warm.txt" 2>/dev/null | head -10 || echo "  (no baseline on file)"
  echo
  echo "${BOLD}AFTER (cached)${RESET}"
  grep -E "^\s+/api|^\s+http_req|^\s+requests|^\s+rps" "$HERE/.cache/cached.txt" 2>/dev/null | head -10
  hr
}

cmd_bench_sql() {
  section "SQL-level bench — the real Readyset vs upstream comparison"
  run_tools python3 scripts/bench_sql.py \
    --rs-host cache --rs-port 5433 \
    --up-host postgres --up-port 5432 \
    --duration 20 --concurrency 8
}

cmd_help() {
  sed -n '2,13p' "$0"
}

cmd_full() {
  require_docker
  local t0; t0=$(date +%s)
  cmd_up
  cmd_seed
  cmd_multiply
  cmd_drop_caches
  cmd_warm
  cmd_cache
  cmd_report
  cmd_bench_sql

  local dt=$(( $(date +%s) - t0 ))
  echo
  hr
  echo "${BOLD}${GREEN}Demo complete in $((dt / 60))m $((dt % 60))s${RESET}"
  echo
  echo "  Strapi admin : ${BOLD}http://localhost:1337/admin${RESET}   (admin@foodadvisor.demo / Demo12345!)"
  echo "  Grafana      : ${BOLD}http://localhost:4001${RESET}         (FoodAdvisor dashboard)"
  echo "  Prometheus   : ${BOLD}http://localhost:9091${RESET}"
  echo
  echo "  ${DIM}Tear down:${RESET}  ./demo.sh down"
  hr
}

# ------------------------------------------------------------------ dispatch
cmd=${1:-full}
case "$cmd" in
  full)         cmd_full ;;
  up)           require_docker; cmd_up ;;
  seed)         require_docker; cmd_seed ;;
  multiply)     require_docker; cmd_multiply ;;
  drop-caches)  require_docker; cmd_drop_caches ;;
  warm)         require_docker; cmd_warm ;;
  cache)        require_docker; cmd_cache ;;
  report)       require_docker; cmd_report ;;
  bench)        require_docker; cmd_drop_caches; cmd_warm; cmd_cache; cmd_report; cmd_bench_sql ;;
  bench-sql)    require_docker; cmd_bench_sql ;;
  down)         require_docker; compose down -v ;;
  help|-h|--help) cmd_help ;;
  *) cmd_help; exit 1 ;;
esac
