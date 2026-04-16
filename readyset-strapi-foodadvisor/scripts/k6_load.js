// k6 load test against Strapi FoodAdvisor.
//
// Run with:
//   k6 run --vus 20 --duration 60s scripts/k6_load.js
//
// Fixed pagination parameters (page & pageSize) keep the query shape constant so
// Readyset caches one query per endpoint instead of a new cache per page size.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';

const BASE = __ENV.STRAPI_URL || 'http://localhost:1337';

const latencyRestaurants = new Trend('restaurants_ms');
const latencyArticles = new Trend('articles_ms');
const latencyCategories = new Trend('categories_ms');

export const options = {
  scenarios: {
    mixed: {
      executor: 'constant-vus',
      vus: Number(__ENV.VUS || 20),
      duration: __ENV.DURATION || '60s',
    },
  },
  thresholds: {
    'http_req_failed': ['rate<0.05'],
  },
  // k6 only computes p(50) / p(99) if we opt in — default is p(90)/p(95).
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max', 'count'],
};

// These endpoints use lightweight Strapi queries that DO go heavier on the DB
// as the dataset grows (count, sort, pagination) but do NOT ask Strapi to
// hydrate deep populate graphs — that's Node CPU, not DB, and Strapi just
// times out on 43k rows regardless of caching.
//
// The SQL shapes behind these are exactly what `scripts/bench_sql.py` hits:
//   - GET /api/restaurants                -> SELECT ... FROM restaurants LIMIT N + count(*)
//   - GET /api/restaurants?sort=name:asc  -> SELECT ... ORDER BY name ASC LIMIT N
//   - GET /api/categories?populate[restaurants][count]=true
//                                          -> GROUP BY place/category aggregate
const ENDPOINTS = [
  {
    url: `${BASE}/api/restaurants?pagination[page]=1&pagination[pageSize]=50`,
    trend: latencyRestaurants,
  },
  {
    url: `${BASE}/api/restaurants?sort=name:asc&pagination[page]=1&pagination[pageSize]=50`,
    trend: latencyArticles,  // reuse trend bucket
  },
  {
    url: `${BASE}/api/categories?pagination[page]=1&pagination[pageSize]=20`,
    trend: latencyCategories,
  },
];

export default function () {
  for (const e of ENDPOINTS) {
    const r = http.get(e.url, { tags: { name: e.url.split('?')[0] } });
    e.trend.add(r.timings.duration);
    check(r, { 'status 200': (x) => x.status === 200 });
    sleep(0.1);
  }
}

export function handleSummary(data) {
  const fmt = (m) => {
    if (!m) return 'n/a';
    const v = m.values || {};
    // Trend exposes med as p50; p(99) requires summaryTrendStats above.
    return `p50=${(v['med'] || 0).toFixed(1)}ms  p95=${(v['p(95)'] || 0).toFixed(1)}ms  p99=${(v['p(99)'] || 0).toFixed(1)}ms`;
  };
  const lines = [
    '',
    '  === Readyset · Strapi FoodAdvisor — k6 summary ===',
    `  /api/restaurants          ${fmt(data.metrics.restaurants_ms)}`,
    `  /api/restaurants?sort=... ${fmt(data.metrics.articles_ms)}`,
    `  /api/categories           ${fmt(data.metrics.categories_ms)}`,
    `  http_req_duration         ${fmt(data.metrics.http_req_duration)}`,
    `  requests                  ${data.metrics.http_reqs.values.count}`,
    `  rps                       ${data.metrics.http_reqs.values.rate.toFixed(1)}`,
    '',
  ];
  return {
    stdout: lines.join('\n'),
    'k6-summary.json': JSON.stringify(data, null, 2),
  };
}
