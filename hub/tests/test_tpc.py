# -*- coding: utf-8 -*-
"""
TPC (Trip.com / Ctrip) 마감·수집·오픈 검사.

2026-09-09 에 새로 붙인 OTA 다. 여기서 지키려는 것 네 가지:

  1) **이중검색** — 내부명칭으로 검색하고, 그 결과에서 지정 상품번호 행을
     골라 들어간다. 이름만으로 나온 첫 행을 쓰거나 번호로 URL 직행하면 안 된다.
     ('경주' 로 검색하면 12건이 나온다. 그 중 하나만 우리 상품이다)

  2) **날짜 안전장치** — On/off 창은 기본이 'Set by Week' 이고 거기에
     'Everyday' 가 있다. 날짜 탭으로 못 넘어간 채 OK 를 누르면 그 상품의
     모든 날짜가 닫힌다. 그래서 고른 날짜가 정확히 하나인 것을 확인하기
     전에는 OK 를 누르지 않는다.

  3) **OK 는 반영이 아니다** — 화면 아래 Submit 까지 해야 서버에 들어간다.
     그리고 새로고침해서 서버가 준 값으로 검증한다.

  4) **'못 봤다' 를 '없다' 로 세지 않는다** — 스위치를 못 본 칸(no_switch)은
     스킵이 아니라 실패다. 화면이 'Not set' 이라고 말한 것만 스킵이다.
     ([[unknown-is-not-ok]] 와 같은 뿌리다)

    python hub/tests/test_tpc.py
"""
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))
sys.path.insert(0, str(ROOT / "OTA Close"))

import tpc_dom  # noqa: E402
import tpc_targets  # noqa: E402
from shared.cdp_page import CdpError  # noqa: E402

TPC_SRC = (ROOT / "OTA Close" / "tpc.py").read_text(encoding="utf-8", errors="replace")
DOM_SRC = (ROOT / "OTA Close" / "tpc_dom.py").read_text(encoding="utf-8", errors="replace")

bad: list[str] = []


# ── 1) 지정 상품 목록 ────────────────────────────────────────────────────
print("  [1] 지정 상품 목록")
names = tpc_targets.names()
print(f"     {len(names)}개  {', '.join(names)}")
if len(names) != 10:
    bad.append(f"상품이 {len(names)}개다 (10개여야 한다)")
if len(set(names)) != len(names):
    bad.append("내부명칭이 중복됐다")
ids = [t["product_id"] for t in tpc_targets.TARGETS]
if len(set(ids)) != len(ids):
    bad.append("상품번호가 중복됐다")
for t in tpc_targets.TARGETS:
    if not t["product_id"].isdigit():
        bad.append(f"상품번호가 숫자가 아니다: {t}")
    if t["region"] not in ("KOREA", "JAPAN", "AUSTRALIA"):
        bad.append(f"모르는 지역: {t}")
want_regions = {"KOREA": 3, "JAPAN": 6, "AUSTRALIA": 1}
got_regions: dict[str, int] = {}
for t in tpc_targets.TARGETS:
    got_regions[t["region"]] = got_regions.get(t["region"], 0) + 1
if got_regions != want_regions:
    bad.append(f"지역 분포가 다르다: {got_regions} != {want_regions}")

# 이름 찾기는 정확히 같을 때만. 비슷하다고 골라 주면 엉뚱한 상품을 연다.
if tpc_targets.find_by_tour("경주") is None:
    bad.append("정확한 이름 '경주' 를 못 찾는다")
if tpc_targets.find_by_tour(" 경주 ") is None:
    bad.append("앞뒤 공백만 다른 이름을 못 찾는다")
for wrong in ("경주Express", "경주 반일", "감천", ""):
    if tpc_targets.find_by_tour(wrong) is not None:
        bad.append(f"비슷한 이름을 골라 준다: {wrong!r}")


# ── 2) 내일 날짜 ─────────────────────────────────────────────────────────
print("  [2] 내일 날짜 계산 (월말/연말/윤년)")
import tpc  # noqa: E402
cases = [(datetime(2026, 9, 30, 23, 59), "2026-10-01"),
         (datetime(2026, 12, 31, 0, 1), "2027-01-01"),
         (datetime(2028, 2, 28, 12, 0), "2028-02-29")]
for now, want in cases:
    got = tpc.tomorrow(now)
    print(f"     {now:%Y-%m-%d %H:%M} -> {got}")
    if got != want:
        bad.append(f"내일 계산이 틀렸다: {now} -> {got} (기대 {want})")


