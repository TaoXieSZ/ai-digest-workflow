import json
from dataclasses import dataclass

import pytest

from digest.classifier import (
    Classifier,
    ClassifierParseError,
    EventMetadata,
    _clean_iso,
    _parse,
)


@dataclass
class FakeClient:
    response: str

    def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
        return self.response


def test_parse_event_with_full_metadata() -> None:
    raw = json.dumps(
        {
            "kind": "event",
            "event_metadata": {
                "event_date": "2026-06-15",
                "registration_deadline": "2026-06-10",
                "location": "深圳南山·腾讯滨海大厦",
                "registration_url": "https://example.com/signup",
            },
        }
    )
    cls = _parse(raw)
    assert cls.kind == "event"
    assert cls.event_metadata == EventMetadata(
        event_date="2026-06-15",
        registration_deadline="2026-06-10",
        location="深圳南山·腾讯滨海大厦",
        registration_url="https://example.com/signup",
    )


def test_parse_news_no_metadata() -> None:
    raw = json.dumps({"kind": "news", "event_metadata": None})
    cls = _parse(raw)
    assert cls.kind == "news"
    assert cls.event_metadata is None


def test_parse_strips_markdown_fence() -> None:
    raw = '```json\n{"kind": "tool", "event_metadata": null}\n```'
    cls = _parse(raw)
    assert cls.kind == "tool"


def test_parse_unknown_kind_downgrades_to_other() -> None:
    raw = json.dumps({"kind": "spam", "event_metadata": None})
    assert _parse(raw).kind == "other"


def test_parse_invalid_json_raises() -> None:
    with pytest.raises(ClassifierParseError):
        _parse("not json at all")


def test_parse_event_with_bad_date_drops_field() -> None:
    raw = json.dumps(
        {
            "kind": "event",
            "event_metadata": {
                "event_date": "明天",  # invalid format
                "registration_deadline": "2026-06-10",
                "location": None,
                "registration_url": None,
            },
        }
    )
    cls = _parse(raw)
    assert cls.kind == "event"
    assert cls.event_metadata is not None
    assert cls.event_metadata.event_date is None
    assert cls.event_metadata.registration_deadline == "2026-06-10"


def test_clean_iso_accepts_only_yyyy_mm_dd() -> None:
    assert _clean_iso("2026-05-04") == "2026-05-04"
    assert _clean_iso("2026/05/04") is None
    assert _clean_iso("") is None
    assert _clean_iso(None) is None
    assert _clean_iso(1234) is None


def test_classifier_truncates_long_content() -> None:
    long_content = "x" * 5000
    captured: dict[str, str] = {}

    class Capturer:
        def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
            captured["prompt"] = prompt
            return json.dumps({"kind": "other", "event_metadata": None})

    classifier = Classifier(Capturer(), max_content_chars=200)
    classifier.classify(title="t", content=long_content)
    # The truncated content body in the prompt should be ≤ 200 chars
    assert "x" * 200 in captured["prompt"]
    assert "x" * 201 not in captured["prompt"]


def test_classifier_injects_today_into_prompt() -> None:
    from datetime import date

    captured: dict[str, str] = {}

    class Capturer:
        def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
            captured["prompt"] = prompt
            return json.dumps({"kind": "other", "event_metadata": None})

    Classifier(Capturer()).classify(title="t", content="c")
    today_iso = date.today().isoformat()
    assert today_iso in captured["prompt"], (
        f"expected today date {today_iso} in prompt, got: {captured['prompt'][:200]}"
    )


def test_classifier_uses_default_model_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-test")
    captured: dict[str, str] = {}

    class Capturer:
        def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
            captured["model"] = model
            return json.dumps({"kind": "other", "event_metadata": None})

    Classifier(Capturer()).classify(title="x", content="y")
    assert captured["model"] == "claude-haiku-test"


def test_classify_many_empty_input_skips_threadpool() -> None:
    captured: dict[str, int] = {"calls": 0}

    class Counter:
        def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
            captured["calls"] += 1
            return json.dumps({"kind": "other", "event_metadata": None})

    out = Classifier(Counter()).classify_many([])
    assert out == []
    assert captured["calls"] == 0


def test_classify_many_preserves_order() -> None:
    class ByTitle:
        def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
            # Pull whatever follows "标题：" in the prompt and echo as kind.
            i = prompt.find("标题：")
            tag = prompt[i + 3 : i + 5] if i >= 0 else ""
            kind = (
                "event" if tag == "EV" else "news" if tag == "NW" else "other"
            )
            return json.dumps({"kind": kind, "event_metadata": None})

    items = [("EV-1", None), ("NW-2", None), ("EV-3", None)]
    out = Classifier(ByTitle()).classify_many(items, concurrency=3)
    assert [c.kind for c in out if c is not None] == ["event", "news", "event"]


