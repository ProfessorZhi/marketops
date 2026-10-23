"""All customer provider names share admission and the managed gateway.

Old code had two independent direct-provider planes. These tests retain the
negative boundary and assert the replacement: one default dispatch path.
Positive default-factory dispatch, caps and persistence are tested in
``test_delivery_runtime`` against only a replaced lowest wire transport.
"""
import inspect
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import research_mcp as mcp
import research_wiring as wiring
import _s0_evidence_fixture as s0_fixture

BRIEF = {'record_type': 'research_brief', 'target_kind': 'venue', 'goal': 'pool',
         'require_named_person': False, 'minimum_targets': None}

class SingleGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'data'
        env = patch.dict(os.environ, {'MARKETOPS_RESEARCH_DATA_ROOT': str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        mcp.start('r1', 'scope', 'public venues', 'rules', BRIEF)

    def test_without_admission_no_legacy_name_reaches_any_provider(self):
        import research_runtime_transport as transport
        calls = [lambda: mcp.invoke('tinyfish_mcp_search', 'r1', 'tiny', query='venues'),
                 lambda: mcp.invoke('serpapi_mcp_search', 'r1', 'serp', mcp_params={'q':'venues'}),
                 lambda: mcp.search('serper', 'venues', 'US', 'en', 'r1', 'serper'),
                 lambda: mcp.invoke('crawl4ai_local', 'r1', 'crawl', url='https://example.org/')]
        with patch.object(transport, 'mcp_session') as remote, patch.object(transport, 'serper_transport') as rest:
            for call in calls:
                with self.assertRaisesRegex(ValueError, 'CAPABILITY_NOT_AVAILABLE'):
                    call()
            remote.assert_not_called()
            rest.assert_not_called()
        self.assertFalse((self.root / 'state' / 'usage-ledger.sqlite3').exists())

    def test_paid_agent_and_legacy_direct_rest_stay_unavailable(self):
        for action in ('tinyfish_mcp_agent', 'tinyfish_mcp_wait', 'tinyfish_search',
                       'tinyfish_fetch', 'crawl4ai_cloud'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'CAPABILITY_NOT_AVAILABLE'):
                mcp.invoke(action, 'r1', 'no-dispatch')
        self.assertFalse((self.root / 'state' / 'usage-ledger.sqlite3').exists())

    def test_tools_cannot_supply_their_own_admission_or_runtime_consent(self):
        for fn in (mcp.invoke, mcp.search, mcp.managed_call):
            params = inspect.signature(fn).parameters
            for forbidden in ('evidence', 'runtime_config', 'network_enabled', 'max_calls'):
                self.assertNotIn(forbidden, params)
        names = mcp.build_server()._tool_manager._tools
        self.assertNotIn('marketops_runtime_configure', names)
        self.assertNotIn('marketops_grant', names)

    def test_evidence_written_elsewhere_does_not_unlock_this_root(self):
        s0_fixture.install(Path(self.tmp.name) / 'other')
        with self.assertRaisesRegex(ValueError, 'CAPABILITY_NOT_AVAILABLE'):
            mcp.search('serper', 'venues', 'US', 'en', 'r1', 'no-admission')

    def test_absent_gate_is_never_permission(self):
        real = wiring._import_if_present
        with patch.object(wiring, '_import_if_present', lambda name: None
                if name == 'research_s0_preflight_gate' else real(name)):
            allowed, code, failed = wiring.s0_provider_entry(self.root)
        self.assertFalse(allowed)
        self.assertEqual('CAPABILITY_NOT_AVAILABLE', code)
        self.assertEqual(('S0_GATE_ABSENT',), failed)

    def test_broken_gate_fails_without_echoing_evidence(self):
        import research_s0_preflight_gate as gate
        with patch.object(gate, 'evaluate', side_effect=RuntimeError('synthetic-secret')):
            allowed, code, failed = wiring.s0_provider_entry(self.root)
        self.assertFalse(allowed)
        self.assertEqual(('S0_EVALUATION_FAILED',), failed)
        self.assertNotIn('synthetic-secret', str((code, failed)))

    def test_only_managed_invoke_remains_in_customer_provider_dispatch(self):
        source = (ROOT / 'scripts' / 'research_mcp.py').read_text()
        self.assertNotIn('return extra.invoke(', source)
        self.assertNotIn('result = dual_search_mcp.search(', source)
        self.assertEqual(1, source.count('research_wiring.managed_invoke('))
        for fn in (mcp.search, mcp.invoke):
            self.assertIn('managed_call(', inspect.getsource(fn))

if __name__ == '__main__':
    unittest.main()
