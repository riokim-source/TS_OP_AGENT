"""봇 인터페이스 공통 타입."""

from __future__ import annotations

from typing import List, TypedDict


class Result(TypedDict):
    agency: str          # "KKDAY" / "MRT" / "KLOOK" / "GG" / "VI"
    success: int         # 마감 성공한 옵션 수
    failed: int          # 실패 개수
    skipped: int         # 이미 마감되어 스킵된 개수
    errors: List[str]    # 사람이 읽을 수 있는 에러 메시지들
