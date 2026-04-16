# Readyset demos

Reproducible, self-contained demos showing [Readyset](https://readyset.io)
caching real application workloads on Postgres and MySQL. Only host
requirement: **Docker**.

## Featured — Strapi on Readyset

[`readyset-strapi-foodadvisor/`](readyset-strapi-foodadvisor/) — stand up
Strapi's [FoodAdvisor](https://github.com/strapi/foodadvisor) example app
(Postgres + Strapi + Readyset + Prometheus + Grafana) with real data
(573 OpenStreetMap restaurants → scaled to 43,000 via SQL multiplier,
29 Wikipedia cuisine articles, CC-licensed food photos).

```bash
git clone https://github.com/vgrippa/mydemos
cd mydemos/readyset-strapi-foodadvisor
./demo.sh
```

≈8 min first run (Docker build + seed), ≈3 min thereafter. The script
brings the stack up, loads the data, scales it, runs a k6 baseline,
creates caches, runs k6 again, and prints a before/after table.

**Typical result** on Strapi's own queries at 43 k rows, 8 workers:

| Query                 | Postgres | Readyset | Speedup |
| --------------------- | -------: | -------: | ------: |
| `count · restaurants` |  3.5 ms  |  0.33 ms | **10×** |
| `list · order-by-name`|  8.6 ms  |  0.45 ms | **19×** |
| `group-by · place`    |  3.3 ms  |  0.33 ms | **10×** |

End-to-end through Strapi's HTTP API: **≈2×** (remaining latency is
Strapi's own Node.js; Readyset removes the DB share of the budget).

Full instructions, subcommands, architecture, and troubleshooting in
[readyset-strapi-foodadvisor/README.md](readyset-strapi-foodadvisor/README.md).
