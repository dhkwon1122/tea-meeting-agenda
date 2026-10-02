"""Researcher-board와 같은 app_users 테이블을 읽기 전용으로 써서 로그인한다.

이 앱은 계정을 만들거나 바꾸지 않는다 - 계정 생성/삭제/역할·비밀번호
관리는 전부 dhkwon1122/Researcher-board 쪽 관리자 화면에서만 한다
(그 저장소의 services/auth.py, services/user_store.py가 app_users
테이블을 정의/관리한다). 여기서는 같은 DATABASE_URL로 그 테이블을
열어 user_id/password_hash만 확인한다 - 스키마를 직접 정의하지 않고
autoload로 반사(reflect)해서, 저쪽에서 평가/인센티브 권한 같은 칼럼을
추가·변경해도 이 앱이 깨지지 않게 한다.

DATABASE_URL이 없으면 로그인 기능 자체가 꺼지고(is_configured() False),
기존처럼 로그인 없이 그대로 쓸 수 있다.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Optional

import flask
from werkzeug.security import check_password_hash

from .db import get_engine

SESSION_LIFETIME_HOURS = int(os.environ.get("SESSION_LIFETIME_HOURS", "8"))

_table = None
_table_checked = False


class PasswordChangeRequired(Exception):
    """계정이 임시 비밀번호(must_change_password) 상태라 로그인을 거부할 때.

    이 앱에는 비밀번호 변경 화면이 없으므로(Researcher-board 쪽 전용
    기능), 먼저 그쪽에서 비밀번호를 바꾸고 오라고 안내해야 한다.
    """


def _users_table():
    global _table, _table_checked
    if _table_checked:
        return _table
    _table_checked = True

    engine = get_engine()
    if engine is None:
        return None
    try:
        from sqlalchemy import MetaData, Table

        _table = Table("app_users", MetaData(), autoload_with=engine)
    except Exception as exc:
        print(f"[auth] app_users 테이블을 읽지 못했습니다: {exc}")
        _table = None
    return _table


def is_configured() -> bool:
    return get_engine() is not None


def authenticate(user_id: str, password: str) -> Optional[dict]:
    """성공하면 {"user_id", "display_name"}, 실패/미설정이면 None.

    계정이 임시 비밀번호 상태(must_change_password)면 PasswordChangeRequired를
    던진다 - 비밀번호가 틀린 것과는 구분해서 안내해야 하기 때문이다.
    """
    if not user_id or not password:
        return None

    table = _users_table()
    engine = get_engine()
    if table is None or engine is None:
        return None

    from sqlalchemy import select

    try:
        with engine.connect() as conn:
            row = conn.execute(select(table).where(table.c.user_id == user_id)).mappings().first()
    except Exception as exc:
        print(f"[auth] 로그인 조회 실패: {exc}")
        return None

    if row is None:
        return None
    if not check_password_hash(row["password_hash"], password):
        return None
    if row.get("must_change_password"):
        raise PasswordChangeRequired()

    return {
        "user_id": row["user_id"],
        "display_name": row.get("display_name") or row["user_id"],
    }


def diagnose_login(user_id: str, password: str) -> list[str]:
    """authenticate()와 같은 경로를 단계별로 밟으며, 어디서 막히는지 사람이
    읽을 수 있는 메시지 목록으로 돌려준다.

    로그인 화면은 보안상 실패 사유를 전부 "아이디 또는 비밀번호가 올바르지
    않습니다"로 뭉뚱그리므로(DB 연결 실패인지, 계정이 없는지, 비밀번호가
    틀린 것인지 화면만으로는 구분이 안 된다), 운영자가 직접 원인을 추적할
    때는 이 함수를 쓴다 - `python -m confluence_agenda.web.auth_check` 참고.
    """
    lines: list[str] = []

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        lines.append("✗ DATABASE_URL 환경변수가 비어있습니다.")
        return lines
    lines.append(f"✓ DATABASE_URL 설정됨 (끝부분: ...{url[-24:]})")

    engine = get_engine()
    if engine is None:
        lines.append(
            "✗ SQLAlchemy Engine 생성 실패 "
            "(URL 형식이 잘못됐거나 sqlalchemy/psycopg2가 설치되지 않았습니다)."
        )
        return lines
    lines.append("✓ Engine 생성 성공")

    from sqlalchemy import select, text

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        lines.append(f"✗ DB 연결 실패: {exc}")
        lines.append(
            "  → 이 앱을 Docker 컨테이너 안에서 실행 중이라면, DATABASE_URL 호스트가 "
            "'localhost'나 '127.0.0.1'이면 안 됩니다 - 컨테이너 안에서는 그게 그 "
            "컨테이너 자기 자신을 가리켜서 항상 이 오류가 납니다. 'host.docker.internal'로 "
            "바꾸거나(워크스테이션에 떠 있는 Postgres), Researcher-board와 같은 Docker "
            "네트워크에 묶고 서비스명(예: db)을 쓰세요. .env.example의 DATABASE_URL "
            "설명을 참고하세요."
        )
        return lines
    lines.append("✓ DB 연결 성공 (SELECT 1 통과)")

    table = _users_table()
    if table is None:
        lines.append("✗ app_users 테이블을 읽지 못했습니다 (이 DB에 없거나 권한이 부족합니다).")
        lines.append("  → DATABASE_URL이 Researcher-board와 정말 같은 데이터베이스를 가리키는지 확인하세요.")
        return lines
    lines.append("✓ app_users 테이블 확인됨 (컬럼: " + ", ".join(c.name for c in table.columns) + ")")

    try:
        with engine.connect() as conn:
            row = conn.execute(select(table).where(table.c.user_id == user_id)).mappings().first()
    except Exception as exc:
        lines.append(f"✗ 계정 조회 실패: {exc}")
        return lines

    if row is None:
        lines.append(f"✗ user_id='{user_id}' 계정을 app_users 테이블에서 찾지 못했습니다.")
        lines.append("  → 대소문자/오타를 확인하고, Researcher-board 쪽 계정 목록과 비교해보세요.")
        return lines
    lines.append(f"✓ 계정 찾음 (display_name={row.get('display_name')!r})")

    if not check_password_hash(row["password_hash"], password):
        lines.append("✗ 비밀번호가 일치하지 않습니다.")
        return lines
    lines.append("✓ 비밀번호 일치")

    if row.get("must_change_password"):
        lines.append(
            "△ must_change_password=True → 이 앱은 임시 비밀번호 계정의 로그인을 거부합니다. "
            "Researcher-board에서 먼저 비밀번호를 바꿔야 합니다."
        )
        return lines

    lines.append("✓ 로그인 성공 조건을 모두 만족합니다.")
    return lines


def get_current_user() -> Optional[dict]:
    if "user_id" not in flask.session:
        return None
    return {
        "user_id": flask.session["user_id"],
        "display_name": flask.session.get("display_name", ""),
    }


def set_session(user: dict) -> None:
    flask.session.clear()
    flask.session.permanent = True
    flask.session["user_id"] = user["user_id"]
    flask.session["display_name"] = user["display_name"]
    flask.current_app.permanent_session_lifetime = timedelta(hours=SESSION_LIFETIME_HOURS)


def clear_session() -> None:
    flask.session.clear()
