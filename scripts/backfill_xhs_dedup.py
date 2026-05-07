"""One-shot backfill: collapse XHS duplicate rows that exist due to the
pre-fix canonicalize() bug (xsec_token wasn't stripped, so the same note
hashed to N item_ids).

Workflow per (source_id, NEW_canonical_url) group with >1 row:
1. Pick keeper = oldest fetched_at (first time we saw the note).
2. Move/dedupe child-table rows from victims onto keeper:
   - event_metadata        (PK item_id):                 UPDATE OR IGNORE; remaining victims DELETED
   - item_topic_assignments(PK item_id+topic_id+digest): UPDATE OR IGNORE; remaining victims DELETED
   - event_pushes          (PK item_id):                 UPDATE OR IGNORE; remaining victims DELETED
   - feishu_calendar_events(PK item_id):                 UPDATE OR IGNORE; remaining victims DELETED
3. DELETE victim items rows.
4. UPDATE keeper.url = NEW_canonical (so DB reflects post-fix state).

Default is DRY-RUN (prints plan, no writes). Pass --confirm to execute.
Always writes a timestamped backup to data/items.db.bak-* before --confirm.
Idempotent: re-running after success finds 0 dup groups and exits.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import click

# Allow `python scripts/backfill_xhs_dedup.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from digest.url_canonical import canonicalize  # noqa: E402

DEFAULT_DB = Path("data/items.db")
CHILD_TABLES = (
    "event_metadata",
    "item_topic_assignments",
    "event_pushes",
    "feishu_calendar_events",
)


def _find_dup_groups(conn: sqlite3.Connection) -> dict[tuple[str, str], list[sqlite3.Row]]:
    """Recompute canonical_url for every item; return groups with >=2 rows."""
    groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in conn.execute(
        "SELECT id, source_id, url, raw_url, title, fetched_at FROM items"
    ):
        new_canon = canonicalize(r["raw_url"] or r["url"])
        groups[(r["source_id"], new_canon)].append(r)
    return {k: v for k, v in groups.items() if len(v) > 1}


def _collapse_group(
    conn: sqlite3.Connection,
    keeper_id: str,
    victim_ids: list[str],
    new_canonical_url: str,
) -> None:
    """Reattach child rows + delete victims + update keeper url. Caller owns
    the transaction."""
    placeholders = ",".join("?" * len(victim_ids))

    for tbl in CHILD_TABLES:
        # Try to move each victim row onto keeper; skip rows that would
        # violate the keeper's PK (those are pure duplicates of keeper data
        # and get DELETEd in the next step).
        conn.execute(
            f"UPDATE OR IGNORE {tbl} SET item_id=? WHERE item_id IN ({placeholders})",
            (keeper_id, *victim_ids),
        )
        conn.execute(
            f"DELETE FROM {tbl} WHERE item_id IN ({placeholders})",
            victim_ids,
        )

    conn.execute(
        f"DELETE FROM items WHERE id IN ({placeholders})",
        victim_ids,
    )
    conn.execute(
        "UPDATE items SET url=? WHERE id=?",
        (new_canonical_url, keeper_id),
    )


def _backup_db(db: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = db.with_name(f"{db.name}.bak-pre-xhs-dedup-{ts}")
    shutil.copy2(db, dst)
    return dst


@click.command()
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_DB,
    show_default=True,
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Actually rewrite the DB (default: print plan only).",
)
def main(db_path: Path, confirm: bool) -> None:
    if not db_path.exists():
        click.echo(f"ERROR: db not found at {db_path}", err=True)
        sys.exit(2)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    dup_groups = _find_dup_groups(conn)
    if not dup_groups:
        click.echo("No duplicate groups found. DB is clean.")
        return

    extras = sum(len(v) - 1 for v in dup_groups.values())
    click.echo(
        f"{len(dup_groups)} duplicate group(s); will collapse {extras} extra row(s).\n"
    )

    plan: list[tuple[str, list[str], str, str]] = []
    for (src, canon), rows in sorted(dup_groups.items()):
        ordered = sorted(rows, key=lambda r: r["fetched_at"])
        keeper = ordered[0]
        victims = ordered[1:]
        plan.append(
            (
                str(keeper["id"]),
                [str(r["id"]) for r in victims],
                canon,
                str(keeper["title"] or "(untitled)"),
            )
        )
        click.echo(f"  src={src}")
        click.echo(f"    keeper:  {keeper['id'][:12]}  fetched={keeper['fetched_at'][:19]}")
        for v in victims:
            click.echo(f"    victim:  {v['id'][:12]}  fetched={v['fetched_at'][:19]}")
        click.echo(f"    rewrite url -> {canon}")
        click.echo(f"    title:   {keeper['title']}\n")

    if not confirm:
        click.echo("(dry-run) Pass --confirm to execute.")
        return

    backup = _backup_db(db_path)
    click.echo(f"\nBacked up DB to {backup}")
    click.echo("Applying...\n")

    try:
        with conn:  # implicit transaction; commits on success
            for keeper_id, victim_ids, new_canon, _title in plan:
                _collapse_group(conn, keeper_id, victim_ids, new_canon)
    except sqlite3.Error as e:
        click.echo(f"ERROR during apply (transaction rolled back): {e}", err=True)
        click.echo(f"Restore: cp {backup} {db_path}", err=True)
        sys.exit(1)

    # Verify: re-scan should show 0 dup groups now.
    remaining = _find_dup_groups(conn)
    if remaining:
        click.echo(
            f"WARNING: {len(remaining)} dup group(s) remain after apply.",
            err=True,
        )
        sys.exit(1)
    click.echo(
        f"Done. Collapsed {len(plan)} group(s), removed {extras} row(s)."
    )


if __name__ == "__main__":
    main()
