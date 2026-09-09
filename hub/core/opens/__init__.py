# -*- coding: utf-8 -*-
"""
opens
OTA 별 '오픈' 실행기.

각 OTA 가 실제로 무엇을 할 수 있는지가 다르다. 이건 우리 구현의 한계가 아니라
플랫폼의 모델 차이라서, 여기서 명시적으로 구분해 둔다.

  KLOOK : 상품별 inventory 숫자.  마감 = 0, 오픈 = N. 완전 대칭.
  MRT   : 날짜별 '잔여 인원' 숫자. 마감 = 0, 오픈 = N. 완전 대칭.
  GG    : availability 벌크 Block/Unblock + capacity. 수량 개념은 있으나
          현재 마감 봇이 '페이지 단위 벌크' 라서 오픈은 상품 단위 루프가 필요.
  KK    : 세션 status 토글 (Ceased selling <-> Resume). 수량 제한 없음.
  TPC   : (라우팅 key = CP) 날짜별 On/off 토글. 재고는 999 로 잡혀 있어
          숫자를 받지 않는다. 마감/오픈이 같은 창의 Close/Open sales 다.
  VI    : Sold out 토글. 수량은 pricing schedule 에 묶여 있어 직접 못 정함.
          ⚠️ 'Not operating' 은 예약 취소 의미라 절대 건드리지 않는다.

그래서 라스트미닛 메모가 KLOOK/GG/CP/MRT 에는 숫자를, KK/VI 에는 상품명만
찍는 게 우연이 아니다. 각 플랫폼이 받을 수 있는 것과 정확히 맞는다.
"""
from __future__ import annotations

# 채널별 지원 상태
#   "qty"    : 수량 오픈 지원
#   "resume" : 수량 없이 판매 재개만
#   None     : 아직 미구현 (실행 시 명시적으로 SKIP 처리하고 사유를 남긴다)
CAPABILITY: dict[str, str | None] = {
    "KLOOK": "qty",
    "MRT": "qty",
    "GG": "qty",
    "KK": "resume",
    "VI": "resume",
    # TPC(Trip.com/Ctrip). 라우팅표의 key 는 예전부터 CP 다.
    #
    # 수량이 아니라 판매 재개다. vBooking 은 날짜별 재고를 999 로 잡아 두고
    # (화면에 "Sold inventory: 0/999") 실제로는 On/off 로만 운영한다.
    # 2026-09-09 지정 상품 10개를 읽어서 확인했다. 그래서 숫자를 받지 않는다.
    "CP": "resume",
}

IMPLEMENTED: set[str] = {"KLOOK", "MRT", "GG", "VI", "CP"}

NOT_IMPLEMENTED_REASON: dict[str, str] = {
    "KK": "KKday 판매 재개(Resume selling)는 kkday.py 의 Ceased selling 반대 동작 구현이 필요합니다.",
}


def summarize_plan(plan: list[dict]) -> dict:
    """실행 전에 '무엇이 실제로 돌고 무엇이 스킵되는지' 를 보여주기 위한 요약."""
    runnable: dict[str, list[dict]] = {}
    skipped: dict[str, list[dict]] = {}
    for item in plan:
        ch = item["channel"]
        bucket = runnable if ch in IMPLEMENTED else skipped
        bucket.setdefault(ch, []).append(item)
    return {
        "runnable": runnable,
        "skipped": skipped,
        "runnable_count": sum(len(v) for v in runnable.values()),
        "skipped_count": sum(len(v) for v in skipped.values()),
        "reasons": {ch: NOT_IMPLEMENTED_REASON.get(ch, "미구현") for ch in skipped},
    }
