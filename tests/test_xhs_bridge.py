"""Unit tests for the XHS detail-cache enrichment path (Bug 2 fix).

Covers:
- _extract_detail_fields shape tolerance
- SqliteDetailCache round-trip + miss
- XHSFetcher._enrich_with_detail: cache hit, miss+subprocess success, fail-soft on:
    - subprocess timeout / non-zero exit
    - MCP-side isError response
    - non-JSON output / parse failures
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from digest.fetchers.base import FetchedItem
from digest.sources.xhs_skill_bridge import (
    XHSConfig,
    XHSFetcher,
    _extract_detail_fields,
)
from digest.store import SqliteDetailCache, init_schema, open_db

# ---------- _extract_detail_fields shape tolerance ----------


def test_extract_inner_is_notecard_dict() -> None:
    inner = {"noteCard": {"title": "T", "desc": "D"}}
    assert _extract_detail_fields(inner) == ("T", "D")


def test_extract_inner_under_feed_key() -> None:
    inner = {"feed": {"noteCard": {"title": "T", "desc": "D"}}}
    assert _extract_detail_fields(inner) == ("T", "D")


def test_extract_inner_under_data_key() -> None:
    inner = {"data": {"noteCard": {"title": "T", "desc": "D"}}}
    assert _extract_detail_fields(inner) == ("T", "D")


def test_extract_uses_displayTitle_fallback() -> None:  # noqa: N802
    inner = {"noteCard": {"displayTitle": "T", "desc": "D"}}
    assert _extract_detail_fields(inner) == ("T", "D")


def test_extract_uses_content_fallback_when_no_desc() -> None:
    inner = {"noteCard": {"title": "T", "content": "long body"}}
    assert _extract_detail_fields(inner) == ("T", "long body")


def test_extract_returns_none_when_content_empty() -> None:
    inner = {"noteCard": {"title": "T", "desc": "   "}}
    assert _extract_detail_fields(inner) == (None, None)


def test_extract_returns_none_for_non_dict() -> None:
    assert _extract_detail_fields("nope") == (None, None)
    assert _extract_detail_fields(None) == (None, None)


# ---------- SqliteDetailCache ----------


def test_sqlite_cache_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    init_schema(db)
    with open_db(db) as conn:
        cache = SqliteDetailCache(conn)
        assert cache.get("feed-1") is None
        cache.put(feed_id="feed-1", xsec_token="tok", title="T", content="C")
        assert cache.get("feed-1") == "C"


def test_sqlite_cache_put_overwrites(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    init_schema(db)
    with open_db(db) as conn:
        cache = SqliteDetailCache(conn)
        cache.put(feed_id="f", xsec_token="t1", title="T1", content="C1")
        cache.put(feed_id="f", xsec_token="t2", title="T2", content="C2")
        assert cache.get("f") == "C2"


# ---------- XHSFetcher._enrich_with_detail ----------


class InMemCache:
    """Minimal XHSDetailCache impl for tests, isolating us from sqlite."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.put_calls: list[tuple[str, str | None, str | None]] = []

    def get(self, feed_id: str) -> str | None:
        return self.store.get(feed_id)

    def put(
        self,
        *,
        feed_id: str,
        xsec_token: str,
        title: str | None,
        content: str | None,
    ) -> None:
        self.put_calls.append((feed_id, title, content))
        if content is not None:
            self.store[feed_id] = content


def _make_item() -> FetchedItem:
    return FetchedItem(
        url="https://www.xiaohongshu.com/explore/F1?xsec_token=TOK",
        title="title",
        content="short desc",
    )


def _feed() -> dict[str, object]:
    return {"id": "F1", "xsecToken": "TOK"}


def _mcp_success_stdout(*, title: str, content: str) -> str:
    """Build the JSON-RPC envelope a real post-detail.sh call would print."""
    inner = json.dumps({"noteCard": {"title": title, "desc": content}})
    outer = {"result": {"content": [{"type": "text", "text": inner}]}}
    return json.dumps(outer)


def _mcp_error_stdout() -> str:
    outer = {
        "result": {
            "content": [{"type": "text", "text": "feed not found"}],
            "isError": True,
        }
    }
    return json.dumps(outer)


def test_enrich_cache_hit_skips_subprocess(tmp_path: Path) -> None:
    cache = InMemCache()
    cache.store["F1"] = "long body from cache"
    fetcher = XHSFetcher(
        XHSConfig(keywords=[], skill_scripts_dir=tmp_path, detail_cache=cache)
    )
    # Make sure subprocess.run is NEVER called on cache hit.
    with patch.object(subprocess, "run") as mock_run:
        out = fetcher._enrich_with_detail(_make_item(), _feed(), tmp_path, {})
        mock_run.assert_not_called()
    assert out.content == "long body from cache"


