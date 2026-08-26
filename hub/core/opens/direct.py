# -*- coding: utf-8 -*-
"""
direct.py
'특정 상품만 골라 열거나 닫기' 를 OTA 별로 다룬다.

수량 수집을 거치지 않고 사람이 상품을 골라 바로 실행하는 길이다.
수집·오픈이 끝난 뒤 몇 개만 더 열거나 수량을 고치는 일이 매일 생긴다.

⚠️ 채널마다 상품을 부르는 이름이 다르다.
     KLOOK  packages.py 의 패키지 이름 ('에버', 'Biei Signature(한)')
     그 외   내부 투어명 (productmap.json 의 키)
   그래서 한 번에 한 채널만 다룬다. 섞으면 이름이 어느 쪽인지 알 수 없다.

⚠️ '수량 0 = 마감' 은 KLOOK 만 된다.
   MRT/GG 오픈은 0 을 건너뛴다 (열 것이 없다는 뜻이라서). 그 채널을 닫으려면
   마감 봇을 써야 한다. 여기서는 무엇이 되는지 명시적으로 알려준다.
"""
from __future__ import annotations

from . import CAPABILITY, IMPLEMENTED, NOT_IMPLEMENTED_REASON  # noqa: F401
from ..productmap import get_map
from . import klook_open

# 이름을 어디서 가져오나
NAME_SOURCE = {
    "KLOOK": "packages",      # Klook 패키지 이름
    "MRT": "productmap",
    "KK": "productmap",
    "VI": "productmap",
    "GG": "productmap",
}

# 수량 0 으로 '마감' 까지 되는 채널.
# 채널마다 '닫는다' 의 실제 동작이 다르다.
#   KLOOK  Inventory 0 + Activate OFF
#   MRT    remainQuantity 0
#   GG     Block 켜기 (정원 0 이 아니다 — 위 gg_open.close_one 참고)
CLOSE_BY_ZERO = {"KLOOK", "MRT", "GG"}

# 지역을 사람이 골라야 하는 채널.
#   KLOOK  packages.py 에 지역이 붙어 있다
#   MRT    productmap 의 area 를 쓴다
#   GG     맵핑표를 쓰지 않고 화면에서 이름으로 찾는다 -> 어느 Chrome 으로
#          들어갈지 알 수 없으므로 사람이 골라야 한다
NEEDS_REGION = {"GG"}
GG_REGIONS = ["KOREA", "JAPAN", "AUSTRALIA", "UK"]

CHANNEL_LABEL = {"KLOOK": "Klook", "MRT": "MyRealTrip", "GG": "GetYourGuide",
                 "KK": "KKday", "VI": "Viator", "CP": "Trip.com/Ctrip"}


def channels() -> list[dict]:
    """
    직접 오픈에서 고를 수 있는 채널과, 각각 지금 무엇이 되는지.

    되는 것만 보여주면 '왜 KKday 는 없지?' 를 매번 묻게 된다.
    안 되는 것도 이유와 함께 보여준다.
    """
    out = []
    for ch in ("KLOOK", "MRT", "GG", "KK", "VI"):
        n = len(catalog(ch))
        out.append({
            "channel": ch,
            "label": CHANNEL_LABEL.get(ch, ch),
            "ready": ch in IMPLEMENTED,
            "reason": NOT_IMPLEMENTED_REASON.get(ch, ""),
            "count": n,
            "can_close": ch in CLOSE_BY_ZERO,
            "searchable": n > 0,
            "needs_region": ch in NEEDS_REGION,
            "regions": list(GG_REGIONS) if ch in NEEDS_REGION else [],
        })
    return out


