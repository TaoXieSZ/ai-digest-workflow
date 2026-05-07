"""Sync new event-items into Feishu Calendar.

Usage:
    # dry-run (default): print what *would* be pushed
    FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=yyy FEISHU_CALENDAR_ID=zzz \\
        python scripts/run_calendar_sync.py

    # actually push
    ... python scripts/run_calendar_sync.py --confirm

Reads pending events from `data/items.db` (kind='event', event_date >= today,
not yet in feishu_calendar_events). Each event becomes one all-day Feishu
calendar event. registration_deadline / location / registration_url go into
the description.

Idempotent: re-running on the same DB pushes nothing new (DB ledger gates
duplicate sends).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import click

from digest.calendar_sync import summarize, sync_pending_events
from digest.feishu_calendar import FeishuCalendarClient
from digest.store import init_schema, open_db

DEFAULT_DB = Path("data/items.db")
TZ_LOCAL = ZoneInfo("Asia/Shanghai")


@click.command()
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_DB,
    show_default=True,
    help="Path to items.db",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Actually create events in Feishu (default is dry-run).",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Process at most N pending events (useful for first push).",
)
@click.option(
    "--include-undated",
    is_flag=True,
    help=(
        "Also sync event-items where the LLM couldn't extract event_date. "
        "Each gets a placeholder date (today + 7d) and a [日期待定] title "
        "prefix. Off by default; opt in once you've reviewed the dry-run."
    ),
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Enable debug logging.",
)
def main(
    db_path: Path,
    confirm: bool,
    limit: int | None,
    include_undated: bool,
    verbose: bool,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    calendar_id = os.environ.get("FEISHU_CALENDAR_ID")
    missing = [
        name
        for name, val in (
            ("FEISHU_APP_ID", app_id),
            ("FEISHU_APP_SECRET", app_secret),
            ("FEISHU_CALENDAR_ID", calendar_id),
        )
        if not val
    ]
    if missing:
        click.echo(f"ERROR: missing env vars: {', '.join(missing)}", err=True)
        sys.exit(2)
    assert app_id and app_secret and calendar_id  # narrow for mypy

    if not db_path.exists():
        click.echo(f"ERROR: db not found at {db_path}", err=True)
        sys.exit(2)

    today = datetime.now(TZ_LOCAL).date().isoformat()
    now_utc = datetime.now(UTC)
    mode = "CONFIRM" if confirm else "dry-run"
    flags = " include_undated" if include_undated else ""
    click.echo(
        f"[{mode}{flags}] db={db_path} today={today} calendar={calendar_id}"
    )

    init_schema(db_path)  # ensures feishu_calendar_events exists on legacy DBs
    client = FeishuCalendarClient(app_id=app_id, app_secret=app_secret)

    with open_db(db_path) as conn:
        results = sync_pending_events(
            conn=conn,
            client=client,
            calendar_id=calendar_id,
            today=today,
            now=now_utc,
            limit=limit,
            dry_run=not confirm,
            include_undated=include_undated,
        )

    if not results:
        click.echo("No pending events.")
        return

    click.echo()
    click.echo(f"{'STATUS':<10} {'EVENT_DATE':<12} {'FEISHU_EVENT_ID':<24} TITLE")
    click.echo("-" * 100)
    for r in results:
        if r.skipped_dry_run:
            status = "DRY-RUN"
        elif r.ok:
            status = "OK"
        else:
            status = "FAIL"
        ev_id = r.feishu_event_id or "-"
        click.echo(f"{status:<10} {r.event_date:<12} {ev_id:<24} {r.title}")
        if r.error:
            click.echo(f"           error: {r.error}")

    summary = summarize(results)
    click.echo()
    click.echo(
        f"Summary: total={summary['total']} synced={summary['synced']} "
        f"dry_run={summary['dry_run']} failed={summary['failed']}"
    )

    if summary["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
