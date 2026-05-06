"""CLI: fetch all enabled sources once, write new items into SQLite.

Usage:
    python scripts/run_fetch.py [--config PATH] [--db PATH]

Failure isolation: a single source's exception is logged + degrades health score,
never crashes the run.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
import yaml

# Make src importable when invoked as a script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from digest.fetchers.base import FetchedItem, FetchError  # noqa: E402
from digest.fetchers.rss import RSSConfig, RSSFetcher  # noqa: E402
from digest.sources.xhs_skill_bridge import XHSConfig, XHSFetcher  # noqa: E402
from digest.store import (  # noqa: E402
    SqliteDetailCache,
    init_schema,
    insert_item_if_new,
    open_db,
    update_source_health,
    upsert_source,
)
from digest.url_canonical import canonicalize  # noqa: E402

log = logging.getLogger("run_fetch")


@click.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=ROOT / "config" / "sources.yaml",
    help="Path to sources.yaml",
)
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=ROOT / "data" / "items.db",
    help="Path to SQLite db",
)
def main(config_path: Path, db_path: Path) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = yaml.safe_load(config_path.read_text())
    sources: list[dict[str, Any]] = config["sources"]
    init_schema(db_path)

    now = datetime.now(UTC)
    summary: dict[str, int] = {}

    with open_db(db_path) as conn:
        for src in sources:
            sid: str = src["id"]
            upsert_source(
                conn,
                source_id=sid,
                display_name=src["display_name"],
                fetcher_type=src["fetcher_type"],
                config_json=json.dumps(src["config"]),
                enabled=src.get("enabled", True),
            )
            if not src.get("enabled", True):
                log.info("%s: skipped (disabled)", sid)
                continue

            try:
                items = _run_fetcher(src, conn)
            except FetchError as e:
                log.error("fetch failed for %s: %s", sid, e)
                update_source_health(conn, sid, success=False, error=str(e))
                continue
            except Exception as e:  # isolation: one bad source must not kill the run
                log.exception("unexpected error fetching %s", sid)
                update_source_health(conn, sid, success=False, error=repr(e))
                continue

            n_new = 0
            for item in items:
                canonical = canonicalize(item.url)
                if not canonical:
                    continue
                if insert_item_if_new(
                    conn,
                    source_id=sid,
                    canonical_url=canonical,
                    raw_url=item.url,
                    title=item.title,
                    content=item.content,
                    author=item.author,
                    published_at=item.published_at,
                    fetched_at=now,
                ):
                    n_new += 1

            update_source_health(conn, sid, success=True)
            summary[sid] = n_new
            log.info("%s: %d new items (of %d fetched)", sid, n_new, len(items))

    log.info("done. summary: %s", summary)


def _run_fetcher(src: dict[str, Any], conn: sqlite3.Connection) -> list[FetchedItem]:
    import os as _os

    ft = src["fetcher_type"]
    cfg = dict(src["config"])  # shallow copy so we can rewrite url
    if ft == "rss":
        # Expand ${VAR} in url so secrets (auth_code, tokens) live in env
        # not in the committed sources.yaml.
        if isinstance(cfg.get("url"), str):
            cfg["url"] = _os.path.expandvars(cfg["url"])
        return RSSFetcher(RSSConfig(**cfg)).fetch()
    if ft == "xhs":
        # Inject the detail cache so XHSFetcher can enrich noteCard.desc
        # with the full post body via post-detail.sh.
        return XHSFetcher(XHSConfig(**cfg, detail_cache=SqliteDetailCache(conn))).fetch()
    raise FetchError(f"unsupported fetcher_type: {ft}")


if __name__ == "__main__":
    main()
