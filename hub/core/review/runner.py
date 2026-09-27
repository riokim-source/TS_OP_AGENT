# -*- coding: utf-8 -*-
"""
runner.py
리뷰 수집·분석을 OP System 안에서 돌린다.

엔진은 'Review Analyzer' 폴더에 있는 것을 그대로 쓴다 (원본 앱에서 옮겨온 것).
여기서는 **OP 방식에 맞추는 일만** 한다.

  1) Chrome 은 라스트미닛이 쓰는 그것을 쓴다 (KR 9522 / JP 9523 / AU 9524 /
     UK 9525 / GLOBAL 9530). 포트를 여기 다시 적지 않는다 —
     Review Analyzer/core/browser_profiles.py 가 hub/core/routing.py 를 본다.
  2) 진행 상황을 job.log 로 흘린다. 화면(로컬)과 웹(Agent) 이 같은 것을 본다.
  3) 결과는 job.result 줄로 남기고, 엑셀은 **그 PC 에** 저장한다.

⚠️ 리뷰 수집은 로그인된 Chrome 이 있는 PC 에서만 된다. 클라우드에서는 돌 수
   없다. 웹으로 쓸 때는 Agent 가 이 함수를 대신 부른다 (kind="review").
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..paths import SYSTEM_DIR

ENGINE_DIR = SYSTEM_DIR / "Review Analyzer"


def available() -> tuple[bool, str]:
    """엔진을 돌릴 수 있는 상태인가. (된다/안 된다, 사유)"""
    if not (ENGINE_DIR / "rcore" / "pipeline.py").exists():
        return False, f"리뷰 분석 엔진이 없습니다: {ENGINE_DIR}"
    try:
        import selenium  # noqa: F401
    except Exception:
        return False, ("selenium 이 설치돼 있지 않습니다. "
                       "설치.bat 을 다시 실행하거나 pip install selenium 하세요.")
    return True, str(ENGINE_DIR)


def _engine():
    """
    엔진을 import 한다.

    ⚠️ 엔진의 패키지 이름은 'rcore' 다. 원본에서는 'core' 였는데, hub 에도
       같은 이름이 있어서 한쪽이 다른 쪽을 가린다. 이식하면서 바꿨다 —
       sys.modules 를 손대서 가리는 방식은 같은 프로세스에서 도는 화면
       (Streamlit) 의 hub.core 까지 망가뜨린다.
    """
    import importlib
    p = str(ENGINE_DIR)
    if p not in sys.path:
        sys.path.append(p)
    pipeline = importlib.import_module("rcore.pipeline")
    config = importlib.import_module("collectors.config")
    profiles = importlib.import_module("rcore.browser_profiles")
    return pipeline, config, profiles


def channels() -> list[str]:
    _p, config, _pr = _engine()
    return list(config.SUPPORTED_CHANNELS)


def channel_label(ch: str) -> str:
    _p, config, _pr = _engine()
    return config.CHANNEL_DISPLAY.get(ch, ch)


def run(job, params: dict) -> None:
    """
    한 번 수집한다. params:
        path     수집 기준이 되는 예약 리포트 엑셀 (그 PC 의 경로)
        start/end  YYYY-MM-DD (비우면 파일 전체)
        areas    ["Busan", ...]  (비우면 전부)
        channels ["L","KK",...]  (비우면 전부)
        out      결과 엑셀 경로 (비우면 원본 옆에 만든다)
    """
    ok, why = available()
    if not ok:
        job.done(error=why)
        return

    src = Path(str(params.get("path") or "").strip('"'))
    if not src.exists():
        job.done(error=f"엑셀을 찾지 못했습니다: {src}")
        return

    pipeline, _config, profiles = _engine()
    job.log("SYS", f"[리뷰] 엔진 {ENGINE_DIR}")
    job.log("SYS", f"[리뷰] 파일 {src}")

    manager = profiles.BrowserProfileManager()

    def log(msg="") -> None:
        for line in str(msg).splitlines() or [""]:
            if line.strip():
                job.log("REVIEW", line)

    try:
        result = pipeline.run_phase1(
            excel_path=str(src),
            start_date=params.get("start") or None,
            end_date=params.get("end") or None,
            areas=params.get("areas") or None,
            channels=params.get("channels") or None,
            output_path=params.get("out") or None,
            log=log,
            browser_manager=manager,
            auto_launch_browsers=True,
        )
    except Exception as e:
        job.log("SYS", f"[오류] {e}")
        job.done(error=str(e)[:300])
        return

    # 채널별로 몇 건을 모았는지 결과 줄로 남긴다 (사람이 제일 먼저 보는 것)
    for ch, info in (result.collection_status or {}).items():
        for profile, st in (info or {}).items():
            job.result({
                "channel": ch, "region": profile,
                "item": f"{ch} · {profile}",
                "result": str(st.get("status") or ""),
                "memo": str(st.get("message") or st.get("error") or "")[:300],
            })

    n = int(len(result.master)) if result.master is not None else 0
    job.log("SYS", f"[리뷰] 리뷰 {n}건 · 엑셀 {result.output_path}")
    job.done(summary={"kind": "review", "rows": n,
                      "output": str(result.output_path),
                      "file": str(src)})
