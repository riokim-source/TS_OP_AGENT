# -*- coding: utf-8 -*-
"""
'OP 메모' 와 '실제 오픈 대상' 이 항상 같은지 검사.

한 번 어긋난 적이 있다. 오픈 계획을 Office 규칙(is_op=False)으로 만들어서
  OP 메모 : [MRT]: Toyako Niseko 12      (20 이상이면 CP/MRT 반반)
  오픈    : MRT 5889847 = 24             (Office 는 특별지역 전량)
가 되었다. 이러면 사람이 읽은 것보다 두 배가 열린다.

    python hub/tests/test_plan_matches_op.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.lastmin.memo import RowInput, build_open_plan, render_memo  # noqa: E402

CASES = [
    # (area, product, qty)
    ("Sapporo", "Toyako Niseko", 24),      # 특별지역 20↑ : CP/MRT 반반
    ("Fukuoka", "Fukuoka Foodie", 12),     # 특별지역 10~19 : MRT 만
    ("Tokyo", "Kamakura Yokohama", 6),     # 특별지역 10 미만 : CP/MRT 없음
    ("Busan", "경주", 25),                  # 일반지역 20↑ : GG 절반
    ("Busan", "교촌경주", 13),               # 일반지역 20 미만 : KLOOK 전량
    ("Sydney", "Blue Mountain Zig Zag", 5),
]

_QTY = re.compile(r"^(?P<name>.+?)\s+(?P<qty>\d+)$")


def memo_entries(rows) -> dict:
    """OP 메모를 {채널: {상품: 수량}} 으로 파싱. 수량 없는 항목(재개)은 0."""
    text = render_memo([{"date_label": "08/24", "is_latest": True, "rows": rows}],
                       is_op=True)
    out: dict[str, dict] = {}
    for line in text.splitlines():
        m = re.match(r"^\[(\w+)\]:\s*(.*)$", line)
        if not m:
            continue
        ch, body = m.group(1), m.group(2).strip()
        table = out.setdefault(ch, {})
        for part in [x.strip() for x in body.split(",") if x.strip()]:
            part = re.sub(r"\s*\([^)]*\)\s*$", "", part).strip()   # 제한 표기 제거
            q = _QTY.match(part)
            if q:
                table[q.group("name").strip()] = int(q.group("qty"))
            else:
                table[part] = 0
    return out


def plan_entries(rows) -> dict:
    out: dict[str, dict] = {}
    for p in build_open_plan(rows, is_op=True):
        table = out.setdefault(p["channel"], {})
        table[p["product"]] = int(p.get("qty") or 0)
    return out


def main() -> int:
    rows = [RowInput(area=a, product=p, qty=q) for a, p, q in CASES]
    memo = memo_entries(rows)
    plan = plan_entries(rows)

    ok = True
    channels = sorted(set(memo) | set(plan))
    for ch in channels:
        m, p = memo.get(ch, {}), plan.get(ch, {})
        if m == p:
            print(f"  [{ch:5}] 일치 ({len(m)}개)")
            continue
        ok = False
        print(f"  [{ch:5}] 불일치")
        for name in sorted(set(m) | set(p)):
            mv, pv = m.get(name), p.get(name)
            if mv != pv:
                print(f"      {name:28} 메모={mv}  오픈={pv}")
    print()
    print("OP 메모와 오픈 대상 일치" if ok else "★ 어긋남 — 메모보다 많이/적게 열립니다")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
