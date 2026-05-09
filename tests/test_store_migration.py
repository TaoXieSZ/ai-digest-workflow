"""Migration tests: simulate a pre-PR-A db and confirm ALTER TABLE adds new cols."""

import sqlite3
from pathlib import Path

from digest.store import init_schema, open_db

# Pre-PR-A schema (matches PR1 commit, no kind/classified_at/event_metadata).
LEGACY_SCHEMA = """
CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    fetcher_type TEXT NOT NULL,
    config_json TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    health_score REAL DEFAULT 1.0,
    last_success_at TIMESTAMP,
    last_error TEXT
);
CREATE TABLE items (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id),
    url TEXT NOT NULL,
    raw_url TEXT,
    title TEXT,
    content TEXT,
    author TEXT,
    published_at TIMESTAMP,
    fetched_at TIMESTAMP NOT NULL,
    UNIQUE(source_id, url)
);
CREATE TABLE topics (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    summary TEXT,
    digest_date DATE NOT NULL,
    created_at TIMESTAMP NOT NULL
);
CREATE TABLE item_topic_assignments (
    item_id TEXT NOT NULL REFERENCES items(id),
    topic_id TEXT NOT NULL REFERENCES topics(id),
    digest_date DATE NOT NULL,
    PRIMARY KEY (item_id, topic_id, digest_date)
);
CREATE TABLE digests (
    id TEXT PRIMARY KEY,
    digest_date DATE NOT NULL UNIQUE,
    content_md TEXT NOT NULL,
    pushed_at TIMESTAMP,
    archived_at TIMESTAMP,
    push_attempts INTEGER DEFAULT 0,
    archive_attempts INTEGER DEFAULT 0
);
"""


def _create_legacy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.commit()
    conn.close()


def _columns(db: Path, table: str) -> set[str]:
    with open_db(db) as conn:
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_init_schema_migrates_legacy_db(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    _create_legacy_db(db)

    # Pre-conditions
    assert "kind" not in _columns(db, "items")
    assert "classified_at" not in _columns(db, "items")

    init_schema(db)

    # Post-conditions
    items_cols = _columns(db, "items")
    assert "kind" in items_cols
    assert "classified_at" in items_cols
    assert "kind" in _columns(db, "digests")


def test_init_schema_creates_event_tables(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    _create_legacy_db(db)
    init_schema(db)

    with open_db(db) as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    assert "event_metadata" in tables
    assert "event_pushes" in tables


def test_init_schema_is_idempotent_on_migrated_db(tmp_path: Path) -> None:
    """Running init twice on an already-migrated db must not error."""
    db = tmp_path / "legacy.db"
    _create_legacy_db(db)
    init_schema(db)
    init_schema(db)  # second call


def test_existing_data_survives_migration(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    _create_legacy_db(db)

    # Insert legacy row
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO sources (id, display_name, fetcher_type, config_json) "
        "VALUES ('s1','S1','rss','{}')"
    )
    conn.execute(
        """
        INSERT INTO items
            (id, source_id, url, raw_url, title, content,
             author, published_at, fetched_at)
        VALUES
            ('abc', 's1', 'https://e.com/x', 'https://e.com/x',
             'old title', NULL, NULL, NULL, '2026-04-01')
        """
    )
    conn.commit()
    conn.close()

    init_schema(db)

    with open_db(db) as conn:
        row = conn.execute("SELECT * FROM items WHERE id='abc'").fetchone()

    assert row is not None
    assert row["title"] == "old title"
    assert row["kind"] == "unclassified"  # default applied to existing rows


def test_init_schema_adds_registration_contact_to_legacy_event_metadata(
    tmp_path: Path,
) -> None:
    """Pre-existing event_metadata table without registration_contact column
    should get the column added by ALTER TABLE on init_schema."""
    db = tmp_path / "legacy_em.db"
    _create_legacy_db(db)

    # Simulate an event_metadata table that pre-dates the registration_contact addition.
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE event_metadata (
            item_id TEXT PRIMARY KEY,
            event_date DATE,
            registration_deadline DATE,
            location TEXT,
            registration_url TEXT,
            extracted_at TIMESTAMP NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    assert "registration_contact" not in _columns(db, "event_metadata")

    init_schema(db)

    assert "registration_contact" in _columns(db, "event_metadata")
