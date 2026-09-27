from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd


CATEGORIES = [
    "GUIDE",
    "ITINERARY",
    "VEHICLE",
    "ATTRACTION",
    "MEETING_POINT",
    "TIME",
    "LANGUAGE",
    "TICKET",
    "FOOD",
    "WEATHER",
    "CUSTOMER_SERVICE",
    "OTHER",
]

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
DEFAULT_BATCH_SIZE = 12
MAX_REVIEW_CHARS = 5000


@dataclass
class AIAnalysisResult:
    detail: pd.DataFrame
    bad_summary: pd.DataFrame
    good_summary: pd.DataFrame
    improvement_summary: pd.DataFrame
    cached_count: int
    api_count: int
    model: str


def _cache_path() -> Path:
    base = Path(__file__).resolve().parent.parent
    cache_dir = base / "data"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "review_analysis_cache.json"


def _load_cache() -> dict:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    path = _cache_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def review_key(row: pd.Series) -> str:
    agency = str(row.get("Agency", "") or "")
    code = str(row.get("Agency Code", "") or "")
    rating = str(row.get("Rating", "") or "")
    text = str(row.get("Review Text", "") or "")
    raw = f"{agency}|{code}|{rating}|{text}".encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()


def _clean_json_text(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        s = s[start : end + 1]
    return s


def _prompt_for_batch(items: list[dict]) -> str:
    cats = ", ".join(CATEGORIES)
    payload = json.dumps(items, ensure_ascii=False)
    return f"""
You are analyzing customer reviews for a travel tour operator.
The reviews may be in Korean, English, Traditional/Simplified Chinese, Japanese, German, French, Spanish, Thai, or other languages.
Understand the meaning across languages and normalize equivalent meanings into the SAME concise Korean theme label.

Classification rules:
- Ratings 1, 2, 3 are BAD. Extract concrete ISSUE themes.
- Ratings 4, 5 are GOOD. Extract concrete POSITIVE themes.
- For rating 4, also extract explicit IMPROVEMENT themes if the guest says something could be better.
- A single review may have multiple themes.
- Do not invent a complaint or compliment that the review does not contain.
- Ignore generic filler such as "good", "bad", "nice" when there is a more specific reason.

Primary category MUST be exactly one of:
{cats}

Theme rules:
- theme must be Korean, concise, normally 2-8 words.
- Reuse the exact same theme wording for semantically equivalent comments.
- Good examples: "가이드 설명 부족", "체류시간 부족", "일정이 촉박함", "가이드 친절", "편리한 이동", "차량 승차감 불편", "미팅포인트 찾기 어려움".
- Do not include the tour name, customer name, booking number, or star rating in the theme.

For every input review, return one item with:
- id: unchanged input id
- summary_ko: one short Korean sentence summarizing the review
- themes: list of 0-4 objects, each with kind, category, theme
  - kind must be ISSUE, POSITIVE, or IMPROVEMENT
  - 1-3 star reviews should normally only use ISSUE
  - 5 star reviews should normally only use POSITIVE
  - 4 star reviews can use POSITIVE and IMPROVEMENT

Return ONLY valid JSON in exactly this shape:
{{
  "items": [
    {{
      "id": "...",
      "summary_ko": "...",
      "themes": [
        {{"kind":"ISSUE","category":"GUIDE","theme":"가이드 설명 부족"}}
      ]
    }}
  ]
}}

Input reviews:
{payload}
""".strip()


def _call_openai(api_key: str, model: str, items: list[dict]) -> dict:
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError(
            "OpenAI Python 패키지가 설치되어 있지 않습니다. "
            "00_START_HERE.cmd의 1번 설치를 다시 실행하거나 'py -m pip install openai'를 실행하세요."
        ) from exc

    client = OpenAI(api_key=api_key)
    prompt = _prompt_for_batch(items)

    last_exc = None
    for attempt in range(2):
        try:
            response = client.responses.create(
                model=model,
                input=prompt,
                store=False,
            )
            parsed = json.loads(_clean_json_text(response.output_text))
            if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
                raise ValueError("AI response does not contain items array")
            return parsed
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(1.0)
                prompt += "\n\nIMPORTANT: Your previous output was invalid. Return ONLY the JSON object, with no markdown."
                continue
            break

    raise RuntimeError(f"AI 리뷰 분석 요청 실패: {last_exc}") from last_exc


def _chunks(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _validate_theme(theme: dict, rating: float) -> dict | None:
    if not isinstance(theme, dict):
        return None
    kind = str(theme.get("kind", "")).strip().upper()
    category = str(theme.get("category", "")).strip().upper()
    label = " ".join(str(theme.get("theme", "")).strip().split())
    if kind not in {"ISSUE", "POSITIVE", "IMPROVEMENT"}:
        return None
    if category not in CATEGORIES:
        category = "OTHER"
    if not label:
        return None

    # Guardrails: do not let a malformed answer put positive themes on BAD reviews.
    if rating <= 3 and kind != "ISSUE":
        return None
    if rating >= 5 and kind != "POSITIVE":
        return None
    if rating == 4 and kind not in {"POSITIVE", "IMPROVEMENT"}:
        return None

    return {"kind": kind, "category": category, "theme": label}


def _summarize(detail: pd.DataFrame, kind: str, denominator: int) -> pd.DataFrame:
    cols = ["Category", "Theme", "Reviews", "Rate"]
    if detail.empty:
        return pd.DataFrame(columns=cols)
    d = detail[detail["Kind"] == kind].copy()
    if d.empty:
        return pd.DataFrame(columns=cols)
    out = (
        d.groupby(["Category", "Theme"], dropna=False)["Review Key"]
        .nunique()
        .reset_index(name="Reviews")
    )
    out["Rate"] = out["Reviews"] / max(int(denominator), 1)
    out = out.sort_values(["Reviews", "Category", "Theme"], ascending=[False, True, True]).reset_index(drop=True)
    return out[cols]


def analyze_reviews(
    reviews_df: pd.DataFrame,
    api_key: str,
    model: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress: Callable[[str], None] | None = None,
) -> AIAnalysisResult:
    """Analyze review text for one tour and return per-review themes plus summaries."""
    if not api_key or not api_key.strip():
        raise RuntimeError("OpenAI API Key가 설정되지 않았습니다.")

    if reviews_df is None or reviews_df.empty:
        empty = pd.DataFrame()
        return AIAnalysisResult(empty, empty, empty, empty, 0, 0, model or DEFAULT_MODEL)

    model = model or DEFAULT_MODEL
    progress = progress or (lambda _m: None)

    df = reviews_df.copy()
    df = df[df["Review Found"].eq(True)].copy()
    df["Rating"] = pd.to_numeric(df["Rating"], errors="coerce")
    df = df[df["Rating"].between(1, 5, inclusive="both")].copy()
    df["Review Text"] = df["Review Text"].fillna("").astype(str)
    df = df[df["Review Text"].str.strip().ne("")].copy()
    if df.empty:
        empty = pd.DataFrame()
        return AIAnalysisResult(empty, empty, empty, empty, 0, 0, model)

    cache = _load_cache()
    pending: list[dict] = []
    row_meta: dict[str, dict] = {}
    analyzed_by_key: dict[str, dict] = {}
    cached_count = 0

    for _, row in df.iterrows():
        key = review_key(row)
        rating = float(row["Rating"])
        row_meta[key] = {
            "row": row,
            "rating": rating,
        }
        cached = cache.get(key)
        if isinstance(cached, dict) and isinstance(cached.get("themes"), list):
            analyzed_by_key[key] = cached
            cached_count += 1
            continue
        pending.append(
            {
                "id": key,
                "rating": int(rating),
                "text": str(row["Review Text"])[:MAX_REVIEW_CHARS],
            }
        )

    progress(f"AI 분석 대상 {len(df):,}개 리뷰 · 캐시 {cached_count:,}개")

    api_count = 0
    batches = list(_chunks(pending, max(int(batch_size), 1)))
    for bi, batch in enumerate(batches, start=1):
        progress(f"AI 분석 {bi}/{len(batches)} · {len(batch)} reviews")
        parsed = _call_openai(api_key, model, batch)
        got_ids = set()
        for item in parsed.get("items", []):
            if not isinstance(item, dict):
                continue
            key = str(item.get("id", ""))
            if key not in row_meta:
                continue
            rating = row_meta[key]["rating"]
            themes = []
            for th in item.get("themes", []):
                clean = _validate_theme(th, rating)
                if clean:
                    themes.append(clean)
            record = {
                "summary_ko": str(item.get("summary_ko", "") or "").strip(),
                "themes": themes,
                "model": model,
            }
            analyzed_by_key[key] = record
            cache[key] = record
            got_ids.add(key)
            api_count += 1

        # Cache after each batch so a long run can resume safely.
        _save_cache(cache)
        missing = [x["id"] for x in batch if x["id"] not in got_ids]
        if missing:
            progress(f"주의: {len(missing)}개 리뷰는 AI 응답에서 누락됨")

    detail_rows: list[dict] = []
    for key, meta in row_meta.items():
        analysis = analyzed_by_key.get(key)
        if not analysis:
            continue
        row = meta["row"]
        base = {
            "Review Key": key,
            "Agency": str(row.get("Agency", "") or ""),
            "Agency Code": str(row.get("Agency Code", "") or ""),
            "Tour Date": pd.Timestamp(row.get("Date")).strftime("%Y-%m-%d") if pd.notna(row.get("Date")) else "",
            "Review Date": str(row.get("Review Date", "") or ""),
            "Rating": int(meta["rating"]),
            "Review Group": str(row.get("Review Group", "") or ""),
            "Main Guide": str(row.get("Main Guide", "") or ""),
            "Review Text": str(row.get("Review Text", "") or ""),
            "Summary KO": str(analysis.get("summary_ko", "") or ""),
        }
        themes = analysis.get("themes", [])
        if not themes:
            # Keep the analyzed review visible even if no concrete theme was found.
            detail_rows.append({**base, "Kind": "", "Category": "", "Theme": ""})
            continue
        for th in themes:
            detail_rows.append(
                {
                    **base,
                    "Kind": th["kind"],
                    "Category": th["category"],
                    "Theme": th["theme"],
                }
            )

    detail = pd.DataFrame(detail_rows)
    bad_n = int((df["Rating"] <= 3).sum())
    good_n = int((df["Rating"] >= 4).sum())
    four_n = int((df["Rating"] == 4).sum())

    bad_summary = _summarize(detail, "ISSUE", bad_n)
    good_summary = _summarize(detail, "POSITIVE", good_n)
    improvement_summary = _summarize(detail, "IMPROVEMENT", four_n)

    progress("AI 리뷰 분석 완료")
    return AIAnalysisResult(
        detail=detail,
        bad_summary=bad_summary,
        good_summary=good_summary,
        improvement_summary=improvement_summary,
        cached_count=cached_count,
        api_count=api_count,
        model=model,
    )
