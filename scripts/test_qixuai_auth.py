import argparse
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("qixuai_auth.py")
SPEC = importlib.util.spec_from_file_location("qixuai_auth_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
auth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auth)


def login_args(**overrides):
    values = {
        "scope": ["images:write", "files:write", "billing:read"],
        "timeout": 20,
        "no_browser": True,
        "complete": False,
        "wait_seconds": 0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class DeviceAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_config = os.environ.get("QIXUAI_CONFIG_DIR")
        os.environ["QIXUAI_CONFIG_DIR"] = self.temp.name
        self.original_protect = auth.protect_token
        self.original_unprotect = auth.unprotect_token
        auth.protect_token = lambda value: "plain-test:" + value
        auth.unprotect_token = lambda value: value.split(":", 1)[1]

    def tearDown(self):
        auth.protect_token = self.original_protect
        auth.unprotect_token = self.original_unprotect
        if self.original_config is None:
            os.environ.pop("QIXUAI_CONFIG_DIR", None)
        else:
            os.environ["QIXUAI_CONFIG_DIR"] = self.original_config
        self.temp.cleanup()

    def test_login_start_returns_without_polling_and_saves_pending_state(self):
        calls = []
        original_post = auth._post_form
        try:
            def fake_post(url, fields, timeout):
                calls.append(url)
                return 200, {
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": auth.VERIFICATION_URI,
                    "verification_uri_complete": auth.VERIFICATION_URI + "?user_code=ABCD-EFGH",
                    "expires_in": 900,
                    "interval": 5,
                }

            auth._post_form = fake_post
            with contextlib.redirect_stdout(io.StringIO()):
                result = auth.start_device_authorization(login_args())
        finally:
            auth._post_form = original_post
        self.assertEqual(0, result)
        self.assertEqual([auth.DEVICE_CODE_ENDPOINT], calls)
        raw = auth.pending_path().read_text(encoding="utf-8")
        self.assertNotIn('"device_code":', raw)
        self.assertIn("device_code_protected", raw)

    def test_complete_pending_returns_quickly_and_keeps_pending_state(self):
        auth.save_pending_device(
            {
                "device_code": "secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": auth.VERIFICATION_URI,
                "expires_in": 900,
                "interval": 5,
            },
            ["billing:read"],
        )
        original_post = auth._post_form
        try:
            auth._post_form = lambda *args: (400, {"error": "authorization_pending"})
            with contextlib.redirect_stdout(io.StringIO()):
                result = auth.complete_device_authorization(login_args(complete=True, wait_seconds=0))
        finally:
            auth._post_form = original_post
        self.assertEqual(2, result)
        self.assertTrue(auth.pending_path().exists())

    def test_complete_success_saves_credential_and_clears_pending(self):
        auth.save_pending_device(
            {
                "device_code": "secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": auth.VERIFICATION_URI,
                "expires_in": 900,
                "interval": 5,
            },
            ["images:write", "billing:read"],
        )
        original_post = auth._post_form
        original_validate = auth.validate_token
        try:
            auth._post_form = lambda *args: (
                200,
                {"access_token": "device-token", "token_type": "Bearer", "scope": "images:write billing:read"},
            )
            auth.validate_token = lambda *args: None
            with contextlib.redirect_stdout(io.StringIO()):
                result = auth.complete_device_authorization(login_args(complete=True))
        finally:
            auth._post_form = original_post
            auth.validate_token = original_validate
        self.assertEqual(0, result)
        self.assertTrue(auth.credential_path().exists())
        self.assertFalse(auth.pending_path().exists())
        credential = auth.load_credential()
        self.assertEqual(["billing:read", "images:write"], credential["scope"])

    def test_status_reports_pending_authorization(self):
        auth.save_pending_device(
            {
                "device_code": "secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": auth.VERIFICATION_URI,
                "expires_in": 900,
            },
            ["billing:read"],
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = auth.command_status(argparse.Namespace(check=False, timeout=20))
        self.assertEqual(2, result)
        self.assertIn("等待网页确认", output.getvalue())

    def test_complete_wait_is_capped_below_agent_timeout(self):
        auth.save_pending_device(
            {
                "device_code": "secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": auth.VERIFICATION_URI,
                "expires_in": 900,
            },
            ["billing:read"],
        )
        with self.assertRaises(auth.AuthError):
            auth.complete_device_authorization(login_args(complete=True, wait_seconds=51))


if __name__ == "__main__":
    unittest.main()
