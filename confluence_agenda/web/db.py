"""PostgreSQL 접속 계층.

이 앱은 자체 사용자 DB를 두지 않는다 - dhkwon1122/Researcher-board가 쓰는
같은 Postgres 인스턴스를 DATABASE_URL로 가리키면, 그 DB의 app_users
테이블을 읽기 전용으로 써서 로그인한다(auth.py 참고). DATABASE_URL이
없으면 로그인 기능 자체가 꺼지고 기존처럼 로그인 없이 동작한다.

(dhkwon1122/Researcher-board의 services/db.py와 같은 패턴.)
"""

from __future__ import annotations

import os

try:
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
except ImportError:  # sqlalchemy 미설치 환경 - 로그인 기능만 비활성화된다.
    create_engine = None
    make_url = None

_engine = None
_initialized = False


def get_engine():
    """DATABASE_URL이 있으면 SQLAlchemy Engine을, 없으면 None을 반환 (싱글턴)."""
    global _engine, _initialized
    if _initialized:
        return _engine
    _initialized = True

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url or create_engine is None:
        _engine = None
        return None
    try:
        # connect_timeout은 psycopg2 등 네트워크 드라이버 전용 옵션이라
        # sqlite(테스트에서 사용)에는 안 통한다 - 백엔드가 sqlite면 뺀다.
        backend = make_url(url).get_backend_name()
        connect_args = {} if backend == "sqlite" else {"connect_timeout": 5}
        _engine = create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)
    except Exception as exc:  # 잘못된 URL 등
        print(f"[db] Engine 생성 실패, 로그인 비활성화: {exc}")
        _engine = None
    return _engine


def is_configured() -> bool:
    return get_engine() is not None
