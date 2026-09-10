# -*- coding: utf-8 -*-
"""
TPC Submit 뒤처리 검사.

2026-09-10 실제 마감에서 TPC 6건이 '안 바뀜' 으로 끝났다. 실제 화면에 붙어
확인한 사실 두 가지가 원인이었다.

  1) OK 는 이미 서버로 보낸다 — 다만 초안(draft)이다.
         OK -> submitProductDraft {draftTypes:["PriceAndStock"], type:2}
               <- Ack "Success", draftVersion 이 하나 오른다
     화면의 'Saved' 토스트도 **OK 가 낸 것**이다. Submit 이 낸 게 아니다.
     그래서 Submit 대기 코드가 그 토스트를 보고 곧바로 빠져나왔다.
     (로그에서 OK / Submit / Saved 가 전부 같은 초에 찍혔다)

  2) Submit 을 누르면 안내 팝업이 뜬다. 사람이 직접 눌러 확인했다.
         "The product info has been updated. Please update your human
          translations promptly..."          [Not now]  [Manage]
     이 창이 뜨는 것 자체가 Submit 이 먹혔다는 뜻이고, Not now 로 닫으면
     그대로 마감된다. 봇은 이 창을 못 넘기고 있었다.

⚠️ 반드시 default(비-primary) 를 누른다. primary 는 'Manage / 去维护' 라
   누르면 번역 관리 화면으로 끌려간다. 글자로 고르면 화면이 중국어로 바뀔 때 죽는다.

여기서는 브라우저 없이 가짜 화면으로 submit_page() 를 실제로 돌린다.

    python hub/tests/test_tpc_submit.py
"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "OTA Close"))

import tpc_dom as D                      # noqa: E402
from shared.cdp_page import CdpError     # noqa: E402

bad = []
OK_TOAST = "Saved"
NOTICE = ("The product info has been updated. Please update your human "
          "translations promptly to avoid potential impact on sales.")


class FakePage:
    """
    실제 화면 대신. 어떤 JS 를 물어보는지로 갈라서 답한다.

    clicked_primary 가 True 가 되면 'Manage' 를 누른 것이다 — 그러면 안 된다.
    """

    def __init__(self, notice_after_submit=True, new_toast=None,
                 notice_has_footer=True):
        self.submitted = False
        self.notice_up = False
        self.notice_after_submit = notice_after_submit
        self.new_toast = new_toast
        self.notice_has_footer = notice_has_footer
        self.dismissed = 0
        self.clicked_primary = False
        self.timeout = 60.0

    # ── 화면 상태 ────────────────────────────────────────────────────────
    def _messages(self):
        m = [OK_TOAST]                    # OK 가 낸 토스트는 계속 떠 있다
        if self.submitted and self.new_toast:
            m.append(self.new_toast)
        return m

    def _modals(self):
        return [NOTICE] if self.notice_up else []

    def js(self, expr, timeout=None):
        e = " ".join(str(expr).split())
        if "submit-audit-btn" in e and "mclick" in e:
            self.submitted = True
            self.notice_up = self.notice_after_submit
            return "ok"
        if "let n = 0" in e:                       # dismiss_notices
            if not self.notice_up:
                return 0
            if not self.notice_has_footer:
                return 0                            # 손댈 수 없는 창
            self.notice_up = False
            self.dismissed += 1
            return 1
        if "modals:" in e:                          # 대기 루프의 상태 읽기
            return {"modals": self._modals(), "messages": self._messages(),
                    "spinning": 0}
        if "isOurModal" in e:                       # 남은 창 목록
            return list(self._modals())
        if "ant-message-notice" in e:               # 누르기 전 안내 목록
            return list(self._messages())
        return None

    def wait(self, expr, timeout=30.0, poll=0.4, what=""):
        return True

    def url(self):
        return "https://vbooking.ctrip.com/product-edit?productId=1"


def run(label, page, timeout=6.0):
    logs = []
    try:
        seen = D.submit_page(page, log=lambda *a: logs.append(" ".join(map(str, a))),
                             timeout=timeout)
        return seen, None, logs
    except CdpError as e:
        return None, str(e), logs


# ── 1) 정상: Submit -> 안내창 -> Not now 로 닫힘 ─────────────────────────
print("  [1] Submit 뒤 안내창을 Not now 로 닫는가")
p = FakePage(notice_after_submit=True)
seen, err, logs = run("정상", p)
print(f"     결과: {'통과' if err is None else '!! ' + err[:80]}")
print(f"     닫은 안내창 {p.dismissed}개 / primary(Manage) 누름={p.clicked_primary}")
if err:
    bad.append(f"정상 흐름인데 실패로 끝났다: {err[:80]}")
if p.dismissed != 1:
    bad.append(f"안내창을 {p.dismissed}번 닫았다 (1번이어야 한다)")
if p.clicked_primary:
    bad.append("primary(Manage) 를 눌렀다 — 번역 관리 화면으로 끌려간다")
if seen and not any(NOTICE[:30] in m for m in seen.get("modals", [])):
    bad.append("안내창 내용을 기록에 안 남겼다")

# ── 2) OK 의 토스트를 Submit 결과로 세지 않는가 ─────────────────────────
print()
print("  [2] OK 가 낸 'Saved' 를 Submit 결과로 착각하지 않는가")
p = FakePage(notice_after_submit=False, new_toast=None)
seen, err, logs = run("아무 반응 없음", p, timeout=4.0)
print(f"     결과: {'실패로 올림 (맞음)' if err else '!! 성공으로 봤다'}")
if err:
    print(f"       사유: {err[:90]}")
else:
    bad.append("아무 반응이 없었는데 성공으로 봤다 — OK 의 토스트를 센 것이다")

# ── 3) Submit 뒤 새 안내가 뜨면 그건 결과로 센다 ────────────────────────
print()
print("  [3] Submit 이 낸 새 안내는 결과로 센다")
p = FakePage(notice_after_submit=False, new_toast="Submitted successfully")
seen, err, logs = run("새 토스트", p)
got = (seen or {}).get("messages", [])
print(f"     기록된 안내: {got}")
if err:
    bad.append(f"새 안내가 떴는데 실패로 끝났다: {err[:80]}")
if OK_TOAST in got:
    bad.append("누르기 전부터 있던 'Saved' 를 결과로 셌다")
if "Submitted successfully" not in got:
    bad.append("Submit 이 낸 새 안내를 못 셌다")

# ── 4) 못 닫는 창이면 실패로 올린다 ─────────────────────────────────────
print()
print("  [4] 닫을 수 없는 창은 실패로 올리는가")
p = FakePage(notice_after_submit=True, notice_has_footer=False)
seen, err, logs = run("푸터 없는 창", p)
print(f"     결과: {'실패로 올림 (맞음)' if err else '!! 그냥 넘어갔다'}")
if not err:
    bad.append("닫지 못한 창이 남았는데 성공으로 봤다")
elif "닫지 못한 창" not in err:
    bad.append(f"사유가 엉뚱하다: {err[:80]}")

# ── 5) 코드가 default 버튼만 누르는가 ───────────────────────────────────
print()
print("  [5] 안내창은 default(비-primary) 만 누르는가")
src = (ROOT / "OTA Close" / "tpc_dom.py").read_text(encoding="utf-8", errors="replace")
fn = src[src.index("def dismiss_notices("):]
fn = fn[:fn.index("\ndef ", 10)]
uses_cancel = "f.cancel" in fn
uses_confirm = "f.confirm" in fn
print(f"     f.cancel(=Not now) 사용: {uses_cancel} / f.confirm(=Manage) 사용: {uses_confirm}")
if not uses_cancel:
    bad.append("dismiss_notices 가 default 버튼을 안 쓴다")
if uses_confirm:
    bad.append("dismiss_notices 가 primary(Manage) 를 누른다 — 화면이 넘어간다")
# 글자로 고르지 않는가 — 설명 주석이 아니라 실제 JS 부분만 본다
js = fn[fn.index('page.js(r"""'):] if 'page.js(r"""' in fn else ""
js = js[:js.index('""")')] if '""")' in js else js
for word in ("Not now", "Notnow", "稍后", "Manage", "去维护"):
    if word in js:
        bad.append(f"dismiss_notices 가 글자 '{word}' 로 버튼을 고른다 (언어가 바뀌면 죽는다)")
print(f"     JS 안에서 버튼 글자로 찾는 곳: 없음" if not any(
    w in js for w in ("Not now", "Notnow", "稍后", "Manage", "去维护")) else "     !! 글자로 찾는다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — Submit 뒤 안내창을 Not now 로 닫고, OK 의 토스트를 결과로 세지 않는다")
