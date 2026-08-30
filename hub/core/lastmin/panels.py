# -*- coding: utf-8 -*-
"""
panels.py
예약 파일 -> 화면에 뿌릴 패널(투어일자 2개 x Region > Area > Tour).

⚠️ 화면이 둘(기존 콘솔 / Streamlit)이라 이 변환을 한 곳에만 둔다.
   각자 따로 만들면 언어·픽업 후보가 화면마다 달라지고,
   같은 파일을 올렸는데 다른 결과가 나오는 상황이 된다.
"""
from __future__ import annotations

from datetime import datetime

from . import constants as C
from . import loader as lmloader
from .memo import available_languages
from .. import pickups as lmpickups


def build_panels(raw: bytes, filename: str,
                 pick_dates: list | None = None) -> dict:
    """
    반환: {"ok", "panels", "loaded", "option_split_tours", "pickup_catalog", "df"}
    실패 시 {"ok": False, "error"}.

    pick_dates 를 주면 그 두 날짜를 쓴다. 안 주면 가장 나중 2개다.

    ⚠️ 파일에 날짜가 3개 이상이면 '가장 나중 2개' 가 내일이 아닐 수 있다.
       추출 범위를 넓게 잡아 9/15 가 섞여 들어오면 오픈 대상 패널이 9/15 가
       되고, 내일만 있는 투어는 화면에 아예 안 나와 그날 안 열린다.
       그래서 날짜가 3개 이상이면 loaded["date_choice"] 로 알리고,
       화면에서 사람이 직접 고르게 한다.
    """
    df = lmloader.read_reservations(raw, filename)
    all_dates = lmloader.all_dates(df)
    dates = (pick_dates or [])[:2] or lmloader.latest_dates(df, 2)
    dates = [d for d in dates if d in all_dates][:2]
    if len(dates) < 2:
        return {"ok": False,
                "error": "투어일자가 2개 이상 있어야 합니다 (오늘 + 내일)."}
    dates = sorted(dates, reverse=True)

    pk_cat = lmpickups.load()
    panels = []
    for idx, d in enumerate(dates):
        # 전일 패널만 'Last Min 10시 후 예약' 을 자동 집계한다
        groups = lmloader.build_tree(df, d, with_lastmin=(idx != 0))
        for g in groups:
            for a in g["areas"]:
                for row in a["rows"]:
                    base = row["product"]
                    if (row["option_split"] and row["option"]
                            and row["option"] != C.NO_OPTION_LABEL):
                        base = f"{row['product']}({row['option']})"
                    # 언어 후보 = 예약에 나온 언어 U Klook 이 파는 언어
                    row["languages"] = available_languages(base, row["languages"])
                    # 픽업 후보 = 예약에 나온 픽업지 U GG 가 파는 픽업지
                    #   예약이 없는 픽업지도 골라야 '홍대 제외' 같은 지시가 나온다
                    row["pickups"] = lmpickups.merge(row["product"], row["pickups"], pk_cat)
                    row["display"] = base
        panels.append({
            "date": d.isoformat(),
            "date_label": d.strftime("%m/%d"),
            "is_latest": idx == 0,
            "groups": groups,
        })

    return {
        "ok": True,
        "df": df,
        "panels": panels,
        "loaded": {
            "filename": filename,
            "rows": int(len(df)),
            "dates": [d.isoformat() for d in dates],
            # 읽다가 버린 행 (이유별). 비어 있으면 전부 정상이다.
            "problems": list(df.attrs.get("problems") or []),
            # 파일에 있는 모든 투어일자. 3개 이상이면 화면에서 고르게 한다.
            "all_dates": [d.isoformat() for d in all_dates],
            "date_choice": len(all_dates) > 2,
            "at": datetime.now().strftime("%H:%M:%S"),
        },
        "option_split_tours": C.OPTION_SPLIT_TOURS,
        "pickup_catalog": lmpickups.info(),
    }
