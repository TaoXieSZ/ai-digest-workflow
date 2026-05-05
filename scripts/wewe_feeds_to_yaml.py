"""Generate sources.yaml entries from a running wewe-rss instance.

Workflow:
    1. cd deploy/wewe-rss && docker compose up -d
    2. open http://localhost:4000 → 扫码 + 订阅公众号
    3. python scripts/wewe_feeds_to_yaml.py
    4. Paste the printed yaml block into config/sources.yaml

Reads WEWE_PORT + WEWE_AUTH_CODE from deploy/wewe-rss/.env.

Strategy: try OPML export first (standard, stable), fall back to /api/feeds
JSON if needed. Prints ready-to-paste yaml; does NOT modify sources.yaml.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / "deploy" / "wewe-rss" / ".env"


def _load_env() -> tuple[int, str]:
    if not ENV_PATH.exists():
        sys.stderr.write(
            f"ERROR: {ENV_PATH} missing — run setup first:\n"
            "  cd deploy/wewe-rss && cp .env.example .env (or generate via openssl rand -hex 32)\n"
        )
        sys.exit(2)
    port = 4000
    auth_code = ""
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        if k == "WEWE_PORT":
            try:
                port = int(v)
            except ValueError:
                pass
        elif k == "WEWE_AUTH_CODE":
            auth_code = v.strip()
    if not auth_code:
        sys.stderr.write("ERROR: WEWE_AUTH_CODE not set in .env\n")
        sys.exit(2)
    return port, auth_code


def _slugify(name: str) -> str:
    """Turn a Chinese display name into a sources.yaml id (kebab-ish)."""
    # Strip punctuation, transliterate-light: just romanize when possible.
    # If no ascii letters survive, fall back to a hash-ish prefix.
    ascii_part = re.sub(r"[^a-zA-Z0-9]", "_", name).strip("_").lower()
    if ascii_part:
        return ascii_part
    # No ASCII at all — use first 4 chars unicode codepoints joined.
    return "wechat_" + "".join(f"{ord(c):x}" for c in name[:4])


def _emit_entry(*, slug: str, display_name: str, url: str) -> str:
    return (
        f"  - id: {slug}\n"
        f"    display_name: {display_name}\n"
        f"    fetcher_type: rss\n"
        f"    config:\n"
        f"      url: {url}\n"
        f"      max_items: 30\n"
        f"    enabled: false   # 改 true 启用\n"
    )


def _try_opml(port: int, auth_code: str) -> list[tuple[str, str]] | None:
    """Returns list of (display_name, atom_url) or None on failure."""
    candidates = [
        f"http://localhost:{port}/feeds/all.atom?auth_code={auth_code}",  # combined
        f"http://localhost:{port}/feeds.opml?auth_code={auth_code}",
        f"http://localhost:{port}/feeds/opml?auth_code={auth_code}",
    ]
    for url in candidates:
        try:
            r = httpx.get(url, timeout=10.0)
        except httpx.RequestError:
            continue
        if r.status_code != 200 or not r.text.strip():
            continue
        if "<opml" in r.text.lower():
            return _parse_opml(r.text, port, auth_code)
    return None


def _parse_opml(xml_text: str, port: int, auth_code: str) -> list[tuple[str, str]]:
    feeds: list[tuple[str, str]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        sys.stderr.write(f"OPML parse failed: {e!r}\n")
        return []
    for outline in root.iter("outline"):
        title = outline.get("title") or outline.get("text") or ""
        xml_url = outline.get("xmlUrl") or outline.get("xmlurl") or ""
        if not title or not xml_url:
            continue
        # Ensure auth_code is on the URL.
        if "auth_code=" not in xml_url:
            sep = "&" if "?" in xml_url else "?"
            xml_url = f"{xml_url}{sep}auth_code={auth_code}"
        # Normalize to localhost:<port> in case OPML used a different host.
        xml_url = re.sub(
            r"^https?://[^/]+", f"http://localhost:{port}", xml_url
        )
        feeds.append((title, xml_url))
    return feeds


def _try_json_api(port: int, auth_code: str) -> list[tuple[str, str]] | None:
    """Hit common JSON list endpoints. Returns (name, url) pairs or None."""
    candidates = [
        f"http://localhost:{port}/api/feeds",
        f"http://localhost:{port}/api/v1/feeds",
    ]
    for url in candidates:
        try:
            r = httpx.get(
                url, timeout=10.0, headers={"Authorization": f"Bearer {auth_code}"}
            )
        except httpx.RequestError:
            continue
        if r.status_code != 200:
            continue
        try:
            body = r.json()
        except ValueError:
            continue
        # Common shapes: list of feeds; or {items: [...]}, {data: [...]}
        if isinstance(body, dict):
            items = body.get("items") or body.get("data") or body.get("feeds")
        else:
            items = body
        if not isinstance(items, list):
            continue
        feeds: list[tuple[str, str]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = it.get("name") or it.get("title") or it.get("mp_name")
            feed_id = it.get("id") or it.get("feed_id") or it.get("mp_id")
            if not name or not feed_id:
                continue
            atom = (
                f"http://localhost:{port}/feeds/{feed_id}.atom?auth_code={auth_code}"
            )
            feeds.append((name, atom))
        if feeds:
            return feeds
    return None


def main() -> None:
    port, auth_code = _load_env()
    feeds = _try_opml(port, auth_code) or _try_json_api(port, auth_code)
    if not feeds:
        sys.stderr.write(
            "FAILED: could not list feeds via OPML or JSON API.\n"
            f"Check that wewe-rss is running on http://localhost:{port}\n"
            "and that you've subscribed to at least one 公众号 in the UI.\n"
        )
        sys.exit(1)
    print(f"# auto-generated by scripts/wewe_feeds_to_yaml.py — {len(feeds)} feed(s)")
    print("# Paste under `sources:` in config/sources.yaml; flip enabled to true.")
    print()
    seen_slugs: set[str] = set()
    for name, url in feeds:
        slug = _slugify(name) or "wechat"
        # Disambiguate duplicates.
        base, n = slug, 1
        while slug in seen_slugs:
            n += 1
            slug = f"{base}_{n}"
        seen_slugs.add(slug)
        print(_emit_entry(slug=slug, display_name=name, url=url))


if __name__ == "__main__":
    main()
