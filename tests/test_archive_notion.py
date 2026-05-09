"""Unit tests for src/digest/archive_notion.py.

All Notion calls are mocked; we never hit api.notion.com from tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from digest.archive_notion import (
    ArchiveItem,
    NotionArchiveError,
    NotionClient,
    _build_payload,
    _rich_text,
    _trim,
    archive_items,
)


def _item(**overrides: object) -> ArchiveItem:
    base: dict[str, str] = {
        "item_id": "i1",
        "title": "Hello world",
        "kind": "news",
        "url": "https://e.com/1",
        "source": "linux_do",
        "summary": "summary text",
        "topic": "Topic A",
        "digest_date": "2026-05-04",
    }
    base.update(overrides)  # type: ignore[arg-type]
    return ArchiveItem(**base)  # type: ignore[arg-type]


def _ok_resp(page_id: str = "page-uuid-1") -> httpx.Response:
    return httpx.Response(200, json={"id": page_id, "object": "page"})


# ---------- payload builder ----------


def test_build_payload_includes_all_properties() -> None:
    payload = _build_payload("db-1", _item())
    assert payload["parent"] == {"database_id": "db-1"}
    p = payload["properties"]
    assert p["Title"]["title"][0]["text"]["content"] == "Hello world"
    assert p["kind"]["select"]["name"] == "news"
    assert p["digest_date"]["date"]["start"] == "2026-05-04"
    assert p["url"]["url"] == "https://e.com/1"
    assert p["source"]["rich_text"][0]["text"]["content"] == "linux_do"
    assert p["summary"]["rich_text"][0]["text"]["content"] == "summary text"
    assert p["topic"]["rich_text"][0]["text"]["content"] == "Topic A"


def test_build_payload_empty_topic_yields_empty_rich_text() -> None:
    payload = _build_payload("db-1", _item(topic=""))
    assert payload["properties"]["topic"]["rich_text"] == []


def test_build_payload_omits_kind_when_empty() -> None:
    payload = _build_payload("db-1", _item(kind=""))
    assert "kind" not in payload["properties"]


def test_build_payload_url_empty_becomes_null() -> None:
    payload = _build_payload("db-1", _item(url=""))
    assert payload["properties"]["url"]["url"] is None


# ---------- helpers ----------


def test_trim_caps_at_2000() -> None:
    assert len(_trim("x" * 5000)) == 2000


def test_rich_text_empty_string() -> None:
    assert _rich_text("") == {"rich_text": []}


def test_rich_text_truncates() -> None:
    rt = _rich_text("y" * 3000)
    assert len(rt["rich_text"][0]["text"]["content"]) == 2000


# ---------- NotionClient ----------


def test_create_page_success_returns_id() -> None:
    client = NotionClient(token="t", database_id="db-1")
    with patch("digest.archive_notion.httpx.post", return_value=_ok_resp("page-x")):
        page_id = client.create_page(_item())
    assert page_id == "page-x"


def test_create_page_sends_correct_headers() -> None:
    client = NotionClient(token="my-token", database_id="db-1")
    with patch("digest.archive_notion.httpx.post", return_value=_ok_resp()) as mock_post:
        client.create_page(_item())
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer my-token"
    assert kwargs["headers"]["Notion-Version"] == "2022-06-28"


def test_create_page_4xx_raises() -> None:
    client = NotionClient(token="t", database_id="db-1")
    with patch(
        "digest.archive_notion.httpx.post",
        return_value=httpx.Response(400, text="bad request"),
    ):
        with pytest.raises(NotionArchiveError, match="http 400"):
            client.create_page(_item())


def test_create_page_5xx_raises() -> None:
    client = NotionClient(token="t", database_id="db-1")
    with patch(
        "digest.archive_notion.httpx.post",
        return_value=httpx.Response(500, text="server error"),
    ):
        with pytest.raises(NotionArchiveError, match="http 500"):
            client.create_page(_item())


def test_create_page_network_error_raises() -> None:
    client = NotionClient(token="t", database_id="db-1")
    with patch(
        "digest.archive_notion.httpx.post",
        side_effect=httpx.ConnectError("dns fail"),
    ):
        with pytest.raises(NotionArchiveError, match="network error"):
            client.create_page(_item())


def test_create_page_missing_id_raises() -> None:
    client = NotionClient(token="t", database_id="db-1")
    with patch(
        "digest.archive_notion.httpx.post",
        return_value=httpx.Response(200, json={"object": "page"}),  # no id
    ):
        with pytest.raises(NotionArchiveError, match="missing 'id'"):
            client.create_page(_item())


# ---------- archive_items batch ----------


def test_archive_items_all_succeed(tmp_path: Path) -> None:
    client = NotionClient(token="t", database_id="db-1")
    items = [_item(item_id="a"), _item(item_id="b"), _item(item_id="c")]
    succ: list[str] = []
    with patch("digest.archive_notion.httpx.post", return_value=_ok_resp()):
        result = archive_items(client, items, retry_queue=tmp_path / "queue.jsonl", on_success=succ)
    assert result.succeeded == 3
    assert result.failed == 0
    assert result.queued_for_retry == 0
    assert succ == ["a", "b", "c"]
    # Queue should not be created when nothing failed.
    assert not (tmp_path / "queue.jsonl").exists()


def test_archive_items_mixed_failures_queue(tmp_path: Path) -> None:
    client = NotionClient(token="t", database_id="db-1")
    items = [_item(item_id="a"), _item(item_id="b"), _item(item_id="c")]
    queue = tmp_path / "queue.jsonl"

    responses = [_ok_resp(), httpx.Response(500, text="boom"), _ok_resp()]
    succ: list[str] = []
    with patch("digest.archive_notion.httpx.post", side_effect=responses):
        result = archive_items(client, items, retry_queue=queue, on_success=succ)

    assert result.succeeded == 2
    assert result.failed == 1
    assert result.queued_for_retry == 1
    assert succ == ["a", "c"]

    lines = queue.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["item"]["item_id"] == "b"
    assert "error" in entry
    assert "ts" in entry


def test_archive_items_no_retry_queue_just_logs(tmp_path: Path) -> None:
    """When retry_queue is None, failures are logged but not queued."""
    client = NotionClient(token="t", database_id="db-1")
    items = [_item(item_id="a")]
    with patch("digest.archive_notion.httpx.post", return_value=httpx.Response(500)):
        result = archive_items(client, items, retry_queue=None)
    assert result.failed == 1
    assert result.queued_for_retry == 0


def test_archive_items_empty_input() -> None:
    client = NotionClient(token="t", database_id="db-1")
    with patch("digest.archive_notion.httpx.post") as mock_post:
        result = archive_items(client, [])
        mock_post.assert_not_called()
    assert result.succeeded == 0
