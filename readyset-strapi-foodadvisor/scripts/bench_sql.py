"""SQL-level benchmark: real Strapi populate queries, Readyset vs Postgres.

The k6 HTTP benchmark is dominated by Strapi's Node.js CPU (JSON assembly,
N+1 relation hydration) regardless of DB speed — so it hides Readyset's
impact.  This script runs three *actual cached* Strapi queries (pulled from
``SHOW CACHES``) directly over pgwire against both Readyset (:5433) and
upstream Postgres (:5432) with persistent per-worker connections.  That's
apples-to-apples at the DB layer.

Requires `psycopg2` or `psycopg` (v3).  `pip install psycopg2-binary`.

    python3 scripts/bench_sql.py                        # 30 s @ 8 threads
    python3 scripts/bench_sql.py -d 60 -c 16            # 60 s @ 16 threads
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import random
import statistics
import sys
import time


# Real cached Strapi queries (see SHOW CACHES).  Unlike the per-restaurant
# populate queries — which are point lookups that Postgres handles in <1ms
# regardless of dataset size — these touch the restaurants table as a whole
# (COUNT, ORDER BY, GROUP BY), so larger datasets make them measurably
# heavier on upstream while Readyset serves them from a materialised cache.
QUERIES: list[tuple[str, str, str]] = [
    (
        "count·restaurants",
        # q_5edeecff0911f3a0 — count all restaurants with locale + published filter
        'SELECT count("t0"."id") AS "count" '
        'FROM "public"."restaurants" AS "t0" '
        'WHERE (("t0"."locale" = %s) AND "t0"."published_at" IS NOT NULL) '
        'LIMIT %s',
        "count",
    ),
    (
        "list·order-by-name",
        # q_4d082689d091113e — paged list sorted alphabetically (full sort of all rows)
        'SELECT "t0".*, "t0"."id", "t0"."created_by_id", "t0"."updated_by_id" '
        'FROM "public"."restaurants" AS "t0" '
        'WHERE (("t0"."locale" = %s AND ((("t0"."locale" IN (%s)))))) '
        'ORDER BY "t0"."name" ASC LIMIT %s',
        "list",
    ),
    (
        "group-by·place",
        # q_74e433afb8394c5b — restaurants-per-place aggregate
        'SELECT "t1"."place_id", count(*) AS count '
        'FROM "public"."restaurants" AS "t0" '
        'LEFT JOIN "public"."restaurants_place_links" AS "t1" '
        '  ON "t0"."id" = "t1"."restaurant_id" '
        'WHERE ("t1"."place_id" IN (%s)) '
        'GROUP BY "t1"."place_id"',
        "agg",
    ),
]

# Parameter generators per query kind.
def params_for(kind: str, rng):
    if kind == "count":
        return ("en", 1)
    if kind == "list":
        return ("en", "en", 50)
    if kind == "agg":
        return (rng.randint(1, 5),)   # place IDs 1..5 (NYC / SF / London / Paris / Tokyo)
    raise ValueError(kind)


def get_driver():
    try:
        import psycopg2
        import psycopg2.extras  # noqa: F401
        return psycopg2, "psycopg2"
    except ImportError:
        pass
    try:
        import psycopg  # type: ignore
        class Shim:
            @staticmethod
            def connect(dsn): return psycopg.connect(dsn, autocommit=True)
        return Shim, "psycopg3"
    except ImportError:
        pass
    sys.exit("install psycopg2-binary:  pip install psycopg2-binary")


def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    k = (len(data) - 1) * p / 100
    f, c = int(k), min(int(k) + 1, len(data) - 1)
    return data[f] + (data[c] - data[f]) * (k - f)


def summarize(label: str, latencies: list[float]) -> dict:
    latencies = sorted(latencies)
    n = len(latencies)
    return {
        "label": label,
        "count": n,
        "avg_ms": statistics.fmean(latencies) if n else 0,
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "min_ms": latencies[0] if n else 0,
        "max_ms": latencies[-1] if n else 0,
    }


def run_bench(pg, dsn: str, duration: float, concurrency: int) -> list[dict]:
    results: dict[str, list[float]] = {name: [] for name, *_ in QUERIES}
    stop_at = time.time() + duration
    errors = 0

    def worker():
        nonlocal errors
        try:
            conn = pg.connect(dsn)
            conn.autocommit = True
            cur = conn.cursor()
        except Exception as e:
            print(f"  connect failed: {e}", file=sys.stderr)
            return
        rng = random.Random()
        i = 0
        while time.time() < stop_at:
            name, sql, kind = QUERIES[i % len(QUERIES)]
            i += 1
            try:
                t0 = time.perf_counter()
                cur.execute(sql, params_for(kind, rng))
                cur.fetchall()
                results[name].append((time.perf_counter() - t0) * 1000)
            except Exception as e:
                errors += 1
                if errors < 3:
                    print(f"  ! {name}: {e}", file=sys.stderr)
        try:
            cur.close(); conn.close()
        except Exception:
            pass

    with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
        for _ in range(concurrency):
            ex.submit(worker)
        ex.shutdown(wait=True)

    if errors:
        print(f"  !! {errors} errors total", file=sys.stderr)
    return [summarize(name, results[name]) for name, *_ in QUERIES]


def pretty(tag: str, rs: list[dict]) -> None:
    print(f"\n  {tag}")
    print(f"  {'query':22s} {'count':>7s} {'avg':>9s} {'p50':>9s} {'p95':>9s} {'p99':>9s}")
    for r in rs:
        print(
            f"  {r['label']:22s} {r['count']:>7d} "
            f"{r['avg_ms']:>8.2f}ms {r['p50_ms']:>8.2f}ms "
            f"{r['p95_ms']:>8.2f}ms {r['p99_ms']:>8.2f}ms"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-d", "--duration", type=float, default=30.0, help="seconds per target")
    ap.add_argument("-c", "--concurrency", type=int, default=8, help="worker threads per target")
    ap.add_argument("--warmup", type=float, default=5.0, help="warmup seconds against Readyset")
    ap.add_argument("--user", default="readyset")
    ap.add_argument("--password", default="readyset")
    ap.add_argument("--db", default="foodadvisor")
    ap.add_argument("--rs-host", default="127.0.0.1")
    ap.add_argument("--rs-port", type=int, default=5433)
    ap.add_argument("--up-host", default="127.0.0.1")
    ap.add_argument("--up-port", type=int, default=5432)
    args = ap.parse_args()

    pg, driver = get_driver()
    print(f"driver: {driver}   duration: {args.duration:.0f}s   concurrency: {args.concurrency}")

    rs_dsn = f"postgresql://{args.user}:{args.password}@{args.rs_host}:{args.rs_port}/{args.db}"
    up_dsn = f"postgresql://{args.user}:{args.password}@{args.up_host}:{args.up_port}/{args.db}"

    if args.warmup > 0:
        print(f"\nwarming Readyset cache ({args.warmup:.0f}s) ...")
        run_bench(pg, rs_dsn, duration=args.warmup, concurrency=args.concurrency)

    print("\nbenchmarking upstream Postgres ...")
    up = run_bench(pg, up_dsn, duration=args.duration, concurrency=args.concurrency)

    print("\nbenchmarking Readyset ...")
    rs = run_bench(pg, rs_dsn, duration=args.duration, concurrency=args.concurrency)

    pretty(f"UPSTREAM  Postgres  {args.up_host}:{args.up_port}", up)
    pretty(f"READYSET  cache     {args.rs_host}:{args.rs_port}", rs)

    print("\n  speedup (upstream / readyset):")
    print(f"  {'query':22s} {'p50':>10s} {'p95':>10s} {'p99':>10s}")
    for u, r in zip(up, rs):
        def mul(a, b): return f"{(a/b):>9.1f}x" if b > 0 else "    n/a "
        print(f"  {u['label']:22s} {mul(u['p50_ms'], r['p50_ms'])} "
              f"{mul(u['p95_ms'], r['p95_ms'])} {mul(u['p99_ms'], r['p99_ms'])}")

    total_up = sum(r["count"] for r in up)
    total_rs = sum(r["count"] for r in rs)
    print(f"\n  total rows/s:  upstream {total_up/args.duration:>7.0f}   readyset {total_rs/args.duration:>7.0f}   "
          f"({total_rs/total_up:.1f}x)")


if __name__ == "__main__":
    main()
