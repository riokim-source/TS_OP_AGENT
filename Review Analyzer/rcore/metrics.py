from __future__ import annotations

import pandas as pd


def _safe_div(n: float, d: float) -> float:
    return (n / d) if d else 0.0


def _metrics_for_group(g: pd.DataFrame) -> dict:
    total = len(g)
    eligible = int(g["Collection Eligible"].eq(True).sum())
    unsupported = int(g["Collection Status"].eq("UNSUPPORTED").sum())
    errors = int(g["Collection Status"].eq("COLLECTION_ERROR").sum())
    valid_scope = max(eligible - errors, 0)
    reviews = int(g["Review Found"].eq(True).sum())
    good = int(g["Review Group"].eq("GOOD").sum())
    bad = int(g["Review Group"].eq("BAD").sum())

    rating_numeric = pd.to_numeric(g["Rating"], errors="coerce")
    stars = {star: int((rating_numeric == star).sum()) for star in [5, 4, 3, 2, 1]}

    return {
        "Total Booking": total,
        "Eligible": eligible,
        "Unsupported": unsupported,
        "Collection Error": errors,
        "Valid Review Scope": valid_scope,
        "Reviews": reviews,
        "Review Rate": _safe_div(reviews, valid_scope),
        "Overall Coverage": _safe_div(reviews, total),
        "Good": good,
        "Bad": bad,
        "Good Rate": _safe_div(good, reviews),
        "Bad Rate": _safe_div(bad, reviews),
        "5 Star": stars[5],
        "4 Star": stars[4],
        "3 Star": stars[3],
        "2 Star": stars[2],
        "1 Star": stars[1],
    }


def build_overall_metrics(master: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame([_metrics_for_group(master)])


def build_tour_metrics(master: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (area, product), g in master.groupby(["Area", "Product Normalized"], dropna=False):
        row = {"Area": area, "Tour": product}
        row.update(_metrics_for_group(g))
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["Bad Rate", "Bad", "Reviews"], ascending=[False, False, False]).reset_index(drop=True)


def build_area_metrics(master: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for area, g in master.groupby("Area", dropna=False):
        row = {"Area": area}
        row.update(_metrics_for_group(g))
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["Bad Rate", "Bad"], ascending=[False, False]).reset_index(drop=True)


def build_channel_metrics(master: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for agency, g in master.groupby("Agency", dropna=False):
        row = {"Agency": agency}
        row.update(_metrics_for_group(g))
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["Bad Rate", "Bad"], ascending=[False, False]).reset_index(drop=True)


def format_rate_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in ["Review Rate", "Overall Coverage", "Good Rate", "Bad Rate"]:
        if c in out.columns:
            out[c] = out[c].map(lambda x: f"{float(x) * 100:.1f}%")
    return out
