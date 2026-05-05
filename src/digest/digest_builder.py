"""Render a clustered topic list into the daily digest markdown.

Pure module: takes TopicAssignments + per-id item metadata, returns the digest
markdown. The orchestration (which items to feed in, where to push the result)
lives in `daily_digest.py`.

Acceptance per spec:
- 3-5 topics, 2-4 items per topic
- ≤ 500 chars total (hard cap; truncate items beyond budget)
- Each item: title (link to original), source, MM-DD published date
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .cluster import TopicAssignment

DEFAULT_MAX_CHARS = 500


@dataclass(frozen=True)
class DigestItem:
    """Per-item metadata needed to render a digest line. Keyed by `item_id`."""

    item_id: str
    title: str
    url: str
    source: str
    published_at: datetime | None


def render_daily_digest(
    topics: list[TopicAssignment],
    item_lookup: dict[str, DigestItem],
    *,
    digest_date: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Render a markdown digest. Returns the full markdown string.

    Header is included to keep the digest self-contained when pasted into
    notion / wiki / chat. The caller can wrap or split as needed.
    Items whose ids aren't in `item_lookup` are silently dropped.

    Char budget enforcement: build the body topic-by-topic; once adding the
    next topic would push past `max_chars`, stop. Within a topic, items beyond
    `4` are truncated regardless of budget (per spec).
    """
    if not topics:
        return ""

    header = f"📰 AI 资讯日报 · {digest_date}"
    body_chunks: list[str] = []
    so_far = len(header) + 2  # +2 for "\n\n" before first topic

    for topic in topics:
        # Build candidate chunk for this topic, then check budget.
        chunk_lines: list[str] = []
        title = f"**{topic.name}**"
        if topic.summary:
            title = f"**{topic.name}** — {topic.summary}"
        chunk_lines.append(title)

        for iid in topic.item_ids[:4]:  # spec cap: ≤ 4 items per topic
            it = item_lookup.get(iid)
            if it is None:
                continue
            line_parts: list[str] = [f"- [{it.title}]({it.url})"]
            line_parts.append(f"`{it.source}`")
            if it.published_at is not None:
                line_parts.append(it.published_at.strftime("%m-%d"))
            chunk_lines.append(" · ".join(line_parts))

        # Skip topics that ended up empty (all ids dropped from lookup).
        if len(chunk_lines) == 1:
            continue

        chunk = "\n".join(chunk_lines)
        if so_far + len(chunk) + 2 > max_chars and body_chunks:
            # Budget exhausted; stop here. The "and body_chunks" guard ensures
            # we always emit at least one topic even if it overshoots a tight
            # budget — empty digests are useless.
            break
        body_chunks.append(chunk)
        so_far += len(chunk) + 2

    if not body_chunks:
        return ""

    return header + "\n\n" + "\n\n".join(body_chunks)
