# -*- coding: utf-8 -*-
"""
Viator 지정 상품 / 오픈 검사.

바뀐 것 두 가지 (2026-09-09 요청):

  1) 마감이 드롭다운의 상품을 전부(99개) 돌지 않고 지정한 14개만 돈다.
     전부 도느라 20분이 걸렸고, '안 파는 상품' 스킵이 63건씩 쌓여서
     그 안에 진짜 실패가 묻혔다.

  2) 오픈을 만들었다. Viator 는 수량이 없고 날짜 칸의 표시만 바꾼다.
        판매중  화면 선택지 ['Not operating', 'Sold out']   -> 이미 열림
        마감됨  화면 선택지 ['Not operating', 'Available']  -> 여기를 누른다
     (실제 로그에서 확인한 두 가지 상태다)

⚠️ 'Not operating' 은 예약 취소를 뜻한다. 절대 누르면 안 된다.
   이 검사에서 가장 중요한 부분이다.

⚠️ 무엇을 여는지는 OP 텍스트가 정한다. 14개를 전부 여는 게 아니라
   그날 오픈에 VI 로 표시된 투어의 상품만 연다.

    python hub/tests/test_vi_targets.py
"""
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))
sys.path.insert(0, str(ROOT / "OTA Close"))

import vi_targets  # noqa: E402
from core.opens import vi_open  # noqa: E402

VI_SRC = (ROOT / "OTA Close" / "vi.py").read_text(encoding="utf-8", errors="replace")
bad = []

# ── 1) 목록 ─────────────────────────────────────────────────────────────
print("  [1] 지정 상품 목록")
codes = vi_targets.codes()
print(f"     {len(codes)}개")
for region, cs in vi_targets.by_region().items():
    print(f"       {region:10} {len(cs)}개  {', '.join(cs)}")
if len(codes) != 14:
    bad.append(f"상품이 {len(codes)}개 (14개여야 한다)")
if len(set(codes)) != len(codes):
    bad.append("번호가 중복됐다")
for c in codes:
    if not re.fullmatch(r"48881P\d+", c):
        bad.append(f"번호 형식이 이상하다: {c}")
    if not vi_targets.tours_of(c):
        bad.append(f"{c} 에 투어 이름이 없다")
want = {"KOREA": 7, "JAPAN": 6, "AUSTRALIA": 1}
got = {r: len(cs) for r, cs in vi_targets.by_region().items()}
if got != want:
    bad.append(f"지역별 개수가 {got} (기대 {want})")

# ── 2) 마감이 지정 상품만 도는가 ────────────────────────────────────────
print()
print("  [2] 마감이 지정 상품만 도는가")
uses = VI_SRC.count("products = get_target_products(page)")
raw = VI_SRC.count("products = get_all_product_codes(page)")
print(f"     지정 상품만 쓰는 곳 {uses}곳 / 전체를 쓰는 곳 {raw}곳")
if uses < 2:
    bad.append(f"지정 상품을 쓰는 곳이 {uses}곳 (discover + 단일실행 = 2곳이어야 한다)")
if raw:
    bad.append("아직 드롭다운 전체를 도는 곳이 남아 있다")
if "화면(드롭다운)에 없습니다" not in VI_SRC:
    bad.append("목록에 있는데 화면에 없는 번호를 안 남긴다")

# ── 3) 절대 누르면 안 되는 것 ───────────────────────────────────────────
print()
print("  [3] 'Not operating' 안전 가드")
import vi  # noqa: E402
print(f"     누를 수 있는 것: {vi.ALLOWED_MARKS}")
if set(vi.ALLOWED_MARKS) != {"Sold out", "Available"}:
    bad.append(f"누를 수 있는 항목이 {vi.ALLOWED_MARKS} (Sold out / Available 뿐이어야 한다)")


class _FakePage:
    """여기까지 오면 안 된다. 오면 터뜨린다."""

    def evaluate_handle(self, *a, **k):
        raise AssertionError("가드를 지나쳐 화면까지 갔다")

    def evaluate(self, *a, **k):
        raise AssertionError("가드를 지나쳐 화면까지 갔다")


from datetime import date  # noqa: E402

for danger in ("Not operating", "not operating", "NOT OPERATING", "", "Cancel"):
    try:
        vi.find_and_click_mark(_FakePage(), date(2026, 9, 10), False, danger)
    except RuntimeError as e:
        okmsg = "안전 가드" in str(e)
        print(f"     '{danger or '(빈칸)'}' -> {'막힘' if okmsg else '!! 다른 오류: ' + str(e)[:40]}")
        if not okmsg:
            bad.append(f"'{danger}' 이 안전 가드가 아닌 다른 이유로 막혔다")
    except AssertionError as e:
        print(f"     '{danger}' -> !! {e}")
        bad.append(f"'{danger}' 이 안 막혔다 — 예약이 취소될 수 있다")
    except Exception as e:
        print(f"     '{danger}' -> !! {type(e).__name__}: {e}")
        bad.append(f"'{danger}' 처리 중 엉뚱한 오류")

