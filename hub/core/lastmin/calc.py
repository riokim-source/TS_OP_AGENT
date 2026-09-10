# -*- coding: utf-8 -*-
"""
calc.py
라스트미닛 수량 분배 규칙.

⚠️ 이 파일의 숫자와 분기는 기존 "Last Miniute Writer" 에서 그대로 복원한 것이다.
   스크린샷의 실제 메모 출력과 대조 검증했다:
     - 경주 25   (Busan, 일반지역, Office) -> KLOOK 13 / GG 12 / KK·VI 이름만
     - 교촌경주 13 (Busan, 일반지역, Office) -> KLOOK 13 만
     - Toyako Niseko 24 (Sapporo, 특별지역, Office) -> KLOOK 12 / GG 12 / VI 이름만 / KK 없음
                                          (OP)     -> CP 12 / MRT 12
     - Blue Mountain Zig Zag 5 (Sydney, 일반지역)   -> KLOOK 5 만
   규칙을 바꾸면 운영 수량이 달라진다. 바꿀 땐 위 케이스로 다시 검증할 것.
"""
from __future__ import annotations

import re

from . import constants as C


def distribute_general(product: str, remain: int, threshold: int) -> dict[str, list[str]]:
    """
    일반 지역 규칙.
      remain >= threshold : GG 절반(내림), KLOOK 나머지, KK/VI 는 '상품명만'
      remain <  threshold : KLOOK 전량
    """
    out: dict[str, list[str]] = {ch: [] for ch in C.CHANNELS}
    if remain >= threshold:
        gg = remain // 2
        klook = remain - gg
        if klook > 0:
            out["KLOOK"].append(f"{product} {klook}")
        if gg > 0:
            out["GG"].append(f"{product} {gg}")
        out["KK"].append(product)
        out["VI"].append(product)
    else:
        out["KLOOK"].append(f"{product} {remain}")
    return out


def distribute_special(product: str, q: int, is_op: bool) -> dict[str, list[str]]:
    """
    특별 지역(Tokyo/Osaka/Fukuoka/Sapporo) 규칙.

    Office(is_op=False)
      CP/MRT           : q >= 1  -> CP, MRT 둘 다 전량
      KLOOK/GG/KK/VI   : q >= 15 -> GG 절반, KLOOK 나머지, VI 이름만, KK 는 제외
                         q <  15 -> KLOOK 전량
    OP(is_op=True)
      CP/MRT           : 10 <= q < 20 -> MRT 만 전량
                         q >= 20      -> CP 절반, MRT 나머지
      KLOOK/GG/KK/VI   : q >= 20 -> GG 절반, KLOOK 나머지, VI 이름만, KK 는 제외
                         q <  20 -> KLOOK 전량
    """
    out: dict[str, list[str]] = {ch: [] for ch in C.CHANNELS}

    # ── MRT ──────────────────────────────────────────────────────────────
    # ⚠️ MRT 는 예전 그대로다. CP 와 한 덩어리로 적혀 있었을 뿐, 규칙은 별개다.
    if not is_op:
        if q >= 1:
            out["MRT"].append(f"{product} {q}")
    else:
        if 10 <= q < 20:
            out["MRT"].append(f"{product} {q}")
        elif q >= 20:
            out["MRT"].append(f"{product} {q - q // 2}")

    # ── CP ───────────────────────────────────────────────────────────────
    # ⚠️ 2026-09-11: CP 와 TPC 의 수량 규칙을 서로 바꿨다.
    #    CP 가 GG 와 같은 규칙을 쓰고 (Office 15↑ 절반 / OP 20↑ 절반),
    #    TPC 가 예전 CP 규칙(Office 전량 / OP 20↑ 절반)을 쓴다.
    #    MRT 는 그대로다.
    cp_floor = C.THRESHOLD_OP if is_op else C.THRESHOLD_OFFICE
    if q >= cp_floor:
        out["CP"].append(f"{product} {q // 2}")

    # KLOOK / GG / VI  (KK 는 특별지역에서 제외)
    threshold = C.THRESHOLD_OP if is_op else C.THRESHOLD_OFFICE
    if q >= threshold:
        gg = q // 2
        klook = q - gg
        if klook > 0:
            out["KLOOK"].append(f"{product} {klook}")
        if gg > 0:
            out["GG"].append(f"{product} {gg}")
        out["VI"].append(product)
    else:
        out["KLOOK"].append(f"{product} {q}")
    return out


_TPC_CACHE: dict | None = None


def _tpc_targets():
    """
    OTA Close/tpc_targets.py 를 읽는다. **목록의 주인은 그 파일 하나다.**

    여기에 목록을 복사해 두면 안 된다. 두 곳이 어긋나면 메모에는 적혀 있는데
    봇은 그 상품을 모르는 상황이 된다.
    """
    global _TPC_CACHE
    if _TPC_CACHE is not None:
        return _TPC_CACHE
    _TPC_CACHE = {"ok": False, "find": None}
    try:
        from ..paths import ota_close_dir, ensure_on_syspath
        if ensure_on_syspath(ota_close_dir()):
            import tpc_targets as tt      # type: ignore
            _TPC_CACHE = {"ok": True, "find": tt.find_by_tour}
    except Exception:
        pass
    return _TPC_CACHE


