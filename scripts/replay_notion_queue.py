"""Replay failed Notion archive items from the retry queue.

Reads `data/notion_retry_queue.jsonl` (written by `archive_notion._enqueue_for_retry`),
re-POSTs each item to Notion. Successes are dropped; failures stay in the queue
with the new error so a later run can retry them again.

Atomicity: the queue is rewritten via a `.tmp` file + `os.replace` to avoid
losing rows if the process is killed mid-run.

Usage:
    NOTION_TOKEN=... NOTION_DATABASE_ID=... python scripts/replay_notion_queue.py
    python scripts/replay_notion_queue.py --queue data/notion_retry_queue.jsonl --dry-run
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import click

# Allow running as `python scripts/replay_notion_queue.py` without install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from digest.archive_notion import (  # noqa: E402
    ArchiveItem,
    NotionArchiveError,
    NotionClient,
)

log = logging.getLogger("replay_notion_queue")

DEFAULT_QUEUE = Path("data/notion_retry_queue.jsonl")


def _row_to_item(row: dict) -> ArchiveItem:
    """Reverse of `_enqueue_for_retry`'s payload shape."""
    it = row["item"]
    return ArchiveItem(
        item_id=it["item_id"],
        title=it["title"],
        kind=it["kind"],
        url=it["url"],
        source=it["source"],
        summary=it["summary"],
        topic=it["topic"],
        digest_date=it["digest_date"],
    )


def _read_queue(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                log.warning("queue line %d malformed, dropping: %r", lineno, e)
    return rows


def _write_queue_atomic(path: Path, rows: list[dict]) -> None:
    """Rewrite the queue. Empty list → delete the file."""
    if not rows:
        if path.exists():
            path.unlink()
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


@click.command()
@click.option(
    "--queue",
    "queue_path",
    type=click.Path(path_type=Path),
    default=DEFAULT_QUEUE,
    show_default=True,
    help="Path to the JSONL retry queue.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Don't POST or rewrite the queue; just report what would happen.",
)
def main(queue_path: Path, dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if not queue_path.exists():
        click.echo(f"queue not found: {queue_path} (nothing to do)")
        return

    rows = _read_queue(queue_path)
    if not rows:
        click.echo(f"queue is empty: {queue_path}")
        # Clean up empty file.
        if not dry_run:
            queue_path.unlink()
        return

    click.echo(f"loaded {len(rows)} queued items from {queue_path}")

    if dry_run:
        for r in rows:
            it = r.get("item", {})
            click.echo(f"  - {it.get('item_id')} | {it.get('title', '')[:60]}")
        click.echo("dry-run: no POSTs made, queue unchanged")
        return

    token = os.environ.get("NOTION_TOKEN")
    database_id = os.environ.get("NOTION_DATABASE_ID")
    if not token or not database_id:
        click.echo(
            "ERROR: NOTION_TOKEN and NOTION_DATABASE_ID must be set in env",
            err=True,
        )
        sys.exit(2)

    client = NotionClient(token=token, database_id=database_id)

    remaining: list[dict] = []
    succeeded = 0
    failed = 0
    for row in rows:
        try:
            item = _row_to_item(row)
        except (KeyError, TypeError) as e:
            log.warning("skipping malformed row (keeping in queue): %r", e)
            remaining.append(row)
            continue

        try:
            client.create_page(item)
        except NotionArchiveError as e:
            failed += 1
            log.warning("replay failed for %s: %r", item.item_id, e)
            row["error"] = repr(e)
            row["last_retry_ts"] = datetime.utcnow().isoformat() + "Z"
            remaining.append(row)
            continue
        succeeded += 1
        log.info("replay ok: %s", item.item_id)

    _write_queue_atomic(queue_path, remaining)

    click.echo(
        f"replay done: {succeeded} succeeded, {failed} failed, "
        f"{len(remaining)} remain in queue"
    )


if __name__ == "__main__":
    main()
