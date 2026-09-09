# -*- coding: utf-8 -*-
"""
마감 실패가 화면에 실제로 남는지 검사.

2026-09-09 마감. 봇은 실패 3건을 정확히 보고했다.

    합계: 성공 192 / 실패 3 / 스킵 80
      [    GG] 성공 103 / 실패 1 → ERROR: KOREA: page size 50 변경 실패
      [    VI] 성공  34 / 실패 2 → ERROR: 48881P260 / 48881P192 opts_incomplete

그런데 화면에는 '실패 없음' 이 떴다. 마감이 남기는 결과 줄이 전부
result="집계" 인데, 화면은 집계 줄을 실패 세기에서 뺀다(그게 맞다 —
집계는 항목이 아니다). 그래서 실패가 갈 곳이 없었다.

고친 방법: 집계 줄은 그대로 두고, 실패가 있는 채널마다 result="실패" 줄을
따로 남긴다. job.done(error=...) 도 채운다.

⚠️ 여기서는 그날의 **진짜 로그 파일**을 그대로 흘려보낸다. 만들어 낸 줄로
   시험하면 형식이 조금만 달라도 못 잡는다.

    python hub/tests/test_close_failure_visible.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))

from core.close.runner import Tally  # noqa: E402

bad = []

# ── 그날의 실제 로그 (없으면 그날 줄을 그대로 옮긴 것으로 대신한다) ────────
LOGS = [ROOT / "hub/logs/runs/20260909_093738_close.log",
        Path.home() / "Desktop/TS_OP_AGENT-main/hub/logs/runs/20260909_093738_close.log"]
real = next((p for p in LOGS if p.exists()), None)

FALLBACK = """\
09:57:06 [KKDAY] [KKDAY] 봇 집계: success=24 failed=0 skipped=16
09:53:14 [VI] INFO: [VI/Q4] [VI] success=12 failed=1 skipped=19
09:54:23 [VI] INFO: [VI/Q3] [VI] success=5 failed=1 skipped=19
09:57:06 [VI] INFO: [VI/Q2] [VI] success=17 failed=0 skipped=25
09:57:06 [MRT] INFO: [MRT/Q1] 완료 (success=7 failed=0 skipped=0)
09:57:06 [GG] [GG/KOREA] success=88 failed=1 skipped=0
09:57:06 [GG] [GG/JAPAN] success=15 failed=0 skipped=0
09:57:06 [OTA] === OTA Close Bot Summary (2026-09-09) ===
09:57:06 [OTA] [ KKDAY] 성공  24 / 실패   0 / 스킵  16
09:57:06 [OTA] [    GG] 성공 103 / 실패   1 / 스킵   0
09:57:06 [OTA]            └─ ERROR: KOREA: page size 50 변경 실패
09:57:06 [OTA] [    VI] 성공  34 / 실패   2 / 스킵  63
09:57:06 [OTA]            └─ ERROR: worker 치명: 캘린더에서 Thu Sep 10 2026 셀을 찾지 못함
09:57:06 [OTA]            └─ ERROR: 48881P260: opts_incomplete (마감 확정 실패, 수동 확인 필요)
09:57:06 [OTA]            └─ ERROR: 48881P192: opts_incomplete (마감 확정 실패, 수동 확인 필요)
09:57:06 [OTA] [   MRT] 성공  31 / 실패   0 / 스킵   1
09:57:06 [OTA] 합계: 성공 192 / 실패 3 / 스킵 80
"""

if real:
    print(f"  실제 로그 사용: {real.name}")
    lines = real.read_text(encoding="utf-8", errors="replace").splitlines()
else:
    print("  (실제 로그 없음 — 그날 줄을 옮긴 것으로 대신)")
    lines = FALLBACK.splitlines()

# runner 가 하는 것과 같은 방식으로 src 를 붙인다
tally = Tally()
rows = []
for line in lines:
    src = "OTA"
    for bot in ("KKDAY", "KLOOK", "GG", "VI", "MRT"):
        if f"[{bot}]" in line or f"[{bot}/" in line:
            src = bot
            break
    row = tally.feed(line, src)
    if row:
        rows.append(row)

print()
print("  [1] 채널별 집계")
for c, t in sorted(tally.totals.items()):
    print(f"     {c:6} 성공 {t['success']:4} / 실패 {t['failed']:3} / 스킵 {t['skipped']:3}")

print()
print("  [2] 실패 건수")
n = tally.n_failed()
print(f"     합계 실패 {n}건 / 실패한 채널 {sorted(tally.failed_channels())}")
if n != 3:
    bad.append(f"실패 합계가 {n}건 (그날은 3건이었다)")
if sorted(tally.failed_channels()) != ["GG", "VI"]:
    bad.append(f"실패 채널이 {sorted(tally.failed_channels())} (기대 GG, VI)")

print()
print("  [3] 실패 사유가 붙었는가")
for c in sorted(tally.failed_channels()):
    d = tally.detail(c)
    print(f"     {c}: {d[:96]}")
    if d == "로그를 확인하세요":
        bad.append(f"{c} 의 실패 사유를 못 붙였다")
if "page size" not in tally.detail("GG"):
    bad.append("GG 사유에 'page size 50 변경 실패' 가 없다")
if "48881P" not in tally.detail("VI"):
    bad.append("VI 사유에 상품코드가 없다")

print()
print("  [4] 화면이 실패로 세는가")
# common.render_results 와 같은 규칙: '성공'/'집계' 가 아닌 줄만 실패로 센다
final = list(rows)
for chan, t in sorted(tally.failed_channels().items()):
    final.append({"channel": chan, "item": "(채널 전체)", "result": "실패",
                  "memo": f"{t['failed']}건 실패 — {tally.detail(chan)}"})
shown = [r for r in final
         if "성공" not in str(r.get("result", "")) and "집계" not in str(r.get("result", ""))]
print(f"     결과 {len(final)}줄 중 화면이 실패로 세는 줄: {len(shown)}개")
for r in shown:
    print(f"       {r['channel']} · {r['result']} · {str(r['memo'])[:70]}")
if len(shown) != 2:
    bad.append(f"화면이 실패로 세는 줄이 {len(shown)}개 (기대 2개: GG, VI)")

# 집계 줄만 있던 예전 방식이었다면 0개여야 한다 (= 그래서 '실패 없음' 이 떴다)
old = [r for r in rows
       if "성공" not in str(r.get("result", "")) and "집계" not in str(r.get("result", ""))]
print(f"     (옛 방식이었다면: {len(old)}개 → '실패 없음')")
if old:
    bad.append("옛 방식 재현이 안 된다 — 이 테스트가 의미가 없다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 마감 실패는 결과표에 '실패' 로 남는다")
