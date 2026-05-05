"""Two-stage dedup for non-event items.

Stage 1: same canonical URL across sources (e.g. linux.do mirror of an XHS post).
Stage 2: trigram-similar titles within a 24h window (e.g. two outlets writing
about the same model release with slightly different headlines).

The third "LLM fallback" stage from plan-v2 is intentionally skipped — empirically
trigram catches the high-recall set and the LLM tier costs tokens per pair. Add
later if dedup quality turns out to be the bottleneck.

Pure module (no DB / no network); caller passes rows in, gets deduped rows out.
Algorithm cost: O(N) for URL stage, O(N²) within each 24h bucket for trigram —
fine for N up to ~500/day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# Char-level trigram Jaccard threshold for two titles to be considered "same story".
# 0.7 picked empirically — high enough to skip "X 发布新模型" vs "Y 发布新模型"
# (different products, low overlap) but low enough to merge "Anthropic 发布 Claude 4.7"
# vs "Claude 4.7 发布：Anthropic 新模型". Tune in production.
DEFAULT_THRESHOLD = 0.7
DEFAULT_WINDOW_HOURS = 24


@dataclass(frozen=True)
class DedupItem:
    """Subset of an item row that the dedup algorithm cares about.

    `content_len` and `published_at` are tiebreakers when picking which item
    to keep from a duplicate group: prefer longer content, then earlier
    publish (the "source of record" that others mirrored).
    """

    id: str
    title: str
    canonical_url: str
    published_at: datetime | None
    content_len: int = 0


def deduplicate(
    items: list[DedupItem],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> list[DedupItem]:
    """Return one representative per duplicate group; preserve singletons.

    Two items are considered duplicates if either:
      - they share the same `canonical_url`, OR
      - their titles have char-trigram Jaccard >= threshold AND their
        `published_at` are within `window_hours` of each other.

    Items lacking `published_at` participate only in the URL-match stage
    (we can't window them safely).
    """
    if not items:
        return []

    # Stable order: by id, so test output is deterministic.
    items = sorted(items, key=lambda i: i.id)

    parent: dict[str, str] = {it.id: it.id for it in items}

    def find(x: str) -> str:
        # Path-compressing find.
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Stage 1: URL exact match (works regardless of timestamps).
    by_url: dict[str, list[DedupItem]] = {}
    for it in items:
        by_url.setdefault(it.canonical_url, []).append(it)
    for group in by_url.values():
        if len(group) > 1:
            anchor = group[0].id
            for other in group[1:]:
                union(anchor, other.id)

    # Stage 2: trigram + 24h window. Skip items missing published_at.
    dated = [it for it in items if it.published_at is not None]
    trigrams = {it.id: _char_trigrams(it.title) for it in dated}
    window = timedelta(hours=window_hours)
    for i, a in enumerate(dated):
        for b in dated[i + 1 :]:
            assert a.published_at is not None and b.published_at is not None
            if abs(a.published_at - b.published_at) > window:
                continue
            if _jaccard(trigrams[a.id], trigrams[b.id]) >= threshold:
                union(a.id, b.id)

    # Bucket into groups, pick representative per group.
    groups: dict[str, list[DedupItem]] = {}
    for it in items:
        groups.setdefault(find(it.id), []).append(it)

    return [_pick_representative(g) for g in groups.values()]


def _pick_representative(group: list[DedupItem]) -> DedupItem:
    """Prefer the most informative item:
    1) Longest content
    2) Tie -> earliest published_at (the original; mirrors come later)
    3) Tie -> id (deterministic)
    """
    if len(group) == 1:
        return group[0]
    return min(
        group,
        key=lambda it: (
            -it.content_len,
            it.published_at or datetime.max.replace(tzinfo=None),
            it.id,
        ),
    )


def _char_trigrams(s: str) -> set[str]:
    """Char-level 3-grams. Works for Chinese (no whitespace splitting needed).

    Strings shorter than 3 chars are returned as a singleton set containing
    the original string — keeps the function total without short-circuiting.
    """
    s = s.strip()
    if len(s) < 3:
        return {s} if s else set()
    return {s[i : i + 3] for i in range(len(s) - 2)}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity. Empty/empty returns 0 (not a useful match)."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0
