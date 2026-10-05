"""로그인한 사용자별 Confluence PAT(개인 액세스 토큰)을 암호화해 DB에 저장한다.

CONFLUENCE_API_TOKEN을 .env 하나에 넣으면 그 토큰의 주인이 누구든 같은 걸로
Confluence에 접근하게 된다 - 팀원마다 자기 권한으로 접근해야 하는 페이지가
다르면 곤란하다. 그래서 로그인(auth.py, 같은 DATABASE_URL)에 묶어서, 각자
자기 PAT을 화면에서 등록하면 이 테이블(confluence_pat_credentials)에 암호화해
저장하고, 변환 시점에 그 사용자의 토큰만 쓴다.

이 테이블은 app_users와 달리 이 앱이 직접 만들고 소유한다(없으면
CREATE TABLE IF NOT EXISTS로 만든다) - Researcher-board 쪽 스키마를
건드리지 않는다.

DATABASE_URL(로그인)과 CREDENTIAL_ENCRYPTION_KEY(암호화 키)가 둘 다 있어야
이 기능이 켜진다(is_configured() True). 하나라도 없으면 등록 화면 자체가
안 보이고, 기존처럼 전역 CONFLUENCE_API_TOKEN만 쓰는 방식으로 동작한다
(docx_export.resolve_token 참고).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from .db import get_engine

_table = None
_table_checked = False


class CredentialStorageUnavailable(RuntimeError):
    """DATABASE_URL 또는 CREDENTIAL_ENCRYPTION_KEY가 없어 PAT을 저장/조회할 수 없을 때."""


def _cipher():
    key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    try:
        return Fernet(key.encode())
    except Exception as exc:
        print(f"[confluence_credentials] CREDENTIAL_ENCRYPTION_KEY가 올바른 Fernet 키가 아닙니다: {exc}")
        return None


def is_configured() -> bool:
    return get_engine() is not None and _cipher() is not None


def _pat_table(engine):
    global _table, _table_checked
    if _table_checked:
        return _table
    _table_checked = True
    try:
        from sqlalchemy import Column, DateTime, MetaData, String, Table

        table = Table(
            "confluence_pat_credentials",
            MetaData(),
            Column("user_id", String, primary_key=True),
            Column("encrypted_token", String, nullable=False),
            Column("updated_at", DateTime, nullable=False),
        )
        table.create(engine, checkfirst=True)
        _table = table
    except Exception as exc:
        print(f"[confluence_credentials] confluence_pat_credentials 테이블 준비 실패: {exc}")
        _table = None
    return _table


def _require() -> tuple:
    engine = get_engine()
    cipher = _cipher()
    if engine is None or cipher is None:
        raise CredentialStorageUnavailable(
            "Confluence PAT을 저장할 수 없습니다 - DATABASE_URL과 CREDENTIAL_ENCRYPTION_KEY가 "
            "둘 다 설정되어 있어야 합니다. .env.example을 참고해주세요."
        )
    return engine, cipher


def get_pat(user_id: str) -> Optional[str]:
    """복호화한 PAT. 설정이 없거나 등록된 적 없으면 None (예외를 던지지 않음 -
    docx_export.resolve_token()이 전역 토큰으로 조용히 넘어갈 수 있어야 한다)."""
    if not user_id:
        return None
    engine = get_engine()
    cipher = _cipher()
    if engine is None or cipher is None:
        return None
    table = _pat_table(engine)
    if table is None:
        return None

    from sqlalchemy import select

    try:
        with engine.connect() as conn:
            row = conn.execute(
                select(table.c.encrypted_token).where(table.c.user_id == user_id)
            ).first()
    except Exception as exc:
        print(f"[confluence_credentials] PAT 조회 실패: {exc}")
        return None
    if row is None:
        return None
    try:
        return cipher.decrypt(row[0].encode()).decode()
    except Exception as exc:
        print(f"[confluence_credentials] 저장된 PAT 복호화 실패(CREDENTIAL_ENCRYPTION_KEY가 바뀌었을 수 있음): {exc}")
        return None


def status(user_id: str) -> Optional[datetime]:
    """등록 여부/화면 표시용. 토큰을 복호화하지 않고 등록 시각만 돌려준다."""
    if not user_id:
        return None
    engine = get_engine()
    if engine is None:
        return None
    table = _pat_table(engine)
    if table is None:
        return None

    from sqlalchemy import select

    try:
        with engine.connect() as conn:
            row = conn.execute(
                select(table.c.updated_at).where(table.c.user_id == user_id)
            ).first()
    except Exception as exc:
        print(f"[confluence_credentials] PAT 상태 조회 실패: {exc}")
        return None
    return row[0] if row else None


def set_pat(user_id: str, pat: str) -> None:
    engine, cipher = _require()
    table = _pat_table(engine)
    if table is None:
        raise CredentialStorageUnavailable("confluence_pat_credentials 테이블을 준비하지 못했습니다.")

    from sqlalchemy import delete

    encrypted = cipher.encrypt(pat.encode()).decode()
    with engine.begin() as conn:
        conn.execute(delete(table).where(table.c.user_id == user_id))
        conn.execute(
            table.insert().values(
                user_id=user_id, encrypted_token=encrypted, updated_at=datetime.utcnow()
            )
        )


def delete_pat(user_id: str) -> None:
    engine = get_engine()
    if engine is None:
        return
    table = _pat_table(engine)
    if table is None:
        return

    from sqlalchemy import delete

    with engine.begin() as conn:
        conn.execute(delete(table).where(table.c.user_id == user_id))
