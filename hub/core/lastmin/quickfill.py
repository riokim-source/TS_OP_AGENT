# -*- coding: utf-8 -*-
"""
quickfill.py
지역 옆 빈칸에 적은 글을 읽어서 수량·언어·픽업 제한으로 바꾼다.

운영자가 매일 손으로 적는 글을 그대로 받는다. 예:

    감천미포 4, 감천미포 Early Bird 4, 경주 4, 경주 Express 6, 교촌경주 10
    Mt.Fuji Signature 9(중국어불가)
    Amanohashidate 22 (한, 영)
    Yufuin Brewery 10(영어한국어10 중국어0)
    Wollongong Kiama (En Ko and Ch): 17
    알남아 9(홍대제외)

⚠️ 비슷한 이름을 맞춰 주지 않는다
   '경주' 와 '교촌경주' 처럼 한쪽이 다른 쪽에 통째로 들어가는 이름이 실제로 있다.
   부분일치로 찾으면 엉뚱한 상품에 수량이 들어가고, 그대로 OTA 가 열린다.
   띄어쓰기·대소문자·'&'·'.' 정도만 눌러서 **정확히 같을 때만** 짝지운다.

⚠️ 못 알아들은 줄은 조용히 버리지 않는다
   이름을 못 찾았거나 수량이 없으면 problems 로 올려서 화면에 띄운다.
   버리면 그 상품은 0으로 남고, 그날 그만큼 안 열린다.

⚠️ 언어·픽업은 '열 것' 을 고르는 자리다
   '중국어 불가' 는 후보에서 중국어를 빼는 것이고, 아무 말이 없으면 후보 전부다.
   빼라고 한 언어·픽업지가 후보에 아예 없으면 그것도 problems 로 알린다
   (없는 줄 알고 넘어가면 정작 그 자리가 열린 채로 남는다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 언어 코드 -> 글에서 쓰는 표기. 긴 것부터 적는다 (한국어 가 한 보다 먼저 걸려야 한다).
LANG_WORDS: dict[str, list[str]] = {
    "korean":   ["한국어", "한글", "korean", "kor", "ko"],
    "english":  ["영어", "english", "eng", "en"],
    "chinese":  ["중국어", "중문", "chinese", "chn", "ch"],
    "japanese": ["일본어", "일어", "japanese", "jpn", "jp"],
}

# 한 글자 표기(한/영/중/일)는 앞뒤가 한글이 아닐 때만 언어로 본다.
# '한' 이 '한라산' 에 걸리면 안 된다.
_SHORT = {"korean": "한", "english": "영", "chinese": "중", "japanese": "일"}

_EXCLUDE_WORDS = ("불가", "제외", "없음", "안됨", "안 됨", "x", "X")


def _lang_pattern() -> re.Pattern:
    parts = []
    for code, words in LANG_WORDS.items():
        for w in words:
            if re.match(r"^[a-z]+$", w):
                parts.append(f"(?P<{code}_{w}>\\b{w}\\b)")
            else:
                parts.append(f"(?P<{code}_{_tag(w)}>{w})")
        s = _SHORT[code]
        parts.append(f"(?P<{code}_short>(?<![가-힣]){s}(?![가-힣]))")
    return re.compile("|".join(parts), re.IGNORECASE)


def _tag(w: str) -> str:
    return "w" + str(abs(hash(w)) % 10_000_000)


_LANG_RE = _lang_pattern()
_ZERO_RE = re.compile(r"(?<!\d)0(?!\d)")
_QTY_RE = re.compile(r"^(?P<name>.*?)[\s:：]*(?P<qty>\d+)\s*$")
_EXCLUDE_TERM_RE = re.compile(r"([^\s,()（）]+)\s*제외")


@dataclass
class Item:
    """빈칸의 한 덩어리 ('감천미포 4' 같은 것)."""
    raw: str
    name: str = ""
    qty: int | None = None
    langs_in: list[str] = field(default_factory=list)    # 이것만 연다
    langs_out: list[str] = field(default_factory=list)   # 이건 뺀다
    pickup_out: list[str] = field(default_factory=list)  # 이 픽업지는 뺀다
    unknown: str = ""                                    # 괄호 안에서 못 알아들은 글


def split_entries(text: str) -> list[str]:
    """
    줄바꿈과 쉼표로 나눈다. 단 **괄호 안의 쉼표는 나누지 않는다**
    ('Amanohashidate 22 (한, 영)' 이 두 개로 쪼개지면 안 된다).
    """
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in str(text or ""):
        if ch in "(（":
            depth += 1
        elif ch in ")）":
            depth = max(0, depth - 1)
        if (ch in ",\n" or ch == "、") and depth == 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def _read_options(content: str) -> tuple[list[str], list[str], list[str], str]:
    """괄호 속 글 -> (열 언어, 뺄 언어, 뺄 픽업지, 못 알아들은 글)."""
    hits: list[tuple[int, int, str]] = []
    for m in _LANG_RE.finditer(content):
        code = (m.lastgroup or "").rsplit("_", 1)[0]
        if code in LANG_WORDS:
            hits.append((m.start(), m.end(), code))

    langs_in: list[str] = []
    langs_out: list[str] = []
    for i, (_s, end, code) in enumerate(hits):
        nxt = hits[i + 1][0] if i + 1 < len(hits) else len(content)
        gap = content[end:nxt]
        # '중국어 불가' / '중국어0' 처럼 바로 뒤에 붙은 말로 판단한다.
        drop = any(w in gap for w in _EXCLUDE_WORDS) or bool(_ZERO_RE.search(gap))
        target = langs_out if drop else langs_in
        if code not in target:
            target.append(code)

    pickup_out: list[str] = []
    for m in _EXCLUDE_TERM_RE.finditer(content):
        term = m.group(1).strip()
        if not term or _is_lang_word(term):
            continue
        if term not in pickup_out:
            pickup_out.append(term)

    # 무엇으로도 안 읽힌 글자가 남았나 (사람이 새 규칙을 쓴 것일 수 있다)
    left = content
    for s, e, _c in reversed(hits):
        left = left[:s] + " " + left[e:]
    left = _EXCLUDE_TERM_RE.sub(" ", left)
    for w in _EXCLUDE_WORDS:
        left = left.replace(w, " ")
    left = re.sub(r"[\d\s,./·:：和and&+]+", " ", left, flags=re.IGNORECASE).strip()
    return langs_in, langs_out, pickup_out, left


def _is_lang_word(term: str) -> bool:
    t = term.strip().lower()
    for code, words in LANG_WORDS.items():
        if t in words or t == _SHORT[code]:
            return True
    return False


def parse(text: str) -> list[Item]:
    """빈칸의 글 전체 -> Item 목록. 수량이 없으면 qty=None 으로 남긴다."""
    items: list[Item] = []
    for raw in split_entries(text):
        body = raw
        langs_in: list[str] = []
        langs_out: list[str] = []
        pickup_out: list[str] = []
        unknown = ""
        for m in re.finditer(r"[(（]([^)）]*)[)）]", raw):
            a, b, c, d = _read_options(m.group(1))
            langs_in += [x for x in a if x not in langs_in]
            langs_out += [x for x in b if x not in langs_out]
            pickup_out += [x for x in c if x not in pickup_out]
            unknown = (unknown + " " + d).strip()
            body = body.replace(m.group(0), " ")
        m = _QTY_RE.match(body.strip())
        name = (m.group("name") if m else body).strip(" :：,·")
        qty = int(m.group("qty")) if m else None
        items.append(Item(raw=raw, name=name, qty=qty, langs_in=langs_in,
                          langs_out=langs_out, pickup_out=pickup_out, unknown=unknown))
    return items


# ──────────────────────────────────────────────────────────────────────────────
# 화면의 줄과 짝짓기
# ──────────────────────────────────────────────────────────────────────────────
def norm(s: str) -> str:
    """띄어쓰기·대소문자·괄호·'&'·'.' 만 눌러서 비교한다. 그 이상은 맞추지 않는다."""
    t = str(s or "").lower()
    t = re.sub(r"[\s()（）\[\]{}&.\-_/]+", "", t)
    return t


NO_OPTION = "(옵션없음)"


def row_names(row: dict) -> list[tuple[str, int]]:
    """
    그 줄을 부를 수 있는 이름들과 **우선순위** (작을수록 먼저).

    ⚠️ 옵션분리 투어에서 상품명만 적었을 때가 문제다. 감천미포는 옵션이
       '(옵션없음)' 과 'SIGHTSEEING' 으로 나뉘는데, '감천미포 4' 는 늘
       옵션 없는 줄을 뜻한다 (메모 기준선도 그렇게 만들어져 있다).
       그래서 옵션 없는 줄을 먼저 보고, 이름 붙은 옵션 줄은 뒤로 민다.
       이름 붙은 옵션만 둘 이상인데 상품명만 적었으면 — 그건 정말로
       알 수 없다. 고르지 않고 물어본다.
    """
    product = str(row.get("product") or "")
    option = str(row.get("option") or "")
    named = bool(option) and option != NO_OPTION
    out: list[tuple[str, int]] = [(str(row.get("display") or product), 0)]
    if named:
        out += [(f"{product} {option}", 0), (f"{product}({option})", 0), (option, 2)]
    out.append((product, 3 if named else 1))
    return [(n, p) for n, p in out if n]


def resolve(items: list[Item], rows: list[dict]) -> tuple[dict, list[str]]:
    """
    Item 목록 -> {row key: {'qty','lang','pick'}} 와 문제 목록.

    rows 는 그 지역(Area) 의 줄만 넘긴다. 다른 지역 상품을 적었으면 '없다' 고
    말해 주는 편이 낫다 — 조용히 다른 지역에 넣으면 엉뚱한 곳이 열린다.
    """
    index: dict[str, list[tuple[int, dict]]] = {}
    for r in rows:
        for n, prio in row_names(r):
            index.setdefault(norm(n), []).append((prio, r))

    assign: dict[str, dict] = {}
    problems: list[str] = []
    for it in items:
        if not it.name:
            problems.append(f"'{it.raw}' — 상품명을 못 읽었습니다")
            continue
        found = index.get(norm(it.name)) or []
        if not found:
            problems.append(f"'{it.name}' — 이 지역 목록에 없는 이름입니다")
            continue
        best = min(p for p, _r in found)
        uniq = {r["key"]: r for p, r in found if p == best}
        if len(uniq) > 1:
            opts = ", ".join(sorted(str(r.get("option") or "") for r in uniq.values()))
            problems.append(
                f"'{it.name}' — 옵션이 {len(uniq)}개라 어느 줄인지 알 수 없습니다 "
                f"({opts}). 옵션까지 적어 주세요")
            continue
        row = next(iter(uniq.values()))
        if it.qty is None:
            problems.append(f"'{it.raw}' — 수량이 없습니다")
            continue

        langs_all = list(row.get("languages") or [])
        picks_all = list(row.get("pickups") or [])

        lang = list(langs_all)
        if it.langs_out:
            missing = [c for c in it.langs_out if c not in langs_all]
            if missing:
                problems.append(
                    f"'{it.name}' — 빼라고 한 {', '.join(missing)} 가 언어 후보에 없습니다")
            lang = [c for c in langs_all if c not in it.langs_out]
        elif it.langs_in:
            missing = [c for c in it.langs_in if c not in langs_all]
            if missing:
                problems.append(
                    f"'{it.name}' — 적으신 {', '.join(missing)} 가 언어 후보에 없습니다")
            lang = [c for c in langs_all if c in it.langs_in] or list(it.langs_in)

        pick = list(picks_all)
        if it.pickup_out:
            for term in it.pickup_out:
                hit = [p for p in picks_all if norm(term) in norm(p)]
                if not hit:
                    problems.append(
                        f"'{it.name}' — 빼라고 한 '{term}' 가 픽업 후보에 없습니다")
            pick = [p for p in picks_all
                    if not any(norm(t) in norm(p) for t in it.pickup_out)]

        if it.unknown:
            problems.append(f"'{it.name}' — 괄호 속 '{it.unknown}' 은 못 알아들었습니다")

        assign[row["key"]] = {"qty": int(it.qty), "lang": lang, "pick": pick}
    return assign, problems
