# -*- coding: utf-8 -*-
"""
Klook 저장 판정: '눌러져서 사라진 것' 을 실패로 보지 않는지 검사.

2026-08-31 아침 오픈에서 두 건이 실패로 남았다.

    [오류] 1차 Edit schedule Confirm 클릭 실패: 버튼이 보이지 않음
    KLOOK | Korea | MBC 스튜디오 5  | 새버전 실패
    KLOOK | Korea | 에버 15         | 새버전 실패

나중에 직접 오픈으로 다시 돌려 보니 **둘 다 이미 열려 있었다.**
버튼이 안 눌린 게 아니라, 눌려서 모달이 닫힌 것이었다. Ant Design 모달은
클릭이 먹는 순간 닫히는데, click_button 은 다음 재시도에서 버튼을 못 찾아
False 를 돌려준다.

가짜 화면으로 판정만 확인한다. 실제 Klook 재고는 건드리지 않는다.

    python hub/tests/test_klook_confirm_vanished.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Klook Open"))

import klook_worker as W  # noqa: E402


class FakeLocator:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    @property
    def first(self):
        return self

    def is_visible(self, timeout=0):
        return self.page.visible.get(self.sel, False)

    def wait_for(self, state="visible", timeout=0):
        if not self.page.visible.get(self.sel, False):
            raise RuntimeError("not visible")

    def click(self, **kw):
        self.page.clicks.append(self.sel)
        # 클릭이 먹으면 모달이 닫힌다
        if self.page.click_closes:
            self.page.visible[self.sel] = False


class FakePage:
    """필요한 만큼만 흉내낸다."""

    def __init__(self, visible, click_closes=True, vanish_after=None):
        self.visible = dict(visible)
        self.click_closes = click_closes
        # '있는 걸 확인한 뒤' 사라지게 하려면 첫 확인은 통과시켜야 한다.
        # is_visible 이 locator 를 한 번 쓰므로 그 다음부터 없앤다.
        self.vanish_after = vanish_after
        self.calls = 0
        self.clicks = []
        self.url = "https://merchant.klook.com/x"

    def locator(self, sel):
        self.calls += 1
        if self.vanish_after is not None and self.calls > self.vanish_after:
            for k in list(self.visible):
                if "Confirm" in k:
                    self.visible[k] = False
        return FakeLocator(self, sel)

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, *a, **k):
        raise RuntimeError("JS 경로는 쓰지 않는다")


FIRST = 'button.button.ant-btn-primary:has(span:text-is("Confirm"))'
NOTE = 'button.ant-btn-primary:not(.button):has(span:text-is("Confirm"))'
MODAL = '.ant-modal:visible button.button.ant-btn-primary:has(span:text-is("Confirm"))'

CASES = [
    # 이름,                          처음 보임, 누르는 사이 사라짐, 저장으로 봐야 하나
    ("정상 (눌리고 닫힘)",              True,  False, True),
    ("확인 직후 사라짐 (오늘 그 상황)",   True,  True,  True),
    ("모달이 아예 없음",                False, False, False),
]

bad = []
for label, present, vanish, want_ok in CASES:
    page = FakePage({FIRST: present, NOTE: False, MODAL: False},
                    vanish_after=1 if vanish else None)
    try:
        W.confirm_popup(page)
        got_ok = True
        why = ""
    except Exception as e:
        got_ok = False
        why = str(e)[:70]
    mark = "저장됨" if got_ok else f"실패 ({why})"
    print(f"  [{label}]")
    print(f"     판정: {mark}   (기대: {'저장됨' if want_ok else '실패'})")
    if got_ok != want_ok:
        bad.append(label)
        print("     !! 어긋남")

print()
if bad:
    raise SystemExit(f"!! {len(bad)}건 어긋남 — 열린 상품을 실패로 보고한다")
print("전부 통과 — 눌러져서 사라진 것을 실패로 보지 않는다")