# ── 3) 칸 판정 — 못 본 것과 없는 것을 가른다 ─────────────────────────────
print("  [3] 달력 칸 판정")
# ⚠️ 판정 근거는 글자가 아니라 구조다.
#    화면 언어가 바뀌면 'Not set' 은 다른 말이 된다 (2026-09-09 오후에 실제로
#    영어 -> 중국어로 바뀌었다). 그래서 '그날 팔 것이 있는지' 는
#    .date-detail 의 empty 클래스로, '판매중인지' 는 switch 의 aria-checked 로 본다.
CELLS = {
    "판매중": ({"text": "10 | Adult | 0/999",
                "rows": [{"cat": "Adult", "on": True}, {"cat": "Child", "on": True}],
                "empty": False}, "on", ["Adult", "Child"]),
    "일부만 판매중": ({"text": "10", "rows": [{"cat": "Adult", "on": True},
                                              {"cat": "Child", "on": False}],
                       "empty": False}, "on", ["Adult"]),
    "마감됨": ({"text": "10 | Adult", "rows": [{"cat": "Adult", "on": False}],
                "empty": False}, "off", []),
    # 가격/재고가 없는 날. 스위치가 꺼진 채로 붙어 있어도 '마감됨' 이 아니다.
    "그날 안 파는 패키지": ({"text": "11 | Not set",
                             "rows": [{"cat": "", "on": False}],
                             "empty": True}, "not_set", []),
    "자원 없음": ({"text": "01 | Closed", "rows": [{"cat": "", "on": None}],
                   "empty": True}, "not_set", []),
    "스위치를 못 봄": ({"text": "10 | 무언가", "rows": [{"cat": "Adult", "on": None}],
                        "empty": False}, "no_switch", []),
    "칸이 비어 보임": ({"text": "10", "rows": [], "empty": False}, "no_switch", []),
}
for label, (cell, want_v, want_on) in CELLS.items():
    v, on = tpc_dom.cell_verdict(cell)
    print(f"     {label:<20} -> {v} {on}")
    if v != want_v or on != want_on:
        bad.append(f"칸 판정이 틀렸다: {label} -> {v}/{on} (기대 {want_v}/{want_on})")

# no_switch 는 스킵이 아니라 실패여야 한다
if not tpc.Status.NO_SWITCH.is_failure:
    bad.append("NO_SWITCH 가 실패가 아니다 — 못 본 것을 괜찮다고 보고하게 된다")
if tpc.Status.NO_SWITCH.is_skip:
    bad.append("NO_SWITCH 가 스킵으로 세어진다")
if not tpc.Status.NOT_ON_SALE.is_skip:
    bad.append("NOT_ON_SALE(화면이 Not set) 은 스킵이어야 한다")
if not tpc.Status.CHROME_DOWN.is_failure:
    bad.append("CHROME_DOWN 이 실패가 아니다 — 그 지역 재고가 조용히 열린 채 남는다")
for st in ("VERIFY_FAILED", "SUBMIT_FAILED", "DATE_FAILED", "NOT_IN_SEARCH",
           "PRODUCT_MISMATCH", "NO_PACKAGES", "DIALOG_FAILED", "PKG_UNSELECTABLE"):
    if not getattr(tpc.Status, st).is_failure:
        bad.append(f"{st} 가 실패로 안 세어진다")


# ── 4) 이중검색 ──────────────────────────────────────────────────────────
print("  [4] 이중검색 — 명칭으로 찾고 번호로 확인")


class FakeList:
    """상품 목록 화면 흉내. rows_snapshot 이 부르는 js 만 답한다."""

    def __init__(self, rows):
        self.rows = rows

    def js(self, expr, timeout=None):
        return self.rows


ROWS_OK = [
    {"id": "57025021", "supplier": "Approved Supplier product ID: 경주-경주",
     "title": "韩国古都庆州..."},
    {"id": "88485075", "supplier": "Approved Supplier product ID: 경주-선셋캡슐 East",
     "title": "다른 상품"},
]
row = tpc_dom.pick_row(FakeList(ROWS_OK), "경주", "57025021", timeout=1)
if row["id"] != "57025021":
    bad.append("지정 번호 행을 못 골랐다")
print("     여러 건 중 지정 번호 행만 고름 — OK")

for label, rows, name, pid in [
    ("번호가 없다", ROWS_OK, "경주", "99999999"),
    ("결과가 없다", [], "경주", "57025021"),
]:
    try:
        tpc_dom.pick_row(FakeList(rows), name, pid, timeout=1)
        bad.append(f"{label}: 예외가 안 났다 — 못 찾은 것을 그냥 넘긴다")
    except CdpError as e:
        print(f"     {label} -> 실패로 중단 ({str(e)[:44]}...)")

