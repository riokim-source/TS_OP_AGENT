# -*- coding: utf-8 -*-
"""
packages.py
Klook Merchant Center 상품 매핑 단일 source of truth.

⚠️  구조:
- 16개의 작은 dict 로 분리되어 있음 (region × workflow × accept_until).
- 각 dict 안에는 {상품명: id} 만 적으면 됨. 나머지 속성(region, workflow,
  accept_until)은 dict 이름으로 자동 결정되어 PACKAGES 에 주입됨.
- 외부 API (PACKAGES, get_package, classify, ...) 는 기존과 동일.

⚠️  새 상품 추가 방법:
- 해당 region / workflow / accept_until 조건의 dict 를 찾아 한 줄 추가:
    'Sydney Tour': '123456',
- 만약 어느 그룹인지 헷갈리면 → 일반 상품은 *_DEFAULT, 하루 전 22:00 마감은 *_LATE22

⚠️  중복 검사:
- 같은 상품명이 두 dict 에 들어가 있으면 import 시 ValueError 발생.
  (운영 실수 방지용 안전망)

⚠️  분류 판별 기준 (Klook Merchant Center 화면 기준):
- 'See schedule' 버튼이 보임 → 신버전 (activity)
- 'Price / Inventory Settings' 버튼만 보임 → 구버전 (package)
- 좌측 사이드바에 여러 패키지가 있음 → 구버전 (Package ID 로 지정해야 함)

⚠️  지원 region:
- KOREA      (Chrome remote-debugging-port 9522)
- JAPAN      (Chrome remote-debugging-port 9523)
- AUSTRALIA  (Chrome remote-debugging-port 9524)
- UK         (Chrome remote-debugging-port 9525, London 포함)
"""
from __future__ import annotations
import re
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# Accept bookings until 기본값
# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_ACCEPT_UNTIL = {'when': 'same_day', 'time': '06:00'}
LATE22_ACCEPT_UNTIL  = {'when': 'day_before', 'time': '22:00'}

# ──────────────────────────────────────────────────────────────────────────────
# Korea / Package workflow / 당일 06:00 (기본값)
# id = Package ID (구버전 UI)
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_KOREA_PACKAGE_DEFAULT: dict[str, str] = {
    # 서울
    # 남이섬
    '남쁘': '486802',
    '레남쁘': '94329',
    '남쁘아': '486801',
    '남아': '486796',
    '레남아': '486797',
    '쁘남레아': '407743',
    '알남아': '284812',
    '알남': '284825',
    '알남레': '277737',
    '알레남빛': '344442',
    '남이섬셔틀(Ferry Ticket)': '488101',
    '남이섬셔틀': '710483',
    '남레': '356595',
    '광장시장': '291791',
    # EG
    '포천': '323510',
    #'수원화성': '283007',
    '수원화성': '312271',
    '수원광명': '746218',
    # BTS
    'BTS': '298378',
    'Seasonal BTS': '517622',
    # 다크투어
    '다크-서대문': '506213',
    '다크-남영동': '539364',
    # 레고
    '레고랜드': '490439',
    '레고레일': '493765',
    # 꽃
    '벚꽃랜덤(서울)': '3030',
    '겹벚꽃': '33641',
    #가을
    '장태산': '123251',
    # 겨울
    '딸썰어': '320099',
    '어딸남레': '320544',
    '딸남레': '320545',
    # 부산
    '감천미포(Sightseeing)': '531290',
    '감천미포': '299440',
    '청해자감': '694032',
    '아해자감': '694034',
    '캡슐요트': '376785',
    '태감송해': '486807',
    '송감송해': '356374',
    '자갈치선셋': '611250',
    '자갈치야경': '611251',
    '선셋캡슐 East': '531889',
    # 경주
    '경주': '32580',
    '경주 Express': '676245',
    '교촌경주': '684715',
    '경주캡슐': '562245',
    # 꽃
    '광양구례(부산)': '276580',
    '진해(부산)': '484992',
    '진해야간(부산)': '484993',
    '캡슐벚꽃': '9386',
    '경주벚꽃': '9148',
}

# ──────────────────────────────────────────────────────────────────────────────
# Korea / Package workflow / 하루 전 22:00
# id = Package ID (구버전 UI)
# accept_until = {'when': 'day_before', 'time': '22:00'}
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_KOREA_PACKAGE_LATE22: dict[str, str] = {
    # 꽃
    '광양전주(서울)': '276579',
    '진해(서울)': '348798',
    # 단풍
    '내장산': '5474',
    '오대산': '5456',
    '설악산단풍': '5466',
    '대둔산': '5469',
    # 겨울
    '어비비발디': '320537',
}

