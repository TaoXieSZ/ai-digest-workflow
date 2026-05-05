"""CLI: run one daily digest cycle.

Usage:
    python scripts/run_digest.py [--db PATH] [--dry-run]

Reads DEEPSEEK_API_KEY (or other LLM provider) and FEISHU_WEBHOOK_URL from env.

In --dry-run mode: dedup + cluster + render are executed (so LLM tokens DO
spend), but no push and no DB writes for topics/assignments. Useful to preview
what tomorrow's digest looks like.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from digest.classifier import make_client_from_env  # noqa: E402
from digest.cluster import ClusterInput, cluster  # noqa: E402
from digest.daily_digest import CN_TZ, DigestConfig, run_daily_digest  # noqa: E402
from digest.dedup import DedupItem, deduplicate  # noqa: E402
from digest.digest_builder import DigestItem, render_daily_digest  # noqa: E402
from digest.store import (  # noqa: E402
    get_unclustered_non_event_items,
    init_schema,
    open_db,
)

log = logging.getLogger("run_digest")


@click.command()
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=ROOT / "data" / "items.db",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Cluster + render only; do not push or write topic assignments.",
)
def main(db_path: Path, dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "")
    if not webhook_url and not dry_run:
        log.error("FEISHU_WEBHOOK_URL not set (or pass --dry-run)")
        sys.exit(1)

    init_schema(db_path)
    try:
        llm = make_client_from_env()
    except RuntimeError as e:
        log.error("LLM client init failed: %s", e)
        sys.exit(1)

    if dry_run:
        _dry_run(db_path, llm)
        return

    notion_token = os.getenv("NOTION_TOKEN") or None
    notion_db = os.getenv("NOTION_DATABASE_ID") or None
    if (notion_token is None) != (notion_db is None):
        log.warning(
            "NOTION_TOKEN and NOTION_DATABASE_ID must both be set; archiving disabled"
        )
        notion_token = notion_db = None

    config = DigestConfig(
        feishu_webhook_url=webhook_url,
        notion_token=notion_token,
        notion_database_id=notion_db,
    )
    with open_db(db_path) as conn:
        result = run_daily_digest(conn, llm, config)
    log.info(
        "daily digest: candidates=%d after_dedup=%d topics=%d assigned=%d pushed=%s "
        "notion_ok=%d notion_failed=%d",
        result.candidate_items,
        result.after_dedup,
        result.topics,
        result.items_assigned,
        result.pushed,
        result.notion_archived,
        result.notion_failed,
    )


def _dry_run(db_path: Path, llm: object) -> None:
    """Same first 4 phases of run_daily_digest but no DB writes / no network."""
    from datetime import datetime, timedelta

    now = datetime.now(CN_TZ)
    digest_date = now.date().isoformat()
    cutoff = (now - timedelta(days=7)).date().isoformat()

    with open_db(db_path) as conn:
        rows = get_unclustered_non_event_items(
            conn, exclude_assigned_since=cutoff, limit=50
        )
    log.info("[dry-run] %d candidate items", len(rows))
    if not rows:
        return

    dedup_inputs = [
        DedupItem(
            id=r["id"],
            title=r["title"] or "",
            canonical_url=r["url"],
            published_at=r["published_at"],
            content_len=len(r["content"] or ""),
        )
        for r in rows
    ]
    deduped = deduplicate(dedup_inputs)
    log.info("[dry-run] after dedup: %d", len(deduped))

    survivors = {it.id for it in deduped}
    rows_kept = [r for r in rows if r["id"] in survivors]
    cluster_inputs = [
        ClusterInput(item_id=r["id"], title=r["title"] or "", snippet=(r["content"] or "")[:200])
        for r in rows_kept
    ]
    topics = cluster(cluster_inputs, llm)  # type: ignore[arg-type]
    log.info("[dry-run] %d topics returned", len(topics))

    item_lookup = {
        r["id"]: DigestItem(
            item_id=r["id"],
            title=r["title"] or "(无标题)",
            url=r["url"],
            source=r["source_id"],
            published_at=r["published_at"],
        )
        for r in rows_kept
    }
    md = render_daily_digest(topics, item_lookup, digest_date=digest_date)
    click.echo("\n=== DIGEST PREVIEW ===\n")
    click.echo(md)
    click.echo(f"\n=== END (length: {len(md)} chars) ===")


if __name__ == "__main__":
    main()
