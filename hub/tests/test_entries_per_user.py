# -*- coding: utf-8 -*-
"""
두 사람이 같은 예약 파일을 열어도 수량이 안 섞이는지 검사.

예전에는 파일명|날짜 하나로만 저장해서 서로 덮어썼다. 화면을 그릴 때마다
저장하므로 상대가 한 번만 움직여도 내 값이 지워졌고, 새로고침하면 남의
수량이 '직전에 입력한 값' 으로 되살아났다. 클라우드는 모두가 같은 서버를
쓰기 때문에 두 명째부터 바로 문제가 된다.

    python hub/tests/test_entries_per_user.py
"""
import json
import pathlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.lastmin import entries as E  # noqa: E402

E.STORE = pathlib.Path(tempfile.gettempdir()) / "t_entries_per_user.json"
E.STORE.unlink(missing_ok=True)

L = {"filename": "2122.xlsx", "dates": ["2026-08-31", "2026-08-30"]}
A = {"0::Seoul|남이섬|": {"qty": 12}}          # 김리오가 넣은 값
B = {"0::Seoul|에버|": {"qty": 30}}            # 박영희가 넣은 값

bad = 0

print("  [A 저장 -> B 저장 -> A 가 새로고침]")
E.save(L, A, "김리오")
E.save(L, B, "박영희")
got = E.load(L, "김리오")
print(f"     김리오가 되살린 값: {got}")
if got != A:
    bad += 1
    print("     !! 남의 값으로 바뀌었다")

print()
print("  [상대가 작업 중인 것을 아는가]")
for name, want in (("김리오", "박영희"), ("박영희", "김리오")):
    o = E.others(L, name)
    seen = ", ".join(f"{x['who']}({x['count']}건)" for x in o) or "없음"
    print(f"     {name} 화면: {seen}")
    if want not in seen:
        bad += 1
        print(f"     !! {want} 가 안 보인다")

print()
print("  [이름을 안 적어도 동작하는가]")
E.save(L, {"x": {"qty": 1}}, "")
if E.load(L, "") != {"x": {"qty": 1}}:
    bad += 1
    print("     !! 이름 없이 저장한 값이 안 돌아온다")
if E.load(L, "김리오") != A:
    bad += 1
    print("     !! 이름 적은 사람의 값이 흔들렸다")
print("     이름 없이 저장/되살림 정상, 다른 사람에게 영향 없음")

print()
print("  [다른 파일·날짜와 섞이지 않는가]")
L2 = {"filename": "2122.xlsx", "dates": ["2026-09-01", "2026-08-31"]}
if E.load(L2, "김리오"):
    bad += 1
    print("     !! 다른 날짜의 값이 딸려 왔다")
print("     다른 날짜는 빈 값")

print()
print("  [사람 구분 전에 저장된 것도 되살아나는가]")
# ⚠️ 이걸 못 읽으면 바꾼 날 작업 중이던 사람의 수량이 통째로 날아간다
E.STORE.write_text(json.dumps({"sets": {"2122.xlsx|2026-08-31,2026-08-30": A}},
                              ensure_ascii=False), encoding="utf-8")
old = E.load(L, "김리오")
print(f"     옛 자리에서 되살림: {old}")
if old != A:
    bad += 1
    print("     !! 옛 형식이 안 읽힌다 — 바꾼 날 수량이 날아간다")
E.save(L, B, "김리오")
keys = list(json.loads(E.STORE.read_text(encoding="utf-8"))["sets"])
if not any("김리오" in k for k in keys):
    bad += 1
    print("     !! 저장이 새 자리로 안 간다")
print(f"     저장은 새 자리로: {[k for k in keys if '김리오' in k]}")

E.STORE.unlink(missing_ok=True)
print()
if bad:
    raise SystemExit(f"!! {bad}건 어긋남 — 두 사람이 쓰면 수량이 섞인다")
print("전부 통과 — 사람마다 따로 저장되고, 상대가 보인다")
