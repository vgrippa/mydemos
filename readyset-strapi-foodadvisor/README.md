<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/strapi-logo-dark.svg">
    <img alt="Strapi" src="assets/strapi-logo.svg" height="48">
  </picture>
  &nbsp;&nbsp;×&nbsp;&nbsp;
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/readyset-logo-dark.png">
    <img alt="Readyset" src="assets/readyset-logo.png" height="48">
  </picture>
</p>

# Strapi on Readyset — one-command demo

Stand up a real Strapi (FoodAdvisor) on Postgres, cache its queries with
Readyset, and measure the speedup — in under 10 minutes.

## Benchmark

Typical numbers on a laptop — real Strapi-emitted SQL at 43,000 restaurants,
8 concurrent workers, 30 s per side:

| Query                           | Postgres p50 | Readyset p50 |  Speedup |
| ------------------------------- | -----------: | -----------: | -------: |
| `count · restaurants`           |      3.5 ms  |     0.33 ms  | **10.6×** |
| `list · order-by-name`          |      8.6 ms  |     0.45 ms  | **19.0×** |
| `group-by · place`              |      3.3 ms  |     0.33 ms  | **10.0×** |
| **Total throughput** (req / s)  |        1,535 |       20,166 | **13.1×** |

Reproduce anytime with `./demo.sh bench-sql`.

## Quickstart

```bash
git clone https://github.com/vgrippa/mydemos
cd mydemos/readyset-strapi-foodadvisor
./demo.sh
```

That's the entire setup. **Only host requirement: Docker.** Everything else
(`psql`, `k6`, `python3`, `psycopg2`) runs inside sidecar containers.

## What you'll see

After `./demo.sh` finishes (≈8 min first run, ≈3 min thereafter) you get a
**before/after table** printed to the terminal and three live UIs:

| What | Where | Login |
|---|---|---|
| Strapi admin | http://localhost:1337/admin | `admin@foodadvisor.demo` / `Demo12345!` |
| Grafana     | http://localhost:4001       | none (anonymous-admin) |
| Prometheus  | http://localhost:9091       | none |

## What the demo actually does

`./demo.sh` (with no args) runs these phases in order:

| Phase          | What it does                                                                |
| -------------- | --------------------------------------------------------------------------- |
| `up`           | Builds the Strapi image (Docker), starts Postgres + Readyset + Strapi + Prometheus + Grafana |
| `seed`         | Loads 573 restaurants + 29 articles + 24 images into Strapi via REST (data is committed — no network fetch required) |
| `multiply`     | Clones rows in Postgres directly to reach **~43,000 restaurants** — enough that Postgres has to work |
| `drop-caches`  | `DROP CACHE` for every Readyset cache (clean baseline)                      |
| `warm`         | 30 s k6 load against Strapi — **nothing cached yet**                        |
| `cache`        | Walks `SHOW PROXIED QUERIES`, `CREATE CACHE` for every user query (skips `pg_catalog` / `information_schema`) |
| `report`       | 30 s k6 again, now against cached queries. Prints before/after table        |
| `bench-sql`    | Pure-SQL bench via psycopg2 — the dramatic 10–19× numbers                   |

## Running phases individually

Each phase is also a subcommand:

```bash
./demo.sh up            # stand up the stack (no seed)
./demo.sh seed          # load seed data
./demo.sh multiply      # scale to 43k rows
./demo.sh drop-caches   # clean slate
./demo.sh warm          # k6 baseline
./demo.sh cache         # CREATE CACHE for user queries
./demo.sh report        # cached k6 + before/after
./demo.sh bench-sql     # SQL-level bench (runs anytime)
./demo.sh bench         # drop-caches → warm → cache → report → bench-sql
./demo.sh down          # stop + remove containers & volumes
./demo.sh help
```

## Poke around inside

While the stack is up, open another terminal:

