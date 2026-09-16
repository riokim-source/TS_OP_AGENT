# -*- coding: utf-8 -*-
"""
'Invalid' 패키지 하나 때문에 상품 전체가 안 닫히던 것.

2026-09-15, 09-16 이틀 연속 감천미포 마감이 실패했다. 이유:

    On/off 창의 패키지 목록에 H-日文导游 (早班) 만 안 나온다 (화면 11개 / 창 10개).
    그 패키지에는 'Invalid' 표시가 붙어 있다.

봇은 그걸 알아채고 **창을 닫고 상품 전체를 포기**했다. 그래서 닫을 수 있었던
나머지 패키지까지 열린 채로 하루를 보냈다. 못 닫는 것 하나 때문에 닫을 수 있는
것까지 안 닫는 건 손해가 더 크다.

2026-09-16 실제 화면에서 확인한 것 (감천미포 52607026):

  - 창 목록: 11개 중 10개. H 만 없다.
  - 달력 칸의 판매 스위치를 눌러도 6초 뒤 켜진 채로 돌아온다. 그때 나가는
    submitProductDraft 의 packagePriceAndStockList 가 **빈 배열**이다
    (서버는 Success 라고 답한다). 화면에 안내도 안 뜬다.
  - 정상 패키지(G-韩文导游 早班)로 같은 자리를 누르면 5~6초 뒤 제대로 꺼진다.
    → 봇의 클릭 문제가 아니라, Invalid 패키지는 화면에서도 못 닫는다.

그래서 지금은 이렇게 한다.

  1) 닫을 수 있는 것은 닫는다 (Submit 까지).
  2) 못 닫은 것은 PARTIAL 로 **실패에 남긴다.** 조용히 넘기지 않는다.
  3) 저녁 오픈은 PARTIAL 날의 기록도 읽는다 — 안 그러면 아침에 닫은 것이
     하루 더 닫힌 채로 남는다.
  4) 닫을 수 있는 것이 하나도 없으면 예전처럼 OK 를 누르지 않고 멈춘다.

    python hub/tests/test_tpc_invalid_package.py
"""
import json
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "OTA Close"))

import tpc                                  # noqa: E402
from tpc import Status                      # noqa: E402

bad = []

PKGS = [
    "A-中文导游 (常规)", "B-英文导游 (常规)", "C-韩文导游 (常规)",
    "D-日文导游 (常规)", "E-中文导游 (早班)", "F-英文导游 (早班)",
    "G-韩文导游 (早班)", "H-日文导游 (早班)",          # <- 이것만 Invalid
    "I-包车(按人数选购) 中文导游", "J-包车(按人数选购) 英文导游",
    "K-包车(按人数选购) 韩文导游",
]
INVALID = "H-日文导游 (早班)"
DATE = "2026-09-16"
TARGET = {"name": "감천미포", "product_id": "52607026", "region": "KOREA"}


class FakeScreen:
    """
    실제 화면 대신. 패키지별 판매 상태만 들고 있다.

    ⚠️ Invalid 패키지는 창 목록에 안 나오고, Submit 해도 상태가 안 바뀐다.
       실제 화면이 그랬다 (위 설명).
    """

    def __init__(self, on: list[str]):
        self.state = {p: ("on" if p in on else "off") for p in PKGS}
        self.picked: list[str] = []
        self.ok = False
        self.submitted = False
        self.cancelled = 0

    # -- 창 목록에 나오는 패키지 --------------------------------------------
    def listable(self) -> list[str]:
        return [p for p in PKGS if p != INVALID]

    def do_submit(self):
        self.submitted = True
        for p in self.picked:
            if p == INVALID:
                continue          # 눌러도 안 바뀐다
            self.state[p] = "off"


class FakeD:
    """tpc.py 가 부르는 화면 조작. 실제 판정 함수는 진짜 것을 쓴다."""

    def __init__(self, screen: FakeScreen):
        self.s = screen

    def boot(self, *a, **k): pass
    def search_by_name(self, *a, **k): pass
    def pick_row(self, *a, **k): return {"supplier": "감천미포 (KR)"}
    def open_edit_from_row(self, *a, **k): pass
    def assert_product(self, *a, **k): pass
    def goto_pricing(self, *a, **k): pass
    def select_package(self, *a, **k): pass
    def goto_month(self, *a, **k): pass
    def reload_and_wait(self, *a, **k): pass

    def packages(self, page):
        return [{"id": str(1000 + i), "name": n} for i, n in enumerate(PKGS)]

    def read_cell_settled(self, page, date_str, **k):
        # 어느 패키지인지는 select_package 가 아니라 여기서 정한다:
        # 호출 순서가 곧 PKGS 순서다 (_scan_packages 가 그렇게 돈다).
        name = PKGS[self._i]
        self._i += 1
        v = self.s.state[name]
        rows = [{"cat": "Adult", "on": v == "on"}]
        return {"text": name, "rows": rows, "empty": v == "not_set",
                "unlimited": True, "sold": None}

    _i = 0

    def cell_verdict(self, cell):
        import tpc_dom as real
        return real.cell_verdict(cell)

    def open_sale_dialog(self, *a, **k): pass

    def dialog_select_all_packages(self, page, log=lambda *_: None, **k):
        self.s.picked = list(self.s.listable())
        return list(self.s.listable())

    def dialog_select_packages(self, page, names, log=lambda *_: None, **k):
        self.s.picked = list(names)
        return list(names)

    def dialog_check_all_crowds(self, *a, **k): return ["Adult"]
    def dialog_set_operation(self, *a, **k): pass
    def dialog_set_by_date(self, *a, **k): pass
    def dialog_state(self, page): return {"dates": [DATE]}

    def dialog_ok(self, *a, **k):
        self.s.ok = True

    def submit_page(self, *a, **k):
        if not self.s.ok:
            raise AssertionError("OK 없이 Submit 했다")
        self.s.do_submit()
        return {"messages": ["Saved"]}

    def cancel_dialogs(self, *a, **k):
        self.s.cancelled += 1


