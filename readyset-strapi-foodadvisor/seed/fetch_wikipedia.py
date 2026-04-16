"""Pull real cuisine summaries from Wikipedia to use as Strapi articles."""

from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

CUISINES = [
    "Italian cuisine", "Japanese cuisine", "French cuisine", "Mexican cuisine",
    "Chinese cuisine", "Indian cuisine", "Thai cuisine", "Spanish cuisine",
    "Greek cuisine", "Vietnamese cuisine", "Korean cuisine", "Turkish cuisine",
    "Moroccan cuisine", "Peruvian cuisine", "Ethiopian cuisine", "Lebanese cuisine",
    "Ramen", "Sushi", "Pizza", "Pasta", "Taco", "Hamburger", "Paella",
    "Tapas", "Dim sum", "Bibimbap", "Falafel", "Tiramisu", "Croissant",
    "Churro",
]


def fetch_summary(title: str) -> dict | None:
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "readyset-foodadvisor-demo/0.1 (vinicius@readyset.io)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  ! {title}: {e}", file=sys.stderr)
        return None


def main() -> None:
    import sys
    out = DATA / "articles.json"
    if out.exists() and "--force" not in sys.argv:
        count = len(json.loads(out.read_text()))
        print(f"Using committed {out.name} ({count} articles). Pass --force to refetch.", file=sys.stderr)
        return

    articles: list[dict] = []
    for title in CUISINES:
        s = fetch_summary(title)
        if not s:
            continue
        extract = s.get("extract") or ""
        if len(extract) < 80:
            continue
        articles.append({
            "title": s.get("title") or title,
            "slug": (s.get("title") or title).lower().replace(" ", "-"),
            "summary": s.get("description") or "",
            "body": extract,
            "source_url": s.get("content_urls", {}).get("desktop", {}).get("page", ""),
        })
        time.sleep(0.15)

    out.write_text(json.dumps(articles, indent=2, ensure_ascii=False))
    print(f"Wrote {len(articles)} articles -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
