"""Every customer provider route is admitted before reservation or outbound I/O.

The delivery runtime replaces the two historical direct planes with one gateway.
Legacy campaign evidence still fails closed; the explicit request-count product
profile is checked per provider and does not claim a dollar price or account B0.
Only the lowest wire boundary is replaced in the positive dispatch control.
"""
import ast
from contextlib import contextmanager
import inspect
import io
import json
from pathlib import Path
import sys
import textwrap
from unittest.mock import patch

import pytest
from conftest import SCRIPTS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _s0_evidence_fixture as fixture
import research_mcp as mcp
import research_wiring as wiring
import research_runtime_setup as runtime
import research_runtime_transport as transport
import research_usage_ledger as usage
import research_lite_store as store

BRIEF = {'record_type': 'research_brief', 'target_kind': 'venue', 'goal': 'pool',
         'minimum_targets': None, 'require_named_person': False}

@pytest.fixture()
def env_root(monkeypatch, tmp_path):
    root = tmp_path / 'data'
    monkeypatch.setenv('MARKETOPS_RESEARCH_DATA_ROOT', str(root))
    mcp.start('r1', 'test', 'public venues', 'rules', BRIEF)
    store.save_artifact(root, 'r1', 'source', 'fixture', 'https://example.org/',
                        b'An organization operates the public venue. Evidence for a synthetic judgment.', 'rendered_html')
    return root


def entries():
    return {
        'tinyfish': lambda: mcp.invoke('tinyfish_mcp_search', 'r1', 'tinyfish', query='public venues'),
        'serpapi': lambda: mcp.invoke('serpapi_mcp_search', 'r1', 'serpapi', mcp_params={'q':'public venues'}),
        'serper': lambda: mcp.search('serper', 'public venues', 'US', 'en', 'r1', 'serper'),
        'crawl4ai': lambda: mcp.invoke('crawl4ai_local', 'r1', 'crawl', url='https://example.org/'),
        'jev': lambda: mcp.invoke('jev_choice', 'r1', 'jev', document='source',
                                  question='What role?', options=['operator','other']),
    }


def test_without_evidence_every_host_entry_refuses_before_provider_or_reservation(clean_env, env_root):
    with patch.object(transport, 'mcp_session', side_effect=AssertionError('wire touched')) as remote, \
            patch.object(transport, 'serper_transport', side_effect=AssertionError('wire touched')) as rest, \
            patch.object(usage.UsageLedger, 'reserve', side_effect=AssertionError('reserved')) as reserve:
        for call in entries().values():
            with pytest.raises(ValueError, match='CAPABILITY_NOT_AVAILABLE'):
                call()
        remote.assert_not_called()
        rest.assert_not_called()
        reserve.assert_not_called()
    assert not (env_root / 'state' / 'usage-ledger.sqlite3').exists()


def test_each_legacy_campaign_precondition_still_denies_its_own_reason(clean_env, env_root):
    base = fixture.evidence()
    cases = [({}, 'CAPABILITY_NOT_AVAILABLE')]
    boolean = json.loads(json.dumps(base)); boolean.pop('upstream_schema_snapshots')
    cases.append((boolean, 'CAPABILITY_NOT_AVAILABLE'))
    tampered = json.loads(json.dumps(base))
    tampered['upstream_schema_snapshots']['serpapi']['snapshot_serialized'] += ' '
    cases.append((tampered, 'CAPABILITY_NOT_AVAILABLE'))
    for changes, code in [({'captured':False}, 'WALLET_PRECONDITION_FAILED'),
                          ({'next_op_estimate':None}, 'PRICE_UNKNOWN_FAIL_CLOSED'),
                          ({'key_rotation_confirmed':False}, 'WALLET_PRECONDITION_FAILED'),
                          ({'next_op_estimate':'0'}, 'PRICE_UNKNOWN_FAIL_CLOSED'),
                          ({'auto_topup':True}, 'WALLET_PRECONDITION_FAILED')]:
        item = json.loads(json.dumps(base)); item['providers']['serper'].update(changes)
        cases.append((item, code))
    class NeverGateway:
        def invoke(self, **kwargs): raise AssertionError('gateway reached')
    for evidence, expected in cases:
        result = wiring.managed_invoke(capability_id='tinyfish.search', params={'query':'venues'},
            run_id='r1', operation_id='op', account_ref='tinyfish', intent_fingerprint='query',
            root=env_root, evidence=evidence, gateway_object=NeverGateway())
        assert result.gateway_invoked is False
        assert result.reason_code == expected


def test_missing_or_broken_campaign_gate_fails_without_echoing_evidence(clean_env, env_root):
    import research_s0_preflight_gate as gate
    with patch.object(gate, 'evaluate', side_effect=RuntimeError('untrusted-secret')):
        verdict = wiring.s0_provider_entry(env_root)
    assert verdict == (False, 'CAPABILITY_NOT_AVAILABLE', ('S0_EVALUATION_FAILED',))
    real = wiring._import_if_present
    with patch.object(wiring, '_import_if_present', lambda name: None
            if name == 'research_s0_preflight_gate' else real(name)):
        verdict = wiring.s0_provider_entry(env_root)
    assert verdict == (False, 'CAPABILITY_NOT_AVAILABLE', ('S0_GATE_ABSENT',))


