from __future__ import annotations

from typing import Any

import pandas as pd

from collectors.config import SUPPORTED_CHANNELS
from rcore.utils import norm_code


MASTER_REVIEW_COLUMNS = [
    "Collection Eligible",
    "Collection Status",
    "Review Found",
    "Rating",
    "Review Group",
    "Review Date",
    "Review Text",
]


def build_review_map(collected: dict[str, dict[str, dict]]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for agency, reviews in collected.items():
        for code, info in (reviews or {}).items():
            out[(agency, norm_code(code))] = info
    return out


def _init_master_columns(master: pd.DataFrame) -> None:
    """Create result columns with explicit dtypes.

    pandas 3.x (and recent 2.x versions with stricter string dtype handling)
    does not allow assigning booleans/numbers into a column that was first
    created from an empty string.  Keep each output column strongly typed from
    the beginning so the analyzer also works on Python 3.14 + recent pandas.
    """
    idx = master.index
    master["Collection Eligible"] = pd.Series(False, index=idx, dtype="bool")
    master["Collection Status"] = pd.Series("", index=idx, dtype="string")
    master["Review Found"] = pd.Series(False, index=idx, dtype="bool")
    master["Rating"] = pd.Series(pd.NA, index=idx, dtype="Float64")
    master["Review Group"] = pd.Series(pd.NA, index=idx, dtype="string")
    master["Review Date"] = pd.Series(pd.NA, index=idx, dtype="string")
    master["Review Text"] = pd.Series(pd.NA, index=idx, dtype="string")


def build_master_dataset(
    scope_df: pd.DataFrame,
    collected: dict[str, dict[str, dict]],
    collection_status: dict[str, dict[str, Any]],
    row_collection_status: dict[Any, str] | None = None,
) -> pd.DataFrame:
    master = scope_df.copy()
    review_map = build_review_map(collected)
    _init_master_columns(master)

    for idx, row in master.iterrows():
        agency = str(row["Agency"]).strip().upper()
        code = norm_code(row["Agency Code"])

        if agency not in SUPPORTED_CHANNELS:
            master.at[idx, "Collection Eligible"] = False
            master.at[idx, "Collection Status"] = "UNSUPPORTED"
            master.at[idx, "Review Found"] = False
            continue

        master.at[idx, "Collection Eligible"] = True
        if row_collection_status is not None and idx in row_collection_status:
            ch_status = row_collection_status.get(idx, "NOT_RUN")
        else:
            ch_status = collection_status.get(agency, {}).get("status", "NOT_RUN")
        if ch_status != "SUCCESS":
            master.at[idx, "Collection Status"] = "COLLECTION_ERROR"
            master.at[idx, "Review Found"] = False
            continue

        master.at[idx, "Collection Status"] = "SUCCESS"
        info = review_map.get((agency, code))
        if not info:
            master.at[idx, "Review Found"] = False
            continue

        try:
            rating = float(info.get("rating"))
        except (ValueError, TypeError):
            master.at[idx, "Review Found"] = False
            continue

        master.at[idx, "Review Found"] = True
        master.at[idx, "Rating"] = rating
        master.at[idx, "Review Group"] = "GOOD" if rating >= 4 else "BAD"
        master.at[idx, "Review Date"] = str(info.get("review_date", "") or "")
        master.at[idx, "Review Text"] = str(info.get("content", "") or "")

    master["Booking Key"] = (
        master["Agency"].astype(str)
        + "::"
        + master["Agency Code"].astype(str)
    )
    return master
