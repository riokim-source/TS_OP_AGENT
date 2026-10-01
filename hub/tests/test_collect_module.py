# -*- coding: utf-8 -*-
"""
수집 전용 파일(lastmin_collect.py)이 hub 와 **똑같은 답**을 내는지 검사.

다른 Streamlit 앱에 붙여 쓰려고 규칙을 파일 하나로 이어 붙였다. 베낀 코드가
아니라 hub/core 의 그 파일들을 그대로 붙인 것이지만, 붙이는 과정에서
  - 이름이 겹쳐 뒤의 것이 앞의 것을 덮거나 (pickups._norm vs outsourced)
  - 패키지 import 를 걷어내다 빠뜨리거나
하면 조용히 다른 수량이 나간다. 그래서 같은 예약 파일을 양쪽에 넣고
**글자 단위로** 맞춰 본다.

    python hub/tests/test_collect_module.py
"""
import importlib.util
import io
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hub"))

import pandas as pd                                   # noqa: E402

import make_collect_module as MK                      # noqa: E402
from core.lastmin import memo as M                    # noqa: E402
from core.lastmin.panels import build_panels          # noqa: E402

bad = []

# ── 1) 만들기 ────────────────────────────────────────────────────────────
print("  [1] 파일 만들기")
tmp = Path(tempfile.mkdtemp(prefix="lmcollect_")) / "lastmin_collect.py"
text = MK.build()
compile(text, str(tmp), "exec")
tmp.write_text(text, encoding="utf-8")
print(f"     {len(text.splitlines())}줄 · {len(text) / 1024:.0f} KB")

spec = importlib.util.spec_from_file_location("lastmin_collect_test", tmp)
LC = importlib.util.module_from_spec(spec)
sys.modules["lastmin_collect_test"] = LC
spec.loader.exec_module(LC)
print("     불러오기 OK")

# hub 와 같은 바깥 파일을 보게 맞춘다 (Klook 언어 변형·VI/TPC 목록)
from core import paths as P                           # noqa: E402
LC.KLOOK_OPEN_DIR = str(P.klook_open_dir() or "")
LC.OTA_CLOSE_DIR = str(P.ota_close_dir() or "")
LC.PICKUP_CATALOG = str(P.DATA_DIR / "gg_pickups.json")
print(f"     연결: Klook={bool(LC.klook_open_dir())} "
      f"OTA Close={bool(LC.ota_close_dir())}")

# ── 2) 오픈 쪽은 안 들어갔는가 ───────────────────────────────────────────
print()
print("  [2] 오픈 기능은 빠졌는가")
for name in ("build_open_plan", "_resolve_targets", "refresh"):
    gone = not hasattr(LC, name)
    print(f"     {name:20} {'빠짐' if gone else '!! 들어감'}")
    if not gone:
        bad.append(f"오픈/실행 쪽 {name} 가 들어갔다")
for name in ("collect", "rows_from_panel", "render_memo", "parse_quick",
             "distribute", "available_languages", "gg_lang_ok", "mark"):
    if not hasattr(LC, name):
        bad.append(f"수집에 필요한 {name} 가 없다")

# ── 3) 같은 예약 파일 → 같은 메모 ────────────────────────────────────────
print()
print("  [3] 같은 파일을 넣으면 같은 글이 나오는가")
ROWS = [
    # 날짜,        지역,     상품,                  OTA,  인원, 언어,        픽업
    ("2026-10-02", "Seoul", "경주", "L", 4, "English", "홍대입구역"),
    ("2026-10-02", "Seoul", "경주", "GG", 3, "Korean", "명동"),
    ("2026-10-02", "Seoul", "MBC 스튜디오", "L", 2, "English", "홍대입구역"),
    ("2026-10-02", "Tokyo", "Mt. Fuji Highlight", "L", 6, "Chinese", ""),
    ("2026-10-02", "Tokyo", "Mt. Fuji Highlight", "KK", 2, "Japanese", ""),
    ("2026-10-02", "Sapporo", "Toyako Niseko", "L", 5, "English", ""),
    ("2026-10-01", "Seoul", "경주", "L", 2, "English", "홍대입구역"),
    ("2026-10-01", "Tokyo", "Mt. Fuji Highlight", "GG", 1, "English", ""),
]
df = pd.DataFrame([{
    "Date": d, "Area": a, "Product": p, "Agency": ag, "People": n,
    "Language": lang, "Pickup": pick, "Option": None,
    "Reservation Date": f"{d} 11:00",
} for d, a, p, ag, n, lang, pick in ROWS])
buf = io.BytesIO()
df.to_excel(buf, index=False)
raw = buf.getvalue()

hub_res = build_panels(raw, "예약.xlsx")
lc_res = LC.collect(raw, "예약.xlsx")
print(f"     hub ok={hub_res.get('ok')} / 수집파일 ok={lc_res.get('ok')}")
if not (hub_res.get("ok") and lc_res.get("ok")):
    bad.append(f"읽지 못했다: {hub_res.get('error')} / {lc_res.get('error')}")
