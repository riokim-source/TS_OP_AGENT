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
from ..routing import get_routing, cdp_ready_or_restart


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


_LANG_SUFFIX = ("(한)", "(중)", "(일)")


def ambiguous_names() -> dict:
    """
    구분이 안 되는 상품 이름 -> 사유.

    ⚠️ 'activity' 방식은 **더 이상 막지 않는다** (2026-09-27).
       Klook 새 UI 는 한 Activity 안에 언어별 줄(unit)이 따로 있다.

           1 English · Adult   ID 828952387910
           2 Korean  · Adult   ID 828952387912   <- '(한)' 은 여기로 들어간다

       봇이 그 줄을 골라서 연다 (klook_worker._parse_section_target →
       window.__targetLangExplicit). 그 언어 줄을 못 찾으면 **첫 줄로
       흘러가지 않고 실패**한다. 그래서 '엉뚱한 상품이 열리는' 일은 없다.
       (2026-09-03 사고는 그때 못 찾으면 첫 줄을 누르던 것 때문이었다)

    ⚠️ 'package' 방식은 다르다. 그쪽은 번호 자체가 언어별로 따로 있어야 하는데
       같은 번호를 쓴다면 맵핑이 잘못된 것이다. 그건 그대로 막는다 —
       봇이 화면에서 가를 방법이 없다.
    """
    pk = _packages_map()
    if not pk:
        return {}
    by_id: dict = {}
    for name, v in pk.items():
        by_id.setdefault((str(v.get("id")), v.get("workflow")), []).append(name)
    out: dict = {}
    for (pid, wf), names in by_id.items():
        if len(names) < 2 or wf != "package":
            continue                       # activity 는 화면에서 언어 줄로 가른다
        plain = [n for n in names if not n.endswith(_LANG_SUFFIX)]
        if not plain:
            continue                       # 변형끼리만 있으면 기본이 없다는 뜻
        for n in names:
            if n.endswith(_LANG_SUFFIX):
                out[n] = (f"'{n}' 과 '{plain[0]}' 이 Klook 패키지 번호 {pid} 로 "
                          f"같습니다. 구버전 UI 는 번호가 언어별로 따로 있어야 하니 "
                          f"맵핑이 잘못된 것입니다. 번호를 고치기 전에는 열지 않습니다.")
    return out


def _packages_map() -> dict:
    try:
        ensure_on_syspath(klook_open_dir())
        import packages as pk           # type: ignore
        return dict(pk.PACKAGES)
    except Exception:
        return {}


def plan_to_text(plan: list[dict]) -> str:
    """
    오픈 계획 -> Klook 입력 텍스트.

    ⚠️ 메모 문자열을 그대로 넣으면 안 된다. main.parse_tasks 의 형식은
       '상품명 수량' 으로 끝나야 해서 "(중국어 불가)" 같은 주석이 붙으면
       '형식 인식 실패' 로 떨어진다. 그래서 계획에서 product/qty 만 뽑아 쓴다.
    """
    bad = ambiguous_names()
    parts: list[str] = []
    for item in plan:
        if item.get("channel") != "KLOOK":
            continue
        if item.get("mode") != "qty":
            continue
        if item["product"] in bad:
            continue          # 엉뚱한 상품을 여느니 열지 않는다 (ambiguous_names 참고)
        parts.append(f"{item['product']} {int(item['qty'])}")
    return ", ".join(parts)


def dropped_for_ambiguity(plan: list[dict]) -> list[dict]:
    """이번 계획에서 '구분 불가' 로 빠진 것들."""
    bad = ambiguous_names()
    return [{"name": i["product"], "qty": int(i.get("qty") or 0), "why": bad[i["product"]]}
            for i in plan
            if i.get("channel") == "KLOOK" and i.get("mode") == "qty"
            and i.get("product") in bad]


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
        "unknown": ([{"text": u.get("input_text"), "memo": u.get("memo")} for u in parsed["unknown"]]
                    + [{"text": f"{d['name']} {d['qty']}", "memo": d["why"]}
                       for d in dropped_for_ambiguity(plan)]),
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
        ok_cdp, why = cdp_ready_or_restart(r, key, job.log)
        if not ok_cdp:
            job.log("SYS", f"[오류] {key}: Chrome 에 붙지 못했습니다 "
                           f"(port {r.profile_port(key)}) — {why}. "
                           f"봇이 다시 켜는 것까지 해봤지만 안 됐습니다. "
                           f"그 Chrome 창을 직접 닫고 다시 켠 뒤 실행하세요.")
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

        # ⚠️ 로그인 확인은 탭을 열고 닫는다. 그 탭이 늦게 닫히면 Chrome 이
        #    '붙을 수 없는' 상태가 되고, 30초 뒤 worker 가 통째로 실패한다.
        #    (2026-09-30: 10:30 에 로그인 OK 였던 KR 이 10:30:43 에 안 붙어
        #     한국 15건이 전부 '결과를 남기지 않음' 으로 날아갔다)
        #    그래서 **붙기 직전에 한 번 더** 본다. 멀쩡하면 1~3초로 끝난다.
        ok2, why2 = cdp_ready_or_restart(r, key, job.log)
        if not ok2:
            job.log("SYS", f"[오류] {key}: 로그인 확인 뒤 Chrome 에 붙지 못했습니다 — {why2}. "
                           f"그 Chrome 창을 직접 닫고 다시 켠 뒤 실행하세요.")
            logged_out.append(region)
    return logged_out


