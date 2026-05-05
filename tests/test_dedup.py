"""Unit tests for src/digest/dedup.py.

Covers both stages (URL exact, trigram + 24h window), tiebreakers for
representative selection, and degenerate inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from digest.dedup import (
    DEFAULT_THRESHOLD,
    DedupItem,
    _char_trigrams,
    _jaccard,
    deduplicate,
)


def _item(
    id: str,
    title: str,
    *,
    url: str | None = None,
    published: datetime | None = None,
    content_len: int = 100,
) -> DedupItem:
    return DedupItem(
        id=id,
        title=title,
        canonical_url=url or f"https://e.com/{id}",
        published_at=published,
        content_len=content_len,
    )


# ---------- trigram + jaccard primitives ----------


def test_char_trigrams_basic() -> None:
    assert _char_trigrams("abcd") == {"abc", "bcd"}
    assert _char_trigrams("a") == {"a"}
    assert _char_trigrams("") == set()


def test_char_trigrams_chinese() -> None:
    # No whitespace splitting needed for CJK.
    tg = _char_trigrams("深圳AI黑客松")
    assert "深圳A" in tg
    assert "I黑客" in tg


def test_jaccard_identical() -> None:
    assert _jaccard({"abc", "bcd"}, {"abc", "bcd"}) == 1.0


def test_jaccard_disjoint() -> None:
    assert _jaccard({"abc"}, {"xyz"}) == 0.0


def test_jaccard_empty_either_side() -> None:
    assert _jaccard(set(), {"abc"}) == 0.0
    assert _jaccard({"abc"}, set()) == 0.0


# ---------- deduplicate: edges ----------


def test_deduplicate_empty_input() -> None:
    assert deduplicate([]) == []


def test_deduplicate_singletons_pass_through() -> None:
    items = [_item("a", "Topic A"), _item("b", "Topic B unrelated")]
    out = deduplicate(items)
    assert {it.id for it in out} == {"a", "b"}


# ---------- stage 1: URL exact match ----------


def test_dedup_same_url_across_sources_collapses() -> None:
    """Two items with the same canonical URL → one representative."""
    items = [
        _item("a1", "title from source 1", url="https://canon/x"),
        _item("a2", "title from source 2", url="https://canon/x"),
    ]
    out = deduplicate(items)
    assert len(out) == 1


def test_dedup_url_match_ignores_window() -> None:
    """URL match works even when published_at is far apart or missing."""
    old = datetime(2026, 1, 1, tzinfo=UTC)
    now = datetime(2026, 5, 1, tzinfo=UTC)
    items = [
        _item("old", "x", url="https://c/y", published=old),
        _item("new", "y", url="https://c/y", published=now),
    ]
    assert len(deduplicate(items)) == 1


# ---------- stage 2: trigram + 24h window ----------


def test_dedup_similar_titles_within_window_collapse() -> None:
    t = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    items = [
        _item(
            "a",
            "Anthropic 发布 Claude Opus 4.7 详细解读",
            url="https://x/1",
            published=t,
        ),
        _item(
            "b",
            "Anthropic 发布 Claude Opus 4.7 详细解读 (转载)",
            url="https://x/2",
            published=t + timedelta(hours=2),
        ),
    ]
    out = deduplicate(items)
    assert len(out) == 1


def test_dedup_similar_titles_outside_window_kept_apart() -> None:
    """Same title 30h apart → treated as separate stories (re-coverage)."""
    t = datetime(2026, 5, 1, tzinfo=UTC)
    items = [
        _item("a", "OpenAI 发布 GPT-X 模型", url="https://x/1", published=t),
        _item(
            "b",
            "OpenAI 发布 GPT-X 模型",
            url="https://x/2",
            published=t + timedelta(hours=30),
        ),
    ]
    assert len(deduplicate(items)) == 2


def test_dedup_low_similarity_not_grouped() -> None:
    """Two unrelated titles, same time → not grouped."""
    t = datetime(2026, 5, 1, tzinfo=UTC)
    items = [
        _item("a", "Anthropic 发布新模型 Claude 4.7", url="https://x/1", published=t),
        _item("b", "Cursor 推出 1.5 版本带 Agent 模式", url="https://x/2", published=t),
    ]
    assert len(deduplicate(items)) == 2


def test_dedup_threshold_boundary() -> None:
    """At exactly the threshold, items merge (>= comparison)."""
    t = datetime(2026, 5, 1, tzinfo=UTC)
    # Construct two strings whose trigram Jaccard >= 0.7
    items = [
        _item("a", "深圳 AI 黑客松开赛通知", url="https://x/1", published=t),
        _item("b", "深圳 AI 黑客松开赛通知！", url="https://x/2", published=t),
    ]
    out = deduplicate(items, threshold=DEFAULT_THRESHOLD)
    assert len(out) == 1


def test_dedup_no_published_at_skipped_for_trigram() -> None:
    """Items without published_at don't participate in trigram stage."""
    items = [
        _item("a", "OpenAI 发布 GPT-X", url="https://x/1", published=None),
        _item("b", "OpenAI 发布 GPT-X", url="https://x/2", published=None),
    ]
    # No URL match, no window → both kept.
    assert len(deduplicate(items)) == 2


# ---------- transitivity ----------


def test_dedup_transitive_grouping() -> None:
    """Pairwise-similar titles → all merged via union-find."""
    t = datetime(2026, 5, 1, tzinfo=UTC)
    # All three share the long base "Anthropic 发布 Claude 4.7 模型详细解读" — pairwise
    # Jaccard of their char-trigrams is well above 0.7.
    items = [
        _item(
            "a",
            "Anthropic 发布 Claude 4.7 模型详细解读",
            url="https://x/1",
            published=t,
        ),
        _item(
            "b",
            "Anthropic 发布 Claude 4.7 模型详细解读 (转载)",
            url="https://x/2",
            published=t + timedelta(hours=1),
        ),
        _item(
            "c",
            "Anthropic 发布 Claude 4.7 模型详细解读 (机器之心)",
            url="https://x/3",
            published=t + timedelta(hours=2),
        ),
    ]
    out = deduplicate(items)
    assert len(out) == 1


# ---------- representative selection ----------


def test_dedup_picks_longest_content_as_representative() -> None:
    t = datetime(2026, 5, 1, tzinfo=UTC)
    items = [
        _item("short", "Topic X 报道", url="https://c/x", published=t, content_len=50),
        _item("long", "Topic X 报道", url="https://c/x", published=t, content_len=2000),
    ]
    out = deduplicate(items)
    assert len(out) == 1
    assert out[0].id == "long"


def test_dedup_ties_break_by_earliest_published() -> None:
    early = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
    late = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    items = [
        _item("late", "X", url="https://c/x", published=late, content_len=100),
        _item("early", "X", url="https://c/x", published=early, content_len=100),
    ]
    out = deduplicate(items)
    assert out[0].id == "early"