# 이름이 다른 행에 같은 번호가 있으면 멈춘다
ROWS_MISMATCH = [{"id": "57025021",
                  "supplier": "Approved Supplier product ID: 부산시티-부산시티",
                  "title": "엉뚱한 상품"}]
try:
    tpc_dom.pick_row(FakeList(ROWS_MISMATCH), "경주", "57025021", timeout=1)
    bad.append("번호는 맞고 이름이 다른데 통과시켰다")
except CdpError:
    print("     번호는 맞지만 내부명칭이 다르면 중단 — OK")

# 소스에 URL 직행이 없어야 한다 (이중검색의 뜻이 사라진다)
for src, label in ((TPC_SRC, "tpc.py"), (DOM_SRC, "tpc_dom.py")):
    if "product-edit?productId=" in src:
        bad.append(f"{label} 가 상품 수정 URL 로 바로 들어간다 (이중검색이 아니다)")
if "product-name-text" not in DOM_SRC:
    bad.append("검색 결과 행의 상품명을 눌러 들어가지 않는다")


# ── 5) 날짜 안전장치 ─────────────────────────────────────────────────────
print("  [5] 날짜 안전장치 — 하나만 골랐을 때만 OK")
if 'input[type=radio][value="2"]' not in DOM_SRC:
    bad.append("'날짜 지정' 라디오(value=2)로 넘어가는 코드가 없다")
# ⚠️ 근거가 바뀌었다. 달력 칸의 'selected' 는 커서라서 아무것도 안 눌러도
#    오늘 날짜에 붙어 있다 (2026-09-10 실측). 진짜로 고른 날짜는 창 오른쪽
#    칸(.calendar-right)에 들어간다. 거기서 '정확히 하나' 를 확인해야 한다.
if "got.length === 1" not in DOM_SRC:
    bad.append("고른 날짜가 정확히 하나인지 확인하지 않는다")
if "calendar-right" not in DOM_SRC:
    bad.append("고른 날짜를 창 오른쪽 칸(.calendar-right)에서 확인하지 않는다")
if "picked != [target_date]" not in TPC_SRC:
    bad.append("OK 를 누르기 전에 고른 날짜를 다시 확인하지 않는다")
# 순서를 볼 때는 상품 하나를 처리하는 함수 안만 본다.
# 파일 전체에서 찾으면 맨 위 enum 정의가 먼저 걸려서 엉뚱한 순서가 나온다.
_i0 = TPC_SRC.find("def process_product(")
_i1 = TPC_SRC.find("def run(", _i0)
if not (0 < _i0 < _i1):
    bad.append("process_product 를 찾지 못했다 (검사가 무의미해진다)")
BODY = TPC_SRC[_i0:_i1]
i_date = BODY.find("Status.DATE_FAILED")
i_ok = BODY.find("D.dialog_ok(")
if not (0 <= i_date < i_ok):
    bad.append("날짜 확인이 OK 보다 뒤에 있다 — 확인 전에 눌러 버린다")
print("     날짜 확인 -> 그 다음 OK 순서 — OK")


# ── 6) OK 다음에 Submit, 그리고 새로고침 검증 ────────────────────────────
print("  [6] Submit 과 검증")
i_submit = BODY.find("D.submit_page(")
i_reload = BODY.find("D.reload_and_wait(")
i_success = BODY.find("r.status = Status.SUCCESS")
if not (0 < i_ok < i_submit):
    bad.append("OK 뒤에 Submit 을 하지 않는다 — 화면만 바뀌고 재고는 열린 채 남는다")
if not (0 < i_submit < i_reload < i_success):
    bad.append("Submit -> 새로고침 -> 검증 -> 성공 순서가 아니다")
if "location.reload()" not in DOM_SRC:
    bad.append("검증할 때 새로고침을 하지 않는다 (화면에 남은 값으로 판정하게 된다)")
if "submit-audit-btn" not in DOM_SRC:
    bad.append("Submit 버튼을 찾는 코드가 없다")
print("     OK -> Submit -> 새로고침 -> 서버 값으로 검증 — OK")


# ── 7) 마감은 Select All, 오픈은 아니다 ──────────────────────────────────
print("  [7] 마감 Select All / 오픈은 원래 팔던 것만")
if "dialog_select_all_packages" not in TPC_SRC:
    bad.append("마감이 Select All 을 쓰지 않는다")
close_block = BODY[BODY.find("if closing:"):BODY.find("D.dialog_check_all_crowds")]
if "dialog_select_all_packages" not in close_block:
    bad.append("Select All 이 마감 쪽에 있지 않다")
