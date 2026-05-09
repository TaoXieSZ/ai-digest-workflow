"""Unit tests for src/digest/cluster.py."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from digest.cluster import (
    ClusterInput,
    ClusterParseError,
    TopicAssignment,
    _parse,
    cluster,
)


@dataclass
class FakeClient:
    response: str
    last_prompt: str | None = None

    def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
        self.last_prompt = prompt
        return self.response


# ---------- _parse ----------


def test_parse_valid_topics() -> None:
    raw = json.dumps(
        [
            {"name": "Claude 4.7", "summary": "新版发布", "item_ids": ["a", "b"]},
            {"name": "AI 编程工具", "summary": "周边更新", "item_ids": ["c"]},
        ]
    )
    out = _parse(raw, valid_ids={"a", "b", "c"})
    assert out == [
        TopicAssignment(name="Claude 4.7", summary="新版发布", item_ids=["a", "b"]),
        TopicAssignment(name="AI 编程工具", summary="周边更新", item_ids=["c"]),
    ]


def test_parse_strips_markdown_fence() -> None:
    raw = '```json\n[{"name":"x","summary":"y","item_ids":["a"]}]\n```'
    out = _parse(raw, valid_ids={"a"})
    assert len(out) == 1 and out[0].name == "x"


def test_parse_drops_hallucinated_ids() -> None:
    raw = json.dumps([{"name": "X", "summary": "y", "item_ids": ["real", "fake"]}])
    out = _parse(raw, valid_ids={"real"})
    assert out[0].item_ids == ["real"]


def test_parse_drops_topic_with_no_real_ids() -> None:
    raw = json.dumps([{"name": "X", "summary": "y", "item_ids": ["fake"]}])
    out = _parse(raw, valid_ids={"real"})
    assert out == []


def test_parse_drops_topic_with_empty_name() -> None:
    raw = json.dumps([{"name": "  ", "summary": "y", "item_ids": ["a"]}])
    assert _parse(raw, valid_ids={"a"}) == []


def test_parse_handles_missing_summary() -> None:
    raw = json.dumps([{"name": "X", "item_ids": ["a"]}])
    out = _parse(raw, valid_ids={"a"})
    assert out[0].summary == ""


def test_parse_invalid_json_raises() -> None:
    with pytest.raises(ClusterParseError, match="not valid JSON"):
        _parse("not json", valid_ids=set())


def test_parse_top_level_not_list_raises() -> None:
    with pytest.raises(ClusterParseError, match="top-level not a list"):
        _parse('{"name": "x"}', valid_ids=set())


def test_parse_skips_non_dict_entries() -> None:
    raw = json.dumps([{"name": "X", "summary": "y", "item_ids": ["a"]}, "junk", 42])
    out = _parse(raw, valid_ids={"a"})
    assert len(out) == 1


# ---------- cluster() integration ----------


def test_cluster_empty_input_skips_llm_call() -> None:
    client = FakeClient(response="this should never be parsed")
    out = cluster([], client)
    assert out == []
    assert client.last_prompt is None  # no call


def test_cluster_passes_items_to_prompt() -> None:
    items = [
        ClusterInput(item_id="a", title="Claude 4.7 发布", snippet="新模型..."),
        ClusterInput(item_id="b", title="GPT-X 也来了", snippet="OpenAI..."),
    ]
    client = FakeClient(
        response=json.dumps([{"name": "新模型", "summary": "两家都发", "item_ids": ["a", "b"]}])
    )
    out = cluster(items, client)
    assert len(out) == 1
    assert set(out[0].item_ids) == {"a", "b"}
    assert client.last_prompt is not None
    assert "id=a | 标题=Claude 4.7 发布" in client.last_prompt
    assert "id=b | 标题=GPT-X 也来了" in client.last_prompt
    # The prompt should communicate the topic count constraints
    assert "3-5 个主题" in client.last_prompt


def test_cluster_truncates_long_snippet_in_prompt() -> None:
    long = "x" * 5000
    items = [ClusterInput(item_id="a", title="t", snippet=long)]
    client = FakeClient(response=json.dumps([{"name": "x", "summary": "y", "item_ids": ["a"]}]))
    cluster(items, client)
    assert client.last_prompt is not None
    # Default snippet limit is 200
    assert "x" * 200 in client.last_prompt
    assert "x" * 201 not in client.last_prompt


def test_cluster_drops_hallucinated_ids_e2e() -> None:
    items = [ClusterInput(item_id="real", title="t", snippet="s")]
    # LLM returned an id we never sent
    client = FakeClient(
        response=json.dumps([{"name": "x", "summary": "y", "item_ids": ["real", "fake"]}])
    )
    out = cluster(items, client)
    assert out[0].item_ids == ["real"]
