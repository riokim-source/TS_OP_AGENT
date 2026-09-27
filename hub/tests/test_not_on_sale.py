# -*- coding: utf-8 -*-
"""
'실패' 와 '판매중 아님' 을 가르는 검사.

2026-09-27 실제 마감에서 실패 3건이 올라왔는데, 두 건은 그날 팔 것이 없는
상품이었다.

  MRT 3890255 / 4843937 — "부분 마감: 2/3개만 0 (1개 미완)"
      그 날짜 칸이 전부 **빈칸('')** 이었다. 아직 판매를 시작하지 않은 상품이라
      넣을 수량 자체가 없는데, 0 을 써 넣으려다 한 칸에서 타임아웃이 나서
      부분 마감 실패가 됐다.

  VI 48881P93 — "opts_incomplete (마감 확정 실패, 수동 확인 필요)"
      1차에서 no_slots(= 다음날·다다음날 헤더는 보이는데 대상 날짜만 없음,
      그날 운영 안 함) 로 확정했는데, 끝의 재시도가 판단 불가로 끝나면서
      그 판정을 뒤집어 실패로 올렸다.

⚠️ 이 검사의 절반은 반대쪽이다. **모르는 것은 '판매중 아님' 이 아니다.**
   값을 못 읽었거나 아무 헤더도 안 그려졌으면 그건 실패로 남아야 한다.
   조용히 스킵하면 열린 재고가 그대로 팔린다.

    python hub/tests/test_not_on_sale.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "OTA Close"))

import mrt                                   # noqa: E402
import vi                                    # noqa: E402

bad = []


def cells(*vals):
    return [{"name": f"stockBundles.{i}.stocks.1.remainQuantity", "value": v}
            for i, v in enumerate(vals)]


# ── 1) MRT — 그날 팔 것이 있는가 ────────────────────────────────────────
print("  [1] MRT — 그 날짜 칸을 보고 '판매중 아님' 을 가른다")
CASES = [
    ("전부 빈칸 (오늘 3890255)", cells("", "", ""), True),
    ("전부 0", cells("0", "0", "0"), True),
    ("빈칸과 0 이 섞임", cells("", "0", ""), True),
    ("한 칸에 10명 남아 있음", cells("", "10", ""), False),
    ("한 칸을 못 읽음 (모름)", cells("", None, ""), False),
    ("칸이 아예 없음", [], False),
]
for label, cs, want in CASES:
    got = mrt.nothing_to_sell(cs, total=0, split=False)
    mark = "" if got == want else f"   !! 기대 {want}"
    print(f"     {label:26} -> {'판매중 아님' if got else '마감 진행'}{mark}")
    if got != want:
        bad.append(f"{label}: {got} (기대 {want})")

# 오픈(수량 배분)에는 절대 쓰지 않는다 — 0 이라고 건너뛰면 안 열린다
if mrt.nothing_to_sell(cells("", "", ""), total=6, split=True):
    bad.append("오픈인데 '판매중 아님' 으로 건너뛴다")
if mrt.nothing_to_sell(cells("0", "0"), total=4, split=False):
    bad.append("수량이 있는데 건너뛴다")

print()
print("  [2] MRT — 판정이 실제 흐름에 연결됐는가")
SRC = (ROOT / "OTA Close" / "mrt.py").read_text(encoding="utf-8")
for label, cond in (
        ("판정을 쓴다", "if nothing_to_sell(cells, total, split):" in SRC),
        ("'not_on_sale' 로 돌려준다", 'return total_cells, total_cells, "not_on_sale"' in SRC),
        ("마감 결과가 스킵이 된다", '"result": "스킵"' in SRC and "판매중 아님" in SRC),
        ("저장을 누르지 않는다", 'if state == "not_on_sale":' in SRC)):
    print(f"     {label:24} {'예' if cond else '!! 아니오'}")
    if not cond:
        bad.append(f"{label} — 아니다")

# ── 3) VI — 근거 있는 판정을 판단 불가가 뒤집지 못한다 ──────────────────
print()
print("  [3] VI — 1차 '그날 운영 안 함' 을 2차 판단 불가가 뒤집는가")
VCASES = [
    ("no_slots → opts_incomplete (오늘 48881P93)", "no_slots", "opts_incomplete", True),
    ("no_slots → apply_failed", "no_slots", "apply_failed", True),
    ("no_slots → checkbox_missing", "no_slots", "checkbox_missing", True),
    ("no_section → opts_incomplete", "no_section", "opts_incomplete", False),
    ("apply_failed → opts_incomplete", "apply_failed", "opts_incomplete", False),
    ("timeout_with_target → opts_incomplete", "timeout_with_target", "opts_incomplete", False),
    ("no_slots → click_fail", "no_slots", "click_fail", False),
    ("빈 값", "", "", False),
]
for label, prev, new, want in VCASES:
    got = vi.keep_not_operating(prev, new)
    mark = "" if got == want else f"   !! 기대 {want}"
    print(f"     {label:42} -> {'스킵 유지' if got else '실패'}{mark}")
    if got != want:
        bad.append(f"{label}: {got} (기대 {want})")

VSRC = (ROOT / "OTA Close" / "vi.py").read_text(encoding="utf-8")
i_keep = VSRC.find("keep_not_operating(prev_reason")
i_fail = VSRC.find('errors.append(f"{p[\'code\']}: {res.get("reason")}')
if i_fail < 0:
    i_fail = VSRC.find("마감 확정 실패, 수동 확인 필요")
ok_order = 0 < i_keep < i_fail
print(f"     실패로 올리기 전에 먼저 본다: {'예' if ok_order else '!! 아니오'}")
if not ok_order:
    bad.append("VI 가 근거 있는 판정을 보기 전에 실패로 올린다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 팔 것이 없으면 스킵, 모르면 실패")
