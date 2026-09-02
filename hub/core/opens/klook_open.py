# -*- coding: utf-8 -*-
"""
klook_open.py
KLOOK 오픈 = 기존 Klook Open 봇(klook_core.Runner)을 그대로 쓴다.

새로 짜지 않는 이유:
  klook_worker.process_task 는 inventory 값만 다를 뿐 오픈/마감이 같은 코드다.
  (OTA Close 의 klook.py 도 inventory=0 으로 이걸 호출해서 마감한다)
  이미 매일 돌고 있는 경로라 여기서 재구현하면 위험만 늘어난다.

hub 가 하는 일은 두 가지뿐이다:
  1. 오픈 계획 -> "상품명 수량, 상품명 수량" 텍스트로 변환 (main.parse_tasks 형식)
  2. region 별 CDP URL 을 hub 라우팅 값으로 덮어쓰기
     (UI 에서 Chrome 연결을 바꾸면 그게 바로 반영되어야 한다)
"""
from __future__ import annotations

from ..paths import klook_open_dir, ensure_on_syspath
from ..routing import get_routing, cdp_attach_ok


def available() -> tuple[bool, str]:
    d = klook_open_dir()
    if d is None:
        return False, "Klook Open 폴더를 찾을 수 없습니다 (packages.py 가 있는 폴더)."
    return True, str(d)


def _core():
    ok, msg = available()
    if not ok:
        raise RuntimeError(msg)
    ensure_on_syspath(klook_open_dir())
    import klook_core as core  # type: ignore
    return core


def plan_to_text(plan: list[dict]) -> str:
    """
    오픈 계획 -> Klook 입력 텍스트.

    ⚠️ 메모 문자열을 그대로 넣으면 안 된다. main.parse_tasks 의 형식은
       '상품명 수량' 으로 끝나야 해서 "(중국어 불가)" 같은 주석이 붙으면
       '형식 인식 실패' 로 떨어진다. 그래서 계획에서 product/qty 만 뽑아 쓴다.
    """
    parts: list[str] = []
    for item in plan:
        if item.get("channel") != "KLOOK":
            continue
        if item.get("mode") != "qty":
            continue
        parts.append(f"{item['product']} {int(item['qty'])}")
    return ", ".join(parts)


def text_to_plan(text: str) -> tuple[list[dict], list[str]]:
    """
    '상품명 수량, 상품명 수량' -> 오픈 계획.

    수량 수집을 거치지 않고 특정 상품만 열 때 쓴다. 결과 모양은 수집에서
    나오는 계획과 같아서, 그 뒤 경로(preview / run)를 그대로 쓴다.

    반환 (계획, 형식이 이상한 줄들)
    """
    plan: list[dict] = []
    bad: list[str] = []
    for raw in str(text or "").replace("\n", ",").split(","):
        part = raw.strip()
        if not part:
            continue
        bits = part.rsplit(None, 1)
        if len(bits) != 2 or not bits[1].lstrip("-").isdigit():
            bad.append(part)
            continue
        plan.append({"channel": "KLOOK", "mode": "qty",
                     "product": bits[0].strip(), "qty": int(bits[1])})
    return plan, bad


def plan_to_lines(plan: list[dict]) -> str:
    """계획 -> 사람이 고칠 수 있는 텍스트 (한 줄에 하나)."""
    return "\n".join(f"{p['product']} {int(p['qty'])}"
                      for p in plan
                      if p.get("channel") == "KLOOK" and p.get("mode") == "qty")


def catalog() -> list[dict]:
    """상품 목록. 화면의 '상품 찾기' 에서 쓴다."""
    try:
        return list(_core().product_catalog())
    except Exception:
        return []


def apply_routing_to_cdp(log=None) -> dict[str, str]:
    """hub 라우팅의 KLOOK 프로필 포트를 klook_core 의 REGION_CDP_URLS 에 반영."""
    core = _core()
    r = get_routing()
    applied: dict[str, str] = {}
    for region in core.SUPPORTED_REGIONS:
        key = r.route(region, "KLOOK")
        if key is None:
            continue
        url = f"http://localhost:{r.profile_port(key)}"
        core.cli.REGION_CDP_URLS[region] = url
        core.REGION_CDP_URLS[region] = url
        applied[region] = f"{key} ({url})"
    if log:
        for region, desc in applied.items():
            log("SYS", f"[Chrome] KLOOK/{region} -> {desc}")
    return applied


def preview(plan: list[dict], target_date: str | None) -> dict:
    core = _core()
    apply_routing_to_cdp()
    text = plan_to_text(plan)
    parsed = core.parse_input(text)
    return {
        "text": text,
        "total": parsed["total"],
        "unknown": [{"text": u.get("input_text"), "memo": u.get("memo")} for u in parsed["unknown"]],
        "warnings": parsed["warnings"],
        "regions": {
            region: [{"name": t["name"], "qty": t["inventory"], "workflow": t["workflow"]}
                     for t in parsed["region_tasks"].get(region, [])]
            for region in core.SUPPORTED_REGIONS
            if parsed["region_tasks"].get(region)
        },
        "date_text": core.describe_date(target_date),
    }


