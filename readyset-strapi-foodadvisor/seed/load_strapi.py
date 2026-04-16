"""Load real restaurants / articles / images into Strapi via REST.

Runs idempotently: bootstraps admin on first run, creates an API token,
enables public permissions, uploads the image pool, then POSTs restaurants,
categories, places, and articles with correct relations so ``populate=deep,2``
actually has nested data to return.
"""

from __future__ import annotations

import itertools
import json
import mimetypes
import os
import pathlib
import random
import re
import sys
import time
from typing import Any, Iterable
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
IMAGE_DIRS = [DATA / "images" / "baseline", DATA / "images" / "unsplash"]

STRAPI = os.environ.get("STRAPI_URL", "http://localhost:1337").rstrip("/")
ADMIN_EMAIL = os.environ.get("STRAPI_ADMIN_EMAIL", "admin@foodadvisor.demo")
ADMIN_PASSWORD = os.environ.get("STRAPI_ADMIN_PASSWORD", "Demo12345!")
ADMIN_FIRSTNAME = os.environ.get("STRAPI_ADMIN_FIRSTNAME", "Demo")
ADMIN_LASTNAME = os.environ.get("STRAPI_ADMIN_LASTNAME", "Admin")

SEED_MARKER = HERE / ".cache" / ".seeded"


def request(method: str, path: str, *, token: str | None = None,
            json_body: Any = None, raw_body: bytes | None = None,
            headers: dict | None = None) -> tuple[int, dict | list | None]:
    url = f"{STRAPI}{path}"
    hdrs = headers.copy() if headers else {}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            return r.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body.decode(errors="replace")}


def wait_for_strapi(max_wait: int = 180) -> None:
    start = time.time()
    while time.time() - start < max_wait:
        try:
            code, _ = request("GET", "/_health")
            if code in (200, 204):
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f"Strapi not ready at {STRAPI} after {max_wait}s")


def bootstrap_admin() -> str:
    """Register the admin if the instance is fresh, then log in and return JWT."""
    code, resp = request("POST", "/admin/register-admin", json_body={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "firstname": ADMIN_FIRSTNAME,
        "lastname": ADMIN_LASTNAME,
    })
    if code in (200, 201):
        print("  admin registered", file=sys.stderr)
    elif code == 400:
        print("  admin already exists, logging in", file=sys.stderr)
    else:
        raise RuntimeError(f"register-admin failed: {code} {resp}")

    code, resp = request("POST", "/admin/login", json_body={
        "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
    })
    if code != 200:
        raise RuntimeError(f"admin login failed: {code} {resp}")
    return resp["data"]["token"]


def create_api_token(admin_jwt: str) -> str:
    code, resp = request("POST", "/admin/api-tokens", token=admin_jwt, json_body={
        "name": f"foodadvisor-seed-{int(time.time())}",
        "description": "programmatic seed loader",
        "type": "full-access",
        "lifespan": None,
    })
    if code not in (200, 201):
        raise RuntimeError(f"api-token create failed: {code} {resp}")
    return resp["data"]["accessKey"]


def enable_public_read(admin_jwt: str) -> None:
    """Grant find + findOne on restaurant/article/category/place to public role."""
    code, roles = request("GET", "/users-permissions/roles", token=admin_jwt)
    if code != 200:
        print(f"  ! could not list roles: {code}", file=sys.stderr)
        return
    public = next((r for r in roles["roles"] if r["type"] == "public"), None)
    if not public:
        print("  ! public role not found", file=sys.stderr)
        return

    code, detail = request("GET", f"/users-permissions/roles/{public['id']}", token=admin_jwt)
    if code != 200:
        print(f"  ! could not fetch public role: {code}", file=sys.stderr)
        return

    perms = detail["role"]["permissions"]
    targets = ["api::restaurant", "api::category", "api::place", "api::article"]
    for api in targets:
        ctl = perms.get(api, {}).get("controllers", {})
        # Strapi nests by controller name (singular)
        for ctrl_name, actions in ctl.items():
            for action_name, action in actions.items():
                if action_name in ("find", "findOne"):
                    action["enabled"] = True

    code, resp = request("PUT", f"/users-permissions/roles/{public['id']}",
                         token=admin_jwt, json_body=detail["role"])
    if code != 200:
        print(f"  ! could not update public role: {code} {resp}", file=sys.stderr)
    else:
        print("  public read permissions enabled", file=sys.stderr)


