"""
실행 결과 통지.

현재: stdout + logs/summary_YYYY-MM-DD.txt
TODO: 카카오톡 / Slack / 이메일 연동
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List

from .types import Result

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def notify(results: List[Result], target_date_str: str) -> None:
    lines = [
        f"=== OTA Close Bot Summary ({target_date_str}) ===",
        f"실행 완료 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    total_s = total_f = total_sk = 0
    for r in results:
        lines.append(
            f"[{r['agency']:>6}] 성공 {r['success']:>3} / 실패 {r['failed']:>3} / 스킵 {r['skipped']:>3}"
        )
        total_s += r["success"]
        total_f += r["failed"]
        total_sk += r["skipped"]
        for err in r.get("errors", []):
            lines.append(f"           └─ ERROR: {err}")
    lines.append("")
    lines.append(f"합계: 성공 {total_s} / 실패 {total_f} / 스킵 {total_sk}")
    lines.append("=" * 50)

    body = "\n".join(lines)
    print(body)

    out = LOGS_DIR / f"summary_{target_date_str}.txt"
    out.write_text(body, encoding="utf-8")