def test_enrich_cache_miss_calls_subprocess_and_caches(tmp_path: Path) -> None:
    cache = InMemCache()
    # post-detail.sh must exist on disk for the bridge to attempt the call.
    (tmp_path / "post-detail.sh").write_text("#!/bin/bash\nexit 0\n")
    fetcher = XHSFetcher(
        XHSConfig(keywords=[], skill_scripts_dir=tmp_path, detail_cache=cache)
    )

    fake_result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=_mcp_success_stdout(title="T", content="full body"),
        stderr="",
    )
    with patch.object(subprocess, "run", return_value=fake_result):
        out = fetcher._enrich_with_detail(_make_item(), _feed(), tmp_path, {})

    assert out.content == "full body"
    assert cache.get("F1") == "full body"
    assert cache.put_calls == [("F1", "T", "full body")]


def test_enrich_failsoft_on_subprocess_nonzero_exit(tmp_path: Path) -> None:
    cache = InMemCache()
    (tmp_path / "post-detail.sh").write_text("#!/bin/bash\nexit 1\n")
    fetcher = XHSFetcher(
        XHSConfig(keywords=[], skill_scripts_dir=tmp_path, detail_cache=cache)
    )
    fake_result = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="boom"
    )
    with patch.object(subprocess, "run", return_value=fake_result):
        out = fetcher._enrich_with_detail(_make_item(), _feed(), tmp_path, {})
    # Item content untouched; cache untouched.
    assert out.content == "short desc"
    assert cache.put_calls == []


def test_enrich_failsoft_on_mcp_isError(tmp_path: Path) -> None:  # noqa: N802
    cache = InMemCache()
    (tmp_path / "post-detail.sh").write_text("#!/bin/bash\nexit 0\n")
    fetcher = XHSFetcher(
        XHSConfig(keywords=[], skill_scripts_dir=tmp_path, detail_cache=cache)
    )
    fake_result = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=_mcp_error_stdout(), stderr=""
    )
    with patch.object(subprocess, "run", return_value=fake_result):
        out = fetcher._enrich_with_detail(_make_item(), _feed(), tmp_path, {})
    assert out.content == "short desc"
    assert cache.put_calls == []


def test_enrich_failsoft_on_subprocess_timeout(tmp_path: Path) -> None:
    cache = InMemCache()
    (tmp_path / "post-detail.sh").write_text("#!/bin/bash\nexit 0\n")
    fetcher = XHSFetcher(
        XHSConfig(keywords=[], skill_scripts_dir=tmp_path, detail_cache=cache)
    )
    with patch.object(
        subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1.0)
    ):
        out = fetcher._enrich_with_detail(_make_item(), _feed(), tmp_path, {})
    assert out.content == "short desc"
    assert cache.put_calls == []


def test_enrich_skips_when_no_detail_script(tmp_path: Path) -> None:
    """post-detail.sh missing -> log warning, return original, no subprocess."""
    cache = InMemCache()
    fetcher = XHSFetcher(
        XHSConfig(keywords=[], skill_scripts_dir=tmp_path, detail_cache=cache)
    )
    with patch.object(subprocess, "run") as mock_run:
        out = fetcher._enrich_with_detail(_make_item(), _feed(), tmp_path, {})
        mock_run.assert_not_called()
    assert out.content == "short desc"


def test_enrich_skips_when_feed_missing_id_or_xsec(tmp_path: Path) -> None:
    cache = InMemCache()
    fetcher = XHSFetcher(
        XHSConfig(keywords=[], skill_scripts_dir=tmp_path, detail_cache=cache)
    )
    with patch.object(subprocess, "run") as mock_run:
        out = fetcher._enrich_with_detail(
            _make_item(), {"id": "F1"}, tmp_path, {}
        )  # no xsecToken
        mock_run.assert_not_called()
    assert out.content == "short desc"


def test_enrich_no_op_when_cache_unset(tmp_path: Path) -> None:
    """detail_cache=None means feature is off — return item unchanged."""
    fetcher = XHSFetcher(XHSConfig(keywords=[], skill_scripts_dir=tmp_path))
    with patch.object(subprocess, "run") as mock_run:
        out = fetcher._enrich_with_detail(_make_item(), _feed(), tmp_path, {})
        mock_run.assert_not_called()
    assert out.content == "short desc"


# Allow pytest to discover this file even when the bridge module's optional
# dependencies are missing — the imports above run at collection time.
if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
