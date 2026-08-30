# -*- coding: utf-8 -*-
"""
loader.py
내부 예약 파일(xlsx/xls/csv) -> 라스트미닛 화면 트리.

원본 Last Miniute Writer 의 load_file() 규칙을 그대로 따른다:
  - People 은 숫자 강제 변환 후 결측 0
  - TourDate = Date 컬럼의 날짜부
  - 최신 투어일자 2개만 사용 (내림차순) -> [0]=최신(오픈 대상), [1]=전일(10시 후 예약)

여기에 새로 추가된 것: 투어별 Language / Pickup / Option 후보값 수집.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import pandas as pd

from . import constants as C
from ..routing import area_region

# Option 컬럼은 "옵션명(인원수)" 형태다. 예: "One Way B [Stadium -> Seoul](2)"
_OPT_TAIL = re.compile(r"\s*\(\s*\d+\s*\)\s*$")

REQUIRED_COLUMNS = ["Date", "Area", "Product", "Agency", "People"]


def option_label(raw) -> str:
    """Option 셀 -> 옵션 이름 (뒤의 (인원수) 제거)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return ""
    return _OPT_TAIL.sub("", text).strip()


def _clean(series: pd.Series) -> list[str]:
    vals = [str(v).strip() for v in series.dropna().unique()]
    return sorted({v for v in vals if v and v.lower() != "nan"})


