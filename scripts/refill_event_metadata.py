"""One-shot: re-extract event_metadata for existing events.

The 5/9 release added `registration_contact` (non-URL fallback) + relative-date
prompt rules to the classifier. Events classified before that release have:
- `registration_contact = NULL` for everyone (column didn't exist)
- `registration_deadline` filled at low rate because the LLM wasn't told to
  resolve "本周内" / "3 天内" / "五一前" against today's date

This script re-runs the classifier on candidate events (kind='event' AND
registration_url IS NULL AND registration_contact IS NULL) and upserts any
new fields the new prompt is able to extract. It does NOT touch:
- events that already have a registration_url (no improvement expected)
- the items.kind column (we don't downgrade events here even if the new run
  classifies as non-event — out of scope, leave for a separate cleanup)

Usage:
    DEEPSEEK_API_KEY=... python scripts/refill_event_metadata.py --dry-run
    DEEPSEEK_API_KEY=... python scripts/refill_event_metadata.py --confirm \\
        [--db data/items.db] [--limit 5] [--no-backup]
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import click

# Allow running as `python scripts/refill_event_metadata.py` without install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from digest.classifier import (  # noqa: E402
    Classifier,
    EventMetadata,
    make_client_from_env,
)
from digest.store import open_db, upsert_event_metadata  # noqa: E402

log = logging.getLogger("refill_event_metadata")

CANDIDATES_SQL = """
SELECT i.id, i.source_id, i.title, i.content,
       em.event_date, em.registration_deadline,
       em.location, em.registration_url, em.registration_contact
  FROM items i
  JOIN event_metadata em ON em.item_id = i.id
 WHERE i.kind = 'event'
   AND em.registration_url IS NULL
   AND em.registration_contact IS NULL
 ORDER BY i.fetched_at DESC
"""


def _row_to_existing(row: sqlite3.Row) -> EventMetadata:
    return EventMetadata(
        event_date=row["event_date"],
        registration_deadline=row["registration_deadline"],
        location=row["location"],
        registration_url=row["registration_url"],
        registration_contact=row["registration_contact"],
    )


def _diff_fields(old: EventMetadata, new: EventMetadata) -> list[str]:
    """Return list of fields where `new` adds info `old` didn't have.

    A None→non-None transition is a clear improvement (and the only thing this
    script is trying to deliver). non-None→different-non-None is reported but
    NOT counted as improvement — could be the LLM disagreeing, leave alone.
    """
    fields = (
        "event_date",
        "registration_deadline",
        "location",
        "registration_url",
        "registration_contact",
    )
    improved: list[str] = []
    for f in fields:
        old_v = getattr(old, f)
        new_v = getattr(new, f)
        if old_v is None and new_v is not None:
            improved.append(f)
    return improved


def _backup_db(db: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db.with_name(f"{db.name}.bak-refill-{ts}")
    shutil.copy2(db, backup)
    return backup


@click.command()
@click.option(
    "--db",
    "db_path",
    type=click.Path(path_type=Path),
    default=Path("data/items.db"),
    show_default=True,
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Re-classify but don't write. Prints what WOULD change.",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Required to actually write. Mutually exclusive with --dry-run.",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Process at most N candidates (for testing).",
)
@click.option(
    "--no-backup",
    is_flag=True,
    help="Skip the items.db.bak-refill-{ts} backup. Only use after dry-run.",
)
def main(
    db_path: Path,
    dry_run: bool,
    confirm: bool,
    limit: int | None,
    no_backup: bool,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if dry_run and confirm:
        click.echo("ERROR: --dry-run and --confirm are mutually exclusive", err=True)
        sys.exit(2)
    if not dry_run and not confirm:
        click.echo("ERROR: pass --dry-run to preview or --confirm to write", err=True)
        sys.exit(2)
    if not db_path.exists():
        click.echo(f"ERROR: db not found: {db_path}", err=True)
        sys.exit(2)

    if confirm and not no_backup:
        backup = _backup_db(db_path)
        click.echo(f"backup: {backup}")

    client = make_client_from_env()
    classifier = Classifier(client)

    improved = 0
    no_change = 0
    downgraded = 0
    failed = 0

    with open_db(db_path) as conn:
        rows = conn.execute(CANDIDATES_SQL).fetchall()
        if limit is not None:
            rows = rows[:limit]
        click.echo(f"candidates: {len(rows)}")

        for row in rows:
            title = row["title"] or ""
            try:
                cls = classifier.classify(title=title, content=row["content"])
            except Exception as e:  # noqa: BLE001
                failed += 1
                log.warning("classify failed for %s (%s): %r", row["id"], title[:30], e)
                continue

            if cls.kind != "event" or cls.event_metadata is None:
                downgraded += 1
                log.info(
                    "  downgrade %s: %s → %s (left as-is)",
                    row["id"],
                    title[:40],
                    cls.kind,
                )
                continue

            old = _row_to_existing(row)
            new = cls.event_metadata
            added = _diff_fields(old, new)

            if not added:
                no_change += 1
                continue

            improved += 1
            click.echo(
                f"  + {row['id']} [{row['source_id']}] {title[:50]}"
                f"\n     fields filled: {', '.join(added)}"
            )
            for f in added:
                click.echo(f"       {f} = {getattr(new, f)!r}")

            if confirm:
                # Merge: keep any existing non-None fields (don't lose a
                # previously-extracted value if the new run drops one), apply
                # newly-filled fields on top.
                merged = EventMetadata(
                    event_date=new.event_date or old.event_date,
                    registration_deadline=new.registration_deadline or old.registration_deadline,
                    location=new.location or old.location,
                    registration_url=new.registration_url or old.registration_url,
                    registration_contact=new.registration_contact or old.registration_contact,
                )
                upsert_event_metadata(
                    conn,
                    item_id=row["id"],
                    event_date=merged.event_date,
                    registration_deadline=merged.registration_deadline,
                    location=merged.location,
                    registration_url=merged.registration_url,
                    registration_contact=merged.registration_contact,
                    extracted_at=datetime.now(),
                )

    click.echo(
        f"\nimproved: {improved} | no-change: {no_change} | "
        f"downgraded: {downgraded} | failed: {failed}"
    )
    if dry_run:
        click.echo("(dry-run; no writes)")


if __name__ == "__main__":
    main()
