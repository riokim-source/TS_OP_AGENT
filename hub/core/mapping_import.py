# -*- coding: utf-8 -*-
"""
mapping_import.py
운영팀이 관리하는 '맵핑리스트' 텍스트를 읽어 투어명 -> OTA 상품 매핑으로 바꾼다.

원본은 노션에서 붙여넣은 메모라 마크다운 잔재(**, \\, </span>, ~~)가 섞여 있다.
그걸 정리해서 기계가 쓸 수 있는 형태로 만든다. 원본 파일은 건드리지 않는다.

MRT   : '투어명 (상품ID)' 형태 -> 바로 매핑됨
KKDAY : '상품번호 투어명' + 'Package X : 투어명' 형태
        -> (product_no, package_letter) 까지만 알 수 있다.
           package_letter 를 실제 package_id 로 바꾸는 건 KKday 페이지를 봐야 한다.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from .paths import SYSTEM_DIR

MRT_FILE_HINTS = ["MRT 맵핑리스트", "MRT 매핑리스트", "mrt 맵핑리스트"]
KK_FILE_HINTS = ["kkday 맵핑리스트", "KKDAY 맵핑리스트", "kkday 매핑리스트"]

# 노션 붙여넣기 잔재
_JUNK = re.compile(r"(\*+|~~|</?span[^>]*>|\\)")
_WEEKDAY = re.compile(r"^[월화수목금토일]요일\s*:")


def _find(hints: list[str]) -> Path | None:
    for p in sorted(SYSTEM_DIR.glob("*.txt")):
        for h in hints:
            if h.lower() in p.name.lower():
                return p
    return None


def _read(path: Path) -> list[str]:
    for enc in ("utf-8", "utf-8-sig", "cp949", "utf-16"):
        try:
            return io.open(path, encoding=enc).read().splitlines()
        except Exception:
            continue
    return []


def _clean(line: str) -> str:
    return _JUNK.sub("", line).replace("​", "").strip()


def _norm_tour(name: str) -> str:
    """'Mt.Fuji Highlight' 와 'Mt. Fuji Highlight' 를 같게 본다."""
    s = re.sub(r"\s+", " ", str(name or "").strip())
    s = re.sub(r"\.(?=\S)", ". ", s)          # 'Mt.Fuji' -> 'Mt. Fuji'
    return re.sub(r"\s+", " ", s).strip()


# ──────────────────────────────────────────────────────────────────────────────
# MRT
# ──────────────────────────────────────────────────────────────────────────────
# 상품ID는 항상 괄호 안 6~9자리 숫자. 이름에도 괄호가 들어갈 수 있어서
# ('Osaka Kobe (Night) (4956172)') 이름을 정규식으로 자르지 않고
# '첫 번째 숫자 괄호' 위치를 찾아 그 앞을 이름으로 쓴다.
_MRT_ID = re.compile(r"\(\s*(\d{6,9})\s*\)")
_MRT_AREA = re.compile(r"^-\s*(?P<area>도쿄|오사카|후쿠오카|삿포로|나고야|호주|서울|부산)\s*$")

MRT_AREA_TO_REGION = {
    "도쿄": "Tokyo", "오사카": "Osaka", "후쿠오카": "Fukuoka", "삿포로": "Sapporo",
    "나고야": "Nagoya", "호주": "Sydney", "서울": "Seoul", "부산": "Busan",
}


def _collect_mrt(text: str, area: str, found: dict) -> None:
    """'이름 (상품ID)' 한 조각을 매핑에 넣는다."""
    m = _MRT_ID.search(text)
    if not m:
        return
    name = _norm_tour(text[:m.start()])
    name = name.strip(" -·,")
    if not name or len(name) < 2 or name[0].isdigit():
        return
    pid = m.group(1)
    cur = found.setdefault(name, {"tour": name, "area": area, "ids": []})
    if pid not in cur["ids"]:
        cur["ids"].append(pid)
    if not cur["area"] and area:
        cur["area"] = area


def parse_mrt(path: Path | None = None) -> dict:
    path = path or _find(MRT_FILE_HINTS)
    if path is None:
        return {"ok": False, "error": "MRT 맵핑리스트 파일을 찾지 못했습니다.", "items": []}

    area = ""
    found: dict[str, dict] = {}
    for raw in _read(path):
        line = _clean(raw)
        if not line:
            continue
        m = _MRT_AREA.match(line)
        if m:
            area = MRT_AREA_TO_REGION.get(m.group("area"), m.group("area"))
            continue
        # '월요일 : Yufuin Dazaifu (4700281) / Kumamoto Takachiho (4973348)'
        # 요일별 줄에도 유효한 '이름 (ID)' 쌍이 들어있다. Regular 줄에서 놓친 조합
        # (예: Yufuin 페이지 안의 Brewery / Dazaifu)이 여기 있어서 같이 읽는다.
        if _WEEKDAY.match(line):
            body = line.split(":", 1)[1]
            for part in body.split("/"):
                _collect_mrt(part.strip(), area, found)
            continue

        _collect_mrt(line.lstrip("-").strip(), area, found)

    return {"ok": True, "source": path.name, "items": sorted(found.values(), key=lambda x: x["tour"])}


# ──────────────────────────────────────────────────────────────────────────────
# KKDAY
# ──────────────────────────────────────────────────────────────────────────────
# 상품 페이지 헤더 예:
#   '- 8974 남이섬 레귤러'   '- - 부산시티 11186'
#   '남이섬 9353 (케이케이테이 순서)'   '부산시티 11186  (KKDAY 상품 순서)'
# 패키지 줄 예:
#   'Package A : (어)남쁘아 (월수목금일)'   'Course E : 캡슐요트 (월수금)'
_KK_PKG = re.compile(r"^(?P<kind>Package|Course)\s*(?P<letter>[A-Z])\s*:\s*(?P<name>.+)$", re.I)
_KK_NUM = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")

# 이름 뒤에 붙는 운영요일/날짜 메모. 이름의 일부인 '(어)' 같은 건 남겨야 해서 패턴을 좁게 잡는다.
_KK_DAYNOTE = re.compile(r"\(\s*(?:매일|[월화수목금토일][월화수목금토일/,\s]*)\s*\)")
_KK_DATENOTE = re.compile(r"\(?\s*\d{1,2}\s*/\s*\d{1,2}.*$")


def _kk_clean_name(text: str) -> str:
    """패키지 줄에서 투어명만 남긴다."""
    t = text.split("→")[0]
    t = t.split("*")[0]
    t = t.split("+")[0]          # '알남 (토/일) + 수 (4/8~5/31)' -> '알남'
    t = t.split(":")[0]          # '알레아 R : 09/15~' -> '알레아 R'
    t = _KK_DAYNOTE.sub(" ", t)
    t = _KK_DATENOTE.sub(" ", t)
    t = re.sub(r"\s+[A-Z]\s*$", "", t)   # 끝에 남은 단독 대문자(구간 표기) 제거
    t = re.sub(r"\s+", " ", t).strip(" -·,:")
    return _norm_tour(t)


def parse_kkday(path: Path | None = None) -> dict:
    """
    반환 items: [{tour, product_no, package_letter, kind}]

    ⚠️ package_letter('Package A')는 KKday 화면상의 순서 표기다.
       실제 마감/오픈에 쓰는 package_id / package_option_id 와는 다르며,
       그 변환은 KKday 상품 페이지를 봐야 알 수 있다 (아직 미구현).
    """
    path = path or _find(KK_FILE_HINTS)
    if path is None:
        return {"ok": False, "error": "kkday 맵핑리스트 파일을 찾지 못했습니다.",
                "items": [], "products": {}}

    products: dict[str, str] = {}
    entries: list[dict] = []
    seen: set[tuple] = set()
    current: str | None = None

    for raw in _read(path):
        line = _clean(raw)
        if not line:
            continue

        m = _KK_PKG.match(line)
        if m:
            if not current:
                continue
            tour = _kk_clean_name(m.group("name"))
            if not tour:
                continue
            key = (tour, current, m.group("letter").upper())
            if key in seen:
                continue
            seen.add(key)
            entries.append({"tour": tour, "product_no": current,
                            "package_letter": m.group("letter").upper(),
                            "kind": m.group("kind").title()})
            continue

        # 상품 페이지 헤더: 숫자가 하나 들어있고 Package/Course 줄이 아닌 줄
        nums = _KK_NUM.findall(line)
        if len(nums) == 1:
            pid = nums[0]
            name = _KK_NUM.sub(" ", line)
            name = re.sub(r"\((?:KKDAY|케이케이테이)[^)]*\)", " ", name, flags=re.I)
            name = re.sub(r"\s+", " ", name).strip(" -·,:")
            name = _norm_tour(name)
            if name and not name[0].isdigit():
                current = pid
                products.setdefault(pid, name)
                key = (name, pid, None)
                if key not in seen:
                    seen.add(key)
                    entries.append({"tour": name, "product_no": pid,
                                    "package_letter": None, "kind": None})

    return {"ok": True, "source": path.name, "products": products, "items": entries}


def summary() -> dict:
    mrt = parse_mrt()
    kk = parse_kkday()
    return {"MRT": mrt, "KK": kk}
