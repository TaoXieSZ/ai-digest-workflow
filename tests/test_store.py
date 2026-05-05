from datetime import UTC, datetime
from pathlib import Path

from digest.store import (
    init_schema,
    insert_item_if_new,
    item_id,
    open_db,
    update_source_health,
    upsert_source,
)


def make_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    init_schema(db)
    return db


def test_item_id_is_stable() -> None:
    a = item_id("linux_do", "https://linux.do/t/1")
    b = item_id("linux_do", "https://linux.do/t/1")
    assert a == b
    assert len(a) == 16


def test_item_id_differs_by_source() -> None:
    a = item_id("linux_do", "https://x.com/y")
    b = item_id("v2ex", "https://x.com/y")
    assert a != b


def test_init_schema_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    init_schema(db)
    init_schema(db)  # second call must not raise


def test_insert_item_returns_true_on_new(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    now = datetime.now(UTC)
    with open_db(db) as conn:
        upsert_source(
            conn, source_id="s1", display_name="S1", fetcher_type="rss", config_json="{}"
        )
        result = insert_item_if_new(
            conn,
            source_id="s1",
            canonical_url="https://x.com/p",
            raw_url="https://x.com/p?utm=1",
            title="T",
            content=None,
            author=None,
            published_at=None,
            fetched_at=now,
        )
    assert result is True


def test_insert_item_returns_false_on_dup(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    now = datetime.now(UTC)
    with open_db(db) as conn:
        upsert_source(
            conn, source_id="s1", display_name="S1", fetcher_type="rss", config_json="{}"
        )
        insert_item_if_new(
            conn,
            source_id="s1",
            canonical_url="https://x.com/p",
            raw_url="raw1",
            title="T",
            content=None,
            author=None,
            published_at=None,
            fetched_at=now,
        )
        dup = insert_item_if_new(
            conn,
            source_id="s1",
            canonical_url="https://x.com/p",
            raw_url="raw2",
            title="T2",
            content=None,
            author=None,
            published_at=None,
            fetched_at=now,
        )
    assert dup is False


def test_health_score_ewma_success_keeps_full_score(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with open_db(db) as conn:
        upsert_source(
            conn, source_id="s1", display_name="S1", fetcher_type="rss", config_json="{}"
        )
        update_source_health(conn, "s1", success=True)
        cur = conn.execute("SELECT health_score FROM sources WHERE id='s1'")
        assert cur.fetchone()["health_score"] == 1.0


def test_health_score_ewma_failure_decays(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with open_db(db) as conn:
        upsert_source(
            conn, source_id="s1", display_name="S1", fetcher_type="rss", config_json="{}"
        )
        # initial = 1.0; first fail -> 0.3*0 + 0.7*1.0 = 0.7
        update_source_health(conn, "s1", success=False, error="boom")
        row = conn.execute(
            "SELECT health_score, last_error FROM sources WHERE id='s1'"
        ).fetchone()
        assert abs(row["health_score"] - 0.7) < 1e-6
        assert row["last_error"] == "boom"
        # second fail -> 0.3*0 + 0.7*0.7 = 0.49
        update_source_health(conn, "s1", success=False, error="again")
        row = conn.execute("SELECT health_score FROM sources WHERE id='s1'").fetchone()
        assert abs(row["health_score"] - 0.49) < 1e-6


def test_upsert_source_updates_existing(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    with open_db(db) as conn:
        upsert_source(
            conn, source_id="s1", display_name="Old", fetcher_type="rss", config_json="{}"
        )
        upsert_source(
            conn,
            source_id="s1",
            display_name="New",
            fetcher_type="html",
            config_json='{"k":1}',
        )
        row = conn.execute(
            "SELECT display_name, fetcher_type, config_json FROM sources WHERE id='s1'"
        ).fetchone()
        assert row["display_name"] == "New"
        assert row["fetcher_type"] == "html"
        assert row["config_json"] == '{"k":1}'
