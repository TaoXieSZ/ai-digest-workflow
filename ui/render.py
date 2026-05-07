"""Markdown rendering helpers for digest content."""

from __future__ import annotations

from typing import Any

from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"breaks": True, "html": False, "linkify": False})


def _open_links_in_new_tab(
    self: Any,
    tokens: list[Any],
    idx: int,
    options: Any,
    env: Any,
) -> str:
    tokens[idx].attrSet("target", "_blank")
    tokens[idx].attrSet("rel", "noreferrer noopener")
    return str(self.renderToken(tokens, idx, options, env))


_md.add_render_rule("link_open", _open_links_in_new_tab)


def render_markdown(text: str) -> str:
    """Render markdown text to HTML; safe to interpolate with `|safe`."""
    if not text:
        return ""
    return str(_md.render(text))
