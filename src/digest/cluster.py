"""LLM-based topic clustering for non-event items.

Single LLM call assigns N items into 3-5 topic groups, each with a name and
1-sentence summary. Output is strict JSON (markdown fence tolerated).

The classifier already provides per-item kind labels; this module groups
*within* the news/tool/other pool by what the items are about (e.g. "Claude 4.7
发布", "AI 编程工具更新", "国产模型动态"). Output drives the daily digest layout.

Design notes:
- Reuses LLMClient protocol from classifier (so DeepSeek / Anthropic / Qwen all
  work without reimplementing transport).
- Input scoped to title + content snippet (≤200 chars) so token usage stays
  predictable for ~100 items/day.
- The model is told to skip "noise" items: returning fewer items than input is
  expected and not an error.
- Items the model fails to assign are silently dropped — they get re-considered
  on the next day's run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .classifier import LLMClient, default_model_for_env

DEFAULT_MIN_TOPICS = 3
DEFAULT_MAX_TOPICS = 5
DEFAULT_MAX_PER_TOPIC = 4
DEFAULT_SNIPPET_CHARS = 200


@dataclass(frozen=True)
class ClusterInput:
    item_id: str
    title: str
    snippet: str  # truncated content; classifier already saw it, no preprocessing here


@dataclass(frozen=True)
class TopicAssignment:
    name: str  # short label, ≤ 12 chars per prompt
    summary: str  # 1 sentence, ≤ 60 chars
    item_ids: list[str]


_PROMPT = """你是一个面向 AI 领域专业开发者的资讯主题聚类器。
你的读者是熟练使用各种 AI agent / 编程工具的资深用户，**只对高信号内容有兴趣**。

给定下面 N 条 AI 圈条目（标题 + 内容片段），把**有信号的**归到
%(min_topics)d-%(max_topics)d 个主题，**剩下的全部丢弃**。

## 筛选标准（严格执行）

✅ **保留**：
- 新模型 / 新 agent 框架 / 新工具的**发布**或**深度评测**
- 技术解读、架构分析、性能对比（含具体数字 / 代码 / benchmark）
- 工程实践分享（具体方案 + 数据，不是"我感觉变快了"）
- 行业动态：公司新闻、融资、产品发布、政策
- 高质量开源项目发布

❌ **丢弃**（即使内容沾 AI/编程也丢）：
- 用户求助 / 报 bug / 问基础用法（"我的 X 不工作怎么办"、"为什么 Y 这么傻"）
- 入门求购 / "大家都用什么" / "推荐一下" 类调研
- 个人感想 / 心情分享 / 抱怨 / 凡尔赛
- 账号买卖 / 额度共享 / 公益站讨论
- 通用闲聊（哪怕装在 AI 话题外壳下）

## 输出每个主题

- name: ≤ 12 字的中文标签
- summary: ≤ 60 字的一句话主题概括
- 包含 1-%(max_per_topic)d 个最相关的条目

返回的 item_ids 总数应**显著少于输入** —— 论坛帖子 70-80%% 都是噪声是正常的。
宁缺毋滥，不要为凑主题数往里塞低质内容。

仅输出严格 JSON，不要任何前后文字、markdown 代码块或解释。结构：

[
  {
    "name": "主题标签",
    "summary": "一句话概括",
    "item_ids": ["id1", "id2", ...]
  },
  ...
]

待聚类条目（共 %(n_items)d 条）：

%(items_block)s
"""


class ClusterParseError(Exception):
    """LLM output failed to parse as a topic list."""


def cluster(
    items: list[ClusterInput],
    client: LLMClient,
    *,
    model: str | None = None,
    min_topics: int = DEFAULT_MIN_TOPICS,
    max_topics: int = DEFAULT_MAX_TOPICS,
    max_per_topic: int = DEFAULT_MAX_PER_TOPIC,
    max_tokens: int = 2000,
) -> list[TopicAssignment]:
    """Run one LLM call to bucket items into topics.

    Empty input short-circuits to an empty result with no LLM call.
    """
    if not items:
        return []

    items_block = "\n".join(
        f"- id={it.item_id} | 标题={it.title} | 片段={it.snippet[:DEFAULT_SNIPPET_CHARS]}"
        for it in items
    )
    prompt = _PROMPT % {
        "min_topics": min_topics,
        "max_topics": max_topics,
        "max_per_topic": max_per_topic,
        "n_items": len(items),
        "items_block": items_block,
    }
    raw = client.create_message(
        model=model or default_model_for_env(),
        prompt=prompt,
        max_tokens=max_tokens,
    )
    return _parse(raw, valid_ids={it.item_id for it in items})


def _parse(raw: str, *, valid_ids: set[str]) -> list[TopicAssignment]:
    """Parse LLM output into TopicAssignment list.

    Tolerant of: markdown code fences, leading/trailing whitespace.
    Drops: ids that weren't in the input (LLM hallucinations); empty topics.
    """
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ClusterParseError(f"output not valid JSON: {raw[:300]!r}") from e

    if not isinstance(obj, list):
        raise ClusterParseError(f"top-level not a list: got {type(obj).__name__}")

    out: list[TopicAssignment] = []
    for entry in obj:
        if not isinstance(entry, dict):
            continue
        name_raw = entry.get("name")
        summary_raw = entry.get("summary", "")
        ids_raw = entry.get("item_ids")

        if not isinstance(name_raw, str) or not name_raw.strip():
            continue
        if not isinstance(ids_raw, list):
            continue
        # Drop hallucinated ids; keep only those that came from the input.
        clean_ids = [i for i in ids_raw if isinstance(i, str) and i in valid_ids]
        if not clean_ids:
            continue
        summary = summary_raw if isinstance(summary_raw, str) else ""
        out.append(
            TopicAssignment(
                name=name_raw.strip(),
                summary=summary.strip(),
                item_ids=clean_ids,
            )
        )
    return out
