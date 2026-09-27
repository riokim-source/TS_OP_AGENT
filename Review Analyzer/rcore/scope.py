from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from collectors.config import SUPPORTED_CHANNELS


@dataclass
class ScopeResult:
    scope_df: pd.DataFrame
    eligible_df: pd.DataFrame
    unsupported_df: pd.DataFrame
    crawl_df: pd.DataFrame
    selected_areas: list[str]
    selected_channels: list[str]
    start_date: pd.Timestamp
    end_date: pd.Timestamp


def build_scope(
    df: pd.DataFrame,
    start_date,
    end_date,
    areas: Iterable[str] | None = None,
    channels: Iterable[str] | None = None,
) -> ScopeResult:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if start > end:
        raise ValueError("시작일이 종료일보다 늦습니다.")

    selected_areas = list(areas) if areas else sorted(df["Area"].unique().tolist())
    selected_channels = list(channels) if channels else list(SUPPORTED_CHANNELS)

    scope_df = df[
        (df["Date"] >= start)
        & (df["Date"] <= end)
        & (df["Area"].isin(selected_areas))
    ].copy()

    eligible_df = scope_df[scope_df["Agency"].isin(SUPPORTED_CHANNELS)].copy()
    unsupported_df = scope_df[~scope_df["Agency"].isin(SUPPORTED_CHANNELS)].copy()
    crawl_df = eligible_df[eligible_df["Agency"].isin(selected_channels)].copy()

    return ScopeResult(
        scope_df=scope_df,
        eligible_df=eligible_df,
        unsupported_df=unsupported_df,
        crawl_df=crawl_df,
        selected_areas=selected_areas,
        selected_channels=selected_channels,
        start_date=start,
        end_date=end,
    )


def unsupported_counts(scope: ScopeResult) -> pd.DataFrame:
    if scope.unsupported_df.empty:
        return pd.DataFrame(columns=["Agency", "Bookings"])
    return (
        scope.unsupported_df.groupby("Agency", dropna=False)
        .size()
        .reset_index(name="Bookings")
        .sort_values("Bookings", ascending=False)
        .reset_index(drop=True)
    )