# ──────────────────────────────────────────────────────────────────────────────
# Korea / Activity workflow / 당일 06:00 (기본값)
# id = Activity ID (신버전 UI)
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_KOREA_ACTIVITY_DEFAULT: dict[str, str] = {
    # 서울
    '에버': '2968',
    '남설': '7802',
    '설낙': '2529',
    '저스트비 템플스테이': '210770',
    # MBC
    'MBC 스튜디오': '107366 ',
    'MBC 스튜디오(중)': '107366 ',
    'MBC 스튜디오(한)': '107366 ',
    'MBC 스튜디오(드라마 리허설)': '107366 ',
    'MBC 스튜디오(드라마 리허설)(중)': '107366 ',
    'MBC 스튜디오(드라마 리허설)(한)': '107366 ',
    # 부산
    '특공대(부산)': '107988',
    'BTS(부산)': '208114',
    'BTS 셔틀(부산)': '213130',
}

# ──────────────────────────────────────────────────────────────────────────────
# Korea / Activity workflow / 하루 전 22:00
# id = Activity ID (신버전 UI)
# accept_until = {'when': 'day_before', 'time': '22:00'}
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_KOREA_ACTIVITY_LATE22: dict[str, str] = {
    # TODO: 해당 조건의 상품 등록 시 추가. 형식: '상품명': 'ID',
}

# ──────────────────────────────────────────────────────────────────────────────
# Japan / Package workflow / 당일 06:00 (기본값)
# id = Package ID (구버전 UI)
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_JAPAN_PACKAGE_DEFAULT: dict[str, str] = {
    # 도쿄
    'Mt. Fuji Highlight': '391454',
    'Mt. Fuji Highlight(중)': '535021',
    'Mt. Fuji Signature': '624759',
    'Mt. Fuji Signature(중)': '615377',
    'Fuji Shibazakura': '630311',
    'Kamakura Highlight': '654890',
    'Kamakura Yokohama': '654891',
    'Mt. Fuji Hiking': '555571',
    # 오사카
    'Amanohashidate': '515156',
    'Amanohashidate(한)': '606537',
    'Kyoto & Nara': '515200',
    'Kyoto & Nara(한)': '606542',
    'Kyoto & Nara(중)': '666472',
    'Arashiyama & Nishiki': '607151',
    'Arashiyama & Nishiki(한)': '607153',
    'Arashiyama & Nishiki(중)': '666473',
    'Osaka Kobe': '608215',
    # 나고야
    'Shirakawago Regular': '656584',
    'Shirakawago Regular(한)': '656585',
    # 후쿠오카
    'Yufuin Brewery': '558363',
    'Yufuin Brewery(한)': '610085',
    'Yufuin Brewery(중)': '667048',
    'Yufuin Dazaifu': '558364',
    'Yufuin Dazaifu(한)': '610096',
    'Yufuin Dazaifu(중)': '667049',
    'Fukuoka Foodie': '578996',
    'Fukuoka Foodie(한)': '610110',
    'Kumamoto Volcano': '612519',
    'Kumamoto Volcano(한)': '628807',
    'Kumamoto Takachiho': '661445',
    'Kumamoto Takachiho(한)': '661551',
    'Yanagawa Boat': '642012',
    'Yanagawa Boat(한)': '642013',
    'Yanagawa Boat(중)': '667057',
    'Yanagawa Ukiha': '679335',
    'Yanagawa Ukiha(한)': '679337',
    'Yanagawa Ukiha(중)': '679341',
    # 삿포로
    'Biei Highlight': '632196',
    'Biei Highlight(한)': '641932',
    'Biei Signature': '662841',
    'Biei Signature(한)': '662876',
    'Biei Furano': '662892',
    'Biei Furano(한)': '662934',
}

# ──────────────────────────────────────────────────────────────────────────────
# Japan / Package workflow / 하루 전 22:00
# id = Package ID (구버전 UI)
# accept_until = {'when': 'day_before', 'time': '22:00'}
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_JAPAN_PACKAGE_LATE22: dict[str, str] = {
    # TODO: 해당 조건의 상품 등록 시 추가. 형식: '상품명': 'ID',
}