def upload_image(token: str, path: pathlib.Path) -> int:
    """Multipart-upload a file to /api/upload.  Returns the media ID."""
    boundary = "----foodadvisor" + str(random.randint(10**9, 10**10))
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += (f"Content-Disposition: form-data; name=\"files\"; "
             f"filename=\"{path.name}\"\r\n").encode()
    body += f"Content-Type: {mime}\r\n\r\n".encode()
    body += path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()

    code, resp = request(
        "POST", "/api/upload", token=token, raw_body=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    if code not in (200, 201):
        raise RuntimeError(f"upload {path.name} failed: {code} {resp}")
    return resp[0]["id"]


def slugify(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9\-_.~\s]", "", s).strip()
    s = re.sub(r"[\s-]+", "-", s)
    return s or "item"


PRICE_BY_CUISINE_HINT = {
    "Sushi": "p3", "Steakhouse": "p4", "Fine Dining": "p4",
    "Fast Food": "p1", "Burger": "p1", "Pizza": "p2", "Bakery": "p1",
    "Cafe": "p1", "Bar": "p2",
}


def pick_price(cuisine: str, idx: int) -> str:
    if cuisine in PRICE_BY_CUISINE_HINT:
        return PRICE_BY_CUISINE_HINT[cuisine]
    return ["p1", "p2", "p3", "p4"][idx % 4]


def find_or_create(token: str, endpoint: str, filter_field: str,
                   value: str, payload: dict) -> int:
    qs = urllib.parse.urlencode({f"filters[{filter_field}][$eq]": value})
    code, resp = request("GET", f"{endpoint}?{qs}", token=token)
    if code == 200 and resp.get("data"):
        return resp["data"][0]["id"]
    code, resp = request("POST", endpoint, token=token, json_body={"data": payload})
    if code not in (200, 201):
        raise RuntimeError(f"create {endpoint} failed: {code} {resp}")
    return resp["data"]["id"]


def collect_images() -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for d in IMAGE_DIRS:
        if d.exists():
            paths.extend(sorted(p for p in d.glob("*.jpg") if p.stat().st_size > 1024))
    return paths


def main() -> None:
    if SEED_MARKER.exists() and "--force" not in sys.argv:
        print(f"already seeded (rm {SEED_MARKER} or pass --force to re-run)",
              file=sys.stderr)
        return

    print(f"Waiting for Strapi at {STRAPI} ...", file=sys.stderr)
    wait_for_strapi()

    print("Bootstrapping admin ...", file=sys.stderr)
    admin_jwt = bootstrap_admin()
    api_token = create_api_token(admin_jwt)
    enable_public_read(admin_jwt)

    # Upload image pool
    images = collect_images()
    print(f"Uploading {len(images)} images ...", file=sys.stderr)
    media_ids: list[int] = []
    for p in images:
        try:
            media_ids.append(upload_image(api_token, p))
        except Exception as e:
            print(f"  ! upload {p.name}: {e}", file=sys.stderr)
    print(f"  {len(media_ids)} uploaded", file=sys.stderr)
    if not media_ids:
        raise RuntimeError("no images uploaded — cannot create restaurants (images required)")

    image_cycle = itertools.cycle(media_ids)

    # Load raw data
    restaurants_raw = json.loads((DATA / "restaurants.json").read_text())
    articles_raw = json.loads((DATA / "articles.json").read_text())

    # Categories: one per unique cuisine
    cuisines = sorted({r["cuisine"] for r in restaurants_raw})
    print(f"Creating {len(cuisines)} categories ...", file=sys.stderr)
    cuisine_to_id: dict[str, int] = {}
    for c in cuisines:
        cuisine_to_id[c] = find_or_create(
            api_token, "/api/categories", "name", c,
            {"name": c, "slug": slugify(c)},
        )

    # Places: one per unique city
    cities = sorted({r["city"] for r in restaurants_raw})
    print(f"Creating {len(cities)} places ...", file=sys.stderr)
    city_to_id: dict[str, int] = {}
    for c in cities:
        city_to_id[c] = find_or_create(
            api_token, "/api/places", "name", c, {"name": c},
        )

    # Restaurants
    print(f"Creating {len(restaurants_raw)} restaurants ...", file=sys.stderr)
    created = 0
    for i, r in enumerate(restaurants_raw):
        img_a = next(image_cycle)
        img_b = next(image_cycle)
        description = (
            f"{r['name']} is a {r['cuisine']} restaurant in {r['city']}."
            + (f"  Address: {r['address']}." if r["address"] else "")
        )
        payload = {
            "name": r["name"][:255],
            "price": pick_price(r["cuisine"], i),
            "images": [img_a, img_b],
            "category": cuisine_to_id[r["cuisine"]],
            "place": city_to_id[r["city"]],
            "information": {
                "description": description,
            },
            "publishedAt": "2024-01-01T00:00:00.000Z",
        }
        code, resp = request("POST", "/api/restaurants", token=api_token,
                             json_body={"data": payload})
        if code in (200, 201):
            created += 1
        else:
            if i < 3:
                print(f"  ! restaurant {r['name']}: {code} {resp}", file=sys.stderr)
    print(f"  {created} restaurants created", file=sys.stderr)

    # Articles
    print(f"Creating {len(articles_raw)} articles ...", file=sys.stderr)
    art_created = 0
    for i, a in enumerate(articles_raw):
        # Associate article to a category heuristically (match on cuisine name in title)
        cat_id = None
        title_lower = a["title"].lower()
        for cname, cid in cuisine_to_id.items():
            if cname.lower() in title_lower:
                cat_id = cid
                break
        if cat_id is None:
            cat_id = next(iter(cuisine_to_id.values()))
        body_html = (
            f"<p>{a.get('summary','')}</p>"
            f"<p>{a.get('body','')}</p>"
            + (f"<p><a href=\"{a['source_url']}\">Source: Wikipedia</a></p>"
               if a.get("source_url") else "")
        )
        payload = {
            "title": a["title"],
            "slug": slugify(a["slug"]),
            "image": next(image_cycle),
            "category": cat_id,
            "ckeditor_content": body_html,
            "publishedAt": "2024-01-01T00:00:00.000Z",
        }
        code, resp = request("POST", "/api/articles", token=api_token,
                             json_body={"data": payload})
        if code in (200, 201):
            art_created += 1
        elif i < 2:
            print(f"  ! article {a['title']}: {code} {resp}", file=sys.stderr)
    print(f"  {art_created} articles created", file=sys.stderr)

    SEED_MARKER.parent.mkdir(exist_ok=True)
    SEED_MARKER.write_text(json.dumps({
        "restaurants": created, "articles": art_created,
        "categories": len(cuisine_to_id), "places": len(city_to_id),
        "images": len(media_ids),
    }, indent=2))
    print(f"\nSeed complete: {SEED_MARKER}", file=sys.stderr)


if __name__ == "__main__":
    main()
