# -*- coding: utf-8 -*-
"""
routing.py
Chrome 인스턴스(Profile) 레지스트리 + (Region x OTA) -> Profile 라우팅.

이 파일이 시스템 전체에서 "어떤 Chrome 에 붙을지" 의 유일한 source of truth 다.
Klook Open 과 OTA Close 가 각자 .bat 을 들고 있다가 같은 포트에 다른 프로필을 물려서
서로를 죽이던 문제를 구조적으로 없앤다.

핵심 안전장치 — profile_owns_port()
    지금까지는 is_port_alive(9222) 가 True 면 "우리 Chrome" 이라고 믿었다.
    그런데 9222 를 다른 프로필의 Chrome 이 점유하고 있어도 True 라서,
    봇이 로그인 안 된 엉뚱한 Chrome 에 attach 한 뒤 전부 실패했다.

    판정 순서:
      1) 그 포트를 LISTEN 중인 프로세스의 --user-data-dir 을 직접 읽어서 대조
         (최신 Chrome 은 DevToolsActivePort 파일을 안 남기는 경우가 있어 이게 1순위)
      2) <user-data-dir>/DevToolsActivePort 가 있으면 그 값
      3) 둘 다 못 알아내면 판정을 포기하고 예전 동작(포트 살아있으면 OK)으로 폴백
         — 여기서 막아버리면 멀쩡한 Chrome 도 못 쓰게 된다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Iterable

from .paths import DATA_DIR

CONFIG_PATH = DATA_DIR / "routing.json"
SCHEMA_VERSION = 1

# ──────────────────────────────────────────────────────────────────────────────
# OTA 채널
#   key   : 내부 코드 (라스트미닛 CHANNELS 와 동일해야 한다)
#   label : 화면 표시
#   host  : CDP /json/list 에서 이 OTA 탭이 떠 있는지 판정할 호스트 조각
#   page  : Chrome 을 띄울 때 열어줄 URL
# ──────────────────────────────────────────────────────────────────────────────
# page  : Chrome 을 띄울 때 열어줄 URL
# probe : 로그인 확인용. '봇이 실제로 쓰는 작업 페이지' 여야 한다.
# expect: 로그인돼 있으면 최종 URL 에 반드시 남아 있어야 할 조각.
#
# ⚠️ 사이트 루트로 확인하면 안 된다. GetYourGuide 는 로그아웃 상태에서
#    supplier.getyourguide.com -> getyourguide.supply (마케팅 랜딩) 로 튕기는데
#    URL 에 'login' 이 없어서 '로그인됨' 으로 오판했다. 작업 페이지에 남아 있는지로 본다.
CHANNEL_META: dict[str, dict] = {
    "KLOOK": {"label": "Klook",          "hosts": ["merchant.klook.com", "klook.com"],
              "page": "https://merchant.klook.com/",
              "probe": "https://merchant.klook.com/",
              "expect": "merchant.klook.com"},
    "KK":    {"label": "KKday",          "hosts": ["scm.kkday.com", "kkday.com"],
              "page": "https://scm.kkday.com/",
              "probe": "https://scm.kkday.com/v1/en/product/productlist",
              "expect": "scm.kkday.com/v1"},
    "GG":    {"label": "GetYourGuide",   "hosts": ["supplier.getyourguide.com", "getyourguide.supply", "getyourguide.com"],
              "page": "https://supplier.getyourguide.com/manage/availability",
              "probe": "https://supplier.getyourguide.com/manage/availability",
              "expect": "supplier.getyourguide.com/manage"},
    "VI":    {"label": "Viator",         "hosts": ["supplier.viator.com", "viator.com"],
              "page": "https://supplier.viator.com/availability/",
              "probe": "https://supplier.viator.com/availability/",
              "expect": "supplier.viator.com/availability"},
    "CP":    {"label": "Trip.com/Ctrip", "hosts": ["vbooking.ctrip.com", "ctrip.com", "trip.com"],
              "page": "https://vbooking.ctrip.com/",
              "probe": "https://vbooking.ctrip.com/",
              "expect": "vbooking.ctrip.com"},
    "MRT":   {"label": "MyRealTrip",     "hosts": ["partner.myrealtrip.com", "myrealtrip.com"],
              "page": "https://partner.myrealtrip.com/products/experiences",
              "probe": "https://partner.myrealtrip.com/products/experiences",
              "expect": "partner.myrealtrip.com/products"},
}
CHANNELS: list[str] = list(CHANNEL_META.keys())

REGION_LABELS = {
    "KOREA": "Korea",
    "JAPAN": "Japan",
    "AUSTRALIA": "Australia",
    "UK": "UK",
    "OTHER": "Other / Unmapped",
}
REGIONS: list[str] = list(REGION_LABELS.keys())

# 예약 파일의 Area -> Region
AREA_REGION: dict[str, str] = {
    "seoul": "KOREA", "busan": "KOREA", "jeju": "KOREA", "gyeongju": "KOREA",
    "incheon": "KOREA", "suwon": "KOREA", "daegu": "KOREA",
    "tokyo": "JAPAN", "osaka": "JAPAN", "kyoto": "JAPAN", "fukuoka": "JAPAN",
    "sapporo": "JAPAN", "nagoya": "JAPAN", "hokkaido": "JAPAN",
    "sydney": "AUSTRALIA", "melbourne": "AUSTRALIA", "brisbane": "AUSTRALIA",
    "gold coast": "AUSTRALIA", "cairns": "AUSTRALIA",
    "london": "UK", "edinburgh": "UK",
}

_LOCAL = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")

# 저장할 때는 사용자 폴더를 %LOCALAPPDATA% 토큰으로 되돌린다.
#
# 왜: routing.json 에 이 PC 의 사용자 경로가 그대로 굳으면, 폴더를 다른 PC 로
# 복사했을 때 남의 계정 경로를 가리킨다. 그 경로는 없거나 권한이 없어서 Chrome 이
# 엉뚱한 곳에 프로필을 만들고, 로그인 안 된 채로 '떠 있음' 이 되어 조용히 실패한다.
# 토큰으로 저장해 두면 각자 PC 에서 자기 폴더로 풀린다.
_LOCAL_TOKEN = "%LOCALAPPDATA%"


def portable_dir(path: str) -> str:
    """저장용. 내 LOCALAPPDATA 로 시작하면 토큰으로 바꾼다."""
    p = str(path or "").strip()
    if not p:
        return p
    try:
        if os.path.normcase(p).startswith(os.path.normcase(_LOCAL)):
            return _LOCAL_TOKEN + p[len(_LOCAL):]
    except Exception:
        pass
    return p


def expand_dir(path: str) -> str:
    """읽기용. %LOCALAPPDATA% / ~ 같은 토큰을 이 PC 기준으로 푼다."""
    p = str(path or "").strip()
    if not p:
        return p
    return os.path.expandvars(os.path.expanduser(p))

# ⚠️ 포트 / 프로필 배정 근거 (P0-1 수정)
#   여태 Klook Open 과 OTA Close 가 둘 다 9222~9225 를 쓰면서 user-data-dir 만 달랐다
#   (KlookBot\chrome_* vs OTABot\chrome_*). 먼저 뜬 쪽이 포트를 잡으면 나머지는
#   디버그 포트 바인딩에 실패하는데, 포트는 살아 있으니 봇은 '정상' 으로 오판하고
#   로그인 안 된 Chrome 에 붙어서 전부 실패했다.
#
#   해결: Klook 을 '지역 Chrome' 에 합쳤다.
#     지역 Chrome 은 이미 KR/JP/AU/UK 로 나뉘어 있고 Klook 계정도 지역별이라 1:1 로 맞는다.
#     Chrome 프로필 하나에 사이트별 로그인 하나면 되므로, 한 지역 Chrome 안에
#     Klook + KKday + GG + Ctrip 로그인이 공존해도 문제없다.
#     (KlookBot\chrome_* 프로필은 확인 결과 Klook 로그아웃 상태라 보존할 세션도 없었다)
#
#     KR/JP/AU/UK -> 9522~9525 : Klook + KKday + GG + Trip.com
#     GLOBAL      -> 9530      : Viator + MyRealTrip
#
#   ⚠️ 9222~9230 을 안 쓰는 이유: 바탕화면 start_chrome.bat, 리뷰 분석기 등
#      다른 도구가 이미 그 대역을 쓴다. 같은 포트를 서로 다른 프로필로 물면
#      먼저 뜬 쪽이 이기고 나머지는 로그인 안 된 Chrome 에 붙어 조용히 실패한다.
#      라스트미닛 전용 대역(95xx)으로 빼서 충돌 자체를 없앤다.
#
#   지역 Chrome 마다 Klook 에 한 번씩 로그인해두면 된다. UI 의 '로그인 확인' 으로 상태를 볼 수 있다.
#   계정이 나중에 분리되면 Profile 을 추가하고 표에서 셀만 바꾸면 된다.
DEFAULT_CONFIG: dict = {
    "schema_version": SCHEMA_VERSION,
    # profile = Chrome 인스턴스 1개. 포트 + user-data-dir 로 유일하게 식별된다.
    "profiles": {
        "KR":     {"name": "Korea OTA Account",     "port": 9522, "profile_dir": rf"{_LOCAL_TOKEN}\OTABot\chrome_korea"},
        "JP":     {"name": "Japan OTA Account",     "port": 9523, "profile_dir": rf"{_LOCAL_TOKEN}\OTABot\chrome_japan"},
        "AU":     {"name": "Australia OTA Account", "port": 9524, "profile_dir": rf"{_LOCAL_TOKEN}\OTABot\chrome_australia"},
        "UK":     {"name": "UK OTA Account",        "port": 9525, "profile_dir": rf"{_LOCAL_TOKEN}\OTABot\chrome_uk"},
        "GLOBAL": {"name": "Global (VI/MRT)",       "port": 9530, "profile_dir": rf"{_LOCAL_TOKEN}\OTABot\chrome_global"},
    },
    # 현재 실제 운영 기준. UI 에서 셀 단위로 바꿀 수 있다.
    "routes": {
        "KOREA":     {"KLOOK": "KR", "KK": "KR", "GG": "KR", "VI": "GLOBAL", "CP": "KR", "MRT": "GLOBAL"},
        # KKday 는 일본 상품을 판매하지 않는다 -> 미설정(=실행 안 함).
        # 예전에는 매일 worker 4개가 떠서 패키지 0개를 처리하고 끝났는데,
        # 그 4개가 지역 Chrome 을 점유하는 바람에 같은 Chrome 을 쓰는 GG 가 밀려서 죽었다.
        "JAPAN":     {"KLOOK": "JP", "KK": None, "GG": "JP", "VI": "GLOBAL", "CP": "KR", "MRT": "GLOBAL"},
        "AUSTRALIA": {"KLOOK": "AU", "KK": "AU", "GG": "AU", "VI": "GLOBAL", "CP": "AU", "MRT": None},
        "UK":        {"KLOOK": "UK", "KK": "UK", "GG": "UK", "VI": "GLOBAL", "CP": None, "MRT": None},
        "OTHER":     {ch: None for ch in CHANNELS},
    },
    # Area 이름 정확 일치 override. 예: {"Sapporo": {"KLOOK": "JP2"}}
    "area_routes": {},
}


# 로그인이 풀렸을 때 리다이렉트되는 URL 패턴
_LOGIN_HINT = re.compile(
    r"(/login|/signin|/sign-in|/auth/|/account/login|passport|/users/sign_in|logon)",
    re.I)


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).casefold()


def area_region(area: str) -> str:
    """예약 파일의 Area 값 -> Region. 모르면 OTHER."""
    key = _norm(area)
    if key in AREA_REGION:
        return AREA_REGION[key]
    for hint, region in AREA_REGION.items():
        if hint in key:
            return region
    return "OTHER"


def _route_value(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"NONE", "UNCONFIGURED", "미설정", "-"}:
        return None
    return text.upper()


def _deep_merge(default: dict, override: dict) -> dict:
    out = deepcopy(default)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 포트 / 프로필 소유권 판정
# ──────────────────────────────────────────────────────────────────────────────
def port_open(port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def cdp_ready(port: int, timeout: float = 1.5) -> bool:
    """소켓만 열린 '반쯤 뜬 Chrome' 방어. /json/version 이 응답해야 준비된 것."""
    # 죽은 포트에 긴 타임아웃을 주면 상태 조회 전체가 느려진다 (프로필 9개 x 1.5s).
    if not port_open(port, timeout=min(0.3, timeout)):
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def devtools_active_port(profile_dir: Path) -> int | None:
    """
    <user-data-dir>/DevToolsActivePort 첫 줄 = Chrome 이 실제로 연 디버그 포트.
    최신 Chrome 은 이 파일을 안 만드는 경우가 있어서 보조 수단으로만 쓴다.
    """
    f = Path(profile_dir) / "DevToolsActivePort"
    try:
        first = f.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
        return int(first)
    except Exception:
        return None


_OWNER_CACHE: dict[int, tuple[float, str | None]] = {}
_OWNER_TTL = 3.0

_UDD_RE = re.compile(r'--user-data-dir(?:=|\s+)"?([^"\s]+)"?')


def port_owner_user_data_dir(port: int, force: bool = False) -> str | None:
    """
    이 포트를 LISTEN 중인 프로세스의 --user-data-dir 을 알아낸다.

    P0-1 의 실제 판정 수단. Chrome 이 DevToolsActivePort 파일을 안 남기더라도
    "지금 9222 를 잡고 있는 Chrome 이 어느 프로필인가" 를 확실히 알 수 있다.
    알아내지 못하면 None (그때는 판정을 포기하고 예전 동작으로 폴백한다).
    """
    now = time.time()
    hit = _OWNER_CACHE.get(int(port))
    if hit and not force and now - hit[0] < _OWNER_TTL:
        return hit[1]

    cmdline = None
    if sys.platform.startswith("win"):
        ps = (
            f"$p=(Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
            f"-ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess; "
            f"if($p){{ (Get-CimInstance Win32_Process -Filter \"ProcessId=$p\").CommandLine }}"
        )
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            cmdline = (out.stdout or "").strip() or None
        except Exception:
            cmdline = None

    udd = None
    if cmdline:
        m = _UDD_RE.search(cmdline)
        if m:
            udd = m.group(1).rstrip("\\/")
    _OWNER_CACHE[int(port)] = (now, udd)
    return udd


def _same_dir(a, b) -> bool:
    try:
        return Path(str(a)).resolve() == Path(str(b)).resolve()
    except Exception:
        return str(a).rstrip("\\/").casefold() == str(b).rstrip("\\/").casefold()


def cdp_tabs(port: int, timeout: float = 2.0) -> list[dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return [t for t in data if isinstance(t, dict)]
    except Exception:
        return []


class Routing:
    def __init__(self, config_path: Path | None = None):
        self.config_path = Path(config_path or CONFIG_PATH)
        self.config = self._load()

    # ── config ────────────────────────────────────────────────────────────
    def _load(self) -> dict:
        if not self.config_path.exists():
            cfg = deepcopy(DEFAULT_CONFIG)
            self.config = cfg
            self.save()
            return cfg
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return deepcopy(DEFAULT_CONFIG)
        cfg = _deep_merge(DEFAULT_CONFIG, raw)
        cfg["schema_version"] = SCHEMA_VERSION
        for region in REGIONS:
            cfg["routes"].setdefault(region, {})
            for ch in CHANNELS:
                cfg["routes"][region].setdefault(ch, DEFAULT_CONFIG["routes"][region].get(ch))
                cfg["routes"][region][ch] = _route_value(cfg["routes"][region][ch])
        self.config = cfg
        return cfg

    def save(self) -> None:
        self.config["schema_version"] = SCHEMA_VERSION
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")

    def reset(self) -> None:
        self.config = deepcopy(DEFAULT_CONFIG)
        self.save()

    # ── profiles ──────────────────────────────────────────────────────────
    @property
    def profile_keys(self) -> list[str]:
        return list(self.config.get("profiles", {}).keys())

    def profile(self, key: str) -> dict:
        k = str(key).upper()
        info = self.config.get("profiles", {}).get(k)
        if info is None:
            raise KeyError(f"알 수 없는 Chrome Profile: {key}")
        return info

    def profile_port(self, key: str) -> int:
        return int(self.profile(key).get("port", 9222))

    def profile_dir(self, key: str) -> Path:
        """실제 경로. 저장값의 %LOCALAPPDATA% 같은 토큰을 이 PC 기준으로 푼다."""
        return Path(expand_dir(str(self.profile(key).get("profile_dir"))))

    def upsert_profile(self, key: str, name: str, port: int, profile_dir: str) -> str:
        k = re.sub(r"[^A-Za-z0-9_-]+", "_", str(key).strip()).strip("_").upper()
        if not k:
            raise ValueError("Profile ID 를 입력하세요.")
        port = int(port)
        if not 1024 <= port <= 65535:
            raise ValueError("Debug port 는 1024~65535 사이여야 합니다.")
        for other, info in self.config.get("profiles", {}).items():
            if other != k and int(info.get("port", 0)) == port:
                raise ValueError(f"Port {port} 는 이미 {other} Profile 이 쓰고 있습니다.")
        self.config.setdefault("profiles", {})[k] = {
            "name": str(name or k).strip(),
            "port": port,
            "profile_dir": portable_dir(
                str(profile_dir or rf"{_LOCAL_TOKEN}\OTABot\chrome_{k.lower()}").strip()),
        }
        self.save()
        return k

    def delete_profile(self, key: str) -> None:
        k = str(key).upper()
        if k not in self.config.get("profiles", {}):
            return
        uses = [f"{region}/{ch}"
                for region, routes in self.config.get("routes", {}).items()
                for ch, p in routes.items() if _route_value(p) == k]
        uses += [f"{area}/{ch}"
                 for area, routes in self.config.get("area_routes", {}).items()
                 for ch, p in routes.items() if _route_value(p) == k]
        if uses:
            raise ValueError("아직 라우팅에서 사용 중입니다: " + ", ".join(uses[:12]))
        del self.config["profiles"][k]
        self.save()

    # ── routes ────────────────────────────────────────────────────────────
    def route(self, region_or_area: str, channel: str) -> str | None:
        """(Region 또는 Area) x OTA -> profile key. 미설정이면 None."""
        ch = str(channel or "").upper()
        target = _norm(region_or_area)
        for saved_area, routes in self.config.get("area_routes", {}).items():
            if _norm(saved_area) == target and ch in (routes or {}):
                p = _route_value(routes.get(ch))
                return p if p in self.config.get("profiles", {}) else None
        region = str(region_or_area).upper()
        if region not in REGION_LABELS:
            region = area_region(region_or_area)
        p = _route_value(self.config.get("routes", {}).get(region, {}).get(ch))
        return p if p in self.config.get("profiles", {}) else None

    def set_route(self, region: str, channel: str, profile: str | None) -> None:
        region = str(region).upper()
        if region not in REGION_LABELS:
            raise ValueError(f"알 수 없는 Region: {region}")
        p = _route_value(profile)
        if p is not None and p not in self.config.get("profiles", {}):
            raise ValueError(f"알 수 없는 Chrome Profile: {p}")
        self.config.setdefault("routes", {}).setdefault(region, {})[str(channel).upper()] = p
        self.save()

    def set_area_route(self, area: str, channel: str, profile: str | None) -> None:
        area = str(area or "").strip()
        if not area:
            raise ValueError("Area 이름을 입력하세요.")
        p = _route_value(profile)
        if p is not None and p not in self.config.get("profiles", {}):
            raise ValueError(f"알 수 없는 Chrome Profile: {p}")
        self.config.setdefault("area_routes", {}).setdefault(area, {})[str(channel).upper()] = p
        self.save()

    def remove_area_route(self, area: str, channel: str | None = None) -> None:
        routes = self.config.setdefault("area_routes", {})
        key = next((a for a in routes if _norm(a) == _norm(area)), None)
        if key is None:
            return
        if channel is None:
            routes.pop(key, None)
        else:
            routes[key].pop(str(channel).upper(), None)
            if not routes[key]:
                routes.pop(key, None)
        self.save()

    def channels_for_profile(self, key: str) -> list[str]:
        """이 profile 로 라우팅된 OTA 목록 (Chrome 띄울 때 열 탭 결정)."""
        k = str(key).upper()
        out: list[str] = []
        for routes in self.config.get("routes", {}).values():
            for ch, p in routes.items():
                if _route_value(p) == k and ch not in out:
                    out.append(ch)
        for routes in self.config.get("area_routes", {}).values():
            for ch, p in (routes or {}).items():
                if _route_value(p) == k and ch not in out:
                    out.append(ch)
        return [c for c in CHANNELS if c in out]

    # ── Chrome 프로세스 ────────────────────────────────────────────────────
    @staticmethod
    def find_chrome() -> str | None:
        candidates: list[str] = []
        if sys.platform.startswith("win"):
            for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                base = os.environ.get(env_name)
                if base:
                    candidates.append(str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"))
            try:
                import winreg
                for root, path in [
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
                ]:
                    try:
                        with winreg.OpenKey(root, path) as k:
                            value, _ = winreg.QueryValueEx(k, None)
                            if value:
                                candidates.append(str(value))
                    except OSError:
                        pass
            except Exception:
                pass
        else:
            candidates += ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
        for c in candidates:
            if not c:
                continue
            if Path(c).exists():
                return c
            found = shutil.which(c)
            if found:
                return found
        return shutil.which("chrome") or shutil.which("chrome.exe")

    def profile_owns_port(self, key: str) -> bool:
        """
        이 포트에 떠 있는 Chrome 이 정말 이 프로필인가.

        여기가 P0-1 (포트 충돌) 의 해결 지점이다. 포트가 열려 있다는 사실만으로
        '우리 Chrome' 이라고 믿으면, 다른 프로필(Klook Open 전용 등)이 9222 를
        점유했을 때 로그인 안 된 Chrome 에 붙어서 전부 실패한다.
        """
        port = self.profile_port(key)
        if not cdp_ready(port):
            return False

        # 1순위: 포트를 잡고 있는 프로세스의 --user-data-dir 을 직접 확인
        owner = port_owner_user_data_dir(port)
        if owner is not None:
            return _same_dir(owner, self.profile_dir(key))

        # 2순위: DevToolsActivePort (있으면)
        active = devtools_active_port(self.profile_dir(key))
        if active is not None:
            return active == port

        # 판정 불가 -> 예전 동작(포트가 살아있으면 OK)으로 폴백한다.
        # 여기서 False 를 주면 멀쩡한 Chrome 도 못 쓰게 되므로 막지 않는다.
        return True

    def port_conflict(self, key: str) -> bool:
        """포트는 살아있는데 내 프로필이 아닌 상태 (= 남이 점유)."""
        return cdp_ready(self.profile_port(key)) and not self.profile_owns_port(key)

    def status(self, key: str) -> dict:
        port = self.profile_port(key)
        info = self.profile(key)
        alive = cdp_ready(port)
        owns = self.profile_owns_port(key) if alive else False
        tabs = cdp_tabs(port) if owns else []
        urls = [str(t.get("url") or "") for t in tabs]
        open_channels = [ch for ch, meta in CHANNEL_META.items()
                         if any(h in u for h in meta["hosts"] for u in urls)]
        return {
            "key": str(key).upper(),
            "name": info.get("name", key),
            "port": port,
            # 화면에는 실제 경로를 보여준다. 저장값은 %LOCALAPPDATA% 토큰이라
            # 그대로 띄우면 "내 프로필이 어디 있나" 를 확인할 수 없다.
            "profile_dir": expand_dir(str(info.get("profile_dir"))),
            "alive": alive,
            "owned": owns,
            "conflict": alive and not owns,
            "tabs": len(tabs),
            "open_channels": open_channels,
            "routed_channels": self.channels_for_profile(key),
        }

    # ── 로그인 상태 확인 ───────────────────────────────────────────────────
    def check_login(self, key: str, channels: Iterable[str] | None = None,
                    timeout: float = 20.0) -> dict:
        """
        이 프로필의 Chrome 에서 각 OTA 가 로그인돼 있는지 확인한다.

        방법: 새 탭을 열어 그 OTA 의 관리자 페이지로 보낸 뒤 최종 URL 을 본다.
              로그인이 풀려 있으면 login/auth/signin 류로 리다이렉트된다.
              확인이 끝나면 연 탭은 닫는다 (탭 누수 방지).

        읽기만 한다 — 클릭도, 입력도 하지 않는다.
        """
        key = str(key).upper()
        if not self.profile_owns_port(key):
            return {"ok": False, "error": f"{key} Chrome 이 이 프로필로 떠 있지 않습니다.",
                    "results": []}
        port = self.profile_port(key)
        chans = [c for c in (list(channels) if channels else self.channels_for_profile(key))
                 if c in CHANNEL_META]
        if not chans:
            return {"ok": True, "results": [], "note": "이 프로필에 라우팅된 OTA 가 없습니다."}

        results = []
        for ch in chans:
            results.append(self._probe_login(port, ch, timeout))
        return {"ok": True, "profile": key, "port": port, "results": results}

    @staticmethod
    def _probe_login(port: int, channel: str, timeout: float) -> dict:
        meta = CHANNEL_META[channel]
        url = meta.get("probe") or meta["page"]
        target_id = None
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}",
                method="PUT")
            with urllib.request.urlopen(req, timeout=6) as r:
                info = json.loads(r.read().decode("utf-8", "replace"))
            target_id = info.get("id")
        except Exception as e:
            return {"channel": channel, "state": "unknown",
                    "detail": f"탭을 열지 못했습니다: {type(e).__name__}"}

        # ⚠️ 리다이렉트 '중간' URL 로 판단하면 안 된다.
        #    merchant.klook.com 은 로그인돼 있어도
        #      /  ->  /login?redirect_url=...  ->  (SSO)  ->  /home
        #    으로 두 번 튕긴다. 중간의 /login 에서 끊으면 '로그아웃' 오탐이 난다.
        #    실제로 이 오탐 때문에 멀쩡한 계정을 로그아웃이라고 보고한 적이 있다.
        #
        #    규칙:
        #      - login 류 URL 은 '아직 안 끝났다' 로 보고 타임아웃까지 계속 기다린다
        #      - 그 외 URL 이 연속 2회 같으면 리다이렉트 종료로 보고 확정
        #      - 타임아웃까지 login 류면 그때 비로소 로그아웃으로 판정
        final_url = ""
        prev_url = None
        stable = 0
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                time.sleep(0.7)
                cur = next((t for t in cdp_tabs(port) if t.get("id") == target_id), None)
                if cur is None:
                    break
                url_now = str(cur.get("url") or "")
                if not url_now or url_now.startswith("about:"):
                    continue
                final_url = url_now
                if _LOGIN_HINT.search(url_now):
                    # 로그인 화면은 최종 상태일 수도, SSO 경유 중일 수도 있다. 더 기다린다.
                    prev_url = url_now
                    stable = 0
                    continue
                stable = stable + 1 if url_now == prev_url else 0
                prev_url = url_now
                if stable >= 2:
                    break
        finally:
            if target_id:
                try:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/close/{target_id}", timeout=4).close()
                except Exception:
                    pass

        expect = meta.get("expect") or ""
        if not final_url or final_url.startswith("about:"):
            state = "unknown"
        elif _LOGIN_HINT.search(final_url):
            state = "logged_out"
        elif expect and expect not in final_url:
            # 로그인 페이지가 아니어도 작업 페이지를 벗어났으면 못 쓰는 상태다.
            # (GG 는 마케팅 랜딩으로 튕긴다 — URL 에 login 이 없다)
            state = "logged_out"
        else:
            state = "logged_in"
        return {"channel": channel, "state": state, "url": final_url[:160]}

    def status_all(self) -> list[dict]:
        """프로필 상태 병렬 조회 (포트 프로브 + 프로세스 조회가 직렬이면 10초 넘어간다)."""
        import concurrent.futures as _cf
        keys = self.profile_keys
        if not keys:
            return []
        out: dict[str, dict] = {}
        with _cf.ThreadPoolExecutor(max_workers=min(10, len(keys))) as ex:
            futs = {ex.submit(self.status, k): k for k in keys}
            for f in _cf.as_completed(futs, timeout=20):
                k = futs[f]
                try:
                    out[k] = f.result()
                except Exception as e:
                    out[k] = {"key": k, "name": k, "port": self.profile_port(k),
                              "profile_dir": str(self.profile_dir(k)),
                              "alive": False, "owned": False, "conflict": False,
                              "tabs": 0, "open_channels": [],
                              "routed_channels": self.channels_for_profile(k),
                              "error": str(e)}
        return [out[k] for k in keys if k in out]

    def launch(self, key: str, channels: Iterable[str] | None = None) -> dict:
        """
        프로필용 Chrome 실행. 이미 이 프로필로 떠 있으면 아무것도 안 한다.
        포트를 남이 점유 중이면 실행하지 않고 conflict 를 알린다 (조용한 오작동 방지).
        """
        key = str(key).upper()
        if self.profile_owns_port(key):
            return {"ok": True, "started": False, "message": "이미 실행 중입니다."}
        if self.port_conflict(key):
            return {
                "ok": False, "started": False,
                "message": (f"port {self.profile_port(key)} 를 다른 Chrome 이 점유하고 있습니다. "
                            f"그 Chrome 을 끄거나 이 Profile 의 포트를 바꿔주세요."),
            }
        chrome = self.find_chrome()
        if not chrome:
            return {"ok": False, "started": False, "message": "Google Chrome 을 찾을 수 없습니다."}

        pdir = self.profile_dir(key)
        pdir.mkdir(parents=True, exist_ok=True)
        chs = list(channels) if channels else self.channels_for_profile(key)
        urls = [CHANNEL_META[c]["page"] for c in chs if c in CHANNEL_META] or ["about:blank"]

        args = [
            chrome,
            f"--remote-debugging-port={self.profile_port(key)}",
            f"--user-data-dir={pdir}",
            # 창이 뒤에 있거나 최소화돼도 봇이 동작하도록
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-features=CalculateNativeWinOcclusion",
            "--no-first-run",
            "--no-default-browser-check",
        ] + urls
        # ⚠️ Chrome 을 띄운 프로세스와 완전히 떼어 놓는다.
        #    CREATE_NEW_PROCESS_GROUP 만으로는 부모의 콘솔이 그대로 붙어 있어서,
        #    띄운 쪽(터미널 스크립트 / Streamlit / 콘솔 서버)이 끝나면 Chrome 도 같이 죽는다.
        #    그렇게 강제로 죽으면 세션 쿠키가 저장되지 않아 다음에 로그인이 풀려 있다.
        #    (2026-08-25: 08:20 에 띄운 KR/JP 가 08:53 에 DEAD. 그 전 이틀도 로그인이 계속 풀렸다)
        flags = 0
        if sys.platform.startswith("win"):
            flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                     | getattr(subprocess, "DETACHED_PROCESS", 0x00000008))
        subprocess.Popen(args, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return {"ok": True, "started": True,
                "message": f"{key} Chrome 실행 (port {self.profile_port(key)}) — 로그인 상태를 확인하세요."}

    def ensure(self, key: str, wait_seconds: float = 15.0,
               channels: Iterable[str] | None = None) -> dict:
        """실행 + 준비될 때까지 대기. 반환에 owned 여부 포함."""
        res = self.launch(key, channels=channels)
        if not res.get("ok"):
            return res
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self.profile_owns_port(key):
                res["ready"] = True
                return res
            time.sleep(0.4)
        res["ready"] = self.profile_owns_port(key)
        if not res["ready"]:
            res["ok"] = False
            res["message"] = f"{key} Chrome 이 {wait_seconds:.0f}초 안에 준비되지 않았습니다."
        return res

    def required_profiles(self, pairs: Iterable[tuple[str, str]]) -> dict[str, dict]:
        """[(region_or_area, channel), ...] -> {profile: {channels, regions}} + 미설정 목록."""
        req: dict[str, dict] = {}
        unconfigured: list[dict] = []
        for region, ch in pairs:
            p = self.route(region, ch)
            if p is None:
                unconfigured.append({"region": str(region), "channel": ch})
                continue
            item = req.setdefault(p, {"channels": set(), "regions": set()})
            item["channels"].add(ch)
            item["regions"].add(str(region))
        for item in req.values():
            item["channels"] = sorted(item["channels"], key=lambda c: CHANNELS.index(c) if c in CHANNELS else 99)
            item["regions"] = sorted(item["regions"])
        return {"profiles": req, "unconfigured": unconfigured}


_SINGLETON: Routing | None = None


def get_routing() -> Routing:
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = Routing()
    return _SINGLETON
