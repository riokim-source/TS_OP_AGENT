# -*- coding: utf-8 -*-
"""
전날 패널의 채널 귀속 + [VI] 줄의 지정 목록 필터 검사.

1) TPC 예약이 [CP] 에 찍혔다 (2026-09-15)

   Office 메모 전날 패널에 '[CP]: 감천미포 1' 이 나왔다. 그날 CP 로는 감천미포를
   열지도, 받지도 않았다. 실제 예약 파일을 보니 이것이었다.

       2026-09-15  감천미포  Agency=TPC  1명  (09-15 02:30, 컷오프 이후)

   예전에 CP 가 Trip.com 인 줄 알고 'TPC' 코드를 [CP] 로 모아 두었는데
   (CHANNEL_MAP["CP"]="TPC", 보조 코드 ["TPC","CP"]), 09-10 에 TPC 를 별도
   채널로 갈라 낸 뒤에도 그 매핑이 남아 있었다.

2) [VI] 는 지정 목록(vi_targets.py)에 있는 상품만

   Viator 는 지정 상품만 여닫는다. 목록 밖 상품은 Office/OP 둘 다 뺀다.

   ⚠️ 이름이 한 글자만 달라도 조용히 빠진다. 실제 예약 파일과 대조해 보니
      목록의 이름 셋이 실제와 달랐다 (그래서 VI 오픈도 이 둘을 놓치고 있었다).
          '선셋캡슐'               -> 실제 '선셋캡슐 East'
          'Blue Mountains Zig Zag' -> 실제 'Blue Mountain Zig Zag'
          'Biei Highlights'        -> 실제 'Biei Highlight'

    python hub/tests/test_channel_and_vi_filter.py
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

import pandas as pd                                            # noqa: E402
from core.lastmin import calc, loader, constants as C          # noqa: E402

bad = []

# ── 1) Agency 코드 -> 채널 ──────────────────────────────────────────────
print("  [1] 예약 파일 Agency 코드가 어느 채널로 가는가")
df = pd.DataFrame([
    {"Agency": "TPC", "People": 1},   # 09-15 그 감천미포
    {"Agency": "TPC", "People": 3},
    {"Agency": "CP", "People": 2},
    {"Agency": "L", "People": 4},
    {"Agency": "VI", "People": 5},
])
got = loader._channel_counts(df)
print(f"     {({k: v for k, v in got.items() if v})}")
for ch, want in (("TPC", 4), ("CP", 2), ("KLOOK", 4), ("VI", 5)):
    if got.get(ch) != want:
        bad.append(f"{ch} 가 {got.get(ch)} (기대 {want})")
if got.get("CP") == 6:
    bad.append("TPC 예약이 CP 로 합쳐졌다 (09-15 사고 그대로)")

# TPC 와 CP 를 섞는 매핑이 남아 있으면 안 된다
if C.CHANNEL_MAP.get("CP") != "CP" or C.CHANNEL_MAP.get("TPC") != "TPC":
    bad.append(f"CHANNEL_MAP 이 TPC/CP 를 섞는다: {C.CHANNEL_MAP}")
for ch, codes in C.CHANNEL_ALIASES.items():
    if ch == "CP" and "TPC" in codes:
        bad.append("보조 코드가 TPC 를 CP 로 모은다")
    if ch == "TPC" and "CP" in codes:
        bad.append("보조 코드가 CP 를 TPC 로 모은다")
print(f"     CHANNEL_MAP: TPC->{C.CHANNEL_MAP.get('TPC')}  CP->{C.CHANNEL_MAP.get('CP')}"
      f"   보조 코드: {C.CHANNEL_ALIASES or '없음'}")

# ── 2) 전날 패널에서 실제로 어떻게 찍히는가 ─────────────────────────────
print()
print("  [2] 전날 패널 (10시 후 예약) — 09-15 그날을 재현")
from core.lastmin.memo import RowInput, render_memo  # noqa: E402

cutoff = datetime(2026, 9, 14, 10, 0)
res = pd.DataFrame([
    {"Agency": "TPC", "People": 1, "ResDT": pd.Timestamp("2026-09-15 02:30:53")},
    {"Agency": "L", "People": 1, "ResDT": pd.Timestamp("2026-09-14 08:55:35")},  # 컷오프 전
])
counts = loader._lastmin_counts(res, cutoff)
row = RowInput(area="Busan", product="감천미포", qty=0, channel_qty=counts)
text = render_memo([{"date_label": "09/15", "is_latest": False, "rows": [row]}],
                   is_op=False)
lines = {ln.split(":", 1)[0]: ln.split(":", 1)[1].strip()
         for ln in text.splitlines() if ln.startswith("[") and "]:" in ln}
print(f"     [TPC]: '{lines.get('[TPC]')}'   [CP]: '{lines.get('[CP]')}'")
if lines.get("[TPC]") != "감천미포 1":
    bad.append(f"[TPC] 가 '{lines.get('[TPC]')}' (기대 '감천미포 1')")
if lines.get("[CP]"):
    bad.append(f"[CP] 에 '{lines.get('[CP]')}' 이 찍혔다 — 그날 CP 예약은 없었다")

# ── 3) [VI] 지정 목록 ───────────────────────────────────────────────────
print()
print("  [3] [VI] 는 지정 목록에 있는 것만 (Office/OP 둘 다)")
CASES = [
    ("Busan", "경주", 25, True),
    ("Busan", "교촌경주", 25, False),          # 목록에 없음
    ("Sapporo", "Toyako Niseko", 24, False),  # 목록에 없음
    ("Tokyo", "Mt. Fuji Highlight", 20, True),
    ("Sydney", "Blue Mountain Zig Zag", 20, True),
]
for area, name, q, want_in in CASES:
    for op in (False, True):
        vi = calc.distribute(area, name, q, op)["VI"]
        ok = bool(vi) == want_in
        print(f"     {'OP ' if op else 'OFF'} {name:24} -> {vi or '(빠짐)'}"
              f"{'' if ok else '   !! 기대 ' + ('있음' if want_in else '빠짐')}")
        if not ok:
            bad.append(f"{'OP' if op else 'Office'} {name}: VI={vi}")

# 규칙 자체는 안 바뀌었다: 목록에 있어도 수량이 모자라면 VI 에 안 오른다
vi_low = calc.distribute("Busan", "경주", 5, False)["VI"]
print(f"     OFF 경주 q=5 (규칙상 VI 없음) -> {vi_low or '(없음)'}")
if vi_low:
    bad.append("목록에 있다고 규칙을 건너뛰고 VI 에 올렸다")

# ── 4) 실제 이름과 맞는가 ───────────────────────────────────────────────
print()
print("  [4] 목록 이름이 실제 상품명과 맞는가 (한 글자만 달라도 빠진다)")
for real, why in (("선셋캡슐 East", "예전엔 '선셋캡슐' 로 적혀 있었다"),
                  ("Blue Mountain Zig Zag", "예전엔 'Blue Mountains Zig Zag'"),
                  ("Biei Highlight", "예전엔 'Biei Highlights'"),
                  ("경주 Express", "공백 차이는 맞춘다"),
                  ("Kyoto & Nara", "'&' 차이는 맞춘다")):
    hit = calc.in_vi_list(real)
    print(f"     {real:24} {'OK' if hit else '!! 안 걸림'}   ({why})")
    if not hit:
        bad.append(f"실제 상품명 '{real}' 이 VI 목록에 안 걸린다")

# 기준 예약 파일이 있으면 번호마다 실제 이름이 하나라도 걸리는지 본다
XLSX = ROOT / "2122.xlsx"
if XLSX.exists():
    sys.path.insert(0, str(ROOT / "OTA Close"))
    import vi_targets as vt  # noqa: E402
    names = set(loader.read_reservations(open(XLSX, "rb"), XLSX.name)["Product"]
                .dropna().astype(str).str.strip())
    missing = [c for c in vt.codes()
               if not any(vt.code_for_tour(n) == c for n in names)]
    print(f"     기준 파일(2122.xlsx)에서 이름이 하나도 안 걸리는 번호: {missing or '없음'}")
    # 기준 파일은 날짜가 오래돼서 그날 없던 상품은 안 걸려도 된다. 기록만 남긴다.

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — TPC 예약은 [TPC] 로, [VI] 는 지정 목록만, 목록 이름은 실제와 맞다")
