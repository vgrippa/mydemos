"""Bulk-clone restaurants via direct SQL against upstream Postgres.

Strapi's populate queries are fast because the dataset fits in Postgres'
shared_buffers.  This script clones the restaurant rows + join tables
N-1 times (per --factor N), giving Postgres a larger working set so the
same Strapi queries start doing real work — and Readyset's cache actually
wins.

Usage:
    pip install psycopg2-binary
    python3 scripts/multiply_restaurants.py --factor 20
                    # 1,444 originals -> 28,880 total

Safety:
- Writes ONLY to upstream Postgres (:5432), bypassing Readyset.
- One transaction; rolls back on any error.
- Preserves the original rows untouched; clones use id + k * max_id offsets
  and suffix names/slugs with "(c<k>)" / "-c<k>" to stay unique.
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import psycopg2
except ImportError:
    sys.exit("pip install psycopg2-binary")


TABLES = {
    "restaurants": None,
    "components_restaurant_information": None,
    "restaurants_components": None,
    "restaurants_category_links": None,
    "restaurants_place_links": None,
}


def get_max(cur, table: str) -> int:
    cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table};")
    return cur.fetchone()[0]


def count(cur, table: str) -> int:
    cur.execute(f"SELECT COUNT(*) FROM {table};")
    return cur.fetchone()[0]


def bump_seq(cur, seq: str, new_max: int) -> None:
    cur.execute(f"SELECT setval(%s, %s, true);", (seq, new_max))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--factor", type=int, default=20,
                    help="final count = factor × original (default 20)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--db", default="foodadvisor")
    ap.add_argument("--user", default="readyset")
    ap.add_argument("--password", default="readyset")
    ap.add_argument("--yes", action="store_true",
                    help="skip confirmation prompt")
    args = ap.parse_args()

    if args.factor < 2:
        sys.exit("--factor must be >= 2")
    copies = args.factor - 1  # preserve originals

    print(f"Connecting to {args.host}:{args.port}/{args.db} (direct Postgres, not Readyset)")
    conn = psycopg2.connect(
        host=args.host, port=args.port, dbname=args.db,
        user=args.user, password=args.password,
    )
    conn.autocommit = False
    cur = conn.cursor()

    # Capture original maxes before any insert
    maxes = {t: get_max(cur, t) for t in TABLES}
    counts_before = {t: count(cur, t) for t in TABLES}
    r0 = counts_before["restaurants"]

    print(f"\nCurrent state:")
    for t, m in maxes.items():
        print(f"  {t:42s}  rows={counts_before[t]:>6d}  max(id)={m:>6d}")

    final = r0 * args.factor
    print(f"\nWill create {copies} × {r0} = {copies * r0} new restaurants "
          f"(final total: {final}).")
    if not args.yes:
        resp = input("proceed? [y/N] ").strip().lower()
        if resp != "y":
            print("aborted.")
            return

    t0 = time.time()
    try:
        # ------- 1. components_restaurant_information (no FK deps) ---------
        cur.execute("""
            INSERT INTO components_restaurant_information (id, description)
            SELECT c.id + k.k * %s,
                   c.description || ' [clone ' || k.k || ']'
            FROM components_restaurant_information c
            CROSS JOIN generate_series(1, %s) AS k(k)
            WHERE c.id <= %s;
        """, (maxes["components_restaurant_information"], copies,
              maxes["components_restaurant_information"]))
        print(f"  + components_restaurant_information ({cur.rowcount})")

        # ------- 2. restaurants (UNIQUE slug) ------------------------------
        cur.execute("""
            INSERT INTO restaurants
                (id, name, slug, price, created_at, updated_at, published_at,
                 created_by_id, updated_by_id, locale)
            SELECT r.id + k.k * %s,
                   r.name || ' (c' || k.k || ')',
                   r.slug || '-c' || k.k,
                   r.price, r.created_at, r.updated_at, r.published_at,
                   r.created_by_id, r.updated_by_id, r.locale
            FROM restaurants r
            CROSS JOIN generate_series(1, %s) AS k(k)
            WHERE r.id <= %s;
        """, (maxes["restaurants"], copies, maxes["restaurants"]))
        print(f"  + restaurants ({cur.rowcount})")

        # ------- 3. restaurants_components (entity + component FKs) --------
        cur.execute("""
            INSERT INTO restaurants_components
                (id, entity_id, component_id, component_type, field, "order")
            SELECT rc.id + k.k * %s,
                   rc.entity_id + k.k * %s,
                   rc.component_id + k.k * %s,
                   rc.component_type, rc.field, rc."order"
            FROM restaurants_components rc
            CROSS JOIN generate_series(1, %s) AS k(k)
            WHERE rc.id <= %s;
        """, (maxes["restaurants_components"], maxes["restaurants"],
              maxes["components_restaurant_information"], copies,
              maxes["restaurants_components"]))
        print(f"  + restaurants_components ({cur.rowcount})")

        # ------- 4. restaurants_category_links -----------------------------
        cur.execute("""
            INSERT INTO restaurants_category_links
                (id, restaurant_id, category_id, restaurant_order)
            SELECT rcl.id + k.k * %s,
                   rcl.restaurant_id + k.k * %s,
                   rcl.category_id,
                   rcl.restaurant_order
            FROM restaurants_category_links rcl
            CROSS JOIN generate_series(1, %s) AS k(k)
            WHERE rcl.id <= %s;
        """, (maxes["restaurants_category_links"], maxes["restaurants"], copies,
              maxes["restaurants_category_links"]))
        print(f"  + restaurants_category_links ({cur.rowcount})")

        # ------- 5. restaurants_place_links --------------------------------
        cur.execute("""
            INSERT INTO restaurants_place_links
                (id, restaurant_id, place_id, restaurant_order)
            SELECT rpl.id + k.k * %s,
                   rpl.restaurant_id + k.k * %s,
                   rpl.place_id,
                   rpl.restaurant_order
            FROM restaurants_place_links rpl
            CROSS JOIN generate_series(1, %s) AS k(k)
            WHERE rpl.id <= %s;
        """, (maxes["restaurants_place_links"], maxes["restaurants"], copies,
              maxes["restaurants_place_links"]))
        print(f"  + restaurants_place_links ({cur.rowcount})")

        # ------- advance sequences so Strapi's next insert doesn't collide -
        for tbl, seq in [
            ("restaurants", "restaurants_id_seq"),
            ("components_restaurant_information", "components_restaurant_information_id_seq"),
            ("restaurants_components", "restaurants_components_id_seq"),
            ("restaurants_category_links", "restaurants_category_links_id_seq"),
            ("restaurants_place_links", "restaurants_place_links_id_seq"),
        ]:
            new_max = get_max(cur, tbl)
            bump_seq(cur, seq, new_max)

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    dt = time.time() - t0
    print(f"\nDone in {dt:.1f}s.")
    counts_after = {t: count(cur, t) for t in TABLES}
    for t in TABLES:
        delta = counts_after[t] - counts_before[t]
        print(f"  {t:42s}  {counts_before[t]:>6d} -> {counts_after[t]:>6d}  (+{delta})")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
