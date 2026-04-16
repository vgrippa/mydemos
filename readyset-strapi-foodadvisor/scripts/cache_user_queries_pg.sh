#!/usr/bin/env bash
set -euo pipefail
#
# cache_user_queries_pg.sh
#
# Cache every proxied query *except* ones that touch Postgres system tables
# (pg_catalog, information_schema, pg_toast, pg_stat_*).  Those are almost
# always introspection noise from the driver or admin UI — caching them is
# pointless and wastes state.
#
# Usage:
#   ./cache_user_queries_pg.sh [--dry-run] [--all]
#
#   --dry-run   Print DDL that *would* run, don't execute.
#   --all       Include queries Readyset flagged as unsupported.
#               (Default is supported-only.)
#
# Env (overrides for connection):
#   RS_HOST      (default 127.0.0.1)
#   RS_PORT      (default 5433)
#   RS_USER      (default readyset)
#   RS_DB        (default foodadvisor)
#   PGPASSWORD   (required)

RS_HOST="${RS_HOST:-127.0.0.1}"
RS_PORT="${RS_PORT:-5433}"
RS_USER="${RS_USER:-readyset}"
RS_DB="${RS_DB:-foodadvisor}"
export PGPASSWORD="${PGPASSWORD:-readyset}"

MODE="execute"
SUPPORTED_ONLY="yes"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|-n) MODE="dry-run" ;;
    --all)        SUPPORTED_ONLY="no" ;;
    --help|-h)    sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
  shift
done

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

rs_psql() {
  psql -h "$RS_HOST" -p "$RS_PORT" -U "$RS_USER" -d "$RS_DB" "$@"
}

log "Fetching proxied queries from ${RS_HOST}:${RS_PORT}/${RS_DB} (mode: ${MODE})"

csv_output="$(rs_psql --csv -c 'SHOW PROXIED QUERIES;' 2>&1)" || {
  echo "SHOW PROXIED QUERIES failed:" >&2
  echo "$csv_output" >&2
  exit 1
}

if [[ -z "$csv_output" ]]; then
  log "No proxied queries."
  exit 0
fi

# Filter via python for safe CSV / multi-line SQL parsing.
_tmp_py="$(mktemp /tmp/rs_filter_XXXXXX.py)"
trap 'rm -f "$_tmp_py"' EXIT

cat > "$_tmp_py" <<'PYEOF'
import csv, re, sys

supported_only = sys.argv[1] == "yes"

# Anything matching these is system noise — skip.
SYSTEM_PATTERNS = [
    r"\bpg_catalog\.",
    r"\binformation_schema\.",
    r"\bpg_toast\b",
    r"\bpg_stat_",
    r"\bpg_namespace\b",
    r"\bpg_class\b",
    r"\bpg_attribute\b",
    r"\bpg_type\b",
    r"\bpg_proc\b",
    r"\bpg_index\b",
    r"\bpg_description\b",
    r"\bpg_constraint\b",
    r"\bpg_settings\b",
    r"\bpg_roles\b",
    r"\bpg_database\b",
    r"\bcurrent_schema\s*\(",
    r"\bcurrent_database\s*\(",
    r"\bversion\s*\(\s*\)",
]
SYSTEM_RE = re.compile("|".join(SYSTEM_PATTERNS), re.IGNORECASE)

reader = csv.reader(sys.stdin)
try:
    next(reader)  # header
except StopIteration:
    sys.exit(0)

for row in reader:
    if len(row) < 3:
        continue
    query_id  = row[0].strip()
    sql_text  = row[1] or ""
    supported = row[2].strip().lower()

    if not query_id.startswith("q_"):
        continue
    if supported_only and supported != "yes":
        continue
    if SYSTEM_RE.search(sql_text):
        continue
    print(query_id)
PYEOF

query_ids="$(printf '%s\n' "$csv_output" | python3 "$_tmp_py" "$SUPPORTED_ONLY")"

if [[ -z "$query_ids" ]]; then
  log "No user queries to cache (after filtering out system tables)."
  exit 0
fi

count="$(printf '%s\n' "$query_ids" | wc -l | tr -d ' ')"
log "Caching ${count} user quer$([[ $count -eq 1 ]] && echo y || echo ies):"
printf '%s\n' "$query_ids" | sed 's/^/    /'

ok=0 failed=0 skipped=0
while IFS= read -r qid; do
  [[ -z "$qid" ]] && continue
  ddl="CREATE CACHE FROM ${qid};"

  if [[ "$MODE" == "dry-run" ]]; then
    echo "  [dry-run] $ddl"
    continue
  fi

  if err="$(rs_psql -c "$ddl" 2>&1)"; then
    log "  OK     ${qid}"
    (( ok++ )) || true
  else
    if echo "$err" | grep -qiE "already exists|cache already|duplicate"; then
      log "  SKIP   ${qid}  (already cached)"
      (( skipped++ )) || true
    else
      log "  FAIL   ${qid}"
      printf '    %s\n' "$err" >&2
      (( failed++ )) || true
    fi
  fi
done <<< "$query_ids"

if [[ "$MODE" == "dry-run" ]]; then
  log "Dry run complete — no changes made."
else
  log "Done. succeeded=${ok} skipped=${skipped} failed=${failed}"
fi