def preflight(job, regions) -> list[str]:
    """
    실행 전 Chrome 준비 + Klook 로그인 확인.

    2026-08-23 실행에서 JAPAN 이 통째로 실패했는데, 로그를 보면
    'Page not found ... Log in' 이었다. 셀렉터 문제가 아니라 그 Chrome 이
    Klook 에서 로그아웃돼 있었던 것이다. 봇은 그걸 'DOM 변경 가능성' 으로
    잘못 보고했고 10분을 헛돌았다. 시작 전에 잡는다.

    반환: 로그인이 풀린 region 목록 (실행은 막지 않고 경고만)
    """
    r = get_routing()
    logged_out: list[str] = []
    for region in regions:
        key = r.route(region, "KLOOK")
        if key is None:
            job.log("SYS", f"[주의] {region}/KLOOK 은 Chrome 연결이 미설정입니다.")
            continue
        if r.port_conflict(key):
            job.log("SYS", f"[오류] {key}: port {r.profile_port(key)} 를 다른 프로필의 "
                           f"Chrome 이 점유 중입니다. 그 창을 닫고 다시 실행하세요.")
            logged_out.append(region)
            continue
        if not r.profile_owns_port(key):
            job.log("SYS", f"[Chrome] {key} 부팅 중... (port {r.profile_port(key)})")
            res = r.ensure(key, wait_seconds=25)
            if not (res.get("ok") and res.get("ready")):
                job.log("SYS", f"[오류] {key}: {res.get('message', '부팅 실패')}")
                logged_out.append(region)
                continue
        ok_cdp, why = cdp_attach_ok(r.profile_port(key))
        if not ok_cdp:
            job.log("SYS", f"[오류] {key}: Chrome 은 떠 있는데 봇이 붙지 못합니다 "
                           f"(port {r.profile_port(key)}) — {why}. "
                           f"그 Chrome 창을 닫고 다시 켠 뒤 실행하세요.")
            logged_out.append(region)
            continue
        try:
            chk = r.check_login(key, ["KLOOK"], timeout=20)
            st = (chk.get("results") or [{}])[0].get("state")
            if st == "logged_in":
                job.log("SYS", f"[로그인] {region} ({key}) Klook 로그인됨")
            elif st == "logged_out":
                job.log("SYS", f"[경고] {region} ({key}) Klook 로그인이 풀려 있습니다 "
                               f"— 이대로 두면 이 지역은 전부 실패합니다.")
                logged_out.append(region)
            else:
                job.log("SYS", f"[주의] {region} ({key}) Klook 로그인 상태 확인 불가")
        except Exception as e:
            job.log("SYS", f"[주의] {region} 로그인 확인 실패: {e}")
    return logged_out


def run(job, plan: list[dict], target_date: str | None) -> None:
    """job(Job) 안에서 실행. 완료되면 job.done() 을 채운다."""
    core = _core()
    job.log("SYS", f"[KLOOK] Klook Open 폴더: {klook_open_dir()}")
    apply_routing_to_cdp(job.log)

    text = plan_to_text(plan)
    if not text:
        job.log("SYS", "[KLOOK] 오픈할 항목이 없습니다.")
        job.done(summary={"channel": "KLOOK", "total": 0})
        return

    parsed = core.parse_input(text)
    for w in parsed["warnings"]:
        job.log("KLOOK", w)
    for u in parsed["unknown"]:
        job.result({"channel": "KLOOK", "item": u.get("input_text"),
                    "result": "찾을 수 없음", "memo": u.get("memo", "")})

    if parsed["total"] == 0:
        job.log("SYS", "[KLOOK] packages.py 에 매핑된 상품이 없습니다.")
        job.done(summary={"channel": "KLOOK", "total": 0})
        return

    active = [rg for rg in core.SUPPORTED_REGIONS if parsed["region_tasks"].get(rg)]
    bad = preflight(job, active)
    if bad:
        job.log("SYS", f"[경고] 로그인 필요: {', '.join(bad)} — 해당 Chrome 에서 "
                       f"Klook 에 로그인한 뒤 다시 실행하는 것을 권합니다.")

    job.total = parsed["total"]
    job.log("SYS", f"[KLOOK] {parsed['total']}건 오픈 시작 / 대상 {core.describe_date(target_date)}")

    holder: dict = {}

    def on_log(region, line):
        job.log("KLOOK" if region == "*" else core.REGION_DISPLAY.get(region, region), line)

    def on_result(region, res):
        label = str(res.get("result", "")).strip()
        if str(res.get("workflow", "")).lower() == "activity" and label == "실패":
            label = "새버전 실패"
        job.result({
            "channel": "KLOOK",
            "region": core.REGION_DISPLAY.get(region, region),
            "item": core.cli.item_text_of(res),
            "result": label,
            "memo": str(res.get("memo", "")).strip()[:300],
        })

    def on_done(summary):
        holder["summary"] = summary

    runner = core.Runner(parsed["region_tasks"], parsed["unknown"], target_date,
                         on_log=on_log, on_result=on_result, on_done=on_done)
    job.set_stopper(runner.stop)
    runner.start()

    # Runner 는 자체 스레드로 돈다. 여기서 끝날 때까지 기다린다.
    while runner.running:
        if job.stopping:
            runner.stop()
        import time as _t
        _t.sleep(0.3)

    s = holder.get("summary") or {}
    job.done(summary={
        "channel": "KLOOK",
        "total": parsed["total"],
        "duration": s.get("duration_text", ""),
        "failed": s.get("failed", 0),
        "stopped": s.get("stopped", False),
    })
