"""Auth-state and bearer-header tests for the Python client."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from client.conduit.auth import ConduitAuthState
from client.conduit.sdk import Conduit
from client.conduit_admission import (
    AuthState as BootstrapAuthState,
    HTTP_DEFAULT_HEADERS,
    client_file_url,
    client_manifest_url,
    enrollment_endpoint,
    normalize_base_url,
    normalize_enrollment_model_id,
    smoke_launcher,
    write_conduit_launcher,
    write_env_file,
)


class AuthStateTests(unittest.TestCase):
    def test_auth_state_round_trips(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / '.conduit_auth.json'
            auth = ConduitAuthState(path)
            self.assertIsNone(auth.load_token())
            self.assertTrue(auth.save_token('ctkn.test.token', server_url='https://host'))
            self.assertEqual(auth.load_token(), 'ctkn.test.token')
            self.assertEqual(auth.load_server_url(), 'https://host')

    def test_token_age_days_none_when_no_token_saved(self):
        # FLAG-3: staleness nudge helpers.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / '.conduit_auth.json'
            auth = ConduitAuthState(path)
            self.assertIsNone(auth.load_saved_at())
            self.assertIsNone(auth.token_age_days())

    def test_token_age_days_reports_elapsed_time_since_save(self):
        import time
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / '.conduit_auth.json'
            auth = ConduitAuthState(path)
            auth.save_token('ctkn.aged.token', server_url='https://host')
            saved_at = auth.load_saved_at()
            self.assertIsNotNone(saved_at)
            self.assertLessEqual(abs(time.time() - saved_at), 5)
            age = auth.token_age_days()
            self.assertIsNotNone(age)
            self.assertGreaterEqual(age, 0.0)
            self.assertLess(age, 0.01)  # freshly saved, well under a day

    def test_rejects_legacy_auth_schema(self):
        import json as _json
        import os
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / '.conduit_auth.json'
            path.write_text(_json.dumps({"client_token": "ctkn.legacy"}), encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(ValueError, "Unsupported Conduit auth schema"):
                ConduitAuthState(path).load_token()

    def test_rejects_insecure_auth_permissions(self):
        import json as _json
        import os
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / '.conduit_auth.json'
            path.write_text(_json.dumps({"schemaVersion": 1, "clientToken": "ctkn.test"}), encoding="utf-8")
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(PermissionError, "mode 0600"):
                ConduitAuthState(path).load_token()

    def test_conduit_loads_token_from_auth_file(self):
        with tempfile.TemporaryDirectory() as td:
            auth_path = Path(td) / '.conduit_auth.json'
            auth = ConduitAuthState(auth_path)
            auth.save_token('ctkn.from.file', server_url='https://host')

            conduit = Conduit('https://host', auth_path=auth_path)
            try:
                self.assertEqual(conduit.transport._client.headers.get('Authorization'), 'Bearer ctkn.from.file')
                self.assertEqual(conduit.transport._client.headers.get('ngrok-skip-browser-warning'), '1')
            finally:
                import asyncio
                asyncio.run(conduit.close())

    def test_conduit_loads_token_from_conduit_token_env_var(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            auth_path = Path(td) / '.conduit_auth.json'
            with mock.patch.dict(os.environ, {"CONDUIT_TOKEN": "ctkn.env.conduit.token"}):
                conduit = Conduit('https://host', auth_path=auth_path)
                try:
                    self.assertEqual(conduit.transport._client.headers.get('Authorization'), 'Bearer ctkn.env.conduit.token')
                    self.assertEqual(conduit.transport._client.headers.get('ngrok-skip-browser-warning'), '1')
                finally:
                    import asyncio
                    asyncio.run(conduit.close())

    def test_bootstrap_http_defaults_include_ngrok_skip_header(self):
        self.assertEqual(HTTP_DEFAULT_HEADERS.get('ngrok-skip-browser-warning'), '1')

    def test_enrollment_model_defaults_to_undisclosed(self):
        self.assertEqual(normalize_enrollment_model_id(None), 'undisclosed')
        self.assertEqual(normalize_enrollment_model_id('   '), 'undisclosed')
        self.assertEqual(normalize_enrollment_model_id('known-family'), 'known-family')

    def test_enrollment_model_rejects_ambiguous_sentinels(self):
        for value in ['unknown', 'UNKNOWN', 'unspecified', 'n/a', 'not available']:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'undisclosed'):
                    normalize_enrollment_model_id(value)

    def test_write_env_file_uses_private_permissions(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            os.chdir(td)
            try:
                write_env_file('https://host.example/secret/mcp', '.conduit_auth.json', overwrite=True)
                env_path = Path('.env')
                self.assertTrue(env_path.exists())
                if os.name != 'nt':
                    self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
            finally:
                os.chdir(cwd)

    def test_write_env_file_preserves_unmanaged_entries_when_refreshing(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            os.chdir(td)
            try:
                Path('.env').write_text(
                    '# operator settings\nCUSTOM_FLAG=keep-me\nCONDUIT_URL="https://old.example"\n'
                    'CONDUIT_URL="https://duplicate.example"\nCONDUIT_AUTH_PATH="stale.json"\n',
                    encoding='utf-8',
                )
                # The default auth path is HOME-anchored; passing it explicitly
                # must be treated as 'default' and NOT persisted to .env.
                write_env_file('https://new.example/secret/mcp', str(Path.home() / '.conduit_auth.json'), overwrite=False)
                text = Path('.env').read_text(encoding='utf-8')
                self.assertIn('# operator settings', text)
                self.assertIn('CUSTOM_FLAG=keep-me', text)
                self.assertIn('CONDUIT_URL="https://new.example/secret"', text)
                self.assertEqual(text.count('CONDUIT_URL='), 1)
                self.assertNotIn('CONDUIT_AUTH_PATH=', text)
                if os.name != 'nt':
                    self.assertEqual(Path('.env').stat().st_mode & 0o777, 0o600)
            finally:
                os.chdir(cwd)

    def test_write_env_file_explicit_overwrite_replaces_unmanaged_entries(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            os.chdir(td)
            try:
                Path('.env').write_text('CUSTOM_FLAG=remove-me\n', encoding='utf-8')
                write_env_file('https://host.example/secret', 'custom-auth.json', overwrite=True)
                text = Path('.env').read_text(encoding='utf-8')
                self.assertNotIn('CUSTOM_FLAG', text)
                self.assertIn('CONDUIT_URL="https://host.example/secret"', text)
                self.assertIn('CONDUIT_AUTH_PATH="custom-auth.json"', text)
            finally:
                os.chdir(cwd)

    def test_bootstrap_auth_state_round_trips_with_private_permissions(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / '.conduit_auth.json'
            auth = BootstrapAuthState(path)
            auth.save_token('ctkn.bootstrap.token', server_url='https://host')
            self.assertEqual(auth.load_token(), 'ctkn.bootstrap.token')
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_normalize_base_url_strips_one_trailing_mcp_segment(self):
        self.assertEqual(normalize_base_url('https://host.example/secret/mcp'), 'https://host.example/secret')
        self.assertEqual(normalize_base_url('https://host.example/secret/mcp/mcp'), 'https://host.example/secret/mcp')

    def test_enrollment_endpoint_respects_configured_url(self):
        self.assertEqual(
            enrollment_endpoint('https://host.example/c-047836bb', '/admin/enroll/request'),
            'https://host.example/c-047836bb/admin/enroll/request',
        )
        self.assertEqual(
            enrollment_endpoint('https://host.example', '/admin/enroll/request'),
            'https://host.example/admin/enroll/request',
        )

    def test_client_bundle_urls_use_normalized_base_url(self):
        self.assertEqual(
            client_manifest_url('https://host.example/conduit-local/mcp'),
            'https://host.example/conduit-local/client/manifest.json',
        )
        self.assertEqual(
            client_file_url('https://host.example/conduit-local/mcp', 'client/conduit/transport.py'),
            'https://host.example/conduit-local/client/file?path=client%2Fconduit%2Ftransport.py',
        )

    def test_generated_launcher_is_valid_bash(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            cwd = os.getcwd()
            os.chdir(td)
            try:
                write_conduit_launcher(overwrite=True)
                smoke_launcher()
                launcher = Path('conduit').read_text(encoding='utf-8')
                self.assertIn('client/conduit_cli.py', launcher)
                self.assertNotIn('ROOT/conduit_cli.py', launcher)
            finally:
                os.chdir(cwd)



if __name__ == '__main__':
    unittest.main()