def read_reservations(source, filename: str = "") -> pd.DataFrame:
    """파일 경로 또는 bytes 를 받아 정규화된 DataFrame 을 돌려준다."""
    name = (filename or (source if isinstance(source, str) else "")).lower()
    buf = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source

    if name.endswith(".csv"):
        df = pd.read_csv(buf)
    elif name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(buf)
    else:
        # 확장자를 못 믿을 때는 엑셀 먼저, 실패하면 CSV
        try:
            if isinstance(buf, io.BytesIO):
                buf.seek(0)
            df = pd.read_excel(buf)
        except Exception:
            if isinstance(buf, io.BytesIO):
                buf.seek(0)
            df = pd.read_csv(buf)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("예약 파일에 필요한 컬럼이 없습니다: " + ", ".join(missing))

    for col in ("Option", "Language", "Pickup", "Team", "Guide", "Reservation Date"):
        if col not in df.columns:
            df[col] = None

    # 고치기 전 원본을 들고 있는다 ('숫자가 아니었다' 는 고친 뒤엔 알 수 없다)
    raw_people = df["People"].copy()
    raw_date = df["Date"].copy()

    df["People"] = pd.to_numeric(df["People"], errors="coerce").fillna(0).astype(int)
    df["TourDate"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
    # 예약이 '언제 들어왔는지'. 전일 패널의 'Last Min 10시 후 예약' 집계에 쓴다.
    df["ResDT"] = pd.to_datetime(df["Reservation Date"], errors="coerce")
    df["OptionName"] = df["Option"].map(option_label)
    for col in ("Area", "Product", "Agency", "Language", "Pickup"):
        df[col] = df[col].astype("object").where(df[col].notna(), None)

    # ⚠️ 읽다가 버린 행은 반드시 말해야 한다.
    #    날짜를 못 읽거나 Area/Product 가 비어 있으면 그 행은 집계에서
    #    조용히 빠진다 (groupby 가 결측을 버린다). 그러면 그 투어는 화면에
    #    아예 안 보이고, 안 보이니 수량도 못 넣고, 그날 안 열린다.
    #    숫자가 맞는지가 아니라 '빠진 게 있는지' 를 사람이 알아야 한다.
    df.attrs["problems"] = _scan_problems(df, raw_people, raw_date)
    return df


def _scan_problems(df: pd.DataFrame, raw_people, raw_date) -> list[dict]:
    """집계에서 빠지거나 값이 이상한 행을 이유별로 센다."""
    out: list[dict] = []

    def add(reason: str, mask, fix: str) -> None:
        n = int(mask.sum())
        if n:
            out.append({"reason": reason, "rows": n,
                        "people": int(df.loc[mask, "People"].sum()), "fix": fix})

    add("날짜를 읽지 못함",
        df["TourDate"].isna() & raw_date.notna(),
        "Date 칸의 형식을 확인하세요 (예: 2026-08-31)")
    add("날짜 칸이 비어 있음", raw_date.isna(),
        "Date 가 빈 행입니다")
    for col, label in (("Area", "지역"), ("Product", "상품")):
        blank = df[col].isna() | df[col].map(
            lambda v: isinstance(v, str) and not v.strip())
        add(f"{label}({col}) 칸이 비어 있음", blank,
            f"{col} 를 채우세요 — 이 행은 화면에 안 나옵니다")
    add("인원이 숫자가 아님",
        pd.to_numeric(raw_people, errors="coerce").isna() & raw_people.notna(),
        "People 을 숫자로 고치세요 (0 으로 처리됨)")
    add("인원이 음수", df["People"] < 0,
        "People 이 음수입니다 — 합계에서 빼집니다")
    return out


def all_dates(df: pd.DataFrame) -> list[date]:
    """파일에 들어 있는 모든 투어일자 (나중 것 먼저)."""
    return sorted(df["TourDate"].dropna().unique(), reverse=True)


def latest_dates(df: pd.DataFrame, count: int = 2) -> list[date]:
    dates = sorted(df["TourDate"].dropna().unique(), reverse=True)[:count]
    return list(dates)


def lastmin_cutoff(target: date) -> datetime:
    """투어일자 D 의 라스트미닛 예약 컷오프 = (D - 1일) 10:00."""
    base = target - timedelta(days=C.LASTMIN_CUTOFF_DAYS_BEFORE)
    return datetime.combine(base, time(C.LASTMIN_CUTOFF_HOUR, 0))


@dataclass
class TourRow:
    """화면의 한 줄 = 수량 입력 1개."""
    area: str
    region: str
    product: str
    option: str = ""                     # 옵션분리 투어일 때만 채워짐
    key: str = ""                        # area|product|option
    option_split: bool = False
    people: int = 0                      # 이 날짜의 실제 예약 인원 합
    by_channel: dict = field(default_factory=dict)   # 채널별 예약 인원
    languages: list = field(default_factory=list)    # 선택 가능한 언어 후보
    pickups: list = field(default_factory=list)      # 선택 가능한 픽업 후보
    options: list = field(default_factory=list)      # 옵션 후보 (옵션분리 아닐 때)
    lastmin: dict = field(default_factory=dict)      # 컷오프 이후 들어온 예약 (채널별)

    def to_dict(self) -> dict:
        return {
            "area": self.area, "region": self.region, "product": self.product,
            "option": self.option, "key": self.key, "option_split": self.option_split,
            "people": self.people, "by_channel": self.by_channel,
            "languages": self.languages, "pickups": self.pickups, "options": self.options,
            "lastmin": self.lastmin,
        }


def _row_key(area: str, product: str, option: str) -> str:
    return f"{area}|{product}|{option}"


def build_tree(df: pd.DataFrame, target: date, with_lastmin: bool = False) -> list[dict]:
    """
    한 투어일자에 대한 Region > Area > Tour 트리.

    Language / Pickup / Option 후보는 '그 상품 전체'(파일의 모든 날짜) 기준으로 모은다.
    당일에 한 언어만 예약이 들어왔다고 해서 나머지 언어를 못 고르면 안 되기 때문이다.
    """
    day = df[df["TourDate"] == target]
    cutoff = lastmin_cutoff(target)

    # 상품 단위 후보값 (날짜 무관)
    cand: dict[tuple[str, str], dict] = {}
    for (area, product), g in df.groupby(["Area", "Product"], dropna=True):
        cand[(str(area), str(product))] = {
            "languages": _clean(g["Language"]),
            "pickups": _clean(g["Pickup"]),
            "options": sorted({o for o in g["OptionName"] if o}),
        }

    rows: list[TourRow] = []
    seen: set[str] = set()

    for (area, product), g in day.groupby(["Area", "Product"], dropna=True):
        area_s, product_s = str(area), str(product)
        meta = cand.get((area_s, product_s), {"languages": [], "pickups": [], "options": []})
        split = C.is_option_split(product_s)

        if split:
            buckets = g.groupby(g["OptionName"].replace("", C.NO_OPTION_LABEL), dropna=False)
            for opt, gg in buckets:
                opt_s = str(opt) if str(opt) else C.NO_OPTION_LABEL
                key = _row_key(area_s, product_s, opt_s)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(TourRow(
                    area=area_s, region=area_region(area_s), product=product_s,
                    option=opt_s, key=key, option_split=True,
                    people=int(gg["People"].sum()),
                    by_channel=_channel_counts(gg),
                    lastmin=_lastmin_counts(gg, cutoff) if with_lastmin else {},
                    languages=_clean(gg["Language"]) or meta["languages"],
                    pickups=_clean(gg["Pickup"]) or meta["pickups"],
                    options=[],
                ))
        else:
            key = _row_key(area_s, product_s, "")
            if key in seen:
                continue
            seen.add(key)
            rows.append(TourRow(
                area=area_s, region=area_region(area_s), product=product_s,
                option="", key=key, option_split=False,
                people=int(g["People"].sum()),
                by_channel=_channel_counts(g),
                lastmin=_lastmin_counts(g, cutoff) if with_lastmin else {},
                languages=meta["languages"],
                pickups=meta["pickups"],
                options=meta["options"],
            ))

    # Region > Area > Product 순으로 묶기
    def area_rank(a: str) -> int:
        return C.AREA_ORDER.index(a) if a in C.AREA_ORDER else len(C.AREA_ORDER)

    groups: list[dict] = []
    for group_name, areas in C.REGION_GROUPS:
        area_blocks = []
        for area in areas:
            items = [r for r in rows if r.area == area]
            if not items:
                continue
            items.sort(key=lambda r: (r.product, r.option))
            area_blocks.append({"area": area, "rows": [r.to_dict() for r in items]})
        if area_blocks:
            groups.append({"region": group_name, "areas": area_blocks})

    # REGION_GROUPS 에 없는 Area 는 마지막에 Other 로
    known = {a for _g, areas in C.REGION_GROUPS for a in areas}
    others = sorted({r.area for r in rows if r.area not in known}, key=area_rank)
    if others:
        area_blocks = []
        for area in others:
            items = sorted([r for r in rows if r.area == area], key=lambda r: (r.product, r.option))
            area_blocks.append({"area": area, "rows": [r.to_dict() for r in items]})
        groups.append({"region": "Other", "areas": area_blocks})

    return groups


def _lastmin_counts(g: pd.DataFrame, cutoff: datetime) -> dict[str, int]:
    """컷오프 이후에 들어온 예약만 채널별로 합산."""
    if "ResDT" not in g.columns:
        return {}
    sub = g[g["ResDT"] >= pd.Timestamp(cutoff)]
    if sub.empty:
        return {}
    out = _channel_counts(sub)
    return {ch: v for ch, v in out.items() if v > 0}


def _channel_counts(g: pd.DataFrame) -> dict[str, int]:
    """채널별 예약 인원 (전일 패널의 채널 표에 쓴다)."""
    out = {ch: 0 for ch in C.CHANNELS}
    code_to_ch = {v: k for k, v in C.CHANNEL_MAP.items()}
    # 같은 채널의 다른 표기도 함께 집계 (예: Trip.com 이 TPC / CP 로 섞여 들어옴)
    for ch, codes in getattr(C, "CHANNEL_ALIASES", {}).items():
        for code in codes:
            code_to_ch.setdefault(code, ch)
    for agency, gg in g.groupby("Agency", dropna=True):
        ch = code_to_ch.get(str(agency).strip())
        if ch:
            out[ch] += int(gg["People"].sum())
    return out


def load(source, filename: str = "") -> dict:
    """서버가 쓰는 진입점. {dates:[...], panels:[{date,is_latest,groups}...]}"""
    df = read_reservations(source, filename)
    dates = latest_dates(df, 2)
    if len(dates) < 2:
        raise ValueError("투어일자가 2개 이상 있어야 합니다. (오늘 + 내일 예약이 모두 들어있어야 함)")
    panels = []
    for idx, d in enumerate(dates):
        panels.append({
            "date": d.isoformat(),
            "date_label": d.strftime("%m/%d"),
            "is_latest": idx == 0,
            "groups": build_tree(df, d, with_lastmin=(idx != 0)),
        })
    return {"dates": [d.isoformat() for d in dates], "panels": panels}
