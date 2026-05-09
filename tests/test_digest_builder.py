"""Unit tests for src/digest/digest_builder.py."""

from __future__ import annotations

from datetime import UTC, datetime

from digest.cluster import TopicAssignment
from digest.digest_builder import DigestItem, render_daily_digest


def _item(iid: str, title: str = "T", url: str = "https://e/x", source: str = "src") -> DigestItem:
    return DigestItem(
        item_id=iid,
        title=title,
        url=url,
        source=source,
        published_at=datetime(2026, 5, 4, tzinfo=UTC),
    )


def test_render_empty_topics() -> None:
    assert render_daily_digest([], {}, digest_date="2026-05-04") == ""


def test_render_basic_layout() -> None:
    topics = [
        TopicAssignment(name="Claude 4.7", summary="新版", item_ids=["a", "b"]),
    ]
    items = {"a": _item("a", "标题A", "https://x/a"), "b": _item("b", "标题B", "https://x/b")}
    md = render_daily_digest(topics, items, digest_date="2026-05-04")
    assert md.startswith("📰 AI 资讯日报 · 2026-05-04")
    assert "**Claude 4.7** — 新版" in md
    assert "[标题A](https://x/a)" in md
    assert "[标题B](https://x/b)" in md
    assert "`src`" in md
    assert "05-04" in md


def test_render_omits_summary_when_empty() -> None:
    topics = [TopicAssignment(name="Topic", summary="", item_ids=["a"])]
    md = render_daily_digest(topics, {"a": _item("a")}, digest_date="2026-05-04")
    assert "**Topic**" in md
    assert "**Topic** — " not in md


def test_render_caps_at_four_items_per_topic() -> None:
    topics = [TopicAssignment(name="X", summary="y", item_ids=["a", "b", "c", "d", "e", "f"])]
    items = {iid: _item(iid, title=f"T-{iid}") for iid in "abcdef"}
    md = render_daily_digest(topics, items, digest_date="2026-05-04")
    assert "T-d" in md
    assert "T-e" not in md  # spec cap: ≤ 4 items
    assert "T-f" not in md


def test_render_drops_unknown_ids_silently() -> None:
    topics = [TopicAssignment(name="X", summary="y", item_ids=["a", "ghost"])]
    md = render_daily_digest(topics, {"a": _item("a")}, digest_date="2026-05-04")
    assert "[T](https://e/x)" in md
    assert "ghost" not in md


def test_render_drops_topic_with_all_unknown_ids() -> None:
    topics = [
        TopicAssignment(name="Real", summary="", item_ids=["a"]),
        TopicAssignment(name="Phantom", summary="", item_ids=["ghost1", "ghost2"]),
    ]
    md = render_daily_digest(topics, {"a": _item("a")}, digest_date="2026-05-04")
    assert "**Real**" in md
    assert "**Phantom**" not in md


def test_render_respects_max_chars_budget() -> None:
    """Once adding the next topic would overflow, we stop. At least one topic emits."""
    long_summary = "y" * 300
    topics = [
        TopicAssignment(name=f"Topic-{i}", summary=long_summary, item_ids=[f"i{i}"])
        for i in range(5)
    ]
    items = {f"i{i}": _item(f"i{i}", title="T") for i in range(5)}
    md = render_daily_digest(topics, items, digest_date="2026-05-04", max_chars=500)
    # Hard cap not strict because we always emit at least one — but second topic
    # would have pushed us well past 500. Expect 1 topic emitted.
    assert "**Topic-0**" in md
    assert "**Topic-1**" not in md


def test_render_emits_at_least_one_topic_even_if_oversized() -> None:
    long = "x" * 1000
    topics = [TopicAssignment(name="Big", summary=long, item_ids=["a"])]
    md = render_daily_digest(topics, {"a": _item("a")}, digest_date="2026-05-04", max_chars=100)
    assert "**Big**" in md  # honored despite exceeding budget


def test_render_skips_published_date_when_missing() -> None:
    topics = [TopicAssignment(name="X", summary="", item_ids=["a"])]
    items = {
        "a": DigestItem(
            item_id="a",
            title="T",
            url="https://e/x",
            source="src",
            published_at=None,
        )
    }
    md = render_daily_digest(topics, items, digest_date="2026-05-04")
    # No MM-DD suffix expected
    line = [line for line in md.splitlines() if line.startswith("- ")][0]
    assert line.endswith("`src`")
