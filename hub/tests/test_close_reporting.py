# -*- coding: utf-8 -*-
"""
마감 봇의 '보고' 가 사실과 맞는지 검사.

2026-08-31 아침 마감에서 셋이 사실과 달랐다.

  Viator  apply_filter 결과를 안 봐서 이전 필터가 남은 화면을 그대로 읽었다
          -> 4건이 '마감 확정 실패' 로 끝났다
  KKday   워커가 나눠 도는데 '내가 처리한 게 0건' 을 미발견으로 신고했다
          -> 6개 상품이 전부 처리됐는데도 매일 '등록 해제?' 경보가 떴다
  GG      같은 옵션을 두 번 훑고 둘을 더했다 -> 106개를 212개로 보고

봇을 띄우지 않고 판단 부분만 확인한다.

    python hub/tests/test_close_reporting.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "OTA Close"))

bad = []

# ── 1) Viator: Apply 가 먹었는지 안 보고 넘어가지 않는가 ────────────────
import vi  # noqa: E402

print("  [Viator] Apply 실패를 그냥 지나치지 않는가")
src = (ROOT / "OTA Close" / "vi.py").read_text(encoding="utf-8")
if "if not apply_filter(page):" not in src:
    bad.append("vi: _process_one_product 이 apply_filter 결과를 안 본다")
    print("     !! 결과를 안 본다")
else:
    print("     결과를 보고 apply_failed 로 빠진다")


class FakePage:
    """드롭다운이 닫혔는지만 흉내낸다."""

    def __init__(self, open_count):
        self.open_count = open_count

    def locator(self, sel):
        page = self

        class L:
            def count(self_inner):
                return page.open_count
        return L()


print("  [Viator] 눌려서 닫힌 것을 실패로 보지 않는가")
for label, cnt, want in [("드롭다운 닫힘 (눌린 것)", 0, True),
                         ("드롭다운 남아있음", 1, False)]:
    got = vi._dropdown_closed(FakePage(cnt))
    print(f"     {label:24} -> {'적용됨' if got else '실패'}")
    if got != want:
        bad.append(f"vi._dropdown_closed: {label}")

# ── 2) KKday: 미발견을 productlist 기준으로 판단하는가 ──────────────────
import kkday  # noqa: E402

print()
print("  [KKday] 워커가 안 맡은 것을 '미발견' 으로 신고하지 않는가")
kkday._PRODUCTLIST_MISSING.clear()
kkday._PRODUCTLIST_MISSING.update({"18613"})     # 화면에 정말 없던 것

TARGET = {"9367", "8974", "11186", "17654", "18613", "167055"}
# 이 워커는 8974 / 17654 만 처리했다 (나머지는 다른 워커 담당)
summary = {c: {"total": 0, "packages": []} for c in TARGET}
for c in ("8974", "17654"):
    summary[c]["total"] = 5

not_mine = [c for c in TARGET
            if summary.get(c, {}).get("total", 0) == 0
            and c not in kkday._PRODUCTLIST_MISSING]
missing = sorted(kkday._PRODUCTLIST_MISSING & TARGET)
print(f"     화면에 정말 없던 것 : {missing}")
print(f"     다른 워커 담당      : {sorted(not_mine)}")
if missing != ["18613"]:
    bad.append("kkday: 미발견 목록이 틀렸다")
if "9367" in missing:
    bad.append("kkday: 남이 맡은 상품을 미발견으로 신고한다")

# ── 3) GG: 같은 옵션을 두 번 세지 않는가 ────────────────────────────────
print()
print("  [GG] 두 패스를 더해서 두 배로 보고하지 않는가")
gsrc = (ROOT / "OTA Close" / "gg.py").read_text(encoding="utf-8")
if "return n1 + n2, errors" in gsrc:
    bad.append("gg: 두 패스를 더하고 있다 (106 -> 212)")
    print("     !! n1 + n2 를 그대로 돌려준다")
else:
    n1, n2 = 106, 106
    print(f"     size15 {n1}행 / size50 {n2}행 -> 보고 {max(n1, n2)}개")
    if max(n1, n2) != 106:
        bad.append("gg: 옵션 수가 틀렸다")

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남 — 보고가 사실과 다르다")
print("전부 통과 — 마감 보고가 사실과 맞는다")
