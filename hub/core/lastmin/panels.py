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


def build_panels(raw: bytes, filename: str) -> dict:
    """
    반환: {"ok", "panels", "loaded", "option_split_tours", "pickup_catalog", "df"}
    실패 시 {"ok": False, "error"}.
    """
    df = lmloader.read_reservations(raw, filename)
    dates = lmloader.latest_dates(df, 2)
    if len(dates) < 2:
        return {"ok": False,
                "error": "투어일자가 2개 이상 있어야 합니다 (오늘 + 내일)."}

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
            "at": datetime.now().strftime("%H:%M:%S"),
        },
        "option_split_tours": C.OPTION_SPLIT_TOURS,
        "pickup_catalog": lmpickups.info(),
    }
