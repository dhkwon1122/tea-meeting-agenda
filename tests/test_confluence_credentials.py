import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet

from confluence_agenda.web import confluence_credentials, db

_TEST_KEY = Fernet.generate_key().decode()


class ConfluenceCredentialsTest(unittest.TestCase):
    def setUp(self):
        db._engine = None
        db._initialized = False
        confluence_credentials._table = None
        confluence_credentials._table_checked = False
        self.addCleanup(self._reset_singletons)

        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def _reset_singletons(self):
        db._engine = None
        db._initialized = False
        confluence_credentials._table = None
        confluence_credentials._table_checked = False

    def _sqlite_env(self, *, with_key=True):
        db_path = Path(self._tmpdir.name) / "test.db"
        env = {"DATABASE_URL": f"sqlite:///{db_path}"}
        if with_key:
            env["CREDENTIAL_ENCRYPTION_KEY"] = _TEST_KEY
        return env

    def test_not_configured_without_database_url(self):
        with mock.patch.dict("os.environ", {"CREDENTIAL_ENCRYPTION_KEY": _TEST_KEY}, clear=True):
            self.assertFalse(confluence_credentials.is_configured())
            self.assertIsNone(confluence_credentials.get_pat("dh.kwon"))

    def test_not_configured_without_encryption_key(self):
        env = self._sqlite_env(with_key=False)
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertFalse(confluence_credentials.is_configured())

    def test_not_configured_with_malformed_encryption_key(self):
        env = self._sqlite_env(with_key=False)
        env["CREDENTIAL_ENCRYPTION_KEY"] = "not-a-valid-fernet-key"
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertFalse(confluence_credentials.is_configured())

    def test_set_then_get_round_trips_through_encryption(self):
        env = self._sqlite_env()
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertTrue(confluence_credentials.is_configured())
            confluence_credentials.set_pat("dh.kwon", "my-secret-pat")
            self.assertEqual(confluence_credentials.get_pat("dh.kwon"), "my-secret-pat")

    def test_get_pat_returns_none_for_unregistered_user(self):
        env = self._sqlite_env()
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertIsNone(confluence_credentials.get_pat("nobody"))

    def test_set_pat_overwrites_previous_value(self):
        env = self._sqlite_env()
        with mock.patch.dict("os.environ", env, clear=True):
            confluence_credentials.set_pat("dh.kwon", "old-pat")
            confluence_credentials.set_pat("dh.kwon", "new-pat")
            self.assertEqual(confluence_credentials.get_pat("dh.kwon"), "new-pat")

    def test_delete_pat_removes_it(self):
        env = self._sqlite_env()
        with mock.patch.dict("os.environ", env, clear=True):
            confluence_credentials.set_pat("dh.kwon", "my-secret-pat")
            confluence_credentials.delete_pat("dh.kwon")
            self.assertIsNone(confluence_credentials.get_pat("dh.kwon"))

    def test_delete_pat_is_a_no_op_for_unregistered_user(self):
        env = self._sqlite_env()
        with mock.patch.dict("os.environ", env, clear=True):
            confluence_credentials.delete_pat("nobody")  # 예외 없이 그냥 넘어가야 한다.

    def test_status_reports_none_before_registration_and_timestamp_after(self):
        env = self._sqlite_env()
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertIsNone(confluence_credentials.status("dh.kwon"))
            confluence_credentials.set_pat("dh.kwon", "my-secret-pat")
            self.assertIsNotNone(confluence_credentials.status("dh.kwon"))

    def test_set_pat_raises_when_not_configured(self):
        env = self._sqlite_env(with_key=False)
        with mock.patch.dict("os.environ", env, clear=True):
            with self.assertRaises(confluence_credentials.CredentialStorageUnavailable):
                confluence_credentials.set_pat("dh.kwon", "my-secret-pat")

    def test_decrypt_failure_after_key_rotation_returns_none_instead_of_raising(self):
        env = self._sqlite_env()
        with mock.patch.dict("os.environ", env, clear=True):
            confluence_credentials.set_pat("dh.kwon", "my-secret-pat")

        rotated_env = dict(env)
        rotated_env["CREDENTIAL_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
        with mock.patch.dict("os.environ", rotated_env, clear=True):
            self.assertIsNone(confluence_credentials.get_pat("dh.kwon"))


if __name__ == "__main__":
    unittest.main()
