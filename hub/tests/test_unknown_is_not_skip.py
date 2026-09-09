# -*- coding: utf-8 -*-
"""
'판단할 수 없었다' 를 '괜찮다' 로 보고하지 않는지 검사.

2026-09-09 마감에서 실제로 안 닫힌 것 두 개가 조용히 넘어갔다.

  1) VI 48881P245 (Noboribetsu, Lake Toya and Niseko)
        WARNING: Apply 실패 — 이전 필터가 남아 있어 판단할 수 없다
        INFO:    skip: target 슬롯 없음 (reason=apply_failed)
     -> '스킵'(=그날 안 파는 상품) 으로 세어졌고, 재시도 목록에도 없었다.

  2) MRT 5728538 (시라카와고)
        [안내] 화면 로드됨 + '예약 인원 수정' 버튼 없음 → 판매중 아님
        [MRT/5728538] ... → SKIP
     -> 실제로는 판매중이었다. 그 화면에는 '판매 중지' 버튼이 떠 있었다.
        '예약 인원 관리' 라는 글자를 로드 완료 앵커로 썼는데, 그건 **왼쪽 메뉴
        이름**이라 표가 뜨기 한참 전부터 있다. 그래서 아직 안 그려진 화면을
        '다 떴다' 고 보고 버튼이 없는 것을 '안 파는 상품' 으로 단정했다.

핵심: '봤는데 없더라'(no_section/no_slots) 와 '보지도 못했다'(apply_failed 등)
는 완전히 다르다. 뒤의 것을 스킵으로 세면 열린 재고가 그대로 남는다.

    python hub/tests/test_unknown_is_not_skip.py
"""
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
VI = (ROOT / "OTA Close" / "vi.py").read_text(encoding="utf-8", errors="replace")
MRT = (ROOT / "OTA Close" / "mrt.py").read_text(encoding="utf-8", errors="replace")

bad = []

# ── 1) VI: 판단 불가 사유가 재시도 대상인가 ─────────────────────────────
print("  [1] VI — '판단할 수 없었다' 를 다시 해 보는가")
UNKNOWN = ["apply_failed", "checkbox_missing", "opts_incomplete"]
SEEN = ["no_section", "no_slots"]

# 재시도 목록에 들어가는 사유들
lists = re.findall(r'if res\.get\("reason"\) in \(([^)]*)\):', VI, re.S)
if not lists:
    bad.append("VI 재시도 목록을 찾지 못했다")
for i, body in enumerate(lists, 1):
    names = set(re.findall(r'"([a-z_?]+)"', body))
    missing = [u for u in UNKNOWN if u not in names]
    print(f"     재시도 목록 {i}: {'전부 있음' if not missing else '!! 빠짐 ' + str(missing)}")
    if missing:
        bad.append(f"VI 재시도 목록 {i} 에 {missing} 이 없다")

# 재시도 후에도 안 되면 '실패' 로 세는가
m = re.search(r'elif res\.get\("reason"\) in \(([^)]*)\):\s*\n(?:\s*#.*\n)*\s*failed \+= 1', VI)
print(f"     재시도 뒤에도 안 되면 실패로 셈: {'예' if m else '!! 아니오'}")
if not m:
    bad.append("VI: 판단 불가가 끝까지 남아도 실패로 안 센다")
else:
    names = set(re.findall(r'"([a-z_]+)"', m.group(1)))
    missing = [u for u in UNKNOWN if u not in names]
    if missing:
        bad.append(f"VI 실패 처리에 {missing} 이 없다")
    leaked = [s for s in SEEN if s in names]
    if leaked:
        bad.append(f"VI 실패 처리에 {leaked} 가 들어갔다 — 그건 '봤는데 없더라' 다")
    print(f"       실패로 세는 사유: {sorted(names)}")

# 먼저 돌려보는가 (우선순위)
prio = re.search(r"_PRIO = \{([^}]*)\}", VI, re.S)
if prio:
    body = prio.group(1)
    ranks = dict(re.findall(r'"([a-z_?]+)":\s*(\d+)', body))
    worst_unknown = max(int(ranks.get(u, 9)) for u in UNKNOWN)
    best_seen = min(int(ranks.get(s, 0)) for s in SEEN)
    print(f"     우선순위: 판단불가 최대 {worst_unknown} / 봤는데없음 최소 {best_seen}")
    if worst_unknown >= best_seen:
        bad.append("VI: '판단 불가' 를 '봤는데 없음' 보다 먼저 안 본다")

# ── 2) MRT: '판매중 아님' 을 무엇으로 단정하는가 ────────────────────────
print()
print("  [2] MRT — '판매중 아님' 을 있는 것으로 판정하는가")
fn = MRT[MRT.index("def enable_inventory_edit_mode("):]
fn = fn[:fn.index("\ndef ", 10)]

# 로드 완료 앵커에서 메뉴 이름을 뺐는가
ready = fn[fn.index("def _page_ready("):]
ready = ready[:ready.index("def ", 10)]
# 설명 문구가 아니라 실제 판정 코드(includes 호출)만 본다
menu_used = bool(re.search(r"includes\(\s*['\"]예약 인원 관리['\"]\s*\)", ready))
print(f"     로드 앵커에 메뉴 이름('예약 인원 관리') 씀: "
      f"{'!! 예 — 표 뜨기 전에도 있다' if menu_used else '아니오'}")
if menu_used:
    bad.append("MRT: 왼쪽 메뉴 이름을 로드 완료 근거로 쓴다")

# 판매 상태를 직접 본다
has_state = "_sales_state" in fn
print(f"     판매 상태를 직접 확인: {'예' if has_state else '!! 아니오'}")
if not has_state:
    bad.append("MRT: 판매 상태를 직접 확인하지 않는다")

# '판매 재개' 가 보일 때만 no_button
tail = fn[fn.index("if not btn_seen:"):]
tail = tail[:tail.index("# 2)")] if "# 2)" in tail else tail
gated = re.search(r'state\s*==\s*"stopped"[\s\S]{0,400}?return "no_button"', tail)
print(f"     '판매 재개' 가 보일 때만 스킵: {'예' if gated else '!! 아니오'}")
if not gated:
    bad.append("MRT: '판매 재개' 확인 없이 스킵으로 단정한다")

# 판매중인데 버튼 없으면 실패
selling_fail = re.search(r'state\s*==\s*"selling"[\s\S]{0,400}?return "not_loaded"', tail)
print(f"     판매중인데 버튼 없으면 실패: {'예' if selling_fail else '!! 아니오'}")
if not selling_fail:
    bad.append("MRT: 판매중인데 버튼이 없어도 실패로 안 본다")

# 스킵으로 끝나는 길이 하나뿐인가
n_skip = tail.count('return "no_button"')
print(f"     스킵으로 끝나는 길: {n_skip}개")
if n_skip != 1:
    bad.append(f"MRT: 스킵으로 끝나는 길이 {n_skip}개 (하나여야 한다)")

# ── 3) 그날 로그를 다시 읽으면 어떻게 되나 ──────────────────────────────
print()
print("  [3] 그날 두 건이 이제 어떻게 분류되는가")
for who, reason, before, after in (
        ("VI 48881P245", "apply_failed", "스킵(안 파는 상품)", "재시도 → 실패(수동확인)"),
        ("MRT 5728538", "판매중인데 버튼 없음", "스킵(판매중 아님)", "실패(판정 불가)")):
    print(f"     {who:14} {reason:22} {before} -> {after}")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 모르는 것은 스킵이 아니라 실패로 남는다")
