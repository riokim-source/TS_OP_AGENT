# -*- coding: utf-8 -*-
"""
tpc_targets.py
TPC(Trip.com / vBooking) 에서 매일 마감·오픈하는 상품 목록. **목록의 주인은 이 파일 하나다.**

각 줄이 뜻하는 것
    name        vBooking 검색창 'Supplier product name' 에 넣는 내부명칭.
                화면에는 행마다 "Supplier product ID: <무엇>-<내부명칭>" 으로 보인다.
    product_id  상품 고유번호. 검색 결과 행의 tr[data-row-key] 와 같다.
    region      라우팅 지역 (KOREA / JAPAN / AUSTRALIA). 어느 Chrome 으로 들어갈지가 여기서 갈린다.

⚠️ 이중검색 — 이름과 번호가 서로를 검증한다
    내부명칭으로 **검색**한 다음, 그 결과 목록에서 **product_id 행**을 골라 들어간다.
    이름만으로 나온 첫 행을 쓰면 안 되고 (경주 검색 → 12건이 나온다),
    번호로 상품 수정 URL 에 바로 들어가도 안 된다 (그 번호가 그 상품인지 확인할 길이 없다).
    둘 중 하나라도 안 맞으면 '못 찾음' 이 아니라 **실패**로 남긴다.

⚠️ 패키지는 여기에 적지 않는다
    마감/오픈 창(On/off)에서 'Select All' 로 그 상품의 패키지를 전부 고른다.
    패키지는 시즌(十月~三月 / 三月~九月)·언어·전세로 갈려 있고 계절마다 이름이 바뀌므로,
    이름을 여기 박아 두면 시즌이 바뀐 날 조용히 어긋난다.
    그날 판매 중이 아닌 패키지는 화면이 'Not set' 으로 두므로 손대지 않는다.
"""
from __future__ import annotations

# 채널 코드.
#
# ⚠️ [TPC] 와 [CP] 는 **다른 채널이다.**
#      TPC : 이 봇이 여닫는 Trip.com 채널. 라스트미닛 메모의 [TPC] 줄이고,
#            오픈 계획의 channel 도 'TPC' 다 (2026-09-10 에 CP 에서 갈라 냈다).
#      CP  : 예전부터 있던 다른 채널. 봇이 없고 메모에 수량만 적힌다.
#
# ⚠️ 다만 **Chrome 라우팅표의 key 만** 아직 'CP' 다 (hub/data/routing.json).
#    그건 '어느 Chrome 창으로 들어가는가' 를 정하는 별개의 이름표라서 그대로 둔다.
#    나중에 CP 에도 봇이 생기면 그때 라우팅 key 를 갈라야 한다.
CHANNEL = "TPC"
ROUTING_CHANNEL = "CP"

PRODUCT_LIST_URL = "https://vbooking.ctrip.com/tour-activity-vbk-ssr/product-list"
HOST = "vbooking.ctrip.com"

# 2026-09-09 에 상품번호로 조회해서 화면의 'Supplier product ID' 표기와 하나씩
# 대조했다. 오른쪽 주석이 그때 화면에 있던 값이다.
#
# ⚠️ name 은 눈대중으로 적으면 안 된다. 'Mt.Fuji Highlight' 로 적혀 있던 것이
#    실제로는 'Mt. Fuji Highlight'(마침표 뒤 공백) 여서 검색 결과가 0건이었다.
#    이름이 한 글자만 달라도 그 상품은 그날 통째로 안 닫힌다.
TARGETS: list[dict] = [
    {"name": "경주",                "product_id": "57025021",  "region": "KOREA"},      # 경주-경주
    {"name": "감천미포",             "product_id": "52607026",  "region": "KOREA"},      # 감천미포-감천미포
    {"name": "선셋캡슐 East",        "product_id": "88485075",  "region": "KOREA"},      # 선셋캡슐 East-선셋캡슐 East
    {"name": "Yufuin Brewery",     "product_id": "94583915",  "region": "JAPAN"},      # Yufuin Brewery-…
    {"name": "Yufuin Dazaifu",     "product_id": "94584046",  "region": "JAPAN"},      # Yufuin Dazaifu-…
    {"name": "Biei Signature",     "product_id": "104487661", "region": "JAPAN"},      # Sapporo-Biei Signature
    {"name": "Biei Furano",        "product_id": "104488971", "region": "JAPAN"},      # Sapporo-Biei Furano
    {"name": "Biei Highlight",     "product_id": "103214093", "region": "JAPAN"},      # Biei Highlight-…
    {"name": "Mt. Fuji Highlight", "product_id": "107981358", "region": "JAPAN"},      # Mt. Fuji Highlight-…
    {"name": "Wollongong Kiama",   "product_id": "105738089", "region": "AUSTRALIA"},  # 호주 계정 (9524)
]


def _check() -> None:
    seen_id, seen_name = set(), set()
    for t in TARGETS:
        if not t["name"].strip() or not t["product_id"].strip().isdigit():
            raise ValueError(f"목록이 잘못됐습니다: {t}")
        if t["product_id"] in seen_id:
            raise ValueError(f"상품번호 중복: {t['product_id']}")
        if t["name"] in seen_name:
            raise ValueError(f"내부명칭 중복: {t['name']}")
        seen_id.add(t["product_id"])
        seen_name.add(t["name"])


_check()


def regions() -> list[str]:
    out: list[str] = []
    for t in TARGETS:
        if t["region"] not in out:
            out.append(t["region"])
    return out


def for_regions(wanted: list[str] | None = None) -> list[dict]:
    """지역으로 거른 목록. 빈 값이면 전체."""
    if not wanted:
        return list(TARGETS)
    up = {str(r).strip().upper() for r in wanted if str(r).strip()}
    return [t for t in TARGETS if t["region"] in up]


def by_names(names: list[str] | None = None) -> list[dict]:
    """
    내부명칭으로 고른 목록.

    ⚠️ 목록에 없는 이름은 조용히 버리지 않는다 — 부르는 쪽이 실패로 남길 수 있게
       (찾은 것, 못 찾은 것) 을 같이 돌려준다.
    """
    if not names:
        return list(TARGETS)
    table = {t["name"]: t for t in TARGETS}
    return [table[n] for n in names if n in table]


def unknown_names(names: list[str] | None = None) -> list[str]:
    if not names:
        return []
    table = {t["name"] for t in TARGETS}
    return [n for n in names if n not in table]


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).casefold()


def find_by_tour(tour: str) -> dict | None:
    """
    라스트미닛 메모의 투어 이름 -> 이 목록의 상품.

    ⚠️ 정확히 같은 이름일 때만 돌려준다. 비슷하다고 골라 주면 엉뚱한 상품을
       연다. 못 찾으면 None 이고, 부르는 쪽이 '맵핑 없음' 으로 남긴다.
       (Viator 가 같은 이유로 vi_targets 에 이름을 못 박아 두고 있다)
    """
    key = _norm(tour)
    if not key:
        return None
    for t in TARGETS:
        if _norm(t["name"]) == key:
            return t
    return None


def names() -> list[str]:
    return [t["name"] for t in TARGETS]


def label_of(product_id: str) -> str:
    for t in TARGETS:
        if t["product_id"] == str(product_id):
            return t["name"]
    return str(product_id)
