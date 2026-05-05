"""One-shot: create the Notion database for ai-digest archive.

Usage:
    NOTION_TOKEN=secret_xxx NOTION_PARENT_PAGE_ID=<32-hex from page URL> \\
        python scripts/setup_notion_db.py

Prints the new database id — paste it into .env as NOTION_DATABASE_ID.

Idempotency: not idempotent. Running twice creates two DBs. The script
prints a clear "would create" preview unless --confirm is passed.
"""

from __future__ import annotations

import json
import os
import sys

import click
import httpx

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1/databases"


# Schema for the digest archive — keep in sync with src/digest/archive_notion.py
PROPERTIES = {
    "Title": {"title": {}},
    "kind": {
        "select": {
            "options": [
                {"name": "event", "color": "blue"},
                {"name": "news", "color": "green"},
                {"name": "tool", "color": "purple"},
                {"name": "other", "color": "gray"},
            ]
        }
    },
    "digest_date": {"date": {}},
    "source": {"rich_text": {}},
    "url": {"url": {}},
    "summary": {"rich_text": {}},
    "topic": {"rich_text": {}},
}


def _normalize_page_id(raw: str) -> str:
    """Accept either a bare 32-hex string or a Notion page URL; strip dashes."""
    raw = raw.strip()
    if raw.startswith("http"):
        # URL ends with ...-<32-hex> or ...?v=<view>
        candidate = raw.split("?")[0].split("/")[-1]
        # The page id is the last 32 hex chars
        candidate = candidate.split("-")[-1]
        return candidate
    return raw.replace("-", "")


@click.command()
@click.option(
    "--title",
    default="AI Digest Archive",
    help="Title for the new Notion database",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Actually create the DB. Without this, prints the preview JSON.",
)
def main(title: str, confirm: bool) -> None:
    token = os.environ.get("NOTION_TOKEN")
    parent_raw = os.environ.get("NOTION_PARENT_PAGE_ID")
    if not token or not parent_raw:
        click.echo(
            "ERROR: set NOTION_TOKEN and NOTION_PARENT_PAGE_ID env vars first",
            err=True,
        )
        sys.exit(2)

    page_id = _normalize_page_id(parent_raw)

    payload = {
        "parent": {"type": "page_id", "page_id": page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": PROPERTIES,
    }

    if not confirm:
        click.echo("DRY RUN — would POST this body to /v1/databases:")
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        click.echo("\nRe-run with --confirm to actually create.")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    resp = httpx.post(API, json=payload, headers=headers, timeout=30.0)

    if resp.status_code != 200:
        click.echo(f"FAILED ({resp.status_code}): {resp.text[:500]}", err=True)
        sys.exit(1)

    body = resp.json()
    db_id = body.get("id", "").replace("-", "")
    click.echo(f"OK — database created: {body.get('url')}")
    click.echo(f"\nAdd to .env:\n  NOTION_DATABASE_ID={db_id}")


if __name__ == "__main__":
    main()
