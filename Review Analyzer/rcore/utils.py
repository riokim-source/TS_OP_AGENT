from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd


def norm_code(value: Any) -> str:
    """Normalize agency booking codes safely.

    Handles blanks, numeric values exported as ``12345.0`` or scientific notation,
    removes whitespace, and uppercases the result.
    """
    s = str(value).strip()
    if s.lower() in {"nan", "none", ""}:
        return ""
    s = s.lstrip("'")
    m = re.match(r"^(\d+)\.0$", s)
    if m:
        s = m.group(1)
    elif re.match(r"^\d+(\.\d+)?e[+-]?\d+$", s, flags=re.IGNORECASE):
        try:
            s = format(Decimal(s), "f").split(".")[0]
        except (InvalidOperation, ValueError):
            pass
    return re.sub(r"\s+", "", s).upper()


def normalize_product_name(value: Any) -> str:
    """V1 product normalization: trim and collapse repeated whitespace only."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def to_epoch_ms(value: Any) -> int | None:
    try:
        return int(pd.Timestamp(value).timestamp() * 1000)
    except Exception:
        return None


def parse_any_ms(value: Any) -> int | None:
    """Epoch ms/s or common date strings -> epoch milliseconds."""
    if value is None or value == "":
        return None
    try:
        n = float(value)
        return int(n if n > 1e12 else n * 1000)
    except (ValueError, TypeError):
        pass
    try:
        text = str(value).split("(")[0].strip()
        return to_epoch_ms(pd.Timestamp(text))
    except Exception:
        return None


def format_rating(value: Any) -> str:
    if value is None:
        return ""
    try:
        f = float(value)
        return str(int(f)) if f == int(f) else f"{f:.1f}"
    except (ValueError, TypeError):
        return str(value).strip()
