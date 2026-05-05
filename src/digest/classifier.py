"""LLM classifier: route an item into event / news / tool / other.

Single Anthropic call does both:
1. Categorize into one of {event, news, tool, other}
2. If event, extract structured fields (date / deadline / location / url)

Model is read from ANTHROPIC_MODEL env (default: claude-haiku-4-5).
Temperature 0 for stability. Output is strict JSON.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class EventMetadata:
    event_date: str | None  # ISO date YYYY-MM-DD or None
    registration_deadline: str | None
    location: str | None
    registration_url: str | None


@dataclass(frozen=True)
class Classification:
    kind: str  # event | news | tool | other
    event_metadata: EventMetadata | None  # populated only when kind == "event"


class LLMClient(Protocol):
    """Minimal Anthropic-compatible client surface for testing."""

    def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str: ...


_PROMPT = """你是一个中文 AI 资讯条目分类器。
给定一条来自社区的内容（标题 + 正文片段），判断它属于以下哪一类：

- event: 公开的 AI 活动通告 — meetup、conference、workshop、黑客松开赛/报名通知、
         沙龙、聚会、展会、coffee chat 邀约（长期有效或定期举办的公开邀约）。
         **关键判定**：任何人都可以参与的公开活动信息；活动本身是这条帖子的主体。
- news: AI 行业动态、模型发布、公司新闻、产品更新
- tool: AI 工具/Prompt 技巧/玩法分享（"亲测好用的 N 个工具"类）
- other: 不属于以上任何一类。**包括以下情况**：
  * 找队友/找伙伴/找搭档（即使是为黑客松找队友，也是私人招募，不是公开活动）
  * 团队招募成员/项目招人/招募合作者（即使关键词含"AI"或"黑客松"）
  * 活动复盘/获奖分享/打卡总结（"我们拿了第一名"、"meetup 学到了什么"）
  * 个人感想、行情评论、招聘 JD

如果是 event，从内容中抽取以下字段（找不到的填 null）：
- event_date: 活动举办日期，格式 YYYY-MM-DD，找不到填 null
- registration_deadline: 报名截止日期，格式 YYYY-MM-DD，找不到填 null
- location: 城市 + 详细地点，或 "线上"，找不到填 null
- registration_url: 报名链接（http(s)://...），找不到填 null

今天日期：%(today)s（你必须用这个年份做日期推断；例如"4.23"在今天之后则解析为今年，否则为明年）

仅输出严格 JSON，不要任何前后文字、markdown 代码块或解释。结构：

{
  "kind": "event|news|tool|other",
  "event_metadata": null 或 {
    "event_date": "YYYY-MM-DD 或 null",
    "registration_deadline": "YYYY-MM-DD 或 null",
    "location": "字符串 或 null",
    "registration_url": "url 或 null"
  }
}

待分类内容：

标题：%(title)s

正文：%(content)s
"""


class AnthropicClient:
    """Adapter over the official anthropic SDK with the LLMClient surface."""

    def __init__(self, api_key: str | None = None) -> None:
        # Local import so unit tests don't need anthropic installed unless this is exercised.
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)

    def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
        msg = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        # Concatenate all text blocks (usually one).
        parts: list[str] = []
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                # Use getattr to satisfy mypy's union narrowing across all block subtypes.
                text_val = getattr(block, "text", None)
                if isinstance(text_val, str):
                    parts.append(text_val)
        return "".join(parts)


class OpenAICompatibleClient:
    """Adapter for OpenAI-compatible APIs (DeepSeek, Qwen DashScope, OpenAI proper, etc.)

    DeepSeek default base_url: https://api.deepseek.com
    Qwen default base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def create_message(self, *, model: str, prompt: str, max_tokens: int) -> str:
        resp = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        if not resp.choices:
            return ""
        content = resp.choices[0].message.content
        return content or ""


_DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}

_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "deepseek": "deepseek-chat",
    "qwen": "qwen-turbo",
}


def make_client_from_env() -> LLMClient:
    """Pick a client based on LLM_PROVIDER env var.

    Supported:
    - anthropic (default) — uses ANTHROPIC_API_KEY
    - deepseek — uses DEEPSEEK_API_KEY (or LLM_API_KEY); base_url auto
    - qwen — uses DASHSCOPE_API_KEY (or LLM_API_KEY); base_url auto
    - openai — uses OPENAI_API_KEY; base_url from LLM_BASE_URL or OpenAI default

    Raises RuntimeError if the required key is missing.
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()

    if provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        return AnthropicClient(api_key=key)

    if provider in {"deepseek", "qwen", "openai", "openai-compatible"}:
        key = (
            os.getenv("LLM_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        if not key:
            raise RuntimeError(
                f"no API key found for provider={provider}; "
                "set LLM_API_KEY or the provider-specific *_API_KEY"
            )
        base_url = os.getenv("LLM_BASE_URL") or _DEFAULT_BASE_URLS.get(provider)
        return OpenAICompatibleClient(api_key=key, base_url=base_url)

    raise RuntimeError(f"unknown LLM_PROVIDER: {provider!r}")


def default_model_for_env() -> str:
    """Pick a default model name aligned with LLM_PROVIDER.

    LLM_MODEL takes precedence; otherwise ANTHROPIC_MODEL (legacy);
    otherwise the per-provider default.
    """
    explicit = os.getenv("LLM_MODEL") or os.getenv("ANTHROPIC_MODEL")
    if explicit:
        return explicit
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()
    return _DEFAULT_MODELS.get(provider, "claude-haiku-4-5")


class Classifier:
    def __init__(
        self,
        client: LLMClient,
        *,
        model: str | None = None,
        max_tokens: int = 400,
        max_content_chars: int = 1500,
    ) -> None:
        self._client = client
        self._model: str = model or default_model_for_env()
        self._max_tokens = max_tokens
        self._max_content_chars = max_content_chars

    def classify(self, *, title: str, content: str | None) -> Classification:
        prompt = _PROMPT % {
            "title": title or "(无标题)",
            "content": (content or "")[: self._max_content_chars],
            "today": date.today().isoformat(),
        }
        raw = self._client.create_message(
            model=self._model,
            prompt=prompt,
            max_tokens=self._max_tokens,
        )
        return _parse(raw)


def _parse(raw: str) -> Classification:
    """Parse classifier output into Classification.

    Tolerant of:
    - Markdown code fences (```json ... ```)
    - Leading/trailing whitespace
    - Unknown 'kind' values (downgrade to 'other')
    """
    text = raw.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ClassifierParseError(f"output not valid JSON: {raw!r}") from e

    kind = str(obj.get("kind", "")).lower()
    if kind not in {"event", "news", "tool", "other"}:
        kind = "other"

    em_raw = obj.get("event_metadata")
    event_metadata: EventMetadata | None = None
    if kind == "event" and isinstance(em_raw, dict):
        event_metadata = EventMetadata(
            event_date=_clean_iso(em_raw.get("event_date")),
            registration_deadline=_clean_iso(em_raw.get("registration_deadline")),
            location=_clean_str(em_raw.get("location")),
            registration_url=_clean_str(em_raw.get("registration_url")),
        )

    return Classification(kind=kind, event_metadata=event_metadata)


def _clean_iso(v: object) -> str | None:
    """Accept YYYY-MM-DD only; reject other shapes (None / wrong format)."""
    if not isinstance(v, str):
        return None
    s = v.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    return None


def _clean_str(v: object) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip()
    return s or None


class ClassifierParseError(Exception):
    """Raised when LLM output cannot be parsed as Classification JSON."""
