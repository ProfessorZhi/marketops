"""Protect TinyFish OAuth tokens against insecure alternative Keyring backends.

Only synthetic objects, no real OS Keyring or OAuth account is read.
"""
from __future__ import annotations
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import research_official_mcp_auth as oauth


def fake_keyring_module(backend_name: str, priority: int):
    cls=type("KeyringBackend",(),{"priority":priority})
    cls.__module__=backend_name
    impl=cls()
    module=types.ModuleType("keyring")
    errors=types.ModuleType("keyring.errors")
    class KeyringError(Exception): pass
    errors.KeyringError=KeyringError
    module.get_keyring=lambda:impl
    module.get_password=lambda *args:None
    module.set_password=lambda *args:None
    return module,errors


class OAuthKeyringSecurityTests(unittest.TestCase):
    def test_plaintext_and_composite_backend_cannot_store_oauth_tokens(self):
        for backend in ("keyrings.alt.file","keyring.backends.chainer",
                        "keyring.backends.fail"):
            with self.subTest(module=backend):
                fake,err=fake_keyring_module(backend,10)
                with patch.dict(sys.modules,{"keyring":fake,"keyring.errors":err}):
                    with self.assertRaisesRegex(ValueError,
                            "OS_KEYRING_SECURE_BACKEND_UNAVAILABLE"):
                        oauth._backend()
                    self.assertFalse(oauth.has_saved_tokens())

    def test_native_os_backends_are_allowed_when_available(self):
        for backend in ("keyring.backends.SecretService",
                        "keyring.backends.Windows","keyring.backends.macOS",
                        "keyring.backends.kwallet"):
            with self.subTest(module=backend):
                fake,err=fake_keyring_module(backend,5)
                with patch.dict(sys.modules,{"keyring":fake,"keyring.errors":err}):
                    self.assertIs(fake,oauth._backend())
                    self.assertFalse(oauth.has_saved_tokens())

    def test_low_priority_is_refused_even_on_native_backend(self):
        fake,err=fake_keyring_module("keyring.backends.SecretService",0)
        with patch.dict(sys.modules,{"keyring":fake,"keyring.errors":err}):
            with self.assertRaisesRegex(ValueError,
                    "OS_KEYRING_SECURE_BACKEND_UNAVAILABLE"):
                oauth._backend()


if __name__=="__main__":
    unittest.main()
