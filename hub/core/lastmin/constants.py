# -*- coding: utf-8 -*-
"""
constants.py
라스트미닛 계산 상수. 값은 전부 기존 "Last Miniute Writer" 에서 그대로 가져온 것으로,
스크린샷의 실제 메모 출력(경주 25 -> KLOOK 13 / GG 12, Toyako Niseko 24 -> 특별지역 규칙)과
대조 검증했다. 임의로 바꾸면 운영 수량이 달라진다.
"""
from __future__ import annotations

# OTA 채널 (출력 순서 = 메모 줄 순서)
CHANNELS: list[str] = ["KLOOK", "KK", "VI", "GG", "CP", "MRT"]

# 채널 -> 예약 파일 Agency 컬럼 코드
#
# ⚠️ [CP] 는 Trip.com/Ctrip 인데 예약 파일에서는 'TPC' 로 들어온다.
#    예전에는 'CP' 를 읽어서 Trip.com 예약을 거의 다 놓쳤다
#    (2026-08-21~22 파일 기준 TPC 62건 vs CP 2건).
#    메모에 찍히는 채널 이름은 [CP] 그대로 두고, 읽는 코드만 TPC 로 맞춘다.
CHANNEL_MAP: dict[str, str] = {
    "KLOOK": "L", "KK": "KK", "VI": "VI", "GG": "GG", "CP": "TPC", "MRT": "MRT",
}

# 같은 채널로 취급할 보조 코드 (파일에 두 표기가 섞여 들어오는 경우)
CHANNEL_ALIASES: dict[str, list[str]] = {
    "CP": ["TPC", "CP"],
}

# Area 는 예약 파일의 Area 값. 화면에서는 Region 으로 묶어서 보여준다.
REGION_GROUPS: list[tuple[str, list[str]]] = [
    ("Korea",     ["Seoul", "Busan"]),
    ("Japan",     ["Tokyo", "Osaka", "Nagoya", "Fukuoka", "Sapporo"]),
    ("Australia", ["Sydney"]),
    ("UK",        ["London"]),
]
AREA_ORDER: list[str] = [a for _g, areas in REGION_GROUPS for a in areas]

# CP / MRT 를 별도 규칙으로 태우는 지역 = Japan 전체
#   Office: 1명만 있어도 CP·MRT 전량 / OP: 10~19 MRT만, 20+ 반반
#
# Nagoya 는 2026-08-23 에 추가했다. 그 전까지는 빠져 있어서 나고야 투어
# (Shirakawago Regular) 가 CP·MRT 메모에 한 번도 올라가지 않았다.
# 운영팀 확인 결과 도쿄·오사카와 동일하게 취급하는 것이 맞다.
SPECIAL_CP_MRT: frozenset[str] = frozenset({"Tokyo", "Osaka", "Nagoya", "Fukuoka", "Sapporo"})

# 차량 정원
VEHICLE_ORDER: list[str] = ["Walking", "Staria", "Solati", "County", "Bus"]
CAP_OFFICE: dict[str, int] = {"Walking": 8, "Staria": 8, "Solati": 14, "County": 16, "Bus": 44}
CAP_OP: dict[str, int] = {"Walking": 8, "Staria": 8, "Solati": 13, "County": 15, "Bus": 42}

# 차량이 필요 없는(도보) 상품
WALKING_PRODUCTS: frozenset[str] = frozenset({"광장시장", "다크-서대문", "다크-남영동"})

# 분배 임계값
THRESHOLD_OFFICE = 15
THRESHOLD_OP = 20

# 'Last Min 10시 후 예약' 컷오프
#   투어일자 D 의 라스트미닛 창은 D-1 일 10:00 에 열린다.
#   그 이후에 들어온 예약이 곧 '라스트미닛으로 받은 예약' 이다.
#   (2026-08-21 투어 기준으로 실제 운영 메모의 7개 숫자와 정확히 일치함을 확인)
LASTMIN_CUTOFF_DAYS_BEFORE = 1
LASTMIN_CUTOFF_HOUR = 10

# ──────────────────────────────────────────────────────────────────────────────
# 옵션별로 행을 분리해서 집계하는 투어
#   그 외 투어는 Product 단위로 합산한다.
#   이름은 예약 파일 Product 값과 부분일치(대소문자 무시)로 매칭한다.
# ──────────────────────────────────────────────────────────────────────────────
OPTION_SPLIT_TOURS: list[str] = [
    "Biwako Valley",
    "CJ 스튜디오",
    "Fujiten",
    "Hunter Valley",
    "Kuju Forest Park",
    "Rokko Snow Park",
    "Yeti",
    "감천미포",
    "남이비발디",
    "남이섬셔틀",
    "남이엘리시안",
    "부트레그쇼셔틀",
    "어비비발디",
    "어비엘리시안",
    "에덴",
    "지산",
]

# 언어 표기 (메모용)
LANG_KO: dict[str, str] = {
    "english": "영어",
    "korean": "한국어",
    "chinese": "중국어",
    "japanese": "일본어",
}

# Klook packages.py 의 다국어 변형 접미사
LANG_SUFFIX: dict[str, str] = {
    "korean": "한",
    "chinese": "중",
    "japanese": "일",
    "english": "",   # 접미사 없는 기본 상품
}

NO_OPTION_LABEL = "(옵션없음)"


def is_option_split(product: str) -> bool:
    p = str(product or "").strip().casefold()
    if not p:
        return False
    return any(t.casefold() in p for t in OPTION_SPLIT_TOURS)


def lang_label(lang: str) -> str:
    return LANG_KO.get(str(lang or "").strip().casefold(), str(lang or "").strip())