if "dialog_select_packages(edit, need" not in TPC_SRC:
    bad.append("오픈이 '원래 닫혀 있던 패키지만' 열지 않는다")
if "dialog_select_all_packages(edit" in close_block.split("else:")[-1]:
    bad.append("오픈에서도 Select All 을 쓴다 — 그날 안 파는 자리까지 열린다")
print("     마감=Select All / 오픈=닫혀 있던 것만 — OK")

# 오픈은 '지금 닫혀 있는 것 전부' 가 아니라 '아침에 우리가 닫은 것' 만이다.
#   경주 A-中文导游 [十月 ~ 三月] 는 9월에도 가격이 남은 채 닫혀 있다.
#   그걸 같이 열면 그날 운영하지 않는 자리가 팔린다.
if "def closed_by_us(" not in TPC_SRC:
    bad.append("오픈이 '우리가 닫은 것' 기록을 읽지 않는다")
if "Status.NO_CLOSE_LOG" not in BODY:
    bad.append("마감 기록이 없을 때 그냥 열어 버린다")
if "n for n in need if n in mine" not in BODY:
    bad.append("오픈 대상을 우리가 닫은 것으로 좁히지 않는다")
if not tpc.Status.NO_CLOSE_LOG.is_failure:
    bad.append("NO_CLOSE_LOG 가 실패로 안 세어진다")
print("     오픈은 아침에 우리가 닫은 패키지만 — OK")

# Select All 이 화면의 모든 패키지는 아니다.
#   2026-09-09 감천미포: 화면 11개 / 창 10개. 빠진 H-日文导游(早班) 는 이름에
#   'Invalid' 가 붙어 있었고, 하필 그날 유일하게 열려 있던 패키지였다.
#   그대로 Submit 하면 이미 닫힌 10개를 다시 닫고 '마감했다' 는 모양만 남는다.
i_cannot = BODY.find("cannot = [n for n in need if n not in picked_pkgs]")
if i_cannot < 0:
    bad.append("Select All 이 대상 패키지를 다 못 골랐는지 확인하지 않는다")
elif not (i_cannot < i_ok):
    bad.append("그 확인이 OK 보다 뒤에 있다")
if "Status.PKG_UNSELECTABLE" not in BODY:
    bad.append("창에 없는 패키지를 실패로 남기지 않는다")
print("     Select All 로도 못 고르는 패키지는 Submit 전에 실패 — OK")


# ── 7-2) 화면 글자에 기대지 않는가 ───────────────────────────────────────
print("  [7-2] 화면 언어가 바뀌어도 되는가")
# 2026-09-09 오후, 같은 계정 같은 Chrome 인데 화면이 영어 -> 중국어로 바뀌었다.
#   Search -> 查询 / Cancel -> 取 消 / OK -> 确 定 / Not now -> 稍后处理
# 그 순간 글자로 찾던 코드가 전부 죽었다. 다시 그러지 않게 막는다.
import re as _re
FORBIDDEN = ["'Search'", '"Search"', "'Cancel'", '"Cancel"', "'OK'", '"OK"',
             "'Not set'", '"Not set"', "Set by Date'", 'Set by Date"',
             "not ?now", "Select operation type"]
for token in FORBIDDEN:
    # 주석·문서에 적힌 것은 괜찮다. 코드에서 비교에 쓰는 것만 잡는다.
    for line in DOM_SRC.splitlines():
        st = line.strip()
        if st.startswith("#") or st.startswith("//"):
            continue
        if token in line and ("===" in line or "find(" in line or ".test(" in line):
            bad.append(f"화면 글자로 찾는 곳이 남아 있다: {st[:70]}")
# 달력이 어느 달인지도 제목('Sep 2026' / '2026年9月')으로 판단하면 안 된다.
# date-cell-YYYYMMDD 는 언어와 무관하다.
_gm = DOM_SRC[DOM_SRC.find("def goto_month("):DOM_SRC.find("def read_cell(")]
if "calendarTitle" in _gm and "monthsShown" not in _gm:
    bad.append("달력 이동이 아직 제목(언어를 타는 글자)에 기대고 있다")
if "monthsShown" not in _gm:
    bad.append("달력 이동이 date-cell 로 판단하지 않는다")
for anchor in ["searchButton", "footerButtons", "isOurModal",
               "date-detail", "ant-btn-primary"]:
    if anchor not in DOM_SRC:
        bad.append(f"구조 앵커가 없다: {anchor}")
print("     글자 대신 구조로 찾는다 — OK")


# ── 8) 허브에 붙었는가 ───────────────────────────────────────────────────
print("  [8] 허브 연결")
from core.close import runner  # noqa: E402
from core import opens  # noqa: E402
from core.opens import tpc_open  # noqa: E402

