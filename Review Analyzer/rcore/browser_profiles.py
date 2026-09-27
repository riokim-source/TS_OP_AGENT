# -*- coding: utf-8 -*-
"""
browser_profiles.py  (OP System 이식판)

원본(TOURSTORY_Review_Analyzer v1.3)은 자기만의 Chrome 프로필과 포트를 들고
있었다 (KR 9222 / JP 9223 / AU 9224 / UK 9225, 폴더는 앱 안의 Chrome_debug*).

OP System 에는 이미 매일 쓰는 Chrome 이 있다.

    KR 9522 · JP 9523 · AU 9524 · UK 9525 · GLOBAL 9530
    (hub/core/routing.py, 프로필 폴더는 %LOCALAPPDATA%\\OTABot\\chrome_*)

⚠️ 여기서 포트를 다시 적지 않는다. **hub/core/routing.py 하나가 주인이다.**
   두 벌로 두면 한쪽만 바뀌어 조용히 어긋난다. 그러면 리뷰 수집이 '로그인이
   안 된 Chrome' 에 붙어서 0건을 가져오고, 사람은 리뷰가 없는 줄 안다.

⚠️ 채널 이름이 서로 다르다. 리뷰 엔진은 예약 파일의 Agency 코드를 쓰고,
   OP 라우팅은 화면에 보이는 OTA 이름을 쓴다. 여기서 한 번만 옮긴다.

       리뷰 엔진   OP 라우팅      비고
       L      ->  KLOOK
       KK     ->  KK
       GG     ->  GG
       TPC    ->  CP            Trip.com 계정의 Chrome 키가 CP 다
       MRT    ->  MRT           OP 에서는 GLOBAL(9530) 에 붙는다

바깥에서 부르는 이름(BrowserProfileManager, REGION_LABELS, area_group)은
원본과 같게 둔다. core/pipeline.py 를 고치지 않기 위해서다.
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd

from collectors.config import SUPPORTED_CHANNELS

# ⚠️ 이름이 겹친다. 이 엔진에도 'core' 패키지가 있고 hub 에도 'core' 가 있다.
#    그냥 sys.path 에 hub 를 얹고 'from core.routing import ...' 하면, 이미
#    올라와 있는 이 엔진의 core 를 보고 'routing 이 없다' 며 죽는다.
#    그래서 hub 쪽 core 를 **다른 이름으로** 올린다. routing.py 안의
#    'from .paths import ...' 가 그 이름 안에서 풀리도록 패키지째 올린다.
_OP_ROOT = Path(__file__).resolve().parents[2]
_HUB_CORE = _OP_ROOT / "hub" / "core"
_ALIAS = "op_hub_core"


def _load_hub_core():
    if _ALIAS in sys.modules:
        return sys.modules[_ALIAS]
    spec = importlib.util.spec_from_file_location(
        _ALIAS, _HUB_CORE / "__init__.py",
        submodule_search_locations=[str(_HUB_CORE)])
    if spec is None or spec.loader is None:
        raise ImportError(f"OP 의 hub/core 를 찾지 못했습니다: {_HUB_CORE}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_ALIAS] = mod
    spec.loader.exec_module(mod)
    return mod


_load_hub_core()
_routing_mod = importlib.import_module(f"{_ALIAS}.routing")
Routing = _routing_mod.Routing
area_region = _routing_mod.area_region
port_open = _routing_mod.port_open

# 리뷰 엔진 채널 -> OP 라우팅 채널
CHANNEL_TO_OP = {"L": "KLOOK", "KK": "KK", "GG": "GG", "TPC": "CP", "MRT": "MRT"}

REGION_LABELS = {
    "KOREA": "Korea",
    "JAPAN": "Japan",
    "AUSTRALIA": "Australia",
    "UK": "UK",
    "OTHER": "Other / Unmapped",
}


def area_group(area: str) -> str:
    """Area -> Region. OP 의 판정을 그대로 쓴다 (예약 파일이 같은 파일이다)."""
    return area_region(area)


def _norm_area(area) -> str:
    return re.sub(r"\s+", " ", str(area or "").strip())


class BrowserProfileManager:
    """
    원본과 같은 얼굴을 하고 있지만, 속은 OP 의 routing.py 를 본다.

    pipeline.py 가 쓰는 것만 있으면 된다:
      build_collection_plan / required_profiles / unconfigured_routes
      profile_name / profile_port / debug_address / ensure_profile
    """

    def __init__(self, routing: Routing | None = None):
        self.routing = routing or Routing()

    # ── 프로필 ────────────────────────────────────────────────────────
    def profile_info(self, key: str) -> dict:
        return self.routing.profile(key) or {}

    def profile_name(self, key: str) -> str:
        return str(self.profile_info(key).get("name") or key)

    def profile_port(self, key: str) -> int:
        return int(self.routing.profile_port(key))

    def debug_address(self, key: str) -> str:
        return f"127.0.0.1:{self.profile_port(key)}"

    def is_running(self, key: str) -> bool:
        return port_open(self.profile_port(key))

    def ensure_profile(self, key: str, wait_seconds: float = 20.0):
        """
        Chrome 이 떠 있게 한다. 반환은 원본과 같은 (started, ready).

        started = 이번에 우리가 띄웠나 / ready = 지금 붙을 수 있나
        """
        already = self.is_running(key)
        res = self.routing.ensure(key, wait_seconds=wait_seconds) or {}
        ready = bool(res.get("ok", self.is_running(key)))
        return (not already and ready), ready

    # ── 어느 Chrome 으로 갈 것인가 ────────────────────────────────────
    def route_profile(self, area: str, channel: str) -> str | None:
        ch = CHANNEL_TO_OP.get(str(channel or "").strip().upper())
        if not ch:
            return None
        return self.routing.route(_norm_area(area), ch)

    # ── 수집 계획 ─────────────────────────────────────────────────────
    def build_collection_plan(self, crawl_df, selected_channels: Iterable[str] | None = None
                              ) -> dict[str, list[dict]]:
        """
        예약 줄들을 (채널, Chrome) 묶음으로 나눈다. 원본과 같은 모양으로 돌려준다.
        """
        selected = set(selected_channels or SUPPORTED_CHANNELS)
        if crawl_df is None or len(crawl_df) == 0:
            return {}

        plan: dict[str, dict] = {}
        for idx, row in crawl_df.iterrows():
            ch = str(row.get("Agency", "")).strip().upper()
            if ch not in selected or ch not in SUPPORTED_CHANNELS:
                continue
            area = str(row.get("Area", ""))
            profile = self.route_profile(area, ch)
            item = plan.setdefault(ch, {}).setdefault(profile, {
                "channel": ch, "profile": profile,
                "areas": set(), "row_indices": [], "dates": [],
            })
            item["areas"].add(area)
            item["row_indices"].append(idx)
            item["dates"].append(row.get("Date"))

        out: dict[str, list[dict]] = {}
        for ch, profiles in plan.items():
            out[ch] = []
            for profile, item in profiles.items():
                # ⚠️ NaT 는 None 이 아니다. 그냥 두면 min()/max() 가 NaT 를 돌려주고
                #    뒤에서 strftime 이 터진다 (원본의 주의사항 그대로).
                dates = [d for d in item["dates"] if d is not None and not pd.isna(d)]
                out[ch].append({
                    "channel": ch, "profile": profile,
                    "areas": sorted(item["areas"]),
                    "row_indices": list(item["row_indices"]),
                    "start_date": min(dates) if dates else None,
                    "end_date": max(dates) if dates else None,
                })
        return out

    def required_profiles(self, crawl_df, selected_channels: Iterable[str] | None = None
                          ) -> dict[str, dict]:
        req: dict[str, dict] = {}
        for ch, entries in self.build_collection_plan(crawl_df, selected_channels).items():
            for e in entries:
                if e["profile"] is None:
                    continue
                item = req.setdefault(e["profile"], {"channels": set(), "areas": set()})
                item["channels"].add(ch)
                item["areas"].update(e.get("areas", []))
        return req

    def unconfigured_routes(self, crawl_df, selected_channels: Iterable[str] | None = None
                            ) -> list[dict]:
        """
        Chrome 이 정해지지 않은 (Area, 채널). **조용히 아무 Chrome 으로 보내지 않는다.**
        엉뚱한 계정에서 리뷰를 긁으면 0건이 나오고, 사람은 리뷰가 없는 줄 안다.
        """
        out: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for ch, entries in self.build_collection_plan(crawl_df, selected_channels).items():
            for e in entries:
                if e["profile"] is not None:
                    continue
                for area in e.get("areas", []):
                    key = (area, ch)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({"area": area, "channel": ch,
                                "region": area_group(area)})
        return out