# worker 가 Chrome 에 못 붙었을 때 klook_core 가 채우는 메모 (한 벌로 둔다)
LOST_MEMO = "worker 가 결과를 남기지 않음"


def _is_lost(res: dict) -> bool:
    return LOST_MEMO in str(res.get("memo", ""))


def _task_of(res: dict) -> dict:
    """결과 줄에서 '작업' 만 떼어낸다 (klook_core 의 fallback 줄 = task + result/memo)."""
    return {k: v for k, v in res.items() if k not in ("result", "memo")}


def _drive(job, core, region_tasks, unknown, target_date, on_log, on_result, on_done):
    """Runner 를 돌리고 끝날 때까지 기다린다 (Runner 는 자체 스레드로 돈다)."""
    import time as _t
    runner = core.Runner(region_tasks, unknown, target_date,
                         on_log=on_log, on_result=on_result, on_done=on_done)
    job.set_stopper(runner.stop)
    runner.start()
    while runner.running:
        if job.stopping:
            runner.stop()
        _t.sleep(0.3)


def _retry_lost(job, core, lost: dict, target_date, on_log, report) -> None:
    """
    Chrome 에 못 붙어 지역이 통째로 빈 경우 — Chrome 을 다시 켜고 한 번 더.

    ⚠️ 이게 이번 주 실패의 제일 큰 덩어리였다. 2026-09-30 오픈에서 KR(9522) 이
       worker 붙는 순간에만 막혀 **한국 15건이 전부** 실패로 남았다. 5분 뒤
       MRT 차례에서 같은 Chrome 을 봇이 다시 켜니 멀쩡했다 — 즉 다시 켜고 한
       번만 더 하면 살릴 수 있는 실패였다.

       사람이 로그를 보고 다시 돌리는 것과 같은 일을 봇이 그 자리에서 한다.
    """
    r = get_routing()
    retry_tasks: dict[str, list[dict]] = {}
    for region, rows in lost.items():
        label = core.REGION_DISPLAY.get(region, region)
        job.log("SYS", f"[재시도] {label} {len(rows)}건 — worker 가 Chrome 에 붙지 "
                       f"못했습니다. Chrome 을 다시 켜고 한 번 더 합니다.")
        key = r.route(region, "KLOOK")
        if key is None:
            ok, why = False, "Chrome 연결이 미설정입니다"
        else:
            ok, why = cdp_ready_or_restart(r, key, job.log)
        if not ok:
            job.log("SYS", f"[재시도] {label} 못 함 — {why}")
            for res in rows:
                report(region, res, f" / 재시도 못 함: {why}")
            continue
        retry_tasks[region] = [_task_of(res) for res in rows]

    if not retry_tasks:
        return

    def on_result2(region, res):
        # 두 번째에도 결과가 비면 klook_core 가 같은 fallback 줄을 만들어 준다.
        # 그건 이제 그대로 적는다 (더 이상 재시도하지 않는다).
        tail = " / 재시도에서도 마찬가지였습니다" if _is_lost(res) else " (재시도)"
        report(region, res, tail)

    _drive(job, core, retry_tasks, [], target_date, on_log, on_result2, lambda s: None)


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

    for d in dropped_for_ambiguity(plan):
        job.log("KLOOK", f"[주의] {d['why']}")
        job.result({"channel": "KLOOK", "item": f"{d['name']} {d['qty']}",
                    "result": "구분 불가", "memo": d["why"][:300]})

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
    lost: dict[str, list[dict]] = {}

    def on_log(region, line):
        job.log("KLOOK" if region == "*" else core.REGION_DISPLAY.get(region, region), line)

    def report(region, res, memo_tail: str = ""):
        label = str(res.get("result", "")).strip()
        if str(res.get("workflow", "")).lower() == "activity" and label == "실패":
            label = "새버전 실패"
        job.result({
            "channel": "KLOOK",
            "region": core.REGION_DISPLAY.get(region, region),
            "item": core.cli.item_text_of(res),
            "result": label,
            "memo": (str(res.get("memo", "")).strip() + memo_tail).strip()[:300],
        })

    def on_result(region, res):
        # ⚠️ Chrome 에 못 붙어서 결과가 통째로 빈 것은 **아직 적지 않는다**.
        #    Chrome 을 다시 켜고 한 번 더 해 본 뒤, 그 결과만 적는다.
        #    (그대로 적으면 같은 상품이 '실패' 와 '성공' 두 줄로 남는다)
        if _is_lost(res) and not job.stopping:
            lost.setdefault(region, []).append(res)
            return
        report(region, res)

    def on_done(summary):
        holder["summary"] = summary

    _drive(job, core, parsed["region_tasks"], parsed["unknown"], target_date,
           on_log, on_result, on_done)

    if lost and not job.stopping:
        _retry_lost(job, core, lost, target_date, on_log, report)

    s = holder.get("summary") or {}
    failed = sum(1 for x in list(job.results)
                 if x.get("channel") == "KLOOK" and "실패" in str(x.get("result", "")))
    job.done(summary={
        "channel": "KLOOK",
        "total": parsed["total"],
        "duration": s.get("duration_text", ""),
        "failed": failed,
        "stopped": s.get("stopped", False),
    })
