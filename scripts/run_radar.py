"""CLI: hourly event-radar tick.

Usage:
    python scripts/run_radar.py [--db PATH] [--dry-run]

Reads ANTHROPIC_API_KEY, ANTHROPIC_MODEL, FEISHU_WEBHOOK_URL,
QUIET_HOURS_START, QUIET_HOURS_END from environment.

In --dry-run mode: classify items, but don't push to Feishu (uses a noop pusher).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from digest.classifier import Classifier, make_client_from_env  # noqa: E402
from digest.event_radar import RadarConfig, run_radar  # noqa: E402
from digest.store import init_schema, open_db  # noqa: E402

log = logging.getLogger("run_radar")


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
    help="Classify but do not push (uses no-op webhook).",
)
def main(db_path: Path, dry_run: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "")
    if not webhook_url and not dry_run:
        log.error("FEISHU_WEBHOOK_URL not set (or pass --dry-run)")
        sys.exit(1)

    quiet_start = int(os.getenv("QUIET_HOURS_START", "23"))
    quiet_end = int(os.getenv("QUIET_HOURS_END", "7"))

    init_schema(db_path)
    try:
        classifier = Classifier(make_client_from_env())
    except RuntimeError as e:
        log.error("LLM client init failed: %s", e)
        sys.exit(1)

    if dry_run:
        # Phase 4 push will short-circuit because we override URL to localhost-noop.
        # Simpler: bail before push by setting an obviously-bad URL and catching.
        webhook_url = "http://127.0.0.1:1/__dryrun__"

    config = RadarConfig(
        feishu_webhook_url=webhook_url,
        quiet_start_hour=quiet_start,
        quiet_end_hour=quiet_end,
    )

    with open_db(db_path) as conn:
        if dry_run:
            # Run only the classification phase; show what would be pushed.
            from digest.store import get_unclassified_items

            pending_count = len(get_unclassified_items(conn, limit=config.classify_batch))
            log.info("[dry-run] %d items pending classification", pending_count)

        result = _run_dry_safe(conn, classifier, config) if dry_run else run_radar(
            conn, classifier, config
        )
        log.info(
            "radar result: classified=%d events_found=%d events_pushed=%d quiet=%s",
            result.classified,
            result.events_found,
            result.events_pushed,
            result.skipped_quiet,
        )


def _run_dry_safe(conn, classifier, config):  # type: ignore[no-untyped-def]
    """Dry-run: classify only, skip the push by short-circuiting at events_found == 0
    semantics — we still report what *would* be pushed."""
    from datetime import datetime

    from digest.event_radar import RadarResult, is_quiet_hours
    from digest.store import (
        get_unclassified_items,
        get_unpushed_events,
        set_item_classification,
        upsert_event_metadata,
    )

    now = datetime.now(config.timezone)
    pending = get_unclassified_items(conn, limit=config.classify_batch)
    classified = 0
    for row in pending:
        try:
            cls = classifier.classify(title=row["title"] or "", content=row["content"])
        except Exception:
            log.exception("classifier failed for item %s", row["id"])
            continue
        set_item_classification(conn, item_id=row["id"], kind=cls.kind, classified_at=now)
        if cls.kind == "event" and cls.event_metadata is not None:
            em = cls.event_metadata
            upsert_event_metadata(
                conn,
                item_id=row["id"],
                event_date=em.event_date,
                registration_deadline=em.registration_deadline,
                location=em.location,
                registration_url=em.registration_url,
                extracted_at=now,
            )
        classified += 1

    fresh = get_unpushed_events(conn)
    quiet = is_quiet_hours(now, start=config.quiet_start_hour, end=config.quiet_end_hour)
    log.info(
        "[dry-run] would push %d events (quiet=%s); titles: %s",
        len(fresh),
        quiet,
        [r["title"] for r in fresh[:5]],
    )
    return RadarResult(
        classified=classified,
        events_found=len(fresh),
        events_pushed=0,
        skipped_quiet=quiet,
    )


if __name__ == "__main__":
    main()
