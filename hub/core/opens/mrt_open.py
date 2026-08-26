# -*- coding: utf-8 -*-
"""
mrt_open.py
MRT 오픈 = OTA Close 의 mrt.py 를 '--mode open' 으로 부른다.

MRT 는 마감/오픈이 완전 대칭이다. 같은 잔여인원 입력칸에 0 대신 N 을 넣으면 끝이라
마감 봇의 저장 플로우(편집모드 → 입력 → 저장 → 확인 → 나가기)를 그대로 재사용한다.

투어명 -> MRT 상품ID 는 hub/data/productmap.json (운영팀 맵핑리스트에서 가져옴).
매핑에 없는 투어는 실행하지 않고 사유를 남긴다 — 이름이 비슷하다고 추측해서 열면
엉뚱한 상품에 재고가 열린다.

픽업 분할: 한 상품에 픽업지가 여러 개면 stockBundles 가 그만큼 잡히므로
mrt.py 의 set_target_date_inventory(split=True) 가 수량을 나눠 넣는다.
    Mt. Fuji Highlight 12 + 픽업 2곳 -> 도쿄 6 / 신주쿠 6
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
import re
import subprocess
import sys
import threading

from ..paths import ota_close_dir
from ..productmap import get_map
from .mrt_courses import course_of, is_multi_course
from ..routing import get_routing

RESULT_MARKER = "##MRT_RESULT##"


def available() -> tuple[bool, str]:
    d = ota_close_dir()
    if d is None:
        return False, "OTA Close 폴더를 찾을 수 없습니다 (mrt.py 가 있는 폴더)."
    if not (d / "mrt.py").exists():
        return False, f"mrt.py 가 없습니다: {d}"
    return True, str(d)


def resolve(plan: list[dict]) -> dict:
    """
    오픈 계획 -> MRT (상품ID, 코스) 별 수량.

    상품ID 하나에 서로 다른 투어가 여러 개 들어있다. 페이지에서는 '코스' 로 갈린다.
        5624093 = 비에이 하이라이트 / 비에이 시그니처 / 비에이 & 후라노
        3887808 = 후지산 하이라이트 / 후지산 시그니처
    코스를 지정하지 않으면 그 날짜 열의 모든 줄에 수량이 나눠 들어간다.
    실제로 'Biei Signature 19' 가 시그니처 10 / 후라노 9 로 갈라져 들어갔다.
    그래서 코스가 여럿인 상품은 코스가 지정돼 있을 때만 연다.

    같은 (상품ID, 코스) 를 여러 투어가 공유하면 수량을 합치지 않고 '가장 큰 값' 을 쓴다.
    어차피 그날 운영하는 건 하나다.
    """
    pm = get_map()
    items: dict[str, dict] = {}
    unmapped: list[dict] = []

    for p in plan:
        if p.get("channel") != "MRT" or p.get("mode") != "qty":
            continue
        qty = int(p.get("qty") or 0)
        if qty <= 0:
            continue
        tour = str(p.get("product") or "")
        entry = pm.get(tour, "MRT")
        if not entry or not entry.get("ids"):
            unmapped.append({"tour": tour, "qty": qty,
                             "reason": "productmap 에 MRT 상품ID 없음"})
            continue

        course = course_of(tour)
        for pid in entry["ids"]:
            if course is None and is_multi_course(pid):
                unmapped.append({
                    "tour": tour, "qty": qty,
                    "reason": (f"상품 {pid} 는 한 페이지에 코스가 여러 개인데 "
                               f"'{tour}' 의 코스가 지정돼 있지 않습니다 "
                               f"(hub/data/mrt_courses.json)")})
                continue
            slot = f"{pid}|{course or ''}"
            cur = items.setdefault(slot, {"id": pid, "qty": 0, "tours": [],
                                          "course": course})
            cur["qty"] = max(cur["qty"], qty)
            if tour not in cur["tours"]:
                cur["tours"].append(tour)

    return {"items": list(items.values()), "unmapped": unmapped}


def preview(plan: list[dict]) -> dict:
    r = resolve(plan)
    ok, detail = available()
    return {"ok": ok, "detail": detail, **r}


def run(job, plan: list[dict], target_date: str | None, dry_run: bool = False) -> None:
    ok, msg = available()
    if not ok:
        job.done(error=msg)
        return
    root = ota_close_dir()

    res = resolve(plan)
    for u in res["unmapped"]:
        job.result({"channel": "MRT", "item": f"{u['tour']} {u['qty']}",
                    "result": "매핑 없음", "memo": u["reason"]})
        job.log("SYS", f"[MRT] 매핑 없음: {u['tour']} — {u['reason']}")

    items = res["items"]
    if not items:
        job.log("SYS", "[MRT] 오픈할 항목이 없습니다.")
        job.done(summary={"channel": "MRT", "total": 0})
        return

    # Chrome 준비 (MRT 는 GLOBAL 프로필)
    r = get_routing()
    key = r.route("KOREA", "MRT") or r.route("JAPAN", "MRT")
    if key is None:
        job.done(error="MRT 의 Chrome 연결이 미설정입니다. [Region x OTA 연결] 에서 지정하세요.")
        return
    if r.port_conflict(key):
        job.done(error=f"port {r.profile_port(key)} 를 다른 프로필의 Chrome 이 점유 중입니다.")
        return
    if not r.profile_owns_port(key):
        job.log("SYS", f"[Chrome] {key} 부팅 중... (port {r.profile_port(key)})")
        boot = r.ensure(key, wait_seconds=25)
        if not (boot.get("ok") and boot.get("ready")):
            job.done(error=boot.get("message", f"{key} Chrome 준비 실패"))
            return
    job.log("SYS", f"[Chrome] MRT -> {key} (port {r.profile_port(key)})")

    for it in items:
        c = f" / 코스 '{it['course']}'" if it.get("course") else ""
        job.log("SYS", f"[MRT] {it['id']} = {it['qty']}명  ({', '.join(it['tours'])}){c}")

    # 코스명에 콤마와 '&' 가 들어가서 --items 문자열로는 못 넘긴다
    items_file = Path(tempfile.gettempdir()) / "lmhub_mrt_items.json"
    items_file.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    cmd = [sys.executable, str(root / "mrt.py"), "--mode", "open",
           "--items-file", str(items_file)]
    if target_date:
        cmd += ["--date", target_date]
    if dry_run:
        cmd.append("--dry-run")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["MRT_CDP_URL"] = f"http://localhost:{r.profile_port(key)}"

    job.total = len(items)
    job.log("SYS", ("[MRT] DRY-RUN (실제 입력 없음)" if dry_run else "[MRT] 실제 오픈"))
    job.log("SYS", "[MRT] " + " ".join(cmd))

    proc = subprocess.Popen(
        cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    job.set_stopper(lambda: proc.terminate())

    id_to_tours = {it["id"]: ", ".join(it["tours"]) for it in items}

    def pump():
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            if line.startswith(RESULT_MARKER):
                try:
                    d = json.loads(line[len(RESULT_MARKER):].strip())
                    pid = str(d.get("product_id", ""))
                    job.result({
                        "channel": "MRT",
                        "item": f"{id_to_tours.get(pid, pid)} {d.get('qty', '')}".strip(),
                        "result": d.get("result", ""),
                        "memo": f"[{pid}] {d.get('memo', '')}",
                    })
                except Exception as e:
                    job.log("MRT", f"결과 파싱 실패: {e}")
                continue
            job.log("MRT", line)

    th = threading.Thread(target=pump, daemon=True)
    th.start()
    proc.wait()
    th.join(timeout=5)

    job.done(summary={"channel": "MRT", "total": len(items),
                      "dry_run": dry_run, "returncode": proc.returncode})
