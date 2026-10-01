import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask
from sqlalchemy import Boolean, Column, DateTime, MetaData, String, Table, create_engine
from werkzeug.security import generate_password_hash

from confluence_agenda.web import auth, db


def _make_sqlite_app_users_db(path: Path, rows):
    """실제 Postgres 없이도 auth.py를 끝까지 검증할 수 있도록, Researcher-board의
    app_users 테이블과 같은 모양을 sqlite에 만들어 둔다(사용하는 칼럼만)."""
    engine = create_engine(f"sqlite:///{path}")
    metadata = MetaData()
    users = Table(
        "app_users",
        metadata,
        Column("user_id", String, primary_key=True),
        Column("password_hash", String, nullable=False),
        Column("display_name", String, nullable=False),
        Column("must_change_password", Boolean, nullable=False, default=False),
        Column("created_at", DateTime),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        for row in rows:
            conn.execute(users.insert().values(**row))
    engine.dispose()


class AuthTest(unittest.TestCase):
    def setUp(self):
        # db.py/auth.py의 모듈 전역 싱글턴 캐시를 테스트마다 초기화한다.
        db._engine = None
        db._initialized = False
        auth._table = None
        auth._table_checked = False
        self.addCleanup(self._reset_singletons)

        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def _reset_singletons(self):
        db._engine = None
        db._initialized = False
        auth._table = None
        auth._table_checked = False

    def _sqlite_env(self, rows):
        db_path = Path(self._tmpdir.name) / "test.db"
        _make_sqlite_app_users_db(db_path, rows)
        return {"DATABASE_URL": f"sqlite:///{db_path}"}

    def test_not_configured_without_database_url(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(db.is_configured())
            self.assertFalse(auth.is_configured())
            self.assertIsNone(auth.authenticate("someone", "pw"))

    def test_authenticate_succeeds_with_correct_password(self):
        env = self._sqlite_env(
            [
                {
                    "user_id": "dh.kwon",
                    "password_hash": generate_password_hash("correct horse"),
                    "display_name": "권동혁",
                    "must_change_password": False,
                }
            ]
        )
        with mock.patch.dict("os.environ", env, clear=True):
            user = auth.authenticate("dh.kwon", "correct horse")

        self.assertEqual(user, {"user_id": "dh.kwon", "display_name": "권동혁"})

    def test_authenticate_fails_with_wrong_password(self):
        env = self._sqlite_env(
            [
                {
                    "user_id": "dh.kwon",
                    "password_hash": generate_password_hash("correct horse"),
                    "display_name": "권동혁",
                    "must_change_password": False,
                }
            ]
        )
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertIsNone(auth.authenticate("dh.kwon", "wrong password"))

    def test_authenticate_fails_for_unknown_user(self):
        env = self._sqlite_env([])
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertIsNone(auth.authenticate("nobody", "pw"))

    def test_authenticate_raises_when_must_change_password(self):
        env = self._sqlite_env(
            [
                {
                    "user_id": "newbie",
                    "password_hash": generate_password_hash("12345678"),
                    "display_name": "신규",
                    "must_change_password": True,
                }
            ]
        )
        with mock.patch.dict("os.environ", env, clear=True):
            with self.assertRaises(auth.PasswordChangeRequired):
                auth.authenticate("newbie", "12345678")

    def test_authenticate_rejects_blank_input(self):
        env = self._sqlite_env([])
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertIsNone(auth.authenticate("", "pw"))
            self.assertIsNone(auth.authenticate("user", ""))

    def test_session_roundtrip(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        with app.test_request_context():
            self.assertIsNone(auth.get_current_user())
            auth.set_session({"user_id": "dh.kwon", "display_name": "권동혁"})
            self.assertEqual(
                auth.get_current_user(), {"user_id": "dh.kwon", "display_name": "권동혁"}
            )
            auth.clear_session()
            self.assertIsNone(auth.get_current_user())


if __name__ == "__main__":
    unittest.main()
