from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from integrations import GoogleWorkspace, IntegrationError


class PersistentGoogleSetupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()
        self.environment = patch.dict(os.environ, {
            "BRIGHTLINE_DATA_DIR": str(self.root / "data"),
            "GOOGLE_OAUTH_CLIENT_FILE": "",
            "GOOGLE_OAUTH_TOKEN_FILE": "",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def client(self, name="download.json", identifier="original"):
        source = self.root / name
        source.write_text(json.dumps({"web": {
            "client_id": identifier + ".apps.googleusercontent.com",
            "client_secret": "synthetic-test-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://127.0.0.1:8000/api/google/oauth/callback"],
        }}))
        return source

    def test_saved_configuration_survives_download_removal_and_new_process(self):
        import subprocess
        import sys

        source = self.client()
        saved = GoogleWorkspace.save_client_configuration(source)
        source.unlink()
        self.assertEqual(saved, self.root / "data" / "google-oauth-client.json")
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        result = subprocess.run([
            sys.executable, "-c",
            "from integrations import GoogleWorkspace; "
            "assert GoogleWorkspace.configured_identity()['configured']; "
            "assert not GoogleWorkspace.configured_identity()['authorized']; "
            "flow = GoogleWorkspace.authorization_flow('http://127.0.0.1:8000/api/google/oauth/callback'); "
            "assert flow.authorization_url()[0].startswith('https://accounts.google.com/')",
        ], cwd=Path(__file__).resolve().parent, env=os.environ.copy(), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_explicit_override_wins_and_missing_override_is_not_silently_ignored(self):
        GoogleWorkspace.save_client_configuration(self.client())
        override = self.client("override.json", "different")
        with patch.dict(os.environ, {"GOOGLE_OAUTH_CLIENT_FILE": str(override)}):
            self.assertEqual(GoogleWorkspace._client_path(), override)
            override.unlink()
            identity = GoogleWorkspace.configured_identity()
            self.assertFalse(identity["configured"])
            self.assertIn("moved or is missing", identity["error"])

    def test_invalid_download_does_not_replace_saved_configuration(self):
        saved = GoogleWorkspace.save_client_configuration(self.client())
        before = saved.read_bytes()
        invalid = self.root / "invalid.json"
        for payload in ('{"type":"service_account"}', '[]', 'broken json'):
            invalid.write_text(payload)
            with self.assertRaises(IntegrationError):
                GoogleWorkspace.save_client_configuration(invalid)
            self.assertEqual(saved.read_bytes(), before)

    def test_disconnect_keeps_setup_and_existing_token_is_reused(self):
        saved = GoogleWorkspace.save_client_configuration(self.client())
        token = GoogleWorkspace._token_path()
        token.write_text('{"refresh_token":"synthetic-token"}')
        self.assertEqual(GoogleWorkspace.from_env().client_path, saved)
        self.assertTrue(GoogleWorkspace.configured_identity()["authorized"])
        GoogleWorkspace.disconnect()
        identity = GoogleWorkspace.configured_identity()
        self.assertTrue(identity["configured"])
        self.assertFalse(identity["authorized"])
        self.assertTrue(saved.exists())

    def test_missing_setup_has_actionable_error(self):
        identity = GoogleWorkspace.configured_identity()
        self.assertFalse(identity["configured"])
        self.assertIn("google-oauth-client.json", identity["error"])

    def test_server_start_requires_connect_even_with_saved_credentials(self):
        import server
        from database import Database

        saved = GoogleWorkspace.save_client_configuration(self.client())
        token = GoogleWorkspace._token_path()
        token.write_text('{"refresh_token":"synthetic-token"}')
        database = Database(self.root / "state.db")
        database.set_setting("google_connection_status", "connected")
        database.set_setting("google_connection_identity", GoogleWorkspace.configured_identity()["fingerprint"])
        database.set_setting("google_connection_account", "example@example.test")
        database.set_setting("google_sheets_verified", "1")
        http_server = Mock()
        with patch.object(server, "DB", database), patch.object(server, "ThreadingHTTPServer", return_value=http_server), patch("builtins.print"):
            server.run()
            for connection in server.google_connection_state().values():
                self.assertEqual(connection["status"], "disconnected")
                self.assertFalse(connection["authorized"])
                self.assertTrue(connection["oauth_client_configured"])
            handler = server.AppHandler.__new__(server.AppHandler)
            with patch.object(GoogleWorkspace, "from_env") as workspace:
                with self.assertRaisesRegex(IntegrationError, "Click Connect"):
                    handler._test_google({"service": "google_sheets"})
                workspace.assert_not_called()
        self.assertTrue(saved.is_file())
        self.assertTrue(token.is_file())

    def test_connect_starts_sign_in_but_only_successful_callback_activates_access(self):
        import server
        import time
        from database import Database

        database = Database(self.root / "state.db")
        database.set_setting("google_connection_disabled", "1")
        handler = server.AppHandler.__new__(server.AppHandler)
        handler._redirect = Mock()
        flow = Mock()
        flow.authorization_url.return_value = ("https://accounts.google.com/test", "test-state")
        workspace = Mock()
        workspace.account_email.return_value = "example@example.test"
        workspace.identity_fingerprint = "test-fingerprint"
        workspace.test_connection.return_value = {
            "account": "example@example.test", "output_folder_id": "test-folder",
            "output_folder_name": "Brightline Reports",
        }
        with patch.object(server, "DB", database), patch.dict(server.GOOGLE_OAUTH_FLOWS, {}, clear=True):
            with patch.object(GoogleWorkspace, "authorization_flow", return_value=flow):
                handler._start_google_oauth()
            self.assertEqual(database.get_setting("google_connection_disabled"), "1")
            handler._redirect.assert_called_with("https://accounts.google.com/test")
            handler.path = "/api/google/oauth/callback?state=test-state&code=test-code"
            with patch.object(server, "complete_google_oauth"), patch.object(GoogleWorkspace, "save_oauth_credentials"), patch.object(GoogleWorkspace, "from_env", return_value=workspace):
                handler._google_oauth_callback()
            self.assertEqual(database.get_setting("google_connection_disabled"), "0")
            self.assertEqual(database.get_setting("google_connection_status"), "connected")
            database.set_setting("google_connection_disabled", "1")
            server.GOOGLE_OAUTH_FLOWS["cancelled"] = (time.monotonic(), flow)
            handler.path = "/api/google/oauth/callback?state=cancelled&error=access_denied"
            handler._google_oauth_callback()
            self.assertEqual(database.get_setting("google_connection_disabled"), "1")


if __name__ == "__main__":
    unittest.main()
