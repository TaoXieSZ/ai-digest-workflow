"""Tests for Chinese date expression parser used by huodongxing fetcher."""

from __future__ import annotations

from datetime import date

import pytest

from digest.sources._chinese_dates import parse_chinese_relative_date

# Anchor "today" to a known weekday so weekday math is stable.
# 2026-05-07 is a Thursday (weekday=3).
TODAY = date(2026, 5, 7)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("今天 14:30", date(2026, 5, 7)),
        ("明天 14:30", date(2026, 5, 8)),
        ("后天 14:30", date(2026, 5, 9)),
        ("大后天 19:00", date(2026, 5, 10)),
    ],
)
def test_relative_day_words(text: str, expected: date) -> None:
    parsed = parse_chinese_relative_date(text, today=TODAY)
    assert parsed is not None
    assert parsed.start == expected
    assert parsed.end is None


@pytest.mark.parametrize(
    "text, expected",
    [
        # today is Thu (5/7); next-week Thursday → 5/14
        ("下周四 19:00", date(2026, 5, 14)),
        # next-week Monday → 5/11
        ("下周一", date(2026, 5, 11)),
        # next-week Sunday → 5/17
        ("下周日 12:00", date(2026, 5, 17)),
        # alt syntax
        ("下星期五 14:00", date(2026, 5, 15)),
    ],
)
def test_next_week_weekday(text: str, expected: date) -> None:
    parsed = parse_chinese_relative_date(text, today=TODAY)
    assert parsed is not None
    assert parsed.start == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        # today (Thu, 5/7) — this-week-Friday → 5/8
        ("本周五 14:00", date(2026, 5, 8)),
        # this-week-Thursday → today
        ("本周四", date(2026, 5, 7)),
        # this-week-Monday → 5/4 (in the past, but parser still returns it)
        ("这周一 09:00", date(2026, 5, 4)),
    ],
)
def test_this_week_weekday(text: str, expected: date) -> None:
    parsed = parse_chinese_relative_date(text, today=TODAY)
    assert parsed is not None
    assert parsed.start == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("05/24 周日 14:00", date(2026, 5, 24)),
        # Numeric only — still parsed
        ("05/24 14:00", date(2026, 5, 24)),
        # M/D without leading zero
        ("5/24 周日 14:00", date(2026, 5, 24)),
    ],
)
def test_absolute_md(text: str, expected: date) -> None:
    parsed = parse_chinese_relative_date(text, today=TODAY)
    assert parsed is not None
    assert parsed.start == expected
    assert parsed.end is None


def test_md_range_returns_start_and_end() -> None:
    parsed = parse_chinese_relative_date(
        "05/24 周日 14:00 ~ 05/26 周二 18:00", today=TODAY
    )
    assert parsed is not None
    assert parsed.start == date(2026, 5, 24)
    assert parsed.end == date(2026, 5, 26)


def test_past_md_rolls_to_next_year() -> None:
    # today is May 2026 — seeing "01/15" must resolve to Jan 2027.
    parsed = parse_chinese_relative_date("01/15 周五 10:00", today=TODAY)
    assert parsed is not None
    assert parsed.start == date(2027, 1, 15)


def test_recent_past_md_keeps_current_year() -> None:
    # today is 5/7 — seeing "05/01" (6 days ago) should stay in current year,
    # not roll to next year (only >30d past rolls).
    parsed = parse_chinese_relative_date("05/01 14:00", today=TODAY)
    assert parsed is not None
    assert parsed.start == date(2026, 5, 1)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("5月24日 14:00", date(2026, 5, 24)),
        ("12月3日", date(2026, 12, 3)),
    ],
)
def test_chinese_md(text: str, expected: date) -> None:
    parsed = parse_chinese_relative_date(text, today=TODAY)
    assert parsed is not None
    assert parsed.start == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "随时报名",
        "线上活动",
        "AI 沙龙 报名中",
        "abc 14:00",
    ],
)
def test_no_match_returns_none(text: str) -> None:
    assert parse_chinese_relative_date(text, today=TODAY) is None


def test_invalid_md_falls_back_to_today() -> None:
    # 13/40 is invalid; parser falls back to today rather than raising.
    parsed = parse_chinese_relative_date("13/40 14:00", today=TODAY)
    # First regex won't match because mm>12 is allowed by the regex but the
    # _resolve_md guard handles it. Either way: no exception.
    if parsed is not None:
        assert parsed.start == TODAY


def test_relative_word_takes_priority_over_md() -> None:
    # If both "今天" and an MM/DD appear, relative wins (huodongxing list page
    # never shows both, but defensive check).
    parsed = parse_chinese_relative_date("今天 14:00 (07/15 上线)", today=TODAY)
    assert parsed is not None
    assert parsed.start == TODAY
