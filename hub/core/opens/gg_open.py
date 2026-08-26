# -*- coding: utf-8 -*-
"""
gg_open.py (hub)
GG 오픈 = OTA Close 의 gg_open.py 를 호출한다.

GG 는 다른 OTA 와 달리 별도 매핑표가 필요 없다. 옵션 제목에
투어 코드와 픽업지가 그대로 들어 있기 때문이다.

    알남레 - Shared Tour with Rail Bike, Meet at Hongik Univ Station

그래서 '픽업지 제외' 요청을 GG 에서 정확히 실행할 수 있다.
Klook 은 픽업 단위로 막을 수 없어서 그런 건들이 여기로 넘어온다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading

from ..paths import ota_close_dir
from ..routing import get_routing

RESULT_MARKER = "##GG_RESULT##"


def available() -> tuple[bool, str]:
    d = ota_close_dir()
    if d is None:
        return False, "OTA Close 폴더를 찾을 수 없습니다."
    if not (d / "gg_open.py").exists():
        return False, f"gg_open.py 가 없습니다: {d}"
    return True, str(d)


def resolve(plan: list[dict]) -> dict:
    """오픈 계획 -> GG 항목 목록. 지역별로 묶는다 (지역마다 Chrome 이 다르다)."""
    by_region: dict[str, list[dict]] = {}
    for p in plan:
        if p.get("channel") != "GG" or p.get("mode") != "qty":
            continue
        qty = int(p.get("qty") or 0)
        if qty < 0:
            continue
        # 0 은 '마감' 이다 (GG 는 Block 켜기). gg_open.py 가 그렇게 처리한다.
        by_region.setdefault(p.get("region") or "KOREA", []).append({
            "tour": p.get("product"),
            "qty": qty,
            "pickups": list(p.get("pickups_allowed") or []),
            "note": p.get("note", ""),
        })
    return {"by_region": by_region,
            "total": sum(len(v) for v in by_region.values())}


def preview(plan: list[dict]) -> dict:
    ok, detail = available()
    return {"ok": ok, "detail": detail, **resolve(plan)}


def run(job, plan: list[dict], target_date: str | None, dry_run: bool = False) -> None:
    ok, msg = available()
    if not ok:
        job.done(error=msg)
        return
    root = ota_close_dir()

    res = resolve(plan)
    if not res["total"]:
        job.log("SYS", "[GG] 오픈할 항목이 없습니다.")
        job.done(summary={"channel": "GG", "total": 0})
        return

    if not target_date:
        from datetime import date, timedelta
        target_date = (date.today() + timedelta(days=1)).isoformat()

    r = get_routing()
    total_done = 0
    for region, items in res["by_region"].items():
        key = r.route(region, "GG")
        if key is None:
            for it in items:
                job.result({"channel": "GG", "item": f"{it['tour']} {it['qty']}",
                            "result": "미설정",
                            "memo": f"{region}/GG Chrome 연결이 미설정입니다."})
            continue
        if r.port_conflict(key):
            job.log("SYS", f"[오류] {key}: port {r.profile_port(key)} 를 다른 프로필의 "
                           f"Chrome 이 점유 중입니다.")
            continue
        if not r.profile_owns_port(key):
            job.log("SYS", f"[Chrome] {key} 부팅 중... (port {r.profile_port(key)})")
            boot = r.ensure(key, wait_seconds=25)
            if not (boot.get("ok") and boot.get("ready")):
                job.log("SYS", f"[오류] {key}: {boot.get('message', '부팅 실패')}")
                continue

        # 로그인 확인 — GG 는 2단계 인증(TOTP)이 자주 걸린다
        try:
            chk = r.check_login(key, ["GG"], timeout=20)
            st = (chk.get("results") or [{}])[0].get("state")
            if st == "logged_out":
                url = (chk.get("results") or [{}])[0].get("url", "")
                job.log("SYS", f"[경고] {region} ({key}) GG 로그인이 필요합니다. "
                               f"그 Chrome 창에서 로그인(2단계 인증 포함) 후 다시 실행하세요.")
                job.log("SYS", f"        {url[:120]}")
                for it in items:
                    job.result({"channel": "GG", "item": f"{it['tour']} {it['qty']}",
                                "result": "로그인 필요", "memo": url[:120]})
                continue
            job.log("SYS", f"[로그인] {region} ({key}) GG 로그인됨")
        except Exception as e:
            job.log("SYS", f"[주의] {region} GG 로그인 확인 실패: {e}")

        port = r.profile_port(key)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
            items_file = f.name

        for it in items:
            pk = f" (픽업 {', '.join(it['pickups'])})" if it["pickups"] else " (모든 픽업)"
            job.log("SYS", f"[GG] {region} {it['tour']} +{it['qty']}{pk}")

        cmd = [sys.executable, str(root / "gg_open.py"),
               "--date", target_date, "--items-file", items_file, "--port", str(port)]
        if dry_run:
            cmd.append("--dry-run")

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        job.log("SYS", ("[GG] DRY-RUN (실제 변경 없음)" if dry_run else "[GG] 실제 오픈")
                       + f" — {region}")
        proc = subprocess.Popen(
            cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        job.set_stopper(lambda p=proc: p.terminate())

        def pump(p=proc, rg=region):
            assert p.stdout is not None
            for raw in p.stdout:
                line = raw.rstrip()
                if not line:
                    continue
                if line.startswith(RESULT_MARKER):
                    try:
                        d = json.loads(line[len(RESULT_MARKER):].strip())
                        job.result({
                            "channel": "GG", "region": rg,
                            "item": f"{d.get('tour', '')} {d.get('qty', '')}".strip(),
                            "result": d.get("result", ""),
                            "memo": (d.get("title", "")[:60] + " | " if d.get("title") else "")
                                    + d.get("memo", ""),
                        })
                    except Exception as e:
                        job.log("GG", f"결과 파싱 실패: {e}")
                    continue
                job.log("GG", line)

        th = threading.Thread(target=pump, daemon=True)
        th.start()
        proc.wait()
        th.join(timeout=5)
        total_done += len(items)
        try:
            os.unlink(items_file)
        except Exception:
            pass

    job.done(summary={"channel": "GG", "total": total_done, "dry_run": dry_run})