def catalog(channel: str) -> list[dict]:
    """
    그 채널에서 고를 수 있는 상품 목록.

    없으면 빈 목록이다 (예: GG 는 맵핑표가 아직 비어 있다). 그때는 사람이
    이름을 직접 적을 수 있게 두고, 화면에서 '찾기가 없다' 고 말한다.
    """
    ch = str(channel or "").upper()
    if ch == "KLOOK":
        return [{"name": c["name"], "region": c.get("region", ""),
                 "id": str(c.get("id", "")), "workflow": c.get("workflow", "")}
                for c in klook_open.catalog()]
    table = (get_map().data.get(ch) or {})
    out = []
    for name, entry in sorted(table.items()):
        ids = entry.get("ids") or []
        out.append({"name": name, "region": entry.get("area", ""),
                    "id": ", ".join(str(i) for i in ids), "workflow": ""})
    return out


def text_to_plan(channel: str, text: str,
                 region: str = "") -> tuple[list[dict], list[str]]:
    """'상품명 수량' 여러 줄 -> 오픈 계획. 반환 (계획, 형식이 이상한 줄)."""
    ch = str(channel or "").upper()
    plan, bad = klook_open.text_to_plan(text)      # 형식 해석은 한 벌만 쓴다
    for p in plan:
        p["channel"] = ch
        if ch in NEEDS_REGION and region:
            p["region"] = region
    return plan, bad


def plan_to_lines(plan: list[dict]) -> str:
    return "\n".join(f"{p['product']} {int(p['qty'])}"
                     for p in plan if p.get("mode") == "qty")


def preview(channel: str, plan: list[dict], target_date: str) -> dict:
    """
    실행 전에 '무엇이 실제로 돌고 무엇이 빠지는지'.

    반환 {rows, unknown, warnings, date_text}
      rows    실제로 실행될 것
      unknown 이름을 찾지 못한 것 (그대로 실행하면 빠진다)
    """
    ch = str(channel or "").upper()
    if ch == "KLOOK":
        pv = klook_open.preview(plan, target_date)
        rows = [{"지역": rg, "상품": t["name"], "수량": t["qty"],
                 "방식": t["workflow"],
                 "": "마감(0)" if int(t["qty"] or 0) == 0 else ""}
                for rg, tasks in (pv.get("regions") or {}).items() for t in tasks]
        return {"rows": rows,
                "unknown": [u.get("text") for u in (pv.get("unknown") or [])],
                "warnings": list(pv.get("warnings") or []),
                "date_text": pv.get("date_text") or target_date}

    if ch in NEEDS_REGION:
        # 맵핑표를 쓰지 않는다. 이름은 화면에서 찾으므로 여기서 확인할 수 없다.
        # 대신 무엇이 그대로 나가는지 보여주고, 이름이 다르면 실행 결과에서
        # '못 찾음' 으로 돌아온다.
        rows, warnings = [], []
        for p in plan:
            qty = int(p.get("qty") or 0)
            name = str(p.get("product") or "")
            if qty < 0:
                continue
            rows.append({"지역": p.get("region") or "", "상품": name, "수량": qty,
                         "방식": "이름으로 찾음",
                         "": "마감(Block)" if qty == 0 else ""})
        return {"rows": rows, "unknown": [], "warnings": warnings,
                "date_text": target_date}

    m = get_map()
    rows, unknown, warnings = [], [], []
    for p in plan:
        name = str(p.get("product") or "")
        qty = int(p.get("qty") or 0)
        entry = m.get(name, ch)
        if not entry or not entry.get("ids"):
            unknown.append(f"{name} {qty}")
            continue
        if qty <= 0 and ch not in CLOSE_BY_ZERO:
            warnings.append(f"{name}: 수량 0 은 {CHANNEL_LABEL.get(ch, ch)} 오픈에서 "
                            f"건너뜁니다. 닫으려면 [Inventory 마감] 을 쓰세요.")
            continue
        rows.append({"지역": entry.get("area", ""), "상품": name, "수량": qty,
                     "방식": ", ".join(str(i) for i in entry["ids"]),
                     "": "마감(0)" if qty == 0 else ""})
    return {"rows": rows, "unknown": unknown, "warnings": warnings,
            "date_text": target_date}