# 허용된 것은 가드를 지나가야 한다 (지나가서 화면에 닿으면 AssertionError)
for good in ("Sold out", "Available"):
    try:
        vi.find_and_click_mark(_FakePage(), date(2026, 9, 10), False, good)
        bad.append(f"'{good}' 이 아무 일도 안 했다")
    except AssertionError:
        print(f"     '{good}' -> 통과 (화면까지 감)")
    except RuntimeError as e:
        print(f"     '{good}' -> !! 막힘: {e}")
        bad.append(f"'{good}' 이 막혔다 — 마감/오픈이 안 된다")

# ── 4) OP 텍스트에 있는 것만 여는가 ─────────────────────────────────────
print()
print("  [4] OP 텍스트(계획)에 있는 것만 연다")
PLAN = [
    {"channel": "VI", "product": "경주", "qty": 0, "mode": "resume"},
    {"channel": "VI", "product": "경주Express", "qty": 0, "mode": "resume"},
    {"channel": "VI", "product": "Biei Furano", "qty": 0, "mode": "resume"},
    {"channel": "VI", "product": "듣도보도못한투어", "qty": 0, "mode": "resume"},
    {"channel": "KLOOK", "product": "감천미포", "qty": 6, "mode": "qty"},
    {"channel": "MRT", "product": "Mt. Fuji Highlight", "qty": 10, "mode": "qty"},
]
r = vi_open.resolve(PLAN)
opened = sorted(i["code"] for i in r["items"])
print(f"     열 상품: {opened}")
for i in r["items"]:
    print(f"       {i['code']}  {i['label']}  <- {', '.join(i['tours'])}")
print(f"     맵핑 없음: {[u['tour'] for u in r['unmapped']]}")

if opened != ["48881P233", "48881P43"]:
    bad.append(f"열 상품이 {opened} (기대 48881P43, 48881P233)")
# 같은 번호의 두 투어는 하나로 합쳐져야 한다
p43 = [i for i in r["items"] if i["code"] == "48881P43"]
if p43 and len(p43[0]["tours"]) != 2:
    bad.append("경주 / 경주Express 가 한 줄로 안 합쳐졌다")
if [u["tour"] for u in r["unmapped"]] != ["듣도보도못한투어"]:
    bad.append("모르는 이름을 조용히 넘겼다")
# 다른 채널 줄에 끌려가면 안 된다 (감천미포는 KLOOK 줄에만 있다)
if "48881P170" in opened:
    bad.append("VI 로 표시되지 않은 투어(KLOOK 줄)를 열려고 한다")
if "48881P183" in opened:
    bad.append("VI 로 표시되지 않은 투어(MRT 줄)를 열려고 한다")

# 계획에 VI 가 없으면 아무것도 안 연다
r2 = vi_open.resolve([{"channel": "KLOOK", "product": "경주", "qty": 3, "mode": "qty"}])
print(f"     계획에 VI 가 없을 때: {len(r2['items'])}개")
if r2["items"]:
    bad.append("VI 표시가 없는데도 열려고 한다")

# ── 5) 이름 맞추기 ──────────────────────────────────────────────────────
print()
print("  [5] 이름 맞추기 (공백/대소문자/& 정도만)")
for name, want_code in (("Mt. Fuji Highlight", "48881P183"),
                        ("mt fuji highlight", "48881P183"),
                        ("Kyoto Nara", "48881P206"),
                        ("Arashiyama & Nishiki", "48881P206"),
                        ("arashiyama nishiki", "48881P206"),
                        ("감천미포 Early Bird", "48881P170"),
                        ("Blue Mountains Zig Zag", "48881P232"),
                        ("Biei", None),            # 부분 일치는 안 된다
                        ("후지산", None)):
    got_code = vi_targets.code_for_tour(name)
    mark = "" if got_code == want_code else f"  !! 기대 {want_code}"
    print(f"     {name:26} -> {got_code}{mark}")
    if got_code != want_code:
        bad.append(f"'{name}' 이 {got_code} 로 갔다 (기대 {want_code})")

# ── 6) 오픈이 실행기에 연결됐는가 ───────────────────────────────────────
print()
print("  [6] 오픈 실행기 연결")
RUN_ALL = (ROOT / "hub/core/opens/run_all.py").read_text(encoding="utf-8")
INIT = (ROOT / "hub/core/opens/__init__.py").read_text(encoding="utf-8")
for label, cond in (("run_all 이 VI 를 돌린다", 'vi_open.run(job' in RUN_ALL),
                    ("VI 가 구현됨으로 표시", '"VI"' in INIT.split("IMPLEMENTED")[1][:60]),
                    ("vi.py 에 --mode open", '"open"' in VI_SRC and "--codes" in VI_SRC),
                    ("결과 마커", "##VI_RESULT##" in VI_SRC)):
    print(f"     {label:26} {'예' if cond else '!! 아니오'}")
    if not cond:
        bad.append(f"{label} — 아니다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 지정 14개만 돌고, OP 에 있는 것만 열고, 'Not operating' 은 못 누른다")