# ──────────────────────────────────────────────────────────────────────────────
# Japan / Activity workflow / 당일 06:00 (기본값)
# id = Activity ID (신버전 UI)
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_JAPAN_ACTIVITY_DEFAULT: dict[str, str] = {
    # 신규 페이지 (Klook 새 UI Activity)
    # 도쿄
    'Tokyo Summer Sonic Shuttle': '224901',
    'Tokyo Summer Sonic Shuttle(한)': '224901',
    # 오사카
    #'Osaka Kobe': '180751',
    #'Osaka Kobe(한)': '180751',
    'Osaka Kobe (Night)': '216946',
    'Osaka Kobe (Night)(한)': '216946',
    'Osaka Nagoya City': '216051',
    'Osaka Nagoya City(한)': '216051',
    # 삿포로
    'Toyako Niseko': '204084',
    'Toyako Niseko(한)': '204084',
    'Sapporo Otaru': '203872',
    'Sapporo Otaru(한)': '203872',
    'Shakotan Otaru': '217774',
    # 후쿠오카
    'Itoshima Marine': '222117',
    'Itoshima Marine(한)': '222117',
    'Nagasaki': '231128',
    'Nagasaki(한)': '231128',
    # 꽃
    'Hitachi Ashikaga': '145488',
    'Hitachi Ashikaga(한)': '145488',
    'Itoshima Flower': '204406',
    'Itoshima Flower(한)': '204406',
}

# ──────────────────────────────────────────────────────────────────────────────
# Japan / Activity workflow / 하루 전 22:00
# id = Activity ID (신버전 UI)
# accept_until = {'when': 'day_before', 'time': '22:00'}
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_JAPAN_ACTIVITY_LATE22: dict[str, str] = {
    # TODO: 해당 조건의 상품 등록 시 추가. 형식: '상품명': 'ID',
}

# ──────────────────────────────────────────────────────────────────────────────
# Australia / Package workflow / 당일 06:00 (기본값)
# id = Package ID (구버전 UI)
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_AUSTRALIA_PACKAGE_DEFAULT: dict[str, str] = {
    # TODO: 해당 조건의 상품 등록 시 추가. 형식: '상품명': 'ID',
}

# ──────────────────────────────────────────────────────────────────────────────
# Australia / Package workflow / 하루 전 22:00
# id = Package ID (구버전 UI)
# accept_until = {'when': 'day_before', 'time': '22:00'}
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_AUSTRALIA_PACKAGE_LATE22: dict[str, str] = {
    # TODO: 해당 조건의 상품 등록 시 추가. 형식: '상품명': 'ID',
}

# ──────────────────────────────────────────────────────────────────────────────
# Australia / Activity workflow / 당일 06:00 (기본값)
# id = Activity ID (신버전 UI)
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_AUSTRALIA_ACTIVITY_DEFAULT: dict[str, str] = {
    # 신규 페이지 (Klook 새 UI Activity, 시드니 출발)
    'Blue Mountain Bushwalk': '192042',
    'Blue Mountain Zig Zag': '193815',
    'Hunter Valley(WINERY)': '201033',
    'Hunter Valley(WINERY)(중)': '201033',
    'Hunter Valley(WINERY)(한)': '201033',
    'Hunter Valley(WINERY + SEGWAY)': '201033',
    'Hunter Valley(WINERY + SEGWAY)(중)': '201033',
    'Hunter Valley(WINERY + SEGWAY)(한)': '201033',
    'Hunter Valley(WINERY + ARCHERY)': '201033',
    'Hunter Valley(WINERY + ARCHERY)(중)': '201033',
    'Hunter Valley(WINERY + ARCHERY)(한)': '201033',
    'Wollongong Kiama': '207779',
}

# ──────────────────────────────────────────────────────────────────────────────
# Australia / Activity workflow / 하루 전 22:00
# id = Activity ID (신버전 UI)
# accept_until = {'when': 'day_before', 'time': '22:00'}
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_AUSTRALIA_ACTIVITY_LATE22: dict[str, str] = {
    # TODO: 해당 조건의 상품 등록 시 추가. 형식: '상품명': 'ID',
}

# ──────────────────────────────────────────────────────────────────────────────
# UK / Package workflow / 당일 06:00 (기본값)
# id = Package ID (구버전 UI)
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_UK_PACKAGE_DEFAULT: dict[str, str] = {
    # TODO: 해당 조건의 상품 등록 시 추가. 형식: '상품명': 'ID',
}