else:
    hp, lp = hub_res["panels"], lc_res["panels"]
    keys_h = [r["key"] for p in hp for g in p["groups"] for a in g["areas"] for r in a["rows"]]
    keys_l = [r["key"] for p in lp for g in p["groups"] for a in g["areas"] for r in a["rows"]]
    print(f"     줄 {len(keys_h)}개 / {len(keys_l)}개")
    if keys_h != keys_l:
        bad.append("줄 목록이 다르다")

    # 후보(언어·픽업)도 같아야 한다 — 여기가 다르면 제한 지시가 달라진다
    for ph, pl in zip(hp, lp):
        for gh, gl in zip(ph["groups"], pl["groups"]):
            for ah, al in zip(gh["areas"], gl["areas"]):
                for rh, rl in zip(ah["rows"], al["rows"]):
                    if rh.get("languages") != rl.get("languages"):
                        bad.append(f"{rh['key']} 언어 후보가 다르다: "
                                   f"{rh.get('languages')} vs {rl.get('languages')}")
                    if rh.get("pickups") != rl.get("pickups"):
                        bad.append(f"{rh['key']} 픽업 후보가 다르다")
                    if bool(rh.get("outsourced")) != bool(rl.get("outsourced")):
                        bad.append(f"{rh['key']} 아웃소싱 표시가 다르다")

    # 값 넣기: 수량 + '중국어 제외'(일본) + '홍대 제외'(서울)
    def values(panel, mod):
        out = {}
        for g in panel["groups"]:
            for a in g["areas"]:
                for r in a["rows"]:
                    langs = list(r.get("languages") or [])
                    picks = list(r.get("pickups") or [])
                    drop_lang = ["chinese"] if a["area"] == "Tokyo" else []
                    drop_pick = [x for x in picks if "홍대" in x] if a["area"] == "Seoul" else []
                    out[r["key"]] = {
                        "qty": 21 if r["product"] != "MBC 스튜디오" else 3,
                        "lang": [x for x in langs if x not in drop_lang],
                        "pick": [x for x in picks if x not in drop_pick],
                    }
        return out

    hv = values(hp[0], M)
    lv = values(lp[0], LC)
    if hv != lv:
        bad.append("넣는 값 자체가 다르다 (후보가 달랐다는 뜻)")

    hub_rows = M.rows_from_panel(hp[0], hv)
    lc_rows = LC.rows_from_panel(lp[0], lv)
    for is_op in (False, True):
        a = M.render_memo([{"date_label": hp[0]["date_label"], "is_latest": True,
                            "rows": hub_rows}], is_op=is_op)
        b = LC.render_memo([{"date_label": lp[0]["date_label"], "is_latest": True,
                             "rows": lc_rows}], is_op=is_op)
        same = a == b
        print(f"     {'OP' if is_op else 'Office'} 메모 {'같다' if same else '!! 다르다'}")
        if not same:
            bad.append(f"{'OP' if is_op else 'Office'} 메모가 다르다")
            for x, y in zip(a.splitlines(), b.splitlines()):
                if x != y:
                    print(f"       hub : {x}")
                    print(f"       수집: {y}")
                    break
        if is_op:
            print("       " + " / ".join(
                ln for ln in a.splitlines() if ln.startswith("[")))

    # 빠른 입력도 같은 답이어야 한다
    text_in = "경주 21(홍대제외), Mt. Fuji Highlight 9(중국어불가)"
    from core.lastmin import quickfill as QF           # noqa: E402
    ha = QF.parse(text_in)
    la = LC.parse_quick(text_in)
    same = [x.__dict__ for x in ha] == [x.__dict__ for x in la]
    print(f"     빠른 입력 파싱 {'같다' if same else '!! 다르다'}")
    if not same:
        bad.append("빠른 입력 파싱 결과가 다르다")

# ── 4) 규칙이 실제로 들어 있는가 ─────────────────────────────────────────
print()
print("  [4] 2026-10-01 규칙이 그 파일 안에 있는가")
CHECKS = [
    ("일본어 제외", "japanese" in LC.HIDDEN_LANGUAGES),
    ("일본 상품 GG 언어 조건", callable(getattr(LC, "gg_lang_ok", None))),
    ("수량 0 생략", callable(getattr(LC, "_is_zero", None)) and LC._is_zero(0)
     and not LC._is_zero(None)),
    ("아웃소싱 표시", LC.matches("MBC 스튜디오(드라마리허설)")),
    ("픽업 이름 정규화가 안 덮였다", LC._norm("  MBC  스튜디오 ") == "mbc 스튜디오"),
]
for label, ok in CHECKS:
    print(f"     {label:26} {'예' if ok else '!! 아니오'}")
    if not ok:
        bad.append(f"{label} — 아니다")

import shutil                                         # noqa: E402
shutil.rmtree(tmp.parent, ignore_errors=True)

print()
if bad:
    for b in bad:
        print("  !!", b)
    raise SystemExit(f"!! {len(bad)}건 어긋남")
print("전부 통과 — 수집 전용 파일이 hub 와 같은 답을 낸다")
