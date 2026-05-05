"""Fetcher protocol + shared dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class FetchedItem:
    url: str
    title: str
    content: str | None = None
    author: str | None = None
    published_at: datetime | None = None


class Fetcher(Protocol):
    def fetch(self) -> list[FetchedItem]: ...


class FetchError(Exception):
    """Raised when a fetcher cannot retrieve any usable items."""