class FakeC:
    """탭 관리. 아무것도 안 하지만 부르는 자리는 다 있어야 한다."""

    def open_page(self, port, url): return (object(), "tab-list")
    def pages(self, port, **k): return []

    def wait_new_page(self, port, seen, **k):
        return {"id": "tab-edit", "url": "product-edit?productId=52607026"}

    def attach(self, tgt):
        class P:
            timeout = 60.0

            def close(self): pass
        return P()

    def close_tab(self, *a, **k): pass
    def alive(self, port): return True


def run_close(on: list[str]):
    """화면 상태를 주고 마감을 한 번 돌린다."""
    screen = FakeScreen(on)
    fd = FakeD(screen)
    fd._i = 0
    old_d, old_c, old_sleep = tpc.D, tpc.C, tpc.time.sleep
    tpc.D, tpc.C, tpc.time.sleep = fd, FakeC(), lambda *_: None
    try:
        # _scan_packages 는 마감 전/후 두 번 돈다. 두 번째에 다시 처음부터.
        orig = tpc._scan_packages

        def scan(edit, date):
            fd._i = 0
            return orig(edit, date)

        tpc._scan_packages = scan
        try:
            return screen, tpc.process_product(9522, TARGET, DATE, "close", False)
        finally:
            tpc._scan_packages = orig
    finally:
        tpc.D, tpc.C, tpc.time.sleep = old_d, old_c, old_sleep


# ── 1) Invalid 하나 + 닫을 수 있는 것들 ─────────────────────────────────
print("  [1] 못 닫는 것 하나 때문에 나머지까지 포기하지 않는가")
screen, r = run_close(on=["C-韩文导游 (常规)", "G-韩文导游 (早班)", INVALID])
print(f"     상태 {r.status.value} / 닫은 것 {r.changed}")
print(f"     메모 {r.memo()[:110]}")
if not screen.submitted:
    bad.append("닫을 수 있는 패키지가 있는데 Submit 까지 안 갔다")
if r.status is not Status.PARTIAL:
    bad.append(f"상태가 {r.status.value} (PARTIAL 이어야 한다)")
if sorted(r.changed) != sorted(["C-韩文导游 (常规)", "G-韩文导游 (早班)"]):
    bad.append(f"닫은 것이 {r.changed}")
if INVALID in r.changed:
    bad.append("못 닫은 패키지를 닫았다고 적었다")
if not r.status.is_failure:
    bad.append("PARTIAL 이 실패로 안 세어진다 — 열린 자리가 조용히 남는다")
if INVALID not in r.memo() or "Invalid" not in r.memo():
    bad.append("무엇을 왜 못 닫았는지 메모에 없다")
print(f"     실제 화면 상태: {[p for p, v in screen.state.items() if v == 'on']}")

# ── 2) 못 닫는 것밖에 없으면 OK 를 누르지 않는다 ────────────────────────
print()
print("  [2] 닫을 수 있는 것이 하나도 없으면 멈춘다")
screen2, r2 = run_close(on=[INVALID])
print(f"     상태 {r2.status.value} / OK 눌렀나 {screen2.ok} / Submit 했나 {screen2.submitted}")
if r2.status is not Status.PKG_UNSELECTABLE:
    bad.append(f"상태가 {r2.status.value} (PKG_UNSELECTABLE 이어야 한다)")
if screen2.ok or screen2.submitted:
    bad.append("바꿀 게 없는데 OK/Submit 을 눌렀다")
if screen2.cancelled < 1:
    bad.append("창을 열어 놓고 안 닫았다")

# ── 3) 전부 정상이면 예전 그대로 ────────────────────────────────────────
print()
print("  [3] Invalid 가 없으면 예전과 같다")
screen3, r3 = run_close(on=["C-韩文导游 (常规)"])
print(f"     상태 {r3.status.value} / 닫은 것 {r3.changed}")
if r3.status is not Status.SUCCESS:
    bad.append(f"정상 마감인데 {r3.status.value}")

# ── 4) 저녁 오픈이 PARTIAL 날의 기록도 읽는가 ───────────────────────────
print()
print("  [4] 저녁 오픈이 '아침에 닫은 것' 을 PARTIAL 기록에서도 읽는가")
with tempfile.TemporaryDirectory() as td:
    old_root = tpc.ROOT
    tpc.ROOT = Path(td)
    try:
        d = Path(td) / "logs"
        d.mkdir()
        (d / f"tpc_close_{DATE}_120000.json").write_text(json.dumps([{
            "product_id": "52607026", "status": Status.PARTIAL.value,
            "changed": ["C-韩文导游 (常规)", "G-韩文导游 (早班)"],
        }], ensure_ascii=False), encoding="utf-8")
        mine = tpc.closed_by_us(DATE)
    finally:
        tpc.ROOT = old_root
print(f"     읽은 것: {mine}")
if mine.get("52607026") != ["C-韩文导游 (常规)", "G-韩文导游 (早班)"]:
    bad.append("PARTIAL 날의 마감 기록을 저녁에 못 읽는다 — 그 자리가 하루 더 닫힌다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 못 닫는 패키지는 실패로 남기고, 닫을 수 있는 것은 닫는다")
