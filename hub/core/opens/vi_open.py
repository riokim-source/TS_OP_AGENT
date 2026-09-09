# -*- coding: utf-8 -*-
"""
vi_open.py
Viator 오픈 = OTA Close 의 vi.py 를 '--mode open' 으로 부른다.

Viator 는 수량 개념이 없다. 그날 날짜를 'Sold out' 으로 두느냐 'Available' 로
두느냐 뿐이다. 그래서 라스트미닛 메모도 VI 에는 숫자 없이 상품명만 찍는다
(mode="resume"). 여기서는 그 이름을 상품 번호로 바꿔서 넘긴다.

⚠️ 무엇을 열지는 OP 텍스트가 정한다. 지정 상품 14개를 전부 여는 것이 아니라,
   그날 오픈에 실제로 들어간 투어의 상품만 연다. 안 그러면 그날 운영하지
   않는 자리까지 팔린다.

⚠️ 'Not operating' 은 예약 취소를 뜻한다. 절대 누르지 않는다.
   누를 수 있는 것은 'Sold out'(마감) 과 'Available'(오픈) 둘뿐이고,
   vi.py 의 안전 가드 0 이 그 밖의 값을 막는다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

from ..paths import ota_close_dir

RESULT_MARKER = "##VI_RESULT##"


def _targets():
    """OTA Close/vi_targets.py 를 읽는다. 목록의 주인은 그 파일 하나다."""
    d = ota_close_dir()
    if d is None:
        return None
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
    try:
        import vi_targets
        return vi_targets
    except Exception:
        return None


def available() -> tuple[bool, str]:
    d = ota_close_dir()
    if d is None:
        return False, "OTA Close 폴더를 찾을 수 없습니다 (vi.py 가 있는 폴더)."
    if not (d / "vi.py").exists():
        return False, f"vi.py 가 없습니다: {d}"
    if not (d / "vi_targets.py").exists():
        return False, f"vi_targets.py 가 없습니다: {d}"
    return True, str(d)


def resolve(plan: list[dict]) -> dict:
    """
    오픈 계획 -> 열어야 할 Viator 상품 번호.

    계획에서 channel == "VI" 인 줄만 본다. 그게 OP 텍스트에 VI 로 표시된 것이다.
    한 번호에 투어가 여러 개 묶여 있으므로(경주 / 경주Express 가 같은 번호)
    같은 번호로 모이면 하나로 합친다.

    맵핑에 없는 이름은 열지 않고 사유를 남긴다 — 비슷하다고 추측해서 열면
    엉뚱한 상품이 열린다.
    """
    vt = _targets()
    if vt is None:
        return {"items": [], "unmapped": [], "error": "vi_targets 를 읽지 못했습니다."}

    items: dict[str, dict] = {}
    unmapped: list[dict] = []
    for p in plan:
        if p.get("channel") != "VI":
            continue
        name = str(p.get("product") or "").strip()
        if not name:
            continue
        code = vt.code_for_tour(name)
        if not code:
            unmapped.append({"tour": name,
                             "reason": "vi_targets 에 이 이름이 없습니다"})
            continue
        cur = items.setdefault(code, {"code": code, "tours": [],
                                      "label": vt.label_of(code)})
        if name not in cur["tours"]:
            cur["tours"].append(name)
    return {"items": list(items.values()), "unmapped": unmapped, "error": ""}


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
    if res.get("error"):
        job.done(error=res["error"])
        return

    for u in res["unmapped"]:
        job.result({"channel": "VI", "item": u["tour"],
                    "result": "매핑 없음", "memo": u["reason"]})
        job.log("SYS", f"[VI] 매핑 없음: {u['tour']} — {u['reason']}")

    items = res["items"]
    if not items:
        job.log("SYS", "[VI] 오픈할 항목이 없습니다 (OP 텍스트에 VI 표시가 없습니다).")
        job.done(summary={"channel": "VI", "total": 0})
        return

    for it in items:
        job.log("SYS", f"[VI] {it['code']} {it['label']}  ({', '.join(it['tours'])})")

    codes = [it["code"] for it in items]
    cmd = [sys.executable, str(root / "vi.py"), "--mode", "open",
           "--codes", ",".join(codes)]
    if target_date:
        cmd += ["--date", target_date]
    if dry_run:
        cmd.append("--dry-run")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    job.total = len(items)
    job.log("SYS", ("[VI] DRY-RUN (실제 클릭 없음)" if dry_run else "[VI] 실제 오픈"))
    job.log("SYS", "[VI] " + " ".join(cmd))

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
                    job.result({"channel": "VI", "region": "",
                                "item": d.get("label") or d.get("code", ""),
                                "result": d.get("result", ""),
                                "memo": f"[{d.get('code', '')}] {d.get('memo', '')}"})
                except Exception as e:
                    job.log("VI", f"결과 파싱 실패: {e}")
                continue
            job.log("VI", line)

    th = threading.Thread(target=pump, daemon=True)
    th.start()
    proc.wait()
    th.join(timeout=5)

    if proc.returncode:
        job.done(error=f"Viator 오픈이 비정상 종료했습니다 (코드 {proc.returncode})")
        return
    job.done(summary={"channel": "VI", "total": len(items),
                      "dry_run": dry_run, "returncode": proc.returncode})
