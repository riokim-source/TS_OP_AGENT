# -*- coding: utf-8 -*-
"""
Klook 언어 변형 규칙을 고정한다.

Klook 은 언어마다 상품이 갈라져 있는데(한/중) 차량은 하나다.
두 상품에 같은 수량을 넣으면 Klook 은 그 합만큼 예약을 받는다.
2026-08-23 에 '나눠 넣기' 로 정했다.

packages.py 에 없는 변형은 계획에서 미리 걸러 남은 상품끼리 다시 나눈다.
안 그러면 봇이 '매핑 없음' 으로 건너뛰면서 그 몫의 자리가 조용히 사라진다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.lastmin.memo import (RowInput, build_panel_memo, build_open_plan,
                               klook_language_variants)

ALL = ["english", "korean", "chinese"]

# 변형이 전부 있는 상품 / (한) 이 없는 상품 — 실제 packages.py 기준
FULL = "Yufuin Dazaifu"          # english + korean + chinese
NO_KR = "Mt. Fuji Highlight"     # english + chinese (한국어 상품 없음)

CASES = [
    # (상품, 선택 언어, 수량, OP 에 나와야 할 KLOOK 줄)
    (FULL, ALL,                    8, [f"{FULL} 8"]),          # 제한 없음 = 평소대로
    (FULL, ["english"],            8, [f"{FULL} 8"]),
    (FULL, ["korean"],             8, [f"{FULL}(한) 8"]),
    (FULL, ["english", "korean"],  8, [f"{FULL} 4", f"{FULL}(한) 4"]),
    (FULL, ["english", "korean"],  9, [f"{FULL} 5", f"{FULL}(한) 4"]),
    (FULL, ["korean", "chinese"],  8, [f"{FULL}(한) 4", f"{FULL}(중) 4"]),
    (FULL, ["korean", "chinese"],  9, [f"{FULL}(한) 5", f"{FULL}(중) 4"]),

    # (한) 이 없으므로 나누지 않고 영어 상품에 전량. 자리를 잃지 않는다.
    (NO_KR, ["english", "korean"], 8, [f"{NO_KR} 8"]),
    (NO_KR, ["english", "chinese"], 8, [f"{NO_KR} 4", f"{NO_KR}(중) 4"]),
    # 고른 언어의 Klook 상품이 하나도 없으면 기본 상품에 전량.
    # 상품명에서 언어가 사라지므로 note 에 요청 언어를 남긴다.
    (NO_KR, ["korean"],            8, [f"{NO_KR} 8 (한국어만 / Klook 언어상품 없음)"]),
]


def run():
    bad = 0

    # 전제 확인: 이 테스트가 기대하는 매핑이 실제로 그런지
    if "korean" in klook_language_variants(NO_KR):
        print(f"!! 전제 깨짐: {NO_KR}(한) 이 packages.py 에 생겼습니다. 테스트를 갱신하세요.")
        bad += 1
    if "korean" not in klook_language_variants(FULL):
        print(f"!! 전제 깨짐: {FULL}(한) 이 packages.py 에서 사라졌습니다.")
        bad += 1

    for prod, sel, qty, want in CASES:
        r = RowInput(area="Tokyo", product=prod, qty=qty,
                     languages_all=ALL, languages_sel=sel)
        got = build_panel_memo([r], is_latest=True, is_op=True)["KLOOK"]
        ok = got == want
        bad += 0 if ok else 1
        print(f"{'OK ' if ok else '!! '}{prod:<20}{str(sel):<26}{qty} -> {got}")
        if not ok:
            print(f"    기대: {want}")

        # 메모에 적힌 것과 실제로 여는 것이 같아야 한다
        plan = [(p["product"], p["qty"]) for p in build_open_plan([r], is_op=True)
                if p["channel"] == "KLOOK"]
        # 메모 줄에서 '상품명 수량' 만 떼어낸다 (뒤의 괄호 주석은 봇에 안 들어간다)
        heads = [x.split(" (")[0] for x in got]
        from_memo = [(x.rsplit(" ", 1)[0], int(x.rsplit(" ", 1)[1])) for x in heads]
        if plan != from_memo:
            bad += 1
            print(f"!! 메모 != 오픈 계획\n    메모: {from_memo}\n    계획: {plan}")

    # 합계가 요청 수량을 넘지도, 모자라지도 않아야 한다
    for prod, sel in [(FULL, ["korean", "chinese"]), (FULL, ["english", "korean"]),
                      (FULL, ALL), (NO_KR, ["english", "korean"]),
                      (NO_KR, ["english", "chinese"]), (NO_KR, ["korean"])]:
        r = RowInput(area="Tokyo", product=prod, qty=8,
                     languages_all=ALL, languages_sel=sel)
        total = sum(p["qty"] for p in build_open_plan([r], is_op=True)
                    if p["channel"] == "KLOOK")
        ok = total == 8
        bad += 0 if ok else 1
        print(f"{'OK ' if ok else '!! '}합계 {total} (요청 8)  {prod} {sel}")

    print("\n" + ("전부 통과" if bad == 0 else f"{bad}건 불일치"))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
