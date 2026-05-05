"""End-to-end tests for daily digest orchestration + store helpers + Feishu card."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from digest.daily_digest import CN_TZ, DigestConfig, run_daily_digest
from digest.push_feishu import render_daily_digest_card
from digest.store import (
    get_unclustered_non_event_items,
    init_schema,
    insert_item_if_new,
    open_db,
    record_item_topic_assignment,
    record_topic,
    set_item_classification,
    upsert_daily_digest,
    upsert_source,
)


@dataclass
class FakeLLM:
    """Pre-baked cluster output."""

    response: str

    def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
        return self.response


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    init_schema(db)
    return db


def _seed_classified_item(
    conn, *, source_id: str, url: str, title: str, kind: str, published_at: datetime | None = None
) -> str:
    upsert_source(
        conn,
        source_id=source_id,
        display_name=source_id,
        fetcher_type="rss",
        config_json="{}",
    )
    insert_item_if_new(
        conn,
        source_id=source_id,
        canonical_url=url,
        raw_url=url,
        title=title,
        content=f"content for {title}",
        author=None,
        published_at=published_at,
        fetched_at=datetime.now(CN_TZ),
    )
    iid = conn.execute(
        "SELECT id FROM items WHERE source_id=? AND url=?", (source_id, url)
    ).fetchone()["id"]
    set_item_classification(conn, item_id=iid, kind=kind, classified_at=datetime.now(CN_TZ))
    return iid


# ---------- store helpers ----------


def test_upsert_daily_digest_reuses_id_on_same_date(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        first = upsert_daily_digest(
            conn, digest_date="2026-05-04", candidate_id="uuid-1", content_md="md1"
        )
        second = upsert_daily_digest(
            conn, digest_date="2026-05-04", candidate_id="uuid-2", content_md="md2 updated"
        )
    assert first == "uuid-1"
    assert second == "uuid-1"  # reuses
    with open_db(db) as conn:
        row = conn.execute(
            "SELECT content_md FROM digests WHERE id=?", ("uuid-1",)
        ).fetchone()
    assert row["content_md"] == "md2 updated"  # content refreshed


def test_get_unclustered_filters_kind_and_classification(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        _seed_classified_item(conn, source_id="s", url="https://e/n", title="news", kind="news")
        _seed_classified_item(conn, source_id="s", url="https://e/t", title="tool", kind="tool")
        _seed_classified_item(conn, source_id="s", url="https://e/o", title="other", kind="other")
        _seed_classified_item(conn, source_id="s", url="https://e/e", title="event", kind="event")
        # Unclassified item — should NOT show up.
        upsert_source(conn, source_id="s", display_name="s", fetcher_type="rss", config_json="{}")
        insert_item_if_new(
            conn,
            source_id="s",
            canonical_url="https://e/u",
            raw_url="https://e/u",
            title="unclassified",
            content="x",
            author=None,
            published_at=None,
            fetched_at=datetime.now(CN_TZ),
        )

    with open_db(db) as conn:
        rows = get_unclustered_non_event_items(conn, exclude_assigned_since="2020-01-01")
    titles = {r["title"] for r in rows}
    assert titles == {"news", "tool", "other"}


def test_get_unclustered_excludes_already_assigned(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    with open_db(db) as conn:
        iid_a = _seed_classified_item(
            conn, source_id="s", url="https://e/a", title="A", kind="news"
        )
        iid_b = _seed_classified_item(
            conn, source_id="s", url="https://e/b", title="B", kind="tool"
        )
        record_topic(
            conn,
            topic_id="t1",
            name="t",
            summary="s",
            digest_date="2026-05-04",
            created_at=datetime.now(CN_TZ),
        )
        record_item_topic_assignment(
            conn, item_id=iid_a, topic_id="t1", digest_date="2026-05-04"
        )

    with open_db(db) as conn:
        rows = get_unclustered_non_event_items(conn, exclude_assigned_since="2026-04-28")
    assert {r["id"] for r in rows} == {iid_b}  # iid_a filtered out


# ---------- card render ----------


def test_render_daily_digest_card_basic() -> None:
    payload = render_daily_digest_card("- item line", digest_date="2026-05-04")
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["template"] == "green"
    assert "📰 AI 资讯日报 · 2026-05-04" == payload["card"]["header"]["title"]["content"]
    assert payload["card"]["elements"][0]["content"] == "- item line"


def test_render_daily_digest_card_rejects_empty() -> None:
    with pytest.raises(ValueError):
        render_daily_digest_card("   ", digest_date="2026-05-04")


# ---------- run_daily_digest ----------


def test_run_daily_digest_happy_path(tmp_path: Path) -> None:
    import json

    db = _make_db(tmp_path)
    pub = datetime(2026, 5, 4, 9, 0, tzinfo=UTC)
    with open_db(db) as conn:
        a = _seed_classified_item(
            conn, source_id="s", url="https://e/a", title="title-A", kind="news",
            published_at=pub,
        )
        b = _seed_classified_item(
            conn, source_id="s", url="https://e/b", title="title-B", kind="tool",
            published_at=pub + timedelta(hours=1),
        )

    llm = FakeLLM(
        response=json.dumps(
            [{"name": "Topic", "summary": "summary", "item_ids": [a, b]}]
        )
    )
    config = DigestConfig(
        feishu_webhook_url="https://feishu/x", digest_dir=tmp_path / "digests"
    )

    with patch("digest.daily_digest.push_card") as mock_push, open_db(db) as conn:
        result = run_daily_digest(
            conn, llm, config, now=datetime(2026, 5, 4, 12, 0, tzinfo=CN_TZ)
        )

    assert result.pushed is True
    assert result.candidate_items == 2
    assert result.after_dedup == 2
    assert result.topics == 1
    assert result.items_assigned == 2
    mock_push.assert_called_once()

    # DB side effects
    with open_db(db) as conn:
        n_topics = conn.execute("SELECT count(*) FROM topics").fetchone()[0]
        n_assignments = conn.execute(
            "SELECT count(*) FROM item_topic_assignments"
        ).fetchone()[0]
        n_digests = conn.execute(
            "SELECT count(*) FROM digests WHERE kind='daily_digest' AND pushed_at IS NOT NULL"
        ).fetchone()[0]
    assert n_topics == 1
    assert n_assignments == 2
    assert n_digests == 1

    # Archive file
    archive = tmp_path / "digests" / "daily-2026-05-04.md"
    assert archive.exists()
    assert "📰 AI 资讯日报" in archive.read_text(encoding="utf-8")


def test_run_daily_digest_no_candidates_returns_early(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    llm = FakeLLM(response="should not be called")
    config = DigestConfig(
        feishu_webhook_url="https://feishu/x", digest_dir=tmp_path / "digests"
    )
    with patch("digest.daily_digest.push_card") as mock_push, open_db(db) as conn:
        result = run_daily_digest(
            conn, llm, config, now=datetime(2026, 5, 4, tzinfo=CN_TZ)
        )
    assert result == result.__class__(0, 0, 0, 0, pushed=False)
    mock_push.assert_not_called()


def test_run_daily_digest_no_topics_skips_push(tmp_path: Path) -> None:
    import json

    db = _make_db(tmp_path)
    with open_db(db) as conn:
        _seed_classified_item(conn, source_id="s", url="https://e/a", title="A", kind="news")

    # LLM returns empty list — possible if all items are pure noise.
    llm = FakeLLM(response=json.dumps([]))
    config = DigestConfig(
        feishu_webhook_url="https://feishu/x", digest_dir=tmp_path / "digests"
    )
    with patch("digest.daily_digest.push_card") as mock_push, open_db(db) as conn:
        result = run_daily_digest(
            conn, llm, config, now=datetime(2026, 5, 4, tzinfo=CN_TZ)
        )
    assert result.pushed is False
    assert result.topics == 0
    mock_push.assert_not_called()


def test_run_daily_digest_same_day_repush_does_not_break_on_unique_constraint(
    tmp_path: Path,
) -> None:
    """Regression guard mirroring the event_radar fix: a second same-day call
    must not FK-error even when the first run has already inserted a digest row."""
    import json

    db = _make_db(tmp_path)
    with open_db(db) as conn:
        a = _seed_classified_item(conn, source_id="s", url="https://e/a", title="A", kind="news")

    llm = FakeLLM(
        response=json.dumps([{"name": "T", "summary": "s", "item_ids": [a]}])
    )
    config = DigestConfig(
        feishu_webhook_url="https://feishu/x", digest_dir=tmp_path / "digests"
    )
    today = datetime(2026, 5, 4, 12, 0, tzinfo=CN_TZ)

    # First push
    with patch("digest.daily_digest.push_card"), open_db(db) as conn:
        run_daily_digest(conn, llm, config, now=today)

    # New item arrives; second push same day. Old impl with INSERT OR IGNORE
    # would have FK-failed on the new digest_id.
    with open_db(db) as conn:
        b = _seed_classified_item(conn, source_id="s", url="https://e/b", title="B", kind="tool")
    llm2 = FakeLLM(
        response=json.dumps([{"name": "T2", "summary": "s", "item_ids": [b]}])
    )
    with patch("digest.daily_digest.push_card") as mock_push, open_db(db) as conn:
        result = run_daily_digest(conn, llm2, config, now=today.replace(hour=15))

    assert result.pushed is True
    mock_push.assert_called_once()
    with open_db(db) as conn:
        n_digests = conn.execute(
            "SELECT count(*) FROM digests WHERE digest_date='2026-05-04' AND kind='daily_digest'"
        ).fetchone()[0]
    assert n_digests == 1  # upsert reused the row
