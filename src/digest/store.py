"""SQLite schema + CRUD primitives for the digest pipeline."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

# Register tz-aware-friendly TIMESTAMP adapter/converter once at import time.
# Python's stdlib default chokes on ISO strings with tz offset like "+08:00".
# We round-trip via ISO 8601 + datetime.fromisoformat (Python 3.11+ handles offsets).
sqlite3.register_adapter(datetime, lambda dt: dt.isoformat(sep=" "))


def _convert_timestamp(value: bytes) -> datetime:
    return datetime.fromisoformat(value.decode("utf-8"))


sqlite3.register_converter("TIMESTAMP", _convert_timestamp)
sqlite3.register_converter("timestamp", _convert_timestamp)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    fetcher_type TEXT NOT NULL,
    config_json TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    health_score REAL DEFAULT 1.0,
    last_success_at TIMESTAMP,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id),
    url TEXT NOT NULL,
    raw_url TEXT,
    title TEXT,
    content TEXT,
    author TEXT,
    published_at TIMESTAMP,
    fetched_at TIMESTAMP NOT NULL,
    kind TEXT DEFAULT 'unclassified',  -- event | news | tool | other | unclassified
    classified_at TIMESTAMP,
    UNIQUE(source_id, url)
);

CREATE INDEX IF NOT EXISTS idx_items_fetched ON items(fetched_at);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);

CREATE TABLE IF NOT EXISTS event_metadata (
    item_id TEXT PRIMARY KEY REFERENCES items(id),
    event_date DATE,
    registration_deadline DATE,
    location TEXT,
    registration_url TEXT,
    extracted_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS event_pushes (
    item_id TEXT PRIMARY KEY REFERENCES items(id),
    pushed_at TIMESTAMP NOT NULL,
    digest_id TEXT REFERENCES digests(id)
);

CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    summary TEXT,
    digest_date DATE NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topics_date ON topics(digest_date);

CREATE TABLE IF NOT EXISTS item_topic_assignments (
    item_id TEXT NOT NULL REFERENCES items(id),
    topic_id TEXT NOT NULL REFERENCES topics(id),
    digest_date DATE NOT NULL,
    PRIMARY KEY (item_id, topic_id, digest_date)
);

CREATE TABLE IF NOT EXISTS digests (
    id TEXT PRIMARY KEY,
    digest_date DATE NOT NULL,
    kind TEXT DEFAULT 'daily_digest',  -- event_batch | daily_digest
    content_md TEXT NOT NULL,
    pushed_at TIMESTAMP,
    archived_at TIMESTAMP,
    push_attempts INTEGER DEFAULT 0,
    archive_attempts INTEGER DEFAULT 0,
    UNIQUE(digest_date, kind)
);

-- Cache for XHS post-detail.sh responses. Key is feed_id from XHS search results.
-- xsec_token is stored to support refetch but is not part of the lookup key.
CREATE TABLE IF NOT EXISTS xhs_note_details (
    feed_id TEXT PRIMARY KEY,
    xsec_token TEXT NOT NULL,
    title TEXT,
    content TEXT,
    fetched_at TIMESTAMP NOT NULL
);
"""

def _migrate(conn: sqlite3.Connection) -> None:
    """Reshape pre-PR-A databases to the current schema.

    Ordering: this runs BEFORE `executescript(SCHEMA)` so that when the post-
    pass creates indexes that reference new columns, the columns exist.

    items: ALTER TABLE ADD COLUMN for `kind` and `classified_at`.
    digests: legacy schema had `UNIQUE(digest_date)`; the new schema needs
    `UNIQUE(digest_date, kind)`. Since PR1 never populated `digests`, the
    safe migration is to DROP and let SCHEMA recreate.
    """
    # items: add columns if missing
    items_cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(items)").fetchall()
    }
    if items_cols:
        if "kind" not in items_cols:
            conn.execute(
                "ALTER TABLE items ADD COLUMN kind TEXT DEFAULT 'unclassified'"
            )
        if "classified_at" not in items_cols:
            conn.execute("ALTER TABLE items ADD COLUMN classified_at TIMESTAMP")

    # digests: drop+recreate if legacy schema (no `kind` column)
    digests_cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(digests)").fetchall()
    }
    if digests_cols and "kind" not in digests_cols:
        conn.execute("DROP TABLE digests")


def item_id(source_id: str, canonical_url: str) -> str:
    """Stable 16-char hex id derived from (source_id, canonical_url)."""
    digest = hashlib.sha256(f"{source_id}|{canonical_url}".encode()).hexdigest()
    return digest[:16]