def test_other_root_or_missing_other_account_cannot_change_selected_provider_admission(clean_env, env_root, tmp_path, monkeypatch):
    fixture.install(tmp_path / 'other')
    with pytest.raises(ValueError, match='CAPABILITY_NOT_AVAILABLE'):
        entries()['serper']()
    runtime.configure(env_root, providers=['serper'], domains=['example.org'], max_calls=2,
                      task='public research', confirm=True)
    monkeypatch.setenv('SERPER_API_KEY', 'synthetic-key')
    with patch.object(runtime, 'verify_build_receipt', return_value={'ok':True}):
        assert runtime.admission(env_root, 'serper.search')['allowed']
        assert not runtime.admission(env_root, 'tinyfish.search')['allowed']
    assert not runtime.config(tmp_path / 'other')


def test_real_default_gateway_positive_control_and_cumulative_limit(clean_env, env_root, monkeypatch):
    runtime.configure(env_root, providers=['serper'], domains=['example.org'], max_calls=1,
                      task='public research', confirm=True)
    monkeypatch.setenv('SERPER_API_KEY', 'synthetic-key')
    calls = []
    @contextmanager
    def wire(request, timeout):
        calls.append(request.full_url)
        response = io.BytesIO(b'{"organic":[{"title":"Venue","link":"https://example.org/"}]}')
        response.status = 200
        yield response
    with patch.object(runtime, 'verify_build_receipt', return_value={'ok':True}), \
            patch.object(transport, 'serper_transport', wire):
        first = entries()['serper']()
        assert first['status'] == 'OK'
        assert first['artifact_ids'] and first['stored_record_ids']
        blocked = mcp.managed_call('serper.search', {'query':'another query'}, 'r1', 'second')
        assert blocked['status'] == 'BLOCKED'
        assert blocked['reason_code'] in {'BUDGET_RUN_CEILING', 'BUDGET_PROJECT_CEILING', 'BUDGET_PROVIDER_CEILING'}
    assert len(calls) == 1
    store.read_artifact(env_root, 'r1', first['artifact_ids'][0])


def test_paid_browser_remains_disabled_even_with_legacy_switch_and_s0(clean_env, env_root, monkeypatch):
    fixture.install(env_root)
    monkeypatch.setenv('MARKETOPS_TINYFISH_AGENT_ALLOW_PAID', '1')
    with patch.object(usage.UsageLedger, 'reserve') as reserve, patch.object(transport, 'mcp_session') as wire:
        for action in ('tinyfish_mcp_agent', 'tinyfish_mcp_wait'):
            with pytest.raises(ValueError, match='CAPABILITY_NOT_AVAILABLE'):
                mcp.invoke(action, 'r1', 'paid', url='https://example.org/', paid_limit_usd='0.2')
        reserve.assert_not_called(); wire.assert_not_called()


def test_test_fixtures_never_enter_production_and_unknown_zero_is_not_free(clean_env):
    for path in SCRIPTS.glob('*.py'):
        source = path.read_text(encoding='utf-8')
        assert '_s0_evidence_fixture' not in source
        assert '_runtime_mcp_fixture' not in source
    import research_s0_preflight_gate as gate
    assert fixture.SYNTHETIC_SOURCE not in gate.ZERO_PRICE_SOURCES
    evidence = fixture.evidence()
    evidence['providers']['serper']['next_op_estimate'] = '0'
    assert gate.evaluate(evidence)['launch'] == 'DENY'


def test_provider_dispatch_is_only_the_managed_gateway_not_old_planes(clean_env):
    tree = ast.parse((SCRIPTS / 'research_mcp.py').read_text())
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            pair = (node.func.value.id, node.func.attr)
            assert pair not in {('extra','invoke'), ('dual_search_mcp','search'), ('official_mcp','call')}
            if pair == ('research_wiring','managed_invoke'): calls.append(node)
    assert len(calls) == 1
    for function in (mcp.search, mcp.invoke):
        assert 'managed_call(' in inspect.getsource(function)
    assert 'research_wiring.managed_invoke(' in inspect.getsource(mcp.managed_call)


def test_host_tools_cannot_supply_consent_and_local_tools_cannot_dispatch(clean_env):
    tools = mcp.build_server()._tool_manager._tools
    provider_names = {'marketops_search','marketops_tinyfish','marketops_crawl','marketops_jev_choice',
        'marketops_serpapi_mcp_search','marketops_tinyfish_agent','marketops_tinyfish_agent_wait',
        'marketops_managed_call','marketops_research_step'}
    for name, tool in tools.items():
        source = textwrap.dedent(inspect.getsource(tool.fn))
        tree = ast.parse(source)
        direct = {x.func.id for x in ast.walk(tree) if isinstance(x,ast.Call) and isinstance(x.func,ast.Name)}
        if name not in provider_names:
            assert not direct & {'invoke','search','managed_call','managed_invoke'}
        if name in provider_names:
            for field in ('evidence','runtime_config','network_enabled','max_calls','managed_call','admission'):
                assert field not in tool.parameters.get('properties', {})
    assert 'marketops_runtime_configure' not in tools
    step = ast.parse(textwrap.dedent(inspect.getsource(tools['marketops_research_step'].fn)))
    calls = [node for node in ast.walk(step) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
             and (node.func.value.id, node.func.attr) == ('research_fallback', 'research_step')]
    assert len(calls) == 1
    injected = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    assert isinstance(injected['managed_call'], ast.Name) and injected['managed_call'].id == 'managed_call'
    admission = injected['admission']
    assert isinstance(admission, ast.Lambda) and isinstance(admission.body, ast.Call)
    assert ast.unparse(admission.body.func) == 'research_runtime_setup.admission'
    assert ast.unparse(admission.body.args[0]) == 'data_root'
