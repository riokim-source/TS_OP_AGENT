# -*- coding: utf-8 -*-
"""
tpc_open.py
TPC(Trip.com / Ctrip) 오픈 = OTA Close 의 tpc.py 를 '--mode open' 으로 부른다.

⚠️ 이름이 두 개다
    봇·화면에서 부르는 이름은 **TPC**, 라우팅표와 라스트미닛 계산의 key 는 **CP** 다
    (hub/core/lastmin/constants.py 의 CHANNELS, 예약 파일 Agency 코드는 'TPC').
    여기 오는 계획(plan)의 channel 은 'CP' 이고, 실행하는 봇은 tpc.py 다.

⚠️ 수량이 없다
    vBooking 은 날짜별 재고를 999 로 잡아 두고 실제로는 On/off 로만 운영한다
    (화면에 "Sold inventory: 0/999"). 2026-09-09 에 지정 상품 10개를 읽어서
    확인했다. 그래서 메모에 숫자가 적혀 있어도 숫자를 넘기지 않는다.
    라스트미닛 메모가 [CP] 에 숫자를 찍는 것은 '몇 명 받을 수 있는지' 를
    사람에게 알려 주는 것이지, OTA 에 넣는 값이 아니다.

⚠️ 무엇을 여는지는 OP 텍스트가 정한다
    지정 상품 10개를 전부 여는 것이 아니라, 그날 오픈에 들어간 투어만 연다.
    안 그러면 그날 운영하지 않는 자리까지 팔린다.

⚠️ 그날 원래 안 팔던 패키지는 열지 않는다
    tpc.py 의 open 모드는 화면이 'Not set' 이라고 말하는 패키지를 건드리지 않고,
    닫혀 있던 것만 다시 연다. 마감처럼 Select All 을 쓰지 않는 이유다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

from ..paths import ota_close_dir

RESULT_MARKER = "##TPC_RESULT##"
CHANNEL = "CP"          # 계획/메모에서 쓰는 key
LABEL = "TPC"           # 사람이 보는 이름


def _targets():
    """OTA Close/tpc_targets.py 를 읽는다. 목록의 주인은 그 파일 하나다."""
    d = ota_close_dir()
    if d is None:
        return None
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
    try:
        import tpc_targets
        return tpc_targets
    except Exception:
        return None


def available() -> tuple[bool, str]:
    d = ota_close_dir()
    if d is None:
        return False, "OTA Close 폴더를 찾을 수 없습니다 (tpc.py 가 있는 폴더)."
    for f in ("tpc.py", "tpc_targets.py", "tpc_dom.py"):
        if not (d / f).exists():
            return False, f"{f} 가 없습니다: {d}"
    return True, str(d)


def resolve(plan: list[dict]) -> dict:
    """
    오픈 계획 -> 열어야 할 TPC 상품.

    계획에서 channel == "CP" 인 줄만 본다. 그게 OP 텍스트에 [CP] 로 찍힌 것이다.
    맵핑에 없는 이름은 열지 않고 사유를 남긴다 — 비슷하다고 골라서 열면
    엉뚱한 상품의 재고가 열린다.
    """
    tt = _targets()
    if tt is None:
        return {"items": [], "unmapped": [], "error": "tpc_targets 를 읽지 못했습니다."}

    items: dict[str, dict] = {}
    unmapped: list[dict] = []
    for p in plan:
        if p.get("channel") != CHANNEL:
            continue
        name = str(p.get("product") or "").strip()
        if not name:
            continue
        hit = tt.find_by_tour(name)
        if not hit:
            unmapped.append({"tour": name,
                             "reason": "tpc_targets 에 이 이름이 없습니다"})
            continue
        cur = items.setdefault(hit["product_id"], {
            "product_id": hit["product_id"], "name": hit["name"],
            "region": hit["region"], "tours": []})
        if name not in cur["tours"]:
            cur["tours"].append(name)
    return {"items": list(items.values()), "unmapped": unmapped, "error": ""}


def preview(plan: list[dict]) -> dict:
    r = resolve(plan)
    ok, detail = available()
    return {"ok": ok, "detail": detail, **r}


def run(job, plan: list[dict], target_date: str | None,
        dry_run: bool = False) -> None:
    ok, msg = available()
    if not ok:
        job.done(error=msg)
        return
    root = ota_close_dir()

    res = resolve(plan)
    if res.get("error"):
        job.done(error=res["error"])
        return

    for u in res["unmapped"]:
        job.result({"channel": CHANNEL, "item": u["tour"],
                    "result": "매핑 없음", "memo": u["reason"]})
        job.log("SYS", f"[{LABEL}] 매핑 없음: {u['tour']} — {u['reason']}")

    items = res["items"]
    if not items:
        job.log("SYS", f"[{LABEL}] 오픈할 항목이 없습니다 "
                       f"(OP 텍스트에 [{CHANNEL}] 표시가 없습니다).")
        job.done(summary={"channel": CHANNEL, "total": 0})
        return

    for it in items:
        job.log("SYS", f"[{LABEL}] {it['product_id']} {it['name']} "
                       f"({it['region']}) — {', '.join(it['tours'])}")

    cmd = [sys.executable, str(root / "tpc.py"), "--mode", "open",
           "--targets", ",".join(it["name"] for it in items)]
    if target_date:
        cmd += ["--date", target_date]
    if dry_run:
        cmd.append("--dry-run")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    job.total = len(items)
    job.log("SYS", (f"[{LABEL}] DRY-RUN (OK 안 누름)" if dry_run
                    else f"[{LABEL}] 실제 오픈"))
    job.log("SYS", f"[{LABEL}] " + " ".join(cmd))

    proc = subprocess.Popen(
        cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    job.set_stopper(lambda: proc.terminate())

    def pump():
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            if line.startswith(RESULT_MARKER):
                try:
                    d = json.loads(line[len(RESULT_MARKER):].strip())
                    job.result({"channel": CHANNEL, "region": d.get("region", ""),
                                "item": d.get("name", ""),
                                "result": d.get("result", ""),
                                "memo": f"[{d.get('product_id', '')}] {d.get('memo', '')}"})
                except Exception as e:
                    job.log(LABEL, f"결과 파싱 실패: {e}")
                continue
            job.log(LABEL, line)

    th = threading.Thread(target=pump, daemon=True)
    th.start()
    proc.wait()
    th.join(timeout=5)

    if proc.returncode:
        # ⚠️ tpc.py 는 실패가 하나라도 있으면 1 로 끝난다. 그걸 성공으로 덮으면
        #    화면에는 실패가 한 줄도 안 남는다 (2026-09-09 에 그렇게 3건이 묻혔다).
        job.done(error=f"TPC 오픈에서 실패한 상품이 있습니다 (코드 {proc.returncode}) "
                       f"— 위 결과의 실패 줄을 확인하세요")
        return
    job.done(summary={"channel": CHANNEL, "total": len(items),
                      "dry_run": dry_run, "returncode": proc.returncode})