@contextmanager
def open_db(path: Path) -> Iterator[sqlite3.Connection]:
    """Open SQLite with sane defaults; commits on clean exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(db_path: Path) -> None:
    with open_db(db_path) as conn:
        # Migrate first so that subsequent CREATE INDEX statements (which
        # reference newly-added columns) can succeed on a legacy db.
        _migrate(conn)
        conn.executescript(SCHEMA)


def upsert_source(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    display_name: str,
    fetcher_type: str,
    config_json: str,
    enabled: bool = True,
) -> None:
    conn.execute(
        """
        INSERT INTO sources (id, display_name, fetcher_type, config_json, enabled)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            display_name=excluded.display_name,
            fetcher_type=excluded.fetcher_type,
            config_json=excluded.config_json,
            enabled=excluded.enabled
        """,
        (source_id, display_name, fetcher_type, config_json, int(enabled)),
    )


def insert_item_if_new(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    canonical_url: str,
    raw_url: str,
    title: str,
    content: str | None,
    author: str | None,
    published_at: datetime | None,
    fetched_at: datetime,
) -> bool:
    """Insert one item; return True if newly inserted, False if duplicate."""
    iid = item_id(source_id, canonical_url)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO items
            (id, source_id, url, raw_url, title, content, author, published_at, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            iid,
            source_id,
            canonical_url,
            raw_url,
            title,
            content,
            author,
            published_at,
            fetched_at,
        ),
    )
    return cur.rowcount > 0


def update_source_health(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    success: bool,
    error: str | None = None,
    alpha: float = 0.3,
) -> None:
    """EWMA health score update. α=0.3 by default.

    new = α * sample + (1-α) * prev, where sample ∈ {0, 1}.
    """
    cur = conn.execute("SELECT health_score FROM sources WHERE id = ?", (source_id,))
    row = cur.fetchone()
    if row is None:
        return
    prev = row["health_score"] if row["health_score"] is not None else 1.0
    sample = 1.0 if success else 0.0
    new_score = alpha * sample + (1 - alpha) * prev

    if success:
        conn.execute(
            """
            UPDATE sources
               SET health_score = ?,
                   last_success_at = CURRENT_TIMESTAMP,
                   last_error = NULL
             WHERE id = ?
            """,
            (new_score, source_id),
        )
    else:
        conn.execute(
            "UPDATE sources SET health_score = ?, last_error = ? WHERE id = ?",
            (new_score, error, source_id),
        )


# ---------- PR-A: classification + event metadata + push tracking ----------


def set_item_classification(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    kind: str,
    classified_at: datetime,
) -> None:
    """Mark an item as classified into one of: event | news | tool | other."""
    if kind not in {"event", "news", "tool", "other", "unclassified"}:
        raise ValueError(f"invalid kind: {kind}")
    conn.execute(
        "UPDATE items SET kind = ?, classified_at = ? WHERE id = ?",
        (kind, classified_at, item_id),
    )


def upsert_event_metadata(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    event_date: str | None,
    registration_deadline: str | None,
    location: str | None,
    registration_url: str | None,
    extracted_at: datetime,
) -> None:
    """Insert or replace event metadata for an item.

    Date inputs are ISO strings (YYYY-MM-DD) or None — let SQLite store as-is.
    """
    conn.execute(
        """
        INSERT INTO event_metadata
            (item_id, event_date, registration_deadline, location, registration_url, extracted_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            event_date = excluded.event_date,
            registration_deadline = excluded.registration_deadline,
            location = excluded.location,
            registration_url = excluded.registration_url,
            extracted_at = excluded.extracted_at
        """,
        (
            item_id,
            event_date,
            registration_deadline,
            location,
            registration_url,
            extracted_at,
        ),
    )


def get_unclassified_items(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """Return items still pending classification."""
    cur = conn.execute(
        """
        SELECT id, source_id, url, title, content, fetched_at
          FROM items
         WHERE kind = 'unclassified' OR kind IS NULL
         ORDER BY fetched_at DESC
         LIMIT ?
        """,
        (limit,),
    )
    return cur.fetchall()


def get_unpushed_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Events that have classifier output but haven't been pushed yet."""
    cur = conn.execute(
        """
        SELECT i.id, i.source_id, i.url, i.title, i.content, i.fetched_at,
               em.event_date, em.registration_deadline, em.location, em.registration_url
          FROM items i
          LEFT JOIN event_metadata em ON em.item_id = i.id
          LEFT JOIN event_pushes ep ON ep.item_id = i.id
         WHERE i.kind = 'event'
           AND ep.item_id IS NULL
         ORDER BY i.fetched_at DESC
        """
    )
    return cur.fetchall()


def record_event_push(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    pushed_at: datetime,
    digest_id: str | None = None,
) -> None:
    """Mark an event-item as having been pushed (idempotency key)."""
    conn.execute(
        """
        INSERT INTO event_pushes (item_id, pushed_at, digest_id)
        VALUES (?, ?, ?)
        ON CONFLICT(item_id) DO NOTHING
        """,
        (item_id, pushed_at, digest_id),
    )


def insert_digest(
    conn: sqlite3.Connection,
    *,
    digest_id: str,
    digest_date: str,
    kind: str,
    content_md: str,
) -> None:
    """Pre-write digest before network call (idempotency root)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO digests (id, digest_date, kind, content_md)
        VALUES (?, ?, ?, ?)
        """,
        (digest_id, digest_date, kind, content_md),
    )


class SqliteDetailCache:
    """SQLite-backed cache for XHS post-detail responses.

    Wraps an open sqlite3.Connection. Caller owns the connection's lifecycle
    and commit boundary; this class only issues INSERT/SELECT statements.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, feed_id: str) -> str | None:
        """Return cached content for a feed_id, or None if not cached."""
        row = self._conn.execute(
            "SELECT content FROM xhs_note_details WHERE feed_id = ?",
            (feed_id,),
        ).fetchone()
        if row is None:
            return None
        content = row["content"]
        return content if isinstance(content, str) else None

    def put(
        self,
        *,
        feed_id: str,
        xsec_token: str,
        title: str | None,
        content: str | None,
    ) -> None:
        """Insert or update detail cache entry for a feed_id."""
        self._conn.execute(
            """
            INSERT INTO xhs_note_details
                (feed_id, xsec_token, title, content, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(feed_id) DO UPDATE SET
                xsec_token = excluded.xsec_token,
                title = excluded.title,
                content = excluded.content,
                fetched_at = excluded.fetched_at
            """,
            (feed_id, xsec_token, title, content, datetime.now(UTC)),
        )


def mark_digest_pushed(
    conn: sqlite3.Connection,
    *,
    digest_id: str,
    pushed_at: datetime,
) -> None:
    conn.execute(
        """
        UPDATE digests
           SET pushed_at = ?,
               push_attempts = push_attempts + 1
         WHERE id = ?
        """,
        (pushed_at, digest_id),
    )
