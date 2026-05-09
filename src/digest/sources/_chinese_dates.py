"""Parse Chinese relative/absolute date expressions used by 活动行 list pages.

These pages render a date+time string like:
    "今天 14:30"
    "明天 14:30"
    "后天 14:30"
    "下周三 19:00"
    "05/24 周日 14:00"
    "05/24 周日 14:00 ~ 05/26 周二 18:00"
    "5月24日 14:00"

We need to normalize these to an ISO date (`YYYY-MM-DD`) for the existing
event_date pipeline. We deliberately ignore the time-of-day portion (caller
keeps the raw text in `content` so downstream LLM still sees it).

Returns ``None`` if no recognizable pattern matches — caller can fall back to
storing the raw text and letting the downstream classifier try.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

# 周一=0, 周二=1, ..., 周日=6  (matches `date.weekday()`)
_WEEKDAY_TO_INT = {
    "周一": 0,
    "周二": 1,
    "周三": 2,
    "周四": 3,
    "周五": 4,
    "周六": 5,
    "周日": 6,
    "星期一": 0,
    "星期二": 1,
    "星期三": 2,
    "星期四": 3,
    "星期五": 4,
    "星期六": 5,
    "星期日": 6,
}


@dataclass(frozen=True)
class ParsedDate:
    """Result of date extraction."""

    start: date
    end: date | None  # populated for ranges like "MM/DD ~ MM/DD"
    raw: str  # the original snippet we matched against


def parse_chinese_relative_date(text: str, *, today: date) -> ParsedDate | None:
    """Best-effort parse. Returns None if nothing matches."""
    if not text:
        return None
    s = text.strip()

    # 1. Relative day words. Order matters — longer tokens first to avoid the
    # "大后天" → "后天" substring trap.
    for token, offset in (("大后天", 3), ("后天", 2), ("明天", 1), ("今天", 0)):
        if token in s:
            return ParsedDate(start=today + timedelta(days=offset), end=None, raw=s)

    # 2. "下周X" / "下星期X" → next occurrence of weekday X (strictly after this week).
    m = re.search(r"下周([一二三四五六日])|下星期([一二三四五六日])", s)
    if m:
        wd_chr = m.group(1) or m.group(2)
        target_wd = _WEEKDAY_TO_INT[f"周{wd_chr}"]
        # days until next week's weekday: today's weekday → next-week's weekday
        days_to_next = 7 - today.weekday() + target_wd
        return ParsedDate(start=today + timedelta(days=days_to_next), end=None, raw=s)

    # 3. "本周X" / "这周X" → this week's weekday (could be in the past; we still
    # return it for completeness — caller can choose to drop past dates).
    m = re.search(r"本周([一二三四五六日])|这周([一二三四五六日])", s)
    if m:
        wd_chr = m.group(1) or m.group(2)
        target_wd = _WEEKDAY_TO_INT[f"周{wd_chr}"]
        days_diff = target_wd - today.weekday()
        return ParsedDate(start=today + timedelta(days=days_diff), end=None, raw=s)

    # 4. Absolute "MM/DD" pattern (huodongxing's most common). Optionally with
    # a "~ MM/DD" range and a weekday hint.
    range_match = re.search(
        r"(\d{1,2})/(\d{1,2})(?:\s*周[一二三四五六日])?\s*\d{1,2}:\d{2}\s*~\s*(\d{1,2})/(\d{1,2})",
        s,
    )
    if range_match:
        mm1, dd1, mm2, dd2 = (int(g) for g in range_match.groups())
        start = _resolve_md(today, mm1, dd1)
        end = _resolve_md(today, mm2, dd2)
        return ParsedDate(start=start, end=end, raw=s)

    single_md = re.search(r"(\d{1,2})/(\d{1,2})", s)
    if single_md:
        mm, dd = int(single_md.group(1)), int(single_md.group(2))
        return ParsedDate(start=_resolve_md(today, mm, dd), end=None, raw=s)

    # 5. "M月D日" pattern (less common on huodongxing list pages but appears in
    # detail-rich titles).
    cn_md = re.search(r"(\d{1,2})月(\d{1,2})日", s)
    if cn_md:
        mm, dd = int(cn_md.group(1)), int(cn_md.group(2))
        return ParsedDate(start=_resolve_md(today, mm, dd), end=None, raw=s)

    return None


def _resolve_md(today: date, mm: int, dd: int) -> date:
    """Resolve a (month, day) into a full date.

    If MM/DD is in the past relative to today, assume next year (e.g. seeing
    "01/15" in November means Jan 15 next year). Otherwise stay in current year.
    """
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        # invalid input — fall back to today to avoid raising
        return today
    try:
        candidate = date(today.year, mm, dd)
    except ValueError:
        # e.g. 02/30 — give up gracefully
        return today
    # Heuristic: if MM/DD is more than 30 days in the past, assume next year.
    if (today - candidate).days > 30:
        try:
            candidate = date(today.year + 1, mm, dd)
        except ValueError:
            pass
    return candidate