```bash
# psql directly to Readyset (proxy — what Strapi talks to)
docker compose --profile tools run --rm tools psql -h cache -p 5433 -U readyset -d foodadvisor

# psql directly to upstream Postgres
docker compose --profile tools run --rm tools psql -h postgres -p 5432 -U readyset -d foodadvisor
```

Interesting Readyset commands once you're in `psql` on `:5433`:

```sql
SHOW CACHES;                         -- materialised caches + their hit counts
SHOW PROXIED QUERIES;                -- queries seen but not cached
SHOW READYSET STATUS;                -- adapter + replication state
CREATE CACHE FROM <query>;           -- add a cache by hand
DROP CACHE <cache_id>;
```

## What's in the box

```
readyset-strapi-foodadvisor/
├── demo.sh                ← run this
├── docker-compose.yml
├── strapi/                ← Dockerfile building strapi/foodadvisor + pg driver
├── seed/
│   ├── fetch_osm.py       ← pulls restaurants from OpenStreetMap (skips if JSON is committed)
│   ├── fetch_wikipedia.py ← cuisine articles
│   ├── load_strapi.py     ← bootstraps admin, uploads images, POSTs entities
│   └── data/              ← committed real data (restaurants.json, articles.json, 24 CC-licensed food photos)
├── config/
│   ├── prometheus.yml
│   └── grafana/           ← auto-provisioned dashboard
├── scripts/
│   ├── bench_sql.py         ← pg-wire benchmark (Readyset vs Postgres)
│   ├── k6_load.js           ← HTTP load test against Strapi
│   └── multiply_restaurants.py
├── tools/
│   └── Dockerfile         ← sidecar image: python + postgresql-client + psycopg2
└── ATTRIBUTIONS.md
```

## Ports used on the host

| Port | Service                |
|-----:|------------------------|
| 1337 | Strapi (admin + API)   |
| 5432 | Postgres (upstream)    |
| 5433 | **Readyset (what Strapi talks to)** |
| 6035 | Readyset controller + `/metrics` |
| 4001 | Grafana                |
| 9091 | Prometheus             |
| 9187 | postgres_exporter      |

## Troubleshooting

**Docker daemon not running** → start Docker Desktop (or `sudo systemctl start docker` on Linux).

**Port already in use** → stop whatever's on the conflicting port. The demo uses
host ports 1337 / 5432 / 5433 / 6035 / 4001 / 9091 / 9187 — shut anything else
using those, or edit `docker-compose.yml`.

**Strapi OOM during load tests** → `./demo.sh` sets `NODE_OPTIONS=--max-old-space-size=4096` in compose; if you're on a 4 GB-RAM machine, lower this and skip `./demo.sh multiply`.

**Want fresh OSM/Wikipedia data** → delete `seed/data/restaurants.json` and
`seed/data/articles.json`, then `./demo.sh seed` — fetchers will re-download.

**Grafana shows no data** → wait ~30 s after `cache`. Prometheus scrape interval
is 5 s and Readyset's metric-emission warms up.

## Why Strapi on Readyset?

Strapi has [no native query cache][no-cache] and its own docs warn that
[`populate=*` / deep populate is slow][populate-slow]. Readyset is a drop-in
wire-compatible Postgres proxy that caches read queries transparently. No
code changes in Strapi — just point `DATABASE_HOST` at Readyset.

[no-cache]: https://docs.strapi.io/cloud/getting-started/caching
[populate-slow]: https://docs.strapi.io/dev-docs/api/rest/guides/understanding-populate#performance-issues-and-the-maximum-populate-depth

## Credits

- Restaurant data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) under ODbL
- Articles from [Wikipedia](https://en.wikipedia.org) under CC BY-SA
- Food photos from [Wikimedia Commons](https://commons.wikimedia.org) — per-image attribution in [`seed/data/images/baseline/manifest.json`](seed/data/images/baseline/manifest.json) and [ATTRIBUTIONS.md](ATTRIBUTIONS.md)
- Built on Strapi's [FoodAdvisor](https://github.com/strapi/foodadvisor) example app