# ──────────────────────────────────────────────────────────────────────────────
# UK / Package workflow / 하루 전 22:00
# id = Package ID (구버전 UI)
# accept_until = {'when': 'day_before', 'time': '22:00'}
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_UK_PACKAGE_LATE22: dict[str, str] = {
    # TODO: 해당 조건의 상품 등록 시 추가. 형식: '상품명': 'ID',
}

# ──────────────────────────────────────────────────────────────────────────────
# UK / Activity workflow / 당일 06:00 (기본값)
# id = Activity ID (신버전 UI)
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_UK_ACTIVITY_DEFAULT: dict[str, str] = {
    # TODO: 해당 조건의 상품 등록 시 추가. 형식: '상품명': 'ID',
}

# ──────────────────────────────────────────────────────────────────────────────
# UK / Activity workflow / 하루 전 22:00
# id = Activity ID (신버전 UI)
# accept_until = {'when': 'day_before', 'time': '22:00'}
# ──────────────────────────────────────────────────────────────────────────────
PACKAGES_UK_ACTIVITY_LATE22: dict[str, str] = {
    # TODO: 해당 조건의 상품 등록 시 추가. 형식: '상품명': 'ID',
}

# ──────────────────────────────────────────────────────────────────────────────
# 자동 병합: 위 16개 dict 를 통합 PACKAGES dict 로 합침
# 각 항목에 region / workflow / accept_until 속성을 자동 주입한다.
# 같은 상품명이 두 dict 에 중복되면 ValueError 로 즉시 실패 → 운영 실수 방지.
# ──────────────────────────────────────────────────────────────────────────────
_SECTION_DEFS: list[tuple[dict, str, str, dict]] = [
    (PACKAGES_KOREA_PACKAGE_DEFAULT, 'KOREA', 'package', DEFAULT_ACCEPT_UNTIL),
    (PACKAGES_KOREA_PACKAGE_LATE22, 'KOREA', 'package', LATE22_ACCEPT_UNTIL),
    (PACKAGES_KOREA_ACTIVITY_DEFAULT, 'KOREA', 'activity', DEFAULT_ACCEPT_UNTIL),
    (PACKAGES_KOREA_ACTIVITY_LATE22, 'KOREA', 'activity', LATE22_ACCEPT_UNTIL),
    (PACKAGES_JAPAN_PACKAGE_DEFAULT, 'JAPAN', 'package', DEFAULT_ACCEPT_UNTIL),
    (PACKAGES_JAPAN_PACKAGE_LATE22, 'JAPAN', 'package', LATE22_ACCEPT_UNTIL),
    (PACKAGES_JAPAN_ACTIVITY_DEFAULT, 'JAPAN', 'activity', DEFAULT_ACCEPT_UNTIL),
    (PACKAGES_JAPAN_ACTIVITY_LATE22, 'JAPAN', 'activity', LATE22_ACCEPT_UNTIL),
    (PACKAGES_AUSTRALIA_PACKAGE_DEFAULT, 'AUSTRALIA', 'package', DEFAULT_ACCEPT_UNTIL),
    (PACKAGES_AUSTRALIA_PACKAGE_LATE22, 'AUSTRALIA', 'package', LATE22_ACCEPT_UNTIL),
    (PACKAGES_AUSTRALIA_ACTIVITY_DEFAULT, 'AUSTRALIA', 'activity', DEFAULT_ACCEPT_UNTIL),
    (PACKAGES_AUSTRALIA_ACTIVITY_LATE22, 'AUSTRALIA', 'activity', LATE22_ACCEPT_UNTIL),
    (PACKAGES_UK_PACKAGE_DEFAULT, 'UK', 'package', DEFAULT_ACCEPT_UNTIL),
    (PACKAGES_UK_PACKAGE_LATE22, 'UK', 'package', LATE22_ACCEPT_UNTIL),
    (PACKAGES_UK_ACTIVITY_DEFAULT, 'UK', 'activity', DEFAULT_ACCEPT_UNTIL),
    (PACKAGES_UK_ACTIVITY_LATE22, 'UK', 'activity', LATE22_ACCEPT_UNTIL),
]

PACKAGES: dict[str, dict] = {}
for _section_dict, _region, _workflow, _accept in _SECTION_DEFS:
    for _name, _pid in _section_dict.items():
        if _name in PACKAGES:
            raise ValueError(
                f"packages.py: 중복된 상품명 {_name!r} 이 여러 섹션에 있습니다. 한 섹션에만 등록하세요."
            )
        PACKAGES[_name] = {
            'id': str(_pid),
            'region': _region,
            'workflow': _workflow,
            'accept_until': dict(_accept),
        }
