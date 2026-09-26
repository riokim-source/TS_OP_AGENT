# -*- coding: utf-8 -*-
"""
'실패로 찍혔는데 실제로는 닫혀 있던' 것들.

2026-09-20 ~ 09-25 마감 로그에서 매일 같은 자리가 실패로 올라왔다.

  MRT  3890255 / 4843937 — "부분 마감: 2/3개만 0 (1개 미완)"
        로그를 보면 그 칸은 이미 0 이었다. 값을 읽으려고 셀마다 locator 로
        왕복하다 한 칸에서 3초 타임아웃이 났고, '못 읽음' 을 '목표값과 다름'
        으로 세어 실패가 됐다.

  KKDAY 8974 / 17654 / 18613 — "CONFIRM_FAILED: Confirm 버튼 못 찾음"
        그 순간 화면에 보이던 버튼은 [Clear, Search] 뿐이었다. 창이 이미
        스스로 닫힌 것이다. 재시도에서는 늘 0행(ALREADY_CLOSED)이었다.

둘 다 **누른 결과가 아니라 화면 상태로 판정**하면 사라진다.
반대로 진짜로 열려 있을 때는 그대로 실패여야 한다 — 그게 이 검사의 절반이다.

    python hub/tests/test_false_failures.py
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
import kkday                                 # noqa: E402

bad = []

# ── 1) MRT: 값 읽기는 한 번에 ───────────────────────────────────────────
print("  [1] MRT — 셀 값을 한 번에 읽는가")
NAMES = ["stockBundles.12.stocks.6.remainQuantity",
         "stockBundles.16.stocks.6.remainQuantity",
         "stockBundles.20.stocks.6.remainQuantity"]


class FakePage:
    """evaluate 한 번으로 값을 돌려준다. 몇 번 불렸는지 센다."""

    def __init__(self, values, boom=False):
        self.values = values
        self.boom = boom
        self.calls = 0

    def evaluate(self, js, arg=None):
        self.calls += 1
        if self.boom:
            raise RuntimeError("Timeout 3000ms exceeded")
        return [self.values.get(n) for n in (arg or [])]


pg = FakePage({n: "0" for n in NAMES})
got = mrt.read_cell_values(pg, NAMES)
print(f"     값 {got}")
print(f"     화면에 물어본 횟수 {pg.calls} (셀은 {len(NAMES)}개)")
if got != {n: "0" for n in NAMES}:
    bad.append(f"값을 잘못 읽었다: {got}")
if pg.calls != 1:
    bad.append(f"셀마다 왕복한다 ({pg.calls}회) — 여기서 타임아웃이 났다")

# 없는 칸은 None. '모른다' 를 '맞다' 로 바꾸면 열린 재고를 성공으로 덮는다.
pg2 = FakePage({NAMES[0]: "0", NAMES[1]: None, NAMES[2]: "3"})
got2 = mrt.read_cell_values(pg2, NAMES)
print(f"     섞인 경우 {got2}")
if got2[NAMES[1]] is not None:
    bad.append("화면에 없는 칸을 값이 있는 것처럼 읽었다")
if got2[NAMES[2]] != "3":
    bad.append("남아 있는 수량을 못 읽었다")

# 화면이 통째로 안 읽히면 전부 None (= 전부 미확인 = 실패)
pg3 = FakePage({}, boom=True)
got3 = mrt.read_cell_values(pg3, NAMES)
print(f"     화면이 안 읽힐 때 {got3}")
if set(got3.values()) != {None}:
    bad.append("화면을 못 읽었는데 값이 있는 것처럼 답했다")
if mrt.read_cell_values(pg3, []) != {}:
    bad.append("빈 목록에서 터진다")

# ── 2) MRT: 이미 0 인 칸은 건드리지 않는다 ──────────────────────────────
print()
print("  [2] MRT — 이미 목표값이면 쓰지 않는가")
SRC = (ROOT / "OTA Close" / "mrt.py").read_text(encoding="utf-8")
for label, cond in (("이미 목표값이면 건너뛴다", 'if raw == want:' in SRC),
                    ("빈 칸과 0 을 구분해 찍는다", 'value={raw!r}' in SRC),
                    ("검증도 한 번에 읽는다", "_wrong_now()" in SRC)):
    print(f"     {label:24} {'예' if cond else '!! 아니오'}")
    if not cond:
        bad.append(f"{label} — 아니다")
# 빈 칸('')은 0 이 아니다 — 미입력이라 닫힌 게 아니다
if "raw = cell.get(\"value\")" not in SRC:
    bad.append("값을 원래대로(빈 칸 포함) 읽지 않는다")

# ── 3) KKDAY: Confirm 창이 없을 때 ──────────────────────────────────────
print()
print("  [3] KKDAY — Confirm 창이 안 보이면 화면을 다시 본다")


class FakeKkPage:
    def __init__(self, searchable=True):
        self.searchable = searchable
        self.searched = 0

    def get_by_role(self, role, name=None):
        page = self

        class B:
            def click(self, timeout=None):
                if not page.searchable:
                    raise RuntimeError("Search 버튼 없음")
                page.searched += 1
        return B()


def with_state(rows, confirmed, searchable=True):
    old = (kkday.count_open_rows, kkday.search_confirmed_closed, kkday.wait_search_results)
    kkday.count_open_rows = lambda p: rows
    kkday.search_confirmed_closed = lambda p: confirmed
    kkday.wait_search_results = lambda p, **k: None
    try:
        pg = FakeKkPage(searchable)
        return kkday.closed_now(pg), pg
    finally:
        (kkday.count_open_rows, kkday.search_confirmed_closed,
         kkday.wait_search_results) = old


CASES = [
    ("0행 + 화면이 마감을 확인해 줌", 0, True, True, True),
    ("아직 9행 열려 있음", 9, True, True, False),
    ("0행이지만 빈 테이블(렌더 안 됨)", 0, False, True, False),
    ("Search 버튼도 못 누름 + 0행 확인", 0, True, False, True),
]
for label, rows, confirmed, searchable, want in CASES:
    got, pg = with_state(rows, confirmed, searchable)
    mark = "" if got == want else f"   !! 기대 {want}"
    print(f"     {label:30} -> {'닫힘' if got else '아직 열림'}{mark}")
    if got != want:
        bad.append(f"{label}: {got} (기대 {want})")

KSRC = (ROOT / "OTA Close" / "kkday.py").read_text(encoding="utf-8")
i_fail = KSRC.find("pr.status = PackageStatus.CONFIRM_FAILED")
i_check = KSRC.find("if closed_now(page):")
print(f"     실패로 두기 전에 다시 본다: {'예' if 0 < i_check < i_fail else '!! 아니오'}")
if not (0 < i_check < i_fail):
    bad.append("Confirm 못 찾으면 화면을 안 보고 바로 실패로 둔다")

# ── 4) 오픈: '결과가 하나도 없습니다' 에 진짜 사유를 붙인다 ─────────────
print()
print("  [4] 오픈 — 왜 하나도 못 열었는지 결과에 남는가")
sys.path.insert(0, str(ROOT / "hub"))
from core.opens import run_all                # noqa: E402


class FakeJob:
    def __init__(self):
        self.rows, self.logs = [], []
        self.error, self.stopping, self.finished = None, False, False

    def log(self, tag, msg=""):
        self.logs.append(f"{tag} {msg}")

    def result(self, item):
        self.rows.append(item)

    def done(self, **k):
        self.finished = True


# 2026-09-20 VI 오픈이 남긴 줄 그대로
j = FakeJob()
with run_all._Counter(j) as c:
    j.log("VI", "RuntimeError: Date picker input 을 찾지 못했습니다")
    j.log("VI", "Errors:")
    j.log("VI", "  - open 치명: Date picker input 을 찾지 못했습니다")
print(f"     집은 사유: {c.hint}")
if "Date picker" not in (c.hint or ""):
    bad.append(f"진짜 사유를 못 집었다: {c.hint!r}")
if c.hint.startswith("-") or "Errors:" in c.hint:
    bad.append(f"사유가 지저분하다: {c.hint!r}")
if "log" in vars(j):
    bad.append("job.log 을 원래대로 안 돌려놨다")

# 멀쩡히 돈 경우에는 사유가 없어야 한다 (헛소리를 붙이면 안 된다)
j2 = FakeJob()
with run_all._Counter(j2) as c2:
    j2.log("GG", "레고랜드 - Legoland Only Shared Tour | 0/120 → 0/2 (Block 해제됨)")
    j2.result({"channel": "GG", "item": "레고랜드 2", "result": "성공"})
print(f"     정상일 때 사유: {c2.hint!r} / 결과 {c2.n}건")
if c2.hint:
    bad.append(f"정상인데 사유가 붙었다: {c2.hint!r}")
if c2.n != 1:
    bad.append("결과 수를 잘못 셌다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 닫혔으면 성공, 열려 있으면 실패. 못 읽은 것은 실패다")
