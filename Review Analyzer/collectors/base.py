from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rcore.browser import BrowserSession


Logger = Callable[[str], None]


@dataclass
class CollectorContext:
    browser: BrowserSession
    log: Logger = print


@dataclass
class CollectionResult:
    channel: str
    status: str
    reviews: dict[str, dict]
    error: str = ""

    @property
    def count(self) -> int:
        return len(self.reviews)