if "tpc" not in runner.AGENCIES:
    bad.append("마감 실행기에 tpc 가 없다")
if runner.AGENCY_CHANNEL.get("tpc") != "CP":
    bad.append("tpc -> CP 채널 연결이 없다")
if set(runner.AGENCY_REGIONS.get("tpc", [])) != {"KOREA", "JAPAN", "AUSTRALIA"}:
    bad.append(f"tpc 지역이 다르다: {runner.AGENCY_REGIONS.get('tpc')}")
# 지역 필터가 TPC 에도 걸려야 한다. 빠지면 한 지역만 골라도 전 지역이 돈다.
RUNNER_SRC = (ROOT / "hub" / "core" / "close" / "runner.py").read_text(encoding="utf-8")
if '("klook", "kkday", "gg", "tpc")' not in RUNNER_SRC:
    bad.append("마감 실행기가 TPC 에 지역 필터(TPC_REGIONS)를 안 넘긴다")
if "TPC_REGIONS" not in TPC_SRC:
    bad.append("봇이 TPC_REGIONS 를 읽지 않는다")

if "TPC" not in runner.Tally.CHANNELS:
    bad.append("집계가 TPC 줄을 못 읽는다")
if not runner._SUMMARY_RE.search("[  TPC] 성공   3 / 실패   0 / 스킵   7"):
    bad.append("TPC 요약 줄을 못 읽는다")
if not runner._RESULT_RE.search("[TPC] success=3 failed=0 skipped=7"):
    bad.append("TPC 집계 줄을 못 읽는다")

if "CP" not in opens.IMPLEMENTED:
    bad.append("오픈 지원 목록에 CP 가 없다")
if opens.CAPABILITY.get("CP") != "resume":
    bad.append(f"CP 오픈 방식이 다르다: {opens.CAPABILITY.get('CP')}")
if "CP" in opens.NOT_IMPLEMENTED_REASON:
    bad.append("CP 가 아직 '미구현' 으로 남아 있다")

RUN_ALL = (ROOT / "hub" / "core" / "opens" / "run_all.py").read_text(encoding="utf-8")
if "tpc_open.run(" not in RUN_ALL:
    bad.append("오픈 한 번에 돌리는 자리에 TPC 가 없다")

ok, detail = tpc_open.available()
print(f"     tpc_open.available() = {ok} · {detail}")
if not ok:
    bad.append(f"tpc_open 이 봇 파일을 못 찾는다: {detail}")

# OP 텍스트 -> 계획 -> 상품
plan = [{"channel": "CP", "product": "경주", "qty": 12},
        {"channel": "CP", "product": "없는투어", "qty": 3},
        {"channel": "VI", "product": "경주", "qty": 5}]
res = tpc_open.resolve(plan)
if [i["product_id"] for i in res["items"]] != ["57025021"]:
    bad.append(f"계획 해석이 틀렸다: {res['items']}")
if [u["tour"] for u in res["unmapped"]] != ["없는투어"]:
    bad.append("맵핑 없는 이름을 사유와 함께 남기지 않는다")
print("     [CP] 줄만 골라서 상품번호로 — OK")

# 실패한 봇을 성공으로 덮지 않는가
TPC_OPEN = (ROOT / "hub" / "core" / "opens" / "tpc_open.py").read_text(encoding="utf-8")
if "if proc.returncode:" not in TPC_OPEN or "job.done(error=" not in TPC_OPEN:
    bad.append("tpc_open 이 봇 실패를 결과에 안 남긴다")


# ── 9) 라우팅 ────────────────────────────────────────────────────────────
print("  [9] 라우팅 (Region x CP -> Chrome)")
from core.routing import get_routing  # noqa: E402
r = get_routing()
want_route = {"KOREA": "KR", "JAPAN": "KR", "AUSTRALIA": "AU"}
for region, prof in want_route.items():
    got = r.route(region, "CP")
    print(f"     {region:<10} -> {got}")
    if got != prof:
        bad.append(f"{region}/CP 라우팅이 {got} 다 ({prof} 여야 한다)")
    port = r.profile_port(prof)
    if tpc.REGION_PORT[region] != port:
        bad.append(f"{region} 포트가 어긋난다: 봇 {tpc.REGION_PORT[region]} / 라우팅 {port}")
print("     봇의 REGION_PORT 와 라우팅 포트가 같다 — OK")


# ── 결과 ─────────────────────────────────────────────────────────────────
print()
if bad:
    print("FAIL")
    for b in bad:
        print("  -", b)
    sys.exit(1)
print("PASS — TPC 검사 모두 통과")