def test_classify_many_individual_failure_yields_none_in_place() -> None:
    """One LLM call raising shouldn't drop the rest of the batch."""

    class FlakyClient:
        def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
            if "FAIL" in prompt:
                raise RuntimeError("boom")
            return json.dumps({"kind": "other", "event_metadata": None})

    items = [("ok-1", None), ("FAIL-2", None), ("ok-3", None)]
    out = Classifier(FlakyClient()).classify_many(items, concurrency=3)
    assert len(out) == 3
    assert out[0] is not None and out[0].kind == "other"
    assert out[1] is None  # failed → None at that index
    assert out[2] is not None and out[2].kind == "other"


def test_classify_many_concurrency_actually_parallel() -> None:
    """If we set concurrency=N, total wall time should be ~ (N items / N) * per_call_time,
    not (N items * per_call_time). Use 200 ms sleep × 4 items, concurrency=4 → < 0.5s."""
    import time

    class SlowClient:
        def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
            time.sleep(0.2)
            return json.dumps({"kind": "other", "event_metadata": None})

    items = [(f"t{i}", None) for i in range(4)]
    t0 = time.monotonic()
    out = Classifier(SlowClient()).classify_many(items, concurrency=4)
    elapsed = time.monotonic() - t0
    assert len(out) == 4
    # Sequential would be ~0.8s; parallel with 4 workers is ~0.2s. Allow generous slack.
    assert elapsed < 0.5, f"expected <0.5s with concurrency=4, got {elapsed:.2f}s"


def test_classifier_explicit_model_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", "from-env")
    captured: dict[str, str] = {}

    class Capturer:
        def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
            captured["model"] = model
            return json.dumps({"kind": "other", "event_metadata": None})

    Classifier(Capturer(), model="explicit").classify(title="x", content="y")
    assert captured["model"] == "explicit"


# ---------- factory + provider switching ----------


def test_default_model_for_anthropic_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from digest.classifier import default_model_for_env

    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert default_model_for_env() == "claude-haiku-4-5"


def test_default_model_for_deepseek_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from digest.classifier import default_model_for_env

    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    assert default_model_for_env() == "deepseek-chat"


def test_default_model_explicit_overrides_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from digest.classifier import default_model_for_env

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_MODEL", "deepseek-reasoner")
    assert default_model_for_env() == "deepseek-reasoner"


def test_make_client_anthropic_missing_key_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from digest.classifier import make_client_from_env

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        make_client_from_env()


def test_make_client_deepseek_uses_llm_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from digest import classifier as clf

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    captured: dict[str, str | None] = {}

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
            captured["api_key"] = api_key
            captured["base_url"] = base_url

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    clf.make_client_from_env()
    assert captured["api_key"] == "sk-test"
    assert captured["base_url"] == "https://api.deepseek.com"


def test_make_client_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from digest.classifier import make_client_from_env

    monkeypatch.setenv("LLM_PROVIDER", "weirdai")
    with pytest.raises(RuntimeError, match="unknown LLM_PROVIDER"):
        make_client_from_env()


def test_make_client_deepseek_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from digest.classifier import make_client_from_env

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    for var in ("LLM_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="no API key"):
        make_client_from_env()


def test_make_client_qwen_uses_dashscope_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-qwen")

    captured: dict[str, str | None] = {}

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
            captured["base_url"] = base_url

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    from digest.classifier import make_client_from_env

    make_client_from_env()
    assert captured["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_openai_client_returns_concatenated_content(monkeypatch: pytest.MonkeyPatch) -> None:
    from digest.classifier import OpenAICompatibleClient

    class FakeChoice:
        def __init__(self, content: str) -> None:
            self.message = type("M", (), {"content": content})()

    class FakeResp:
        def __init__(self, content: str) -> None:
            self.choices = [FakeChoice(content)]

    class FakeCompletions:
        def create(self, **kwargs: object) -> FakeResp:
            return FakeResp('{"kind":"news","event_metadata":null}')

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
            self.chat = type("C", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    client = OpenAICompatibleClient(api_key="sk-x")
    out = client.create_message(model="m", prompt="p", max_tokens=100)
    assert out == '{"kind":"news","event_metadata":null}'
