# -*- coding: utf-8 -*-
"""
productmap.py
투어명 -> 각 OTA 상품 매핑.

Klook 은 packages.py 가 이미 완전한 매핑이라 여기서 다루지 않는다.
나머지 OTA 는 운영팀 맵핑리스트(txt)를 읽어 여기에 쌓는다.

저장: hub/data/productmap.json
  {
    "MRT": {"Toyako Niseko": {"ids": ["5889847"], "area": "Sapporo"}},
    "KK":  {"경주": {"product_no": "17654", "package_letter": "A"}},
    "VI":  {"...": {"code": "48881P..."}},
    "GG":  {"...": {"option_prefix": "ToyakoNiseko"}}
  }

⚠️ 자동 이름 매칭은 쓰지 않는다. 실제 재고를 여닫는 작업이라
   '아마 이 상품일 것' 으로 돌리면 엉뚱한 상품에 재고가 열린다.
   매핑에 없는 투어는 그냥 실행 대상에서 빠지고 사유가 남는다.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

from .paths import DATA_DIR

MAP_PATH = DATA_DIR / "productmap.json"
SCHEMA_VERSION = 1
CHANNELS = ["MRT", "KK", "VI", "GG"]


def norm(name: str) -> str:
    """조회용 정규화. 'Mt.Fuji Highlight' == 'Mt. Fuji Highlight'."""
    s = re.sub(r"\s+", " ", str(name or "").strip())
    s = re.sub(r"\.(?=\S)", ". ", s)
    return re.sub(r"\s+", " ", s).strip().casefold()


class ProductMap:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or MAP_PATH)
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for ch in CHANNELS:
                    raw.setdefault(ch, {})
                raw["schema_version"] = SCHEMA_VERSION
                return raw
            except Exception:
                pass
        return {"schema_version": SCHEMA_VERSION, **{ch: {} for ch in CHANNELS}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    # ── 조회 ──────────────────────────────────────────────────────────────
    def get(self, tour: str, channel: str) -> dict | None:
        table = self.data.get(str(channel).upper(), {})
        key = norm(tour)
        for name, entry in table.items():
            if norm(name) == key:
                return entry
        return None

    def has(self, tour: str, channel: str) -> bool:
        return self.get(tour, channel) is not None

    def set(self, tour: str, channel: str, entry: dict | None) -> None:
        ch = str(channel).upper()
        table = self.data.setdefault(ch, {})
        existing = next((n for n in table if norm(n) == norm(tour)), None)
        if entry is None:
            if existing:
                table.pop(existing, None)
        else:
            table[existing or str(tour).strip()] = entry
        self.save()

    def coverage(self, tours: list[str]) -> dict:
        out = {}
        for ch in CHANNELS:
            have = [t for t in tours if self.has(t, ch)]
            out[ch] = {"mapped": len(have), "total": len(tours),
                       "missing": [t for t in tours if t not in have]}
        return out

    # ── 맵핑리스트 txt 에서 가져오기 ────────────────────────────────────────
    def import_from_files(self, overwrite: bool = False) -> dict:
        """
        운영팀 맵핑리스트를 읽어 반영. 기본은 '없는 것만 채우기'라
        화면에서 손으로 고친 값을 덮어쓰지 않는다.
        """
        from . import mapping_import as mi

        report = {"MRT": {"added": 0, "skipped": 0, "source": None},
                  "KK": {"added": 0, "skipped": 0, "source": None},
                  "errors": []}

        mrt = mi.parse_mrt()
        if mrt.get("ok"):
            report["MRT"]["source"] = mrt.get("source")
            for it in mrt["items"]:
                if not overwrite and self.has(it["tour"], "MRT"):
                    report["MRT"]["skipped"] += 1
                    continue
                self.data.setdefault("MRT", {})[it["tour"]] = {
                    "ids": it["ids"], "area": it.get("area", "")}
                report["MRT"]["added"] += 1
        else:
            report["errors"].append(mrt.get("error", "MRT 파싱 실패"))

        kk = mi.parse_kkday()
        if kk.get("ok"):
            report["KK"]["source"] = kk.get("source")
            # Package/Course 가 붙은 항목이 페이지 단위 항목보다 구체적이라 먼저 넣는다.
            # ('경주' 는 17654 페이지이기도 하고 그 안의 Course A 이기도 한데, Course A 가 맞다)
            kk_items = sorted(kk["items"], key=lambda x: 0 if x.get("package_letter") else 1)
            for it in kk_items:
                if not it["tour"]:
                    continue
                if not overwrite and self.has(it["tour"], "KK"):
                    report["KK"]["skipped"] += 1
                    continue
                entry = {"product_no": it["product_no"]}
                if it.get("package_letter"):
                    entry["package_letter"] = it["package_letter"]
                    entry["kind"] = it.get("kind")
                self.data.setdefault("KK", {})[it["tour"]] = entry
                report["KK"]["added"] += 1
        else:
            report["errors"].append(kk.get("error", "KKday 파싱 실패"))

        self.save()
        return report


_SINGLETON: ProductMap | None = None


def get_map() -> ProductMap:
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = ProductMap()
    return _SINGLETON
