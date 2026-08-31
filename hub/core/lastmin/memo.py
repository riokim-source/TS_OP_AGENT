# -*- coding: utf-8 -*-
"""
memo.py
화면 입력(수량 + 언어/픽업/옵션 선택) -> Office / OP 전용 메모 + 자동오픈 계획.

메모(사람용)와 오픈 계획(기계용)은 반드시 분리한다.
  메모 :  "Mt. Fuji Signature 8 (중국어 불가)"
  계획 :  KLOOK / JAPAN / "Mt. Fuji Signature" 8   (= (중) 변형은 열지 않음)

메모 문자열을 그대로 봇에 먹이면 안 된다. Klook 의 입력 파서는
`상품명 수량` 으로 끝나야 해서 "(중국어 불가)" 가 붙으면 형식 인식에 실패한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import constants as C
from . import calc

# ──────────────────────────────────────────────────────────────────────────────
# Klook 다국어 변형 조회 (packages.py 가 있을 때만 동작; 없으면 기본 상품만)
# ──────────────────────────────────────────────────────────────────────────────
_VARIANT_CACHE: dict | None = None


def _packages():
    global _VARIANT_CACHE
    if _VARIANT_CACHE is not None:
        return _VARIANT_CACHE
    _VARIANT_CACHE = {"ok": False, "get": None, "names": []}
    try:
        from ..paths import klook_open_dir, ensure_on_syspath
        if ensure_on_syspath(klook_open_dir()):
            import packages as pk  # type: ignore
            _VARIANT_CACHE = {"ok": True, "get": pk.get_package, "names": list(pk.PACKAGES.keys())}
    except Exception:
        pass
    return _VARIANT_CACHE


def klook_language_variants(base: str) -> dict[str, str]:
    """
    기본 상품명 -> {언어: Klook 상품명}.
    packages.py 의 '(한)/(중)/(일)' 접미사 규칙을 그대로 쓴다.
    영어(접미사 없음)는 기본 상품명 자신.
    """
    pk = _packages()
    out: dict[str, str] = {}
    if not pk["ok"]:
        return out
    get = pk["get"]
    if get(base):
        out["english"] = base
    for lang, suffix in C.LANG_SUFFIX.items():
        if not suffix:
            continue
        cand = f"{base}({suffix})"
        if get(cand):
            out[lang] = cand
    return out


def available_languages(base: str, sheet_languages: list[str]) -> list[str]:
    """
    화면 드롭다운에 띄울 언어 후보.

    ⚠️ 예약에 없는 언어도 반드시 고를 수 있어야 한다.
       '중국어 불가' 같은 지시는 **중국어 예약이 없을 때** 하는 것이다.
       예약을 기준으로 후보를 만들면, 정작 필요한 순간에 그 언어가 목록에
       없어서 제한을 걸 수 없다. 그러면 OTA 에 직접 들어가 손으로 막아야 한다.
       (2026-08-31: 예약에 없는 언어가 목록에서 빠져 지시를 못 만들었다)

    언어는 종류가 정해져 있으므로 넷을 항상 보여준다. 시트나 Klook 에서
    그 밖의 값이 나오면 뒤에 덧붙인다.

    후보를 늘려도 기본값은 '전부 선택' 이라 제한이 걸리지 않는다
    (language_restricted() 는 일부만 골랐을 때만 참). 사람이 빼야 제한이 된다.
    """
    order = ["english", "korean", "chinese", "japanese"]
    got: set[str] = set()
    for raw in sheet_languages:
        got.update(_split_langs(raw))
    got.update(klook_language_variants(base).keys())
    extra = sorted(x for x in got if x not in order)
    return list(order) + extra


_LANG_SEP = re.compile(r"[,/;&+|·]| and ")


def _split_langs(raw) -> list[str]:
    """
    한 칸에 언어가 여러 개 적힌 경우를 낱개로 나눈다.

    ⚠️ 예약 파일에 'Chinese,English' 처럼 한 칸에 둘이 들어온다. 그대로 두면
       그게 통째로 하나의 언어 후보가 되어 목록에 'chinese,english' 로 뜬다.
       보기 나쁜 것으로 끝나지 않는다 -- 후보가 하나 늘어난 만큼
       language_restricted() 의 기준도 늘어나서, 그 뜻 없는 항목 하나만 빼도
       '언어를 제한했다' 가 된다. 그러면 오픈이 통째로 달라진다.
           전부 선택        Mt. Fuji Highlight 10
           그것만 뺌        Mt. Fuji Highlight 5 + (중) 5   <- 사람은 모른다
       (2026-08-31: 감천미포 예약 1건이 'Chinese,English' 였다)

    나눈 뒤에도 모르는 값이면 그대로 남긴다. 조용히 버리면 '그 언어를
    제외한다' 는 지시를 만들 수 없다.
    """
    text = str(raw or "").strip()
    if not text or text.lower() == "nan":
        return []
    return [p.strip().casefold() for p in _LANG_SEP.split(text) if p.strip()]


# ──────────────────────────────────────────────────────────────────────────────
# 입력 모델
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class RowInput:
    area: str
    product: str
    option: str = ""                       # 옵션분리 투어의 옵션명
    option_split: bool = False
    qty: int = 0
    languages_all: list[str] = field(default_factory=list)
    languages_sel: list[str] = field(default_factory=list)
    pickups_all: list[str] = field(default_factory=list)
    pickups_sel: list[str] = field(default_factory=list)
    options_all: list[str] = field(default_factory=list)
    options_sel: list[str] = field(default_factory=list)
    # 전일 패널용: 채널별 직접 입력 수량 / KK·VI 이름만 체크
    channel_qty: dict[str, int] = field(default_factory=dict)
    channel_flag: dict[str, bool] = field(default_factory=dict)
    # 운영자가 손으로 바꾼 OTA. {"KLOOK": "GG"} = 이 투어의 Klook 몫을 GG 로.
    # 기본 배분 규칙으로는 안 되는 그날그날의 사정을 담는 자리다.
    channel_moves: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_json(cls, d: dict) -> "RowInput":
        return cls(
            area=str(d.get("area", "")),
            product=str(d.get("product", "")),
            option=str(d.get("option", "") or ""),
            option_split=bool(d.get("option_split")),
            qty=int(d.get("qty") or 0),
            languages_all=[str(x) for x in (d.get("languages_all") or [])],
            languages_sel=[str(x) for x in (d.get("languages_sel") or [])],
            pickups_all=[str(x) for x in (d.get("pickups_all") or [])],
            pickups_sel=[str(x) for x in (d.get("pickups_sel") or [])],
            options_all=[str(x) for x in (d.get("options_all") or [])],
            options_sel=[str(x) for x in (d.get("options_sel") or [])],
            channel_qty={k: int(v or 0) for k, v in (d.get("channel_qty") or {}).items()},
            channel_flag={k: bool(v) for k, v in (d.get("channel_flag") or {}).items()},
            channel_moves={str(k): str(v) for k, v in (d.get("channel_moves") or {}).items()
                           if str(k) in C.CHANNELS and str(v) in C.CHANNELS and str(k) != str(v)},
        )

    # ── 표시 이름 ─────────────────────────────────────────────────────────
    def display_name(self) -> str:
        """메모/오픈에 쓰는 상품명. 옵션분리 투어는 '상품명(옵션)'."""
        if self.option_split and self.option and self.option != C.NO_OPTION_LABEL:
            return f"{self.product}({self.option})"
        return self.product

    # ── 제한 표기 ─────────────────────────────────────────────────────────
    def pickup_restricted(self) -> bool:
        """픽업지를 일부만 고른 상태인가."""
        a, b = self.pickups_all, self.pickups_sel
        return bool(a) and bool(b) and len(b) < len(a)

    def language_restricted(self) -> bool:
        """언어를 일부만 고른 상태인가."""
        a, b = self.languages_all, self.languages_sel
        return bool(a) and bool(b) and len(b) < len(a)

    def note(self, include_language: bool = True) -> str:
        """
        제한 표기.

        include_language=False 는 Klook 처럼 언어가 이미 상품명에 들어간 경우에 쓴다.
        ('Mt. Fuji Highlight(한) 3 (중국어 불가)' 처럼 중복해서 붙는 걸 막는다)
        """
        parts: list[str] = []

        lang_all = [x.casefold() for x in self.languages_all]
        lang_sel = [x.casefold() for x in self.languages_sel]
        if include_language and lang_all and lang_sel and len(lang_sel) < len(lang_all):
            excluded = [l for l in lang_all if l not in lang_sel]
            if excluded:
                parts.append(", ".join(C.lang_label(l) for l in excluded) + " 불가")

        if self.pickups_all and self.pickups_sel and len(self.pickups_sel) < len(self.pickups_all):
            excluded = [p for p in self.pickups_all if p not in self.pickups_sel]
            kept = [p for p in self.pickups_all if p in self.pickups_sel]
            # 짧은 쪽으로 쓴다. 4곳 중 3곳을 뺐으면 "A제외" 보다 "B만" 이 읽기 쉽다.
            if excluded and len(excluded) <= len(kept):
                parts.append(", ".join(excluded) + "제외")
            elif kept:
                parts.append(", ".join(kept) + "만")

        if (not self.option_split and self.options_all and self.options_sel
                and len(self.options_sel) < len(self.options_all)):
            parts.append("옵션 " + ", ".join(self.options_sel))

        return " / ".join(parts)

    def memo_text(self) -> str:
        note = self.note()
        return f" ({note})" if note else ""

    def language_kept_label(self) -> str:
        """고른 언어를 '한국어만' 처럼 적는다. Klook 언어상품이 없어 기본 상품으로
        되돌릴 때, 그 줄에서 언어 요청이 통째로 사라지지 않게 하려고 쓴다."""
        sel = self.selected_languages()
        if not sel or not self.language_restricted():
            return ""
        return ", ".join(C.lang_label(l) for l in sel) + "만"

    def selected_languages(self) -> list[str]:
        if not self.languages_all:
            return []
        if not self.languages_sel:
            return []
        return [x.casefold() for x in self.languages_sel]


# ──────────────────────────────────────────────────────────────────────────────
# 메모 생성
# ──────────────────────────────────────────────────────────────────────────────
def _line(ch: str, parts: list[str]) -> str:
    return f"[{ch}]: " + ", ".join(parts) + "\n"


LANG_ORDER = ["english", "korean", "chinese", "japanese"]


def klook_variant_name(base: str, lang: str) -> str:
    """
    Klook 상품명 규칙: 기본(영어)은 그대로, 나머지 언어는 접미사를 붙인다.
        ('Mt. Fuji Signature', 'korean') -> 'Mt. Fuji Signature(한)'

    packages.py 에 그 상품이 등록돼 있는지와 무관하게 '이름 규칙' 대로 만든다.
    메모는 사람이 읽고 쓰는 문서라 규칙이 일정해야 하고,
    매핑이 실제로 있는지는 오픈 계획에서 따로 확인해서 알려준다.
    """
    suffix = C.LANG_SUFFIX.get(str(lang).casefold(), "")
    return f"{base}({suffix})" if suffix else base


def klook_memo_names(base: str, r: RowInput) -> list[str]:
    """
    OP 메모 / 오픈에 쓸 Klook 상품명.

    언어를 일부만 고르면 그 언어의 Klook 상품명을 쓴다.
        Mt. Fuji Signature + 한국어 8  ->  'Mt. Fuji Signature(한) 8'
    메모에 적히는 이름과 봇이 여는 상품명이 같아야 어긋나지 않는다.

    언어 제한이 없으면 기본 상품명 하나만 쓴다 (메모 모양을 바꾸지 않기 위해).
    """
    if not r.language_restricted():
        return [base]
    langs = r.selected_languages()
    if not langs:
        return [base]
    # 영어는 접미사가 없는 기본 상품 자신이다.
    #   [영어, 한국어] -> ['Mt. Fuji Highlight', 'Mt. Fuji Highlight(한)']
    # 여기서 수량을 나누는 건 klook_targets 가 한다.

    ordered = [l for l in LANG_ORDER if l in langs] + [l for l in langs if l not in LANG_ORDER]
    names: list[str] = []
    for lang in ordered:
        nm = klook_variant_name(base, lang)
        if nm not in names:
            names.append(nm)
    return names or [base]


def apply_channel_moves(dist: dict, r: RowInput) -> dict:
    """
    자동 배분 결과에 '픽업 제한' 과 '운영자 수동 변경' 을 차례로 반영한다.

    메모와 오픈 계획이 같은 함수를 써야 한다. 한쪽에만 반영하면
    메모에는 GG 라고 적혀 있는데 Klook 이 열리는 사고가 난다.
    """
    # 1) Klook 은 픽업지 단위로 막을 수 없다. 특정 픽업지를 빼야 하는 건을
    #    Klook 에 열면 뺀 픽업지로도 예약이 들어온다. 통째로 GG 로 넘긴다.
    if r.pickup_restricted():
        dist = calc.move_channel(dist, "KLOOK", "GG")
    # 2) 운영자가 화면에서 직접 바꾼 것. 나중에 지정한 것이 이긴다.
    for src, dst in r.channel_moves.items():
        if src in dist and src != dst:
            dist = calc.move_channel(dist, src, dst)
    return dist


def _klook_sellable(names: list[str]) -> list[str]:
    """
    packages.py 에 실제로 등록된 이름만 남긴다.

    없는 이름을 계획에 넣으면 봇이 '매핑 없음' 으로 건너뛰고, 그 몫의 수량이
    조용히 사라진다. 실제로 2026-08-23 에 'Mt. Fuji Highlight 8' 이
    기본 4 + (한) 4 로 갈렸는데 (한) 이 packages.py 에 없어서 4자리만 열렸다.
    여기서 미리 걸러야 남은 상품끼리 8 을 다시 나눠 갖는다.

    packages.py 를 못 읽으면 판단 근거가 없으므로 아무것도 거르지 않는다.
    """
    pk = _packages()
    if not pk["ok"]:
        return list(names)
    return [n for n in names if pk["get"](n)]


def klook_targets(base: str, r: RowInput, qty: int | None) -> list[tuple]:
    """
    Klook 에 넣을 (상품명, 수량) 목록.

    Klook 은 언어마다 상품이 갈라져 있는데(한/중) 차량은 하나다.
    두 상품에 8 씩 넣으면 Klook 은 16 자리가 열린 걸로 보고 그만큼 예약을 받는다.
    그래서 고른 언어 수만큼 수량을 나눠 넣는다.
        8, [(한),(중)]  ->  (한) 4, (중) 4
        9, [(한),(중)]  ->  (한) 5, (중) 4
    MRT/GG 에서 픽업지별로 나누는 것과 같은 규칙(calc.split_across)이다.

    상품이 하나뿐이면(대부분) 나눌 것이 없어 그대로 간다.

    반환 (목록, fallback). fallback=True 면 '고른 언어의 Klook 상품이 없어
    기본 상품으로 되돌렸다' 는 뜻이다.
    """
    names = _klook_sellable(klook_memo_names(base, r))
    fallback = False
    if not names:
        # 고른 언어의 Klook 상품이 하나도 없다 -> 기본 상품 하나에 전량.
        # 자리를 잃지 않는 대신, 그 줄이 '어떤 언어를 요청한 건' 이었는지
        # 메모에서 사라지면 안 된다. 호출한 쪽이 note 를 바꾸도록 알린다.
        names, fallback = [base], True
    if qty is None or len(names) <= 1:
        return [(n, qty) for n in names], fallback
    return list(zip(names, calc.split_across(qty, len(names)))), fallback


def _klook_note(r: RowInput, note_nolang: str, fallback: bool) -> str:
    """
    Klook 줄에 붙일 제한 표기.

    보통 언어는 상품명에 들어가 있어서 note 에서 뺀다.
    그런데 Klook 에 그 언어 상품이 없어 기본 상품으로 되돌린 경우에는
    상품명에도 없고 note 에도 없으면 '한국어만' 이라는 요청 자체가 사라진다.
    그 경우에만 언어를 다시 적는다.
    """
    if not fallback:
        return note_nolang
    lang = r.language_kept_label()
    if not lang:
        return note_nolang
    parts = [f"{lang} / Klook 언어상품 없음"]
    if note_nolang:
        parts.append(note_nolang)
    return " / ".join(parts)


def _fmt(name: str, qty: int | None, note: str) -> str:
    body = f"{name} {qty}" if qty is not None else name
    return body + (f" ({note})" if note else "")


def build_panel_memo(rows: list[RowInput], is_latest: bool, is_op: bool) -> dict[str, list[str]]:
    """
    한 투어일자 패널 -> {채널: [출력문자열...]}

    ⚠️ Office 메모는 기존 도구 출력과 100% 같아야 한다.
       '상품명 수량' 뿐이다. 언어/픽업/옵션 제한 표기도 안 붙이고,
       Klook 언어 변형으로 상품명을 쪼개지도 않는다.
       받아보는 쪽이 읽던 형식이라 한 글자도 바꾸면 안 된다.

       제한 표기와 Klook 언어별 상품명은 OP 메모에만 들어간다.
       실제 오픈을 OP 기준으로 하기 때문이다.
    """
    auto: dict[str, list[str]] = {ch: [] for ch in C.CHANNELS}
    manual: dict[str, list[str]] = {ch: [] for ch in C.CHANNELS}
    split_klook_by_language = is_op

    for r in rows:
        base = r.display_name()
        # Office 는 제한 표기 없음 (기존 출력 유지)
        note = r.note() if is_op else ""
        note_nolang = r.note(include_language=False) if is_op else ""

        if is_latest and r.qty > 0:
            dist = apply_channel_moves(calc.distribute(r.area, base, r.qty, is_op), r)
            for ch in C.CHANNELS:
                for entry in dist[ch]:
                    m = _QTY_TAIL.match(entry)
                    qty = int(m.group("qty")) if m else None
                    if ch == "KLOOK" and split_klook_by_language:
                        tg, fb = klook_targets(base, r, qty)
                        kn = _klook_note(r, note_nolang, fb)
                        for nm, q in tg:
                            # 언어는 보통 상품명에 이미 들어가 있다. '(중국어 불가)' 를 또
                            # 붙이면 같은 투어가 두 번 적힌 것처럼 보여서 읽기만 나빠진다.
                            auto[ch].append(_fmt(nm, q, kn))
                    else:
                        auto[ch].append(_fmt(base, qty, note))

        if is_latest:
            # KK / VI 는 수량 없이 '이름만' 재개 체크도 가능
            for ch in ("KK", "VI"):
                if r.channel_flag.get(ch):
                    already = any(x.split(" (")[0].strip() in (base, f"{base} ") for x in auto[ch])
                    if not already:
                        manual[ch].append(_fmt(base, None, note))

        # 채널별 직접 입력 (전일 패널의 Remaining 표 / 최신 패널 수동 보정)
        for ch in C.CHANNELS:
            q = int(r.channel_qty.get(ch) or 0)
            if q > 0:
                if ch == "KLOOK" and split_klook_by_language:
                    tg, fb = klook_targets(base, r, q)
                    kn = _klook_note(r, note_nolang, fb)
                    for nm, qq in tg:
                        manual[ch].append(_fmt(nm, qq, kn))
                else:
                    manual[ch].append(_fmt(base, q, note))

    return {ch: auto[ch] + manual[ch] for ch in C.CHANNELS}


def render_memo(panels: list[dict], is_op: bool) -> str:
    """
    panels: [{date_label, is_latest, rows:[RowInput...]}, ...]  (최신 일자가 먼저)
    반환: [Office 전용 메모] / [OP 전용 메모] 본문 텍스트
    """
    buf: list[str] = []
    for p in panels:
        buf.append(f"[투어일자 {p['date_label']}]\n")
        buf.append("Last Min 오픈:\n" if p["is_latest"] else "Last Min 10시 후 예약:\n")
        by_ch = build_panel_memo(p["rows"], p["is_latest"], is_op)
        for ch in C.CHANNELS:
            buf.append(_line(ch, by_ch[ch]))
        buf.append("\n")
    return "".join(buf)


# ──────────────────────────────────────────────────────────────────────────────
# 자동 오픈 계획 (기계용)
# ──────────────────────────────────────────────────────────────────────────────
_QTY_TAIL = re.compile(r"^(?P<name>.+?)\s+(?P<qty>\d+)\s*$")


def build_open_plan(rows: list[RowInput], is_op: bool) -> list[dict]:
    """
    최신 일자 패널의 입력 -> 채널별 오픈 작업 목록.

    반환 항목:
      {channel, area, region, product, qty, mode, note}
        mode = "qty"    : 수량 오픈 (KLOOK / GG / CP / MRT)
        mode = "resume" : 수량 없이 판매 재개 (KK / VI)
    """
    from ..routing import area_region

    plan: list[dict] = []
    for r in rows:
        if r.qty <= 0 and not any(r.channel_flag.values()) and not any(r.channel_qty.values()):
            continue
        base = r.display_name()
        note = r.note()
        region = area_region(r.area)

        if r.qty > 0:
            dist = apply_channel_moves(calc.distribute(r.area, base, r.qty, is_op), r)
        else:
            dist = {ch: [] for ch in C.CHANNELS}

        for ch in C.CHANNELS:
            entries = list(dist[ch])
            q_manual = int(r.channel_qty.get(ch) or 0)
            if q_manual > 0:
                entries.append(f"{base} {q_manual}")
            if r.channel_flag.get(ch) and ch in ("KK", "VI"):
                entries.append(base)

            for e in entries:
                m = _QTY_TAIL.match(e)
                if m:
                    name, qty, mode = m.group("name"), int(m.group("qty")), "qty"
                else:
                    name, qty, mode = e.strip(), 0, "resume"

                for target, tqty in _resolve_targets(ch, name, r, qty):
                    plan.append({
                        "channel": ch,
                        "area": r.area,
                        "region": region,
                        "product": target,
                        "qty": tqty if tqty is not None else 0,
                        "mode": mode,
                        "note": note,
                        # GG 는 옵션이 픽업지별로 갈라져 있어서 '어느 픽업을 열지' 가 필요하다.
                        # 제한이 없으면 빈 목록 = 전체.
                        "pickups_allowed": (list(r.pickups_sel)
                                            if r.pickup_restricted() else []),
                        # 화면에서 이 줄이 어느 투어인지 되짚기 위한 키
                        "row_key": f"{r.area}|{r.product}|{r.option}",
                        "moved": dict(r.channel_moves),
                    })
    return plan


def _resolve_targets(channel: str, name: str, r: RowInput,
                     qty: int | None) -> list[tuple]:
    """
    언어 선택을 실제 (상품명, 수량) 으로 해석.

    Klook 은 언어별로 상품이 갈라져 있어서(packages.py 의 '(한)/(중)/(일)'),
    "중국어 불가" 는 '(중) 상품을 열지 않는다' 로 정확히 옮길 수 있다.
    변형이 둘 이상이면 차량이 하나이므로 수량을 나눠 넣는다 (klook_targets).
    다른 OTA 는 언어 변형 매핑이 없으므로 기본 상품 1개만 대상으로 하고,
    언어 제한은 note 로만 전달한다(사람이 확인).

    ⚠️ 오픈 대상은 '메모에 적힌 이름과 수량' 과 항상 똑같아야 한다.
       예전에는 언어 제한이 없을 때 오픈만 packages.py 의 변형 전부로 퍼뜨렸는데,
       그러면 메모엔 'Mt. Fuji Highlight 7' 한 줄인데 실제로는 (중) 까지 두 상품이
       7씩 열린다. 사람이 읽은 것과 다른 일이 벌어지면 안 된다.
       packages.py 에 없는 이름이면 오픈 미리보기에서 '매핑 없음' 으로 걸러진다.
    """
    if channel != "KLOOK":
        return [(name, qty)]
    targets, _fallback = klook_targets(name, r, qty)
    return targets
