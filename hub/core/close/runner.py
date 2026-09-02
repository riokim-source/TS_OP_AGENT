# -*- coding: utf-8 -*-
"""
runner.py
OTA Close 마감 실행을 hub 에서 돌린다.

기존 main.py 를 subprocess 로 그대로 부른다. 웹서버 프로세스 안에서 in-process 로
돌리면 봇들이 뿌리는 stdout 과 Playwright 스레드가 서버에 섞여 들어와서 위험하다.

hub 가 추가로 하는 것:
  - 실행 전에 필요한 Chrome 을 라우팅 기준으로 미리 띄우고 소유권을 검증한다
    (P0-1: 남의 프로필이 포트를 점유했으면 여기서 잡아낸다)
  - --no-boot 으로 넘겨 main.py 가 다시 부팅을 시도하지 않게 한다
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading

from ..paths import ota_close_dir
from .. import kkday_codes
from ..routing import get_routing, cdp_attach_ok

AGENCIES = ["klook", "kkday", "gg", "vi", "mrt"]

# 마감 봇 코드 -> 라우팅 채널 코드
AGENCY_CHANNEL = {"klook": "KLOOK", "kkday": "KK", "gg": "GG", "vi": "VI", "mrt": "MRT"}
AGENCY_REGIONS = {
    "klook": ["KOREA", "JAPAN", "AUSTRALIA", "UK"],
    "kkday": ["KOREA", "JAPAN", "AUSTRALIA", "UK"],
    "gg": ["KOREA", "JAPAN", "AUSTRALIA", "UK"],
    "vi": ["KOREA"],     # 글로벌 계정 1개 -> 대표로 KOREA 라우팅을 본다
    "mrt": ["KOREA"],
}

# 라우팅은 짧은 이름(KR/JP/AU/UK), 마감 봇은 긴 이름(KOREA/...) 을 쓴다.
# 둘 다 받아 주되 안에서는 긴 이름 하나로 통일한다.
_REGION_ALIAS = {"KR": "KOREA", "JP": "JAPAN", "AU": "AUSTRALIA",
                 "GB": "UK", "UK": "UK"}
_ALL_REGIONS = ["KOREA", "JAPAN", "AUSTRALIA", "UK"]

_RESULT_RE = re.compile(r"\[([^\]]+)\]\s*success=(\d+)\s*failed=(\d+)\s*skipped=(\d+)")


def available() -> tuple[bool, str]:
    d = ota_close_dir()
    if d is None:
        return False, "OTA Close 폴더를 찾을 수 없습니다 (kkday.py 가 있는 폴더)."
    return True, str(d)


def required_chromes(agencies: list[str], regions: list[str]) -> dict:
    """선택한 마감 대상이 필요로 하는 Chrome 프로필과 미설정 조합."""
    r = get_routing()
    pairs: list[tuple[str, str]] = []
    for a in agencies:
        ch = AGENCY_CHANNEL.get(a)
        if not ch:
            continue
        for region in (regions or AGENCY_REGIONS.get(a, [])):
            if region in AGENCY_REGIONS.get(a, []) or a in ("vi", "mrt"):
                pairs.append((region, ch))
    return r.required_profiles(pairs)


def prepare_chromes(job, agencies: list[str], regions: list[str]) -> bool:
    """필요한 Chrome 을 띄우고 소유권까지 확인. 하나라도 못 쓰면 사유를 남긴다."""
    r = get_routing()
    req = required_chromes(agencies, regions)

    for item in req["unconfigured"]:
        job.log("SYS", f"[주의] {item['region']}/{item['channel']} Chrome 연결이 미설정입니다 "
                       f"— 이 조합은 실행되지 않습니다.")

    all_ok = True
    for key, info in req["profiles"].items():
        chans = ", ".join(info["channels"])
        if r.profile_owns_port(key):
            # ⚠️ 여기까지는 HTTP(/json/version) 로만 확인한 것이다. 봇은 CDP 로
            #    붙는데, HTTP 는 200 인데 CDP 만 안 되는 Chrome 이 있다.
            #    그대로 두면 워커마다 타임아웃을 다 채우고서야 실패한다.
            #    (2026-09-01 마감 KKday·GG 전멸 / 2026-09-02 오픈 MRT 3건)
            ok, why = cdp_attach_ok(r.profile_port(key))
            if ok:
                job.log("SYS", f"[Chrome] {key} (port {r.profile_port(key)}) "
                               f"연결됨 · {chans} · {why}")
                continue
            job.log("SYS", f"[오류] {key}: Chrome 은 떠 있는데 봇이 붙지 못합니다 "
                           f"(port {r.profile_port(key)}) — {why}")
            job.log("SYS", f"[오류] {key}: 그 Chrome 창을 닫고 다시 켠 뒤 실행하세요. "
                           f"({chans} 가 실행되지 않습니다)")
            all_ok = False
            continue
        if r.port_conflict(key):
            job.log("SYS", f"[오류] {key}: port {r.profile_port(key)} 를 다른 프로필의 Chrome 이 "
                           f"점유 중입니다. 그 창을 닫고 다시 실행하세요.")
            all_ok = False
            continue
        job.log("SYS", f"[Chrome] {key} 부팅 중... (port {r.profile_port(key)} · {chans})")
        res = r.ensure(key, wait_seconds=20, channels=info["channels"])
        if res.get("ok") and res.get("ready"):
            job.log("SYS", f"[Chrome] {key} 준비 완료")
        else:
            job.log("SYS", f"[오류] {key}: {res.get('message', '부팅 실패')}")
            all_ok = False

    check_logins(job, r, req["profiles"])
    return all_ok


def check_logins(job, r, profiles: dict) -> list[str]:
    """
    마감 시작 전에 각 OTA 로그인 상태를 확인한다.

    로그인이 풀린 채로 돌리면 봇이 한 시간을 돌고 전부 실패한다. 그걸 미리 알려준다.
    다만 '경고' 로만 남기고 실행은 막지 않는다 — 로그인 판정은 URL 기반 추정이라
    사이트가 리다이렉트를 바꾸면 오탐이 날 수 있고, 오탐 때문에 운영이 멈추면 더 나쁘다.
    """
    logged_out: list[str] = []
    for key in profiles:
        if not r.profile_owns_port(key):
            continue
        try:
            res = r.check_login(key, profiles[key]["channels"], timeout=12)
        except Exception as e:
            job.log("SYS", f"[주의] {key} 로그인 확인 실패: {e}")
            continue
        for x in res.get("results", []):
            if x["state"] == "logged_out":
                logged_out.append(f"{key}/{x['channel']}")
                job.log("SYS", f"[경고] {key} · {x['channel']} 로그인이 풀려 있습니다 "
                               f"— 이대로 돌리면 이 OTA 는 전부 실패합니다.")
            elif x["state"] == "unknown":
                job.log("SYS", f"[주의] {key} · {x['channel']} 로그인 상태를 확인하지 못했습니다.")
    if logged_out:
        job.log("SYS", f"[경고] 로그인 필요: {', '.join(logged_out)} "
                       f"— 해당 Chrome 창에서 로그인한 뒤 다시 실행하는 것을 권합니다.")
    return logged_out


def run(job, target_date: str, agencies: list[str], regions: list[str],
        dry_run: bool = False) -> None:
    ok, msg = available()
    if not ok:
        job.done(error=msg)
        return
    root = ota_close_dir()

    # ⚠️ 이름 표기를 먼저 맞춘다.
    #
    #    에이전시는 소문자('klook'), 지역은 긴 이름('KOREA') 이 기준이다.
    #    표기가 어긋나면 아래 필터가 '모르는 이름' 으로 보고 조용히 통과시켜
    #    지역 제한이 통째로 풀린다. 한 지역만 고른 줄 알았는데 전 지역이
    #    닫힌다. (2026-08-25 확인: 'KLOOK' + 'KR' 로 부르니 한국·일본·호주가
    #    전부 돌았다)
    agencies = [str(a).strip().lower() for a in (agencies or []) if str(a).strip()]
    regions = [_REGION_ALIAS.get(str(r).strip().upper(), str(r).strip().upper())
               for r in (regions or []) if str(r).strip()]

    unknown_a = [a for a in agencies if a not in AGENCIES]
    unknown_r = [r for r in regions if r not in _ALL_REGIONS]
    if unknown_a or unknown_r:
        bad = ", ".join(unknown_a + unknown_r)
        job.log("SYS", f"[마감] 모르는 이름이 있어 실행하지 않습니다: {bad}")
        job.done(error=f"모르는 에이전시/지역: {bad}")
        return
    agencies = [a for a in AGENCIES if a in agencies]      # 순서 고정

    mode = "DRY-RUN (실제 클릭 없음)" if dry_run else "실제 마감"
    job.log("SYS", f"[마감] {mode}")
    job.log("SYS", f"[마감] 대상 날짜 {target_date} / 에이전시 {', '.join(a.upper() for a in agencies)}")
    if regions:
        job.log("SYS", f"[마감] 지역 {', '.join(regions)}")

    if not prepare_chromes(job, agencies, regions):
        job.log("SYS", "[마감] Chrome 준비가 끝나지 않았습니다. 위 오류를 먼저 해결하세요.")
        job.done(error="Chrome 준비 실패")
        return

    # ⚠️ 실행할 지역이 하나도 없는 에이전시는 명령에서 아예 빼야 한다.
    #    main.py 는 <BOT>_REGIONS 가 비어 있으면 '지정 안 함' 으로 보고
    #    DEFAULT_REGIONS(KOREA/JAPAN/AUSTRALIA/UK) 전부를 돈다.
    #    빈 문자열만 넘기면 '이 조합은 실행 안 함' 이라고 로그를 찍어 놓고
    #    실제로는 전 지역을 닫아 버린다.
    #    (2026-08-25 확인: Klook + UK 만 골랐는데 KOREA/JAPAN/AUSTRALIA 가 전부 돌았다)
    r0 = get_routing()
    allowed_by_agency: dict[str, list[str]] = {}
    for a in agencies:
        ch = AGENCY_CHANNEL.get(a)
        wanted = regions or AGENCY_REGIONS.get(a, [])
        if not ch:
            # 여기 오면 AGENCY_CHANNEL 에 빠진 것이다. 필터 없이 통과시키면
            # 전 지역이 돌아 버리므로, 통과시키지 않고 실행에서 뺀다.
            job.log("SYS", f"[마감] {a.upper()} 는 채널 연결이 없어 제외합니다.")
            allowed_by_agency[a] = []
            continue
        allowed_by_agency[a] = [rg for rg in wanted if r0.route(rg, ch) is not None]

    runnable = [a for a in agencies if allowed_by_agency.get(a)]
    dropped = [a for a in agencies if not allowed_by_agency.get(a)]
    for a in dropped:
        job.log("SYS", f"[마감] {a.upper()} 는 실행할 지역이 없어 제외합니다 "
                       f"(Chrome 연결 미설정).")
    if not runnable:
        job.log("SYS", "[마감] 실행할 조합이 없습니다. "
                       "[Chrome 연결] 에서 Region x OTA 를 지정하세요.")
        job.done(summary={"kind": "close", "date": target_date,
                          "dry_run": dry_run, "totals": {}, "skipped_all": True})
        return
    agencies = runnable

    cmd = [sys.executable, str(root / "main.py"), "--once", "--no-boot",
           "--date", target_date, "--agency", ",".join(agencies)]
    if dry_run:
        cmd.append("--dry-run")
    # ⚠️ --regions 를 넘기면 main.py 가 '모든' 지역봇에 같은 값을 덮어쓴다.
    #    에이전시별로 다르게 주려면 환경변수(<BOT>_REGIONS)만 써야 한다.

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # 에이전시마다 '실제로 그 지역에 상품이 있는지' 는 라우팅이 정한다.
    # 미설정(None) 이면 그 조합은 아예 실행하지 않는다.
    #
    # 이게 중요한 이유: KKday 일본은 판매를 안 해서 매일 worker 4개가 떠서
    # 패키지 0개를 처리하고 끝났는데, 그 4개가 지역 Chrome 을 점유하는 바람에
    # 같은 Chrome 을 쓰는 GG 가 페이지 로드 타임아웃으로 통째로 실패했다.
    # (2026-08-23: GG KOREA 전멸, 성공 46건은 전부 일본이었음)
    for a in agencies:
        if a not in ("klook", "kkday", "gg"):
            continue
        allowed = allowed_by_agency[a]
        wanted = regions or AGENCY_REGIONS.get(a, [])
        skipped = [rg for rg in wanted if rg not in allowed]
        if skipped:
            job.log("SYS", f"[마감] {a.upper()} 는 {', '.join(skipped)} 제외 "
                           f"(Chrome 연결 미설정 - 그 지역은 취급 안 함)")
        env[f"{a.upper()}_REGIONS"] = ",".join(allowed)

    # KKday 는 '매일 마감이 필요한 상품' 만 돈다 (kkday.py 의 DEFAULT_PRODUCT_CODES).
    # 전체(88상품 205패키지)를 돌면 워커 4개가 KKday 를 계속 두드려 페이지가 느려지고,
    # 그 느려짐이 그대로 날짜 입력칸 클릭 실패가 된다 (2026-08-23 아침 34건).
    #
    # 여기서는 값을 넘기지 않는다. 넘기면 목록이 두 곳에 생겨 어긋날 수 있다.
    # 로그에 '오늘 무엇을 도는지' 만 이름과 함께 남긴다.
    if "kkday" in agencies:
        codes = kkday_codes.load()
        if codes:
            names = kkday_codes.names_of(codes)
            job.log("SYS", f"[마감] KKDAY 대상 {len(codes)}개 상품 (그 외 스킵) "
                           f"— 목록: OTA Close/kkday.py DEFAULT_PRODUCT_CODES")
            for c in codes:
                label = ", ".join(names.get(c, [])[:4]) or "(맵핑리스트에 이름 없음)"
                job.log("SYS", f"          {c}  {label}")
        else:
            job.log("SYS", "[주의] KKDAY 대상 목록을 읽지 못했습니다 — 전체 상품을 돌 수 있습니다.")

    job.log("SYS", "[마감] " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    job.set_stopper(lambda: proc.terminate())

    totals: dict[str, dict] = {}

    def pump():
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            src = "OTA"
            for bot in ("KKDAY", "KLOOK", "GG", "VI", "MRT"):
                if f"[{bot}]" in line or f"[{bot}/" in line:
                    src = bot
                    break
            job.log(src, line)
            m = _RESULT_RE.search(line)
            if m:
                label = m.group(1)
                base = label.split("/")[0].upper()
                t = totals.setdefault(base, {"success": 0, "failed": 0, "skipped": 0})
                t["success"] += int(m.group(2))
                t["failed"] += int(m.group(3))
                t["skipped"] += int(m.group(4))
                job.result({"channel": base, "item": label, "result": "집계",
                            "memo": f"성공 {m.group(2)} / 실패 {m.group(3)} / 스킵 {m.group(4)}"})

    th = threading.Thread(target=pump, daemon=True)
    th.start()
    proc.wait()
    th.join(timeout=5)

    job.done(summary={"kind": "close", "date": target_date, "dry_run": dry_run,
                      "totals": totals, "returncode": proc.returncode})
