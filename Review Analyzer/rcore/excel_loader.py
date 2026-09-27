from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from rcore.utils import norm_code, normalize_product_name


REQUIRED_COLS = ["Date", "Agency", "Agency Code", "Area", "Product"]
OPTIONAL_COLS = ["Main Guide", "Guides", "People", "Language", "Pickup"]


@dataclass
class LoadedBookings:
    df: pd.DataFrame
    source_path: str
    sheet_name: str
    min_date: pd.Timestamp
    max_date: pd.Timestamp
    areas: list[str]
    agencies: list[str]


def _pick_main_sheet(xls: dict[str, pd.DataFrame]) -> tuple[str, pd.DataFrame]:
    for name, frame in xls.items():
        lowered = str(name).strip().lower().replace("_", " ").replace("-", " ")
        if "no show" in lowered or lowered == "noshow":
            continue
        return name, frame.copy()
    raise ValueError("메인 예약 데이터 시트를 찾을 수 없습니다.")


def load_bookings(path: str) -> LoadedBookings:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)

    xls = pd.read_excel(path, sheet_name=None)
    sheet_name, df = _pick_main_sheet(xls)
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    df = df[df["Date"].notna()].copy()

    df["Agency"] = df["Agency"].astype(str).str.strip().str.upper()
    df["Agency Code"] = df["Agency Code"].apply(norm_code)
    df["Area"] = df["Area"].astype(str).str.strip()
    df["Product"] = df["Product"].apply(normalize_product_name)
    df["Product Normalized"] = df["Product"].apply(normalize_product_name)

    # Preserve all bookings even when guide is blank.
    if "Main Guide" not in df.columns:
        df["Main Guide"] = ""
    else:
        df["Main Guide"] = df["Main Guide"].fillna("").astype(str).str.strip()

    if "People" in df.columns:
        df["People"] = pd.to_numeric(df["People"], errors="coerce").fillna(0).astype(int)

    df = df[df["Agency Code"] != ""].copy()

    return LoadedBookings(
        df=df,
        source_path=str(p),
        sheet_name=sheet_name,
        min_date=df["Date"].min(),
        max_date=df["Date"].max(),
        areas=sorted(df["Area"].dropna().unique().tolist()),
        agencies=sorted(df["Agency"].dropna().unique().tolist()),
    )
