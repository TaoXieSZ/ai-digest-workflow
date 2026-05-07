"""Print calendars accessible to the configured Feishu self-built app.

Usage:
    FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=yyy \\
        python scripts/feishu_list_calendars.py [--ensure-primary]

Prints all calendars the app can see; with `--ensure-primary` it first POSTs
`/calendars/primary` to make sure the app's own primary calendar exists.

Pick a row, copy its calendar_id into `.env` as FEISHU_CALENDAR_ID.
"""

from __future__ import annotations

import os
import sys

import click

from digest.feishu_calendar import (
    FeishuCalendarClient,
    FeishuCalendarError,
)


@click.command()
@click.option(
    "--ensure-primary",
    is_flag=True,
    help=(
        "POST /calendars/primary first to create the app's primary calendar "
        "if it does not yet exist."
    ),
)
def main(ensure_primary: bool) -> None:
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        click.echo(
            "ERROR: set FEISHU_APP_ID and FEISHU_APP_SECRET env vars first",
            err=True,
        )
        sys.exit(2)

    client = FeishuCalendarClient(app_id=app_id, app_secret=app_secret)

    try:
        primary_id: str | None = None
        if ensure_primary:
            primary = client.get_or_create_primary_calendar()
            primary_id = primary.calendar_id
            click.echo(
                f"primary ensured: {primary.calendar_id}  "
                f"({primary.summary or 'unnamed'})\n"
            )
        cals = client.list_calendars()
    except FeishuCalendarError as exc:
        click.echo(f"FAILED: {exc}", err=True)
        sys.exit(1)

    if not cals:
        click.echo(
            "(no calendars accessible — re-run with --ensure-primary, "
            "or check the app has calendar:calendar / calendar:event scopes)"
        )
        return

    click.echo(f"{'TYPE':<10} {'CALENDAR_ID':<48} SUMMARY")
    click.echo("-" * 80)
    for c in cals:
        click.echo(f"{c.type:<10} {c.calendar_id:<48} {c.summary}")

    suggested = primary_id or next(
        (c.calendar_id for c in cals if c.type == "primary"), None
    )
    if suggested:
        click.echo(f"\n# Add this to .env:\nFEISHU_CALENDAR_ID={suggested}")


if __name__ == "__main__":
    main()