# 임시 변수 정리
del _section_dict, _region, _workflow, _accept, _name, _pid

# ──────────────────────────────────────────────────────────────────────────────
# 하위 호환: ACTIVITY_WORKFLOW_NAMES (PACKAGES 에서 동적 생성)
# ──────────────────────────────────────────────────────────────────────────────
ACTIVITY_WORKFLOW_NAMES = {
    name for name, info in PACKAGES.items() if info.get('workflow') == 'activity'
}

# ──────────────────────────────────────────────────────────────────────────────
# 조회 헬퍼
# ──────────────────────────────────────────────────────────────────────────────
def _norm(text: str) -> str:
    """공백 정규화 + 소문자화 (조회용)"""
    return re.sub(r'\s+', ' ', str(text or '').strip()).casefold()

# 정규화된 키 → 원본 키 매핑 (조회 가속용 캐시)
_NORM_LOOKUP: dict[str, str] = {_norm(k): k for k in PACKAGES.keys()}

def workflow_of(name: str) -> str:
    """상품명의 워크플로우 판정 (activity / package)"""
    info = get_package(name)
    return info['workflow'] if info else 'package'

def get_package(name: str) -> Optional[dict]:
    """
    상품명으로 매핑 조회. 정규화된 키 매칭.
    리턴: {
        'id': str,
        'region': str,
        'workflow': str,
        'canonical_name': str,
        'accept_until': {'when': str, 'time': str},
    } 또는 None
    """
    key = _norm(name)
    canonical = _NORM_LOOKUP.get(key)
    if not canonical:
        return None
    info = PACKAGES[canonical]
    accept_until = info.get('accept_until') or DEFAULT_ACCEPT_UNTIL
    return {
        'id': info['id'],
        'region': info['region'],
        'workflow': info.get('workflow', 'package'),
        'canonical_name': canonical,
        'accept_until': dict(accept_until),
    }

def accept_until_of(name: str) -> dict:
    """
    상품명의 Accept bookings until 설정 조회.
    매핑 없거나 명시 없으면 DEFAULT_ACCEPT_UNTIL.
    """
    info = get_package(name)
    if not info:
        return dict(DEFAULT_ACCEPT_UNTIL)
    return info['accept_until']

def classify(name: str):
    """
    legacy 호환용. (region, canonical_name, package_id, workflow) 또는 (None, name, None, None)
    """
    info = get_package(name)
    if not info:
        return None, name, None, None
    return info['region'], info['canonical_name'], info['id'], info['workflow']

def all_korea_names() -> list[str]:
    """Korea 상품 캐노니컬 이름 목록"""
    return sorted({k for k, v in PACKAGES.items() if v['region'] == 'KOREA'})

def all_japan_names() -> list[str]:
    """Japan 상품 캐노니컬 이름 목록"""
    return sorted({k for k, v in PACKAGES.items() if v['region'] == 'JAPAN'})

def all_australia_names() -> list[str]:
    """Australia 상품 캐노니컬 이름 목록"""
    return sorted({k for k, v in PACKAGES.items() if v['region'] == 'AUSTRALIA'})

def all_uk_names() -> list[str]:
    """UK (London 포함) 상품 캐노니컬 이름 목록"""
    return sorted({k for k, v in PACKAGES.items() if v['region'] == 'UK'})

def stats() -> dict:
    """매핑 통계 (디버그용)"""
    by_region_workflow: dict[tuple[str, str], int] = {}
    for k, v in PACKAGES.items():
        wf = v.get('workflow', 'package')
        by_region_workflow[(v['region'], wf)] = by_region_workflow.get((v['region'], wf), 0) + 1
    return {
        'total': len(PACKAGES),
        'by_region_workflow': by_region_workflow,
    }


if __name__ == '__main__':
    print('=== packages.py 자체 테스트 ===')
    s = stats()
    print(f"총 매핑: {s['total']}개")
    for (region, wf), count in sorted(s['by_region_workflow'].items()):
        print(f"  {region} / {wf}: {count}개")
    print('\n=== 조회 테스트 ===')
    for name in ['에버', 'TOYAKO NISEKO', '남쁘', 'Osaka Kobe', '포천', '없는상품']:
        info = get_package(name)
        print(f'  {name!r} -> {info}')
