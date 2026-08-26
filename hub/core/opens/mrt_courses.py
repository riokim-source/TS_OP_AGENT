# -*- coding: utf-8 -*-
"""
mrt_courses.py
MRT 투어 -> '상품 페이지 안의 코스' 지정.

왜 필요한가
    MRT 는 상품ID 하나에 서로 다른 투어를 코스로 묶어 판다.
        5624093 = [11월-3월] 비에이 하이라이트
                  [4월-10월] 비에이 시그니처
                  [4월-10월] 비에이 & 후라노
    잔여인원 입력칸은 (인원구분 x 코스 x 출발) 마다 하나씩 있다.
    코스를 안 고르면 그 날짜 열의 모든 칸에 수량이 나눠 들어간다.
    실제로 'Biei Signature 19' 가 시그니처 10 / 후라노 9 로 갈라져 들어갔다.

여기 값은 '페이지 줄 텍스트에 들어있는 조각' 이다. 공백은 무시하고 부분일치로 찾는다.
2026-08-23 에 실제 상품 페이지에서 읽은 코스명을 근거로 채웠다.
확실하지 않은 투어는 비워 둔다 -- 찍어서 열면 엉뚱한 코스가 열리므로,
비어 있으면 열지 않고 '코스 미지정' 으로 보고한다.
"""
from __future__ import annotations

import json
import re

from ..paths import DATA_DIR

OVERRIDE_PATH = DATA_DIR / "mrt_courses.json"

# 한 페이지에 코스가 여럿인 상품ID (2026-08-23 실측)
MULTI_COURSE_IDS: set[str] = {
    "3887808", "4397040", "4700281", "4956172", "4973348",
    "5616354", "5624093", "5632837", "5724343", "5912702",
}

# 투어명 -> 코스 조각. 실제 페이지 문구에서 그 코스만 가리키는 부분을 쓴다.
# 값이 리스트면 전부 들어있는 줄만 고른다 (AND).
#   Itoshima Marine 은 [코스 B] 여름코스 안에서 '조기 하차' 와 '하루 종일' 이 또 갈려서
#   두 조각이 다 필요하다.
# 공백은 무시하고 부분일치로 찾는다.
# 2026-08-23 에 실제 상품 페이지에서 읽은 코스명 + 운영팀 확인.
COURSES: dict[str, str | list[str]] = {
    # 3887808  [코스 A] 후지산 하이라이트 / [코스 C] 후지산 시그니처
    "Fuji Highlight":        "후지산 하이라이트",
    "Mt. Fuji Highlight":    "후지산 하이라이트",
    "Mt. Fuji Signature":    "후지산 시그니처",
    # 4397040  [코스 A] 교토 & 나라 / [코스 B] 교토 & 나라 2
    #   두 코스 다 '교토 & 나라' 로 적혀 있어서 이름으로는 못 가른다.
    #   'A' 로 찾으면 'A2' 도 걸리므로 반드시 대괄호 코스표기를 쓴다.
    "Kyoto & Nara":          "[코스 A]",
    "Arashiyama & Nishiki":  "[코스 B]",
    # 4700281  [코스 A] 유후인·벳푸·맥주공장or진격거 / [코스 B] 유후인·벳푸·다자이후
    #   'Yufuin' 단독은 코스가 아니라 두 코스를 묶은 이름이라 일부러 안 넣는다.
    "Yufuin Brewery":        "맥주공장",
    "Yufuin Dazaifu":        "다자이후",
    # 4956172  [코스 A] 고베 데이 투어 / [코스 B] 고베 나이트 투어
    "Osaka & Kobe":          "고베 데이",
    "Osaka Kobe (Night)":    "고베 나이트",
    # 4973348  [10월-3월] 구마모토 1일 투어 / [4월-9월] 구마모토+미야자키 1일 투어
    "Kumamoto Takachiho":    "구마모토+미야자키",
    "Kumamoto Volcano":      "[10월-3월]",
    # 5616354  코스A [인생샷&힐링워킹] / 코스B [지그재그 증기기관차/시닉월드]
    "Blue Mountain Bushwalk": "인생샷",
    "Blue Mountain Zig Zag":  "지그재그",
    # 5624093  비에이 하이라이트 / 비에이 시그니처 / 비에이 & 후라노
    "Biei Highlight":        "비에이 하이라이트",
    "Biei Signature":        "비에이 시그니처",
    "Biei Furano":           "비에이 & 후라노",
    # 5632837  [코스 A] 야나가와 + 딸기 / [코스 B] 야나가와+우키하이나리
    "Yanagawa Boat":         "야나가와 + 딸기",
    "Yanagawa Ukiha":        "우키하",
    # 5724343  [코스 A] 가마쿠라 하이라이트 / [코스 B] 가마쿠라&요코하마
    "Kamakura Highlight":    "가마쿠라 하이라이트",
    "Kamakura Yokohama":     "요코하마",
    # 5912702  [코스 A] 봄/가을코스 / [코스 B] 여름코스(조기 하차 · 하루 종일)
    "Itoshima Flower":       "[코스 A]",
    "Itoshima Marine":       ["여름코스", "하루 종일"],
}


def _overrides() -> dict:
    """운영 중에 고칠 수 있도록 파일 우선."""
    if not OVERRIDE_PATH.exists():
        return {}
    try:
        raw = json.loads(OVERRIDE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {str(k): str(v) for k, v in (raw.get("courses") or {}).items() if str(v).strip()}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).casefold()


def course_of(tour: str) -> str | list[str] | None:
    """투어명 -> 코스 조각(문자열 또는 조각 목록). 없으면 None."""
    table = dict(COURSES)
    table.update(_overrides())
    key = _norm(tour)
    for name, course in table.items():
        if _norm(name) == key:
            return course
    return None


def is_multi_course(product_id: str) -> bool:
    ids = set(MULTI_COURSE_IDS)
    if OVERRIDE_PATH.exists():
        try:
            raw = json.loads(OVERRIDE_PATH.read_text(encoding="utf-8"))
            ids.update(str(x) for x in (raw.get("multi_course_ids") or []))
        except Exception:
            pass
    return str(product_id) in ids