def in_tpc_list(product: str) -> bool:
    """그 상품이 TPC 지정 목록에 있는가 (이름이 정확히 같을 때만)."""
    t = _tpc_targets()
    return bool(t["ok"] and t["find"](product) is not None)


def tpc_share(product: str, q: int, is_op: bool) -> int:
    """
    TPC(Trip.com) 한 상품의 수량. 0 이면 메모에 안 적는다.

    ⚠️ [CP] 와 **다른 채널이다.** CP 는 아직 봇이 없고 수집 텍스트만 그대로 두며,
       TPC 는 2026-09 에 새로 붙인 채널로 tpc.py 봇이 실제로 여닫는다.

    ⚠️ 지정 목록(tpc_targets.py)에 없는 상품은 Office/OP 둘 다 건너뛴다.
       봇이 열 수 없는 것을 메모에 적으면 사람이 손으로 찾아 열어야 한다.

    수량 규칙 (2026-09-11 에 CP 와 서로 바꾼 것)
        Office : q >= 1  -> 전량
        OP     : q >= 20 -> 절반   (20 미만은 적지 않는다)
    지역과 무관하다 — 한국 상품도 같은 규칙을 쓴다.
    """
    if q <= 0 or not in_tpc_list(product):
        return 0
    if not is_op:
        return q
    return q // 2 if q >= C.THRESHOLD_OP else 0


def distribute(area: str, product: str, qty: int, is_op: bool) -> dict[str, list[str]]:
    """한 상품(=화면 한 줄)의 수량을 채널별로 분배."""
    out: dict[str, list[str]] = {ch: [] for ch in C.CHANNELS}
    q = int(qty)
    if q <= 0:
        return out
    if area in C.SPECIAL_CP_MRT:
        out = distribute_special(product, q, is_op)
    else:
        threshold = C.THRESHOLD_OP if is_op else C.THRESHOLD_OFFICE
        out = distribute_general(product, q, threshold)

    # TPC 는 지역 규칙을 타지 않는다. 지정 목록에 있으면 GG 와 같은 몫을 준다.
    n = tpc_share(product, q, is_op)
    if n > 0:
        out["TPC"].append(f"{product} {n}")
    return out


_QTY_TAIL = re.compile(r"^(?P<name>.+?)\s+(?P<qty>\d+)\s*$")


def split_across(total: int, n: int) -> list[int]:
    """
    수량을 n 개로 나눈다. 나머지는 앞쪽부터 1씩.
        8, 2 -> [4, 4]
        9, 2 -> [5, 4]
        5, 3 -> [2, 2, 1]

    MRT 픽업 분할, GG 픽업 분할, Klook 언어 변형 분할이 모두 같은 규칙을 쓴다.
    """
    if n <= 0:
        return []
    base, rem = divmod(max(0, int(total)), n)
    return [base + (1 if i < rem else 0) for i in range(n)]


def move_channel(dist: dict, src: str, dst: str) -> dict:
    """
    분배 결과에서 한 채널 몫을 다른 채널로 통째로 옮긴다.

    쓰는 곳: Klook 은 픽업지 단위로 막을 수 없어서, 특정 픽업지를 빼야 하는 건은
    Klook 에 열면 안 된다. 그 몫을 GG 로 넘긴다 (GG 는 픽업지별 옵션이 따로 있다).

    같은 상품이 양쪽에 있으면 수량을 합친다.
        KLOOK 'A 5' + GG 'A 5'  ->  GG 'A 10'
    """
    out = {ch: list(v) for ch, v in dist.items()}
    moved = out.pop(src, [])
    out[src] = []
    if not moved:
        return out

    target: dict[str, int | None] = {}
    order: list[str] = []
    for entry in out.get(dst, []) + moved:
        m = _QTY_TAIL.match(entry)
        name = (m.group("name") if m else entry).strip()
        qty = int(m.group("qty")) if m else None
        if name not in target:
            target[name] = qty
            order.append(name)
        elif qty is not None:
            target[name] = (target[name] or 0) + qty

    out[dst] = [f"{n} {target[n]}" if target[n] is not None else n for n in order]
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Auto 모드(서울) 차량 정원 기반 잔여 계산
# ──────────────────────────────────────────────────────────────────────────────
def split_guides(cell) -> list[str]:
    """Guide 셀 -> 중복 제거된 가이드 이름 목록."""
    if cell is None:
        return []
    text = str(cell).strip()
    if not text or text.lower() == "nan":
        return []
    names = [re.sub(r"\s+", " ", n.strip()) for n in text.split(",")]
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def pick_vehicle(total_with_guides: int, caps: dict[str, int], product: str) -> tuple[str, int]:
    """정원이 total 을 담을 수 있는 가장 작은 차량. 없으면 Bus."""
    if product in C.WALKING_PRODUCTS:
        return "Walking", caps["Walking"]
    for v in ("Staria", "Solati", "County", "Bus"):
        if total_with_guides <= caps[v]:
            return v, caps[v]
    return "Bus", caps["Bus"]


def remaining_for(vehicle: str, total_with_guides: int, is_op: bool) -> int:
    caps = C.CAP_OP if is_op else C.CAP_OFFICE
    cap = caps.get(vehicle, caps["Bus"])
    return max(cap - int(total_with_guides), 0)
