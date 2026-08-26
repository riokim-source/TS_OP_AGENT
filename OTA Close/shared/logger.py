"""
통합 로거.

- 콘솔 + logs/ota_close_bot_YYYY-MM-DD.log 양쪽에 기록
- agency 이름을 prefix로 붙여서 어느 OTA에서 나온 로그인지 명확히
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


def get_logger(name: str = "ota_close_bot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    today = datetime.now().strftime("%Y-%m-%d")
    fh = logging.FileHandler(LOGS_DIR / f"ota_close_bot_{today}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


def get_agency_logger(agency: str) -> logging.Logger:
    """KKDAY / MRT / KLOOK / GG / VI 별 sub-logger."""
    return get_logger(f"ota.{agency}")
