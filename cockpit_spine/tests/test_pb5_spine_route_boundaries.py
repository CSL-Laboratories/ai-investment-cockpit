"""Offline regressions for the spine session and route consumer boundaries.

Each boundary refuses what it cannot trust instead of passing it on: a source
outcome that does not answer the current request, a payload holding a value
JSON cannot carry (NaN or infinity), and a projection registry or contract pin
whose shape is not the one it promised. No provider, socket, vault checkout, or
pytest needed.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cockpit_spine import audit, cli, projections, session
from cockpit_spine.fake_source import FakeProposalSource, inline_payload
from successor.route_consumer import pin
from successor.route_consumer.consumer import DecisionOutcome, RouteConsumer, is_strict_json
from successor.route_consumer.profile import DECISION_PROFILE, canonical_digest
from successor.route_consumer.records import RouteBinding, prompt_digest

DIGEST = 'sha256:' + '2' * 64
STAMP = '2026-11-15T23:00:00Z'
BINDING = RouteBinding('route-a', 'claude', DIGEST, 'proc-a', 'model-a', 'tenant-a', 'fake', 'ref-a')


class FixedPort:
    def __init__(self, payload):
        self.payload = payload
        self.submissions = 0

    def support(self):
        return {'type': 'AdapterSupport@0', 'engine_id': 'claude', 'adapter_kind': 'fake',
                'state': 'supported', 'reason': 'fake-driver', 'live_calls': False}

    def submit(self, request, **kwargs):
        self.submissions += 1
        return {'type': 'RouteExecutionResult@0', 'request_id': request['id'],
                'status': 'ok', 'error': None, 'dispatched': True, 'repaired': False,
                'usage_state': 'ok', 'output_digest': DIGEST, 'validated': self.payload,
                'health_event': None, 'termination': None}


class FixedSource:
    adapter_kind = 'offline-pb5-regression'

    def __init__(self, outcome):
        self.outcome = outcome

    def fetch(self, **kwargs):
        return self.outcome


def accepted(payload, request_id='req-new'):
    return DecisionOutcome('accepted', 'ok', payload, {'id': request_id},
                           {'request_id': request_id, 'output_digest': DIGEST}, None)


def correlated(payload, request_id='req-new', prompt='synthetic'):
    """An accepted outcome that answers this request and this prompt."""

    return DecisionOutcome('accepted', 'ok', payload,
                           {'id': request_id, 'prompt_digest': prompt_digest(prompt)},
                           {'request_id': request_id, 'output_digest': DIGEST}, None)


def opened(source=None):
    return session.OperatorSession.open(session_id='session-a', mode='paper',
        source=source or FakeProposalSource(), clock=lambda: STAMP)


def decide(payload, **overrides):
    port = FixedPort(payload)
    args = dict(request_id='req-new', profile=DECISION_PROFILE['workload-d04'],
                mode='paper', prompt='synthetic')
    args.update(overrides)
    return RouteConsumer(port, binding=BINDING).decide(**args), port


def clear_contract_caches():
    pin.load_pin.cache_clear()
    pin.load_schema.cache_clear()
    pin._validator.cache_clear()


def stop_with_registry(root, *, source_bytes, path='source.json', derivation=None, value=1):
    """Run ``stop`` against a one-entry registry over ``source_bytes``; return (exit code, stopped)."""

    if source_bytes is not None:
        (root / 'source.json').write_bytes(source_bytes)
        sha = hashlib.sha256(source_bytes).hexdigest()
    else:
        sha = '0' * 64
    entry = projections.make_entry(projection_id='sample', label='Sample', value=value,
        unit='cells', repository=projections.VAULT_REPOSITORY, commit='a' * 40,
        path=path, sha256=sha, derivation=derivation or {'kind': 'value_at', 'pointer': ''})
    registry = root / 'registry.json'
    registry.write_text(json.dumps(projections.registry_document([entry])), encoding='utf-8')
    log = root / 'transcript.jsonl'
    cli.write_log(log, opened().records())
    code = cli.main(['--log', str(log), '--vault-root', str(root),
                     'stop', '--operator', 'operator-a', '--reason', 'done'],
                    registry_path=registry, out=io.StringIO(), clock=lambda: STAMP)
    restored = session.OperatorSession.replay(cli.read_log(log), source=FakeProposalSource())
    return code, restored


def received_chain(payload):
    """A hash-correct transcript whose one proposal carries ``payload`` as is."""

    chain = audit.AuditChain()
    chain.append('session_opened', {'session_id': 'session-a', 'mode': 'paper',
                                    'adapter_kind': 'fake', 'at': STAMP})
    chain.append('proposal_requested', {'request_id': 'req-new',
                                        'prompt_digest': prompt_digest('synthetic'), 'at': STAMP})
    chain.append('proposal_received', {
        'request_id': 'req-new', 'proposal_id': payload['proposal_id'], 'payload': payload,
        'payload_digest': canonical_digest(payload), 'reply_output_digest': DIGEST, 'at': STAMP})
    return chain.records()


class Pb5Regressions(unittest.TestCase):
    def test_f01_stop_survives_malformed_projection_derivation(self):
        with tempfile.TemporaryDirectory(prefix='pb5-stop-') as directory:
            root = Path(directory)
            source = root / 'source.json'
            source.write_text('{"value": 1}', encoding='utf-8')
            entry = projections.make_entry(projection_id='sample', label='Sample', value=1,
                unit='cells', repository=projections.VAULT_REPOSITORY, commit='a' * 40,
                path='source.json', sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                derivation={'kind': 'value_at', 'pointer': None})
            registry = root / 'registry.json'
            registry.write_text(json.dumps(projections.registry_document([entry])), encoding='utf-8')
            log = root / 'transcript.jsonl'
            cli.write_log(log, opened().records())
            code = cli.main(['--log', str(log), '--vault-root', str(root),
                             'stop', '--operator', 'operator-a', '--reason', 'done'],
                            registry_path=registry, out=io.StringIO(), clock=lambda: STAMP)
            self.assertEqual(cli.EXIT_OK, code)
            restored = session.OperatorSession.replay(cli.read_log(log), source=FakeProposalSource())
            self.assertTrue(restored.stopped)
            self.assertEqual('session_stopped', restored.records()[-1]['kind'])

    def test_f02_source_outcome_must_answer_current_request(self):
        s = opened(FixedSource(accepted(inline_payload('old'), 'req-old')))
        result = s.request_proposal(request_id='req-new', prompt='new question')
        self.assertFalse(result.ok)
        self.assertEqual([], s.proposals())
        self.assertEqual('proposal_refused', s.records()[-1]['kind'])
        self.assertEqual('watch_only', s.records()[-1]['data']['fail_closed_to'])

    def test_f03_consumer_refuses_nonfinite_sizes(self):
        for field in ('quantity', 'notional'):
            for value in (float('nan'), float('inf'), -float('inf')):
                with self.subTest(field=field, value=str(value)):
                    payload = inline_payload('size')
                    payload[field] = value
                    result, _ = decide(payload)
                    self.assertFalse(result.usable)
                    self.assertIsNone(result.payload)

    def test_f03_session_refuses_nonfinite_sizes_independently(self):
        for field in ('quantity', 'notional'):
            for value in (float('nan'), float('inf'), -float('inf')):
                with self.subTest(field=field, value=str(value)):
                    payload = inline_payload('size')
                    payload[field] = value
                    s = opened(FixedSource(accepted(payload)))
                    result = s.request_proposal(request_id='req-new', prompt='synthetic')
                    self.assertFalse(result.ok)
                    self.assertEqual([], s.proposals())

    def test_f04_contract_file_failures_are_closed_outcomes(self):
        original = pin.PIN_PATH.read_text(encoding='utf-8')
        for defect in ('missing-schema', 'cut-pin', 'missing-vendored-key'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory(prefix='s6-pin-') as directory:
                folder = Path(directory)
                pin_path = folder / 'PIN.json'
                pin_path.write_text('{' if defect == 'cut-pin' else
                                    '{}' if defect == 'missing-vendored-key' else original,
                                    encoding='utf-8')
                clear_contract_caches()
                try:
                    with mock.patch.object(pin, 'PACKAGE_DIR', folder), mock.patch.object(pin, 'PIN_PATH', pin_path):
                        result, port = decide(inline_payload('broken'))
                        self.assertEqual('fail_closed', result.disposition)
                        self.assertEqual('contract_drift', result.reason)
                        self.assertEqual(0, port.submissions)
                finally:
                    clear_contract_caches()

    # S3: the witness the verbatim session test cannot be. Its outcome is
    # uncorrelated, so it is refused for that reason; here everything but the
    # size is right.
    def test_s3_correlated_nonfinite_size_is_quarantined_as_invalid_payload(self):
        for field in ('quantity', 'notional'):
            for value in (float('nan'), float('inf'), -float('inf')):
                with self.subTest(field=field, value=str(value)):
                    payload = inline_payload('size')
                    payload[field] = value
                    s = opened(FixedSource(correlated(payload)))
                    result = s.request_proposal(request_id='req-new', prompt='synthetic')
                    self.assertFalse(result.ok)
                    self.assertEqual(session.PAYLOAD_INVALID, result.code)
                    self.assertEqual([], s.proposals())
                    self.assertTrue(s.records()[-1]['data']['quarantined'])

    def test_s3_a_correlated_finite_outcome_is_still_received(self):
        s = opened(FixedSource(correlated(inline_payload('size'))))
        result = s.request_proposal(request_id='req-new', prompt='synthetic')
        self.assertTrue(result.ok)
        self.assertEqual(1, len(s.proposals()))

    def test_s3_replay_refuses_what_live_refuses(self):
        payload = inline_payload('size')
        self.assertEqual(1, len(session.OperatorSession.replay(
            received_chain(copy.deepcopy(payload)), source=FakeProposalSource()).proposals()))
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=str(value)):
                bad = copy.deepcopy(payload)
                bad['quantity'] = value
                with self.assertRaises(audit.AuditError):
                    session.OperatorSession.replay(received_chain(bad), source=FakeProposalSource())

    # S2: every identity the outcome carries must be this request's.
    def assert_refused_to_watch_only(self, s, result):
        self.assertFalse(result.ok)
        self.assertEqual([], s.proposals())
        self.assertEqual('proposal_refused', s.records()[-1]['kind'])
        self.assertEqual('watch_only', s.records()[-1]['data']['fail_closed_to'])

    def test_s2_same_request_id_but_other_prompt_is_refused(self):
        s = opened(FixedSource(correlated(inline_payload('x'), prompt='another question')))
        result = s.request_proposal(request_id='req-new', prompt='synthetic')
        self.assert_refused_to_watch_only(s, result)

    def test_s2_reply_for_another_request_is_refused(self):
        outcome = correlated(inline_payload('x'))
        outcome = DecisionOutcome(outcome.disposition, outcome.reason, outcome.payload,
            outcome.request_record, {'request_id': 'req-old', 'output_digest': DIGEST}, None)
        s = opened(FixedSource(outcome))
        self.assert_refused_to_watch_only(s, s.request_proposal(request_id='req-new', prompt='synthetic'))

    def test_s2_reply_without_a_request_id_is_refused(self):
        outcome = correlated(inline_payload('x'))
        outcome = DecisionOutcome(outcome.disposition, outcome.reason, outcome.payload,
            outcome.request_record, {'output_digest': DIGEST}, None)
        s = opened(FixedSource(outcome))
        self.assert_refused_to_watch_only(s, s.request_proposal(request_id='req-new', prompt='synthetic'))

    def test_s2_missing_request_record_is_refused(self):
        outcome = correlated(inline_payload('x'))
        outcome = DecisionOutcome(outcome.disposition, outcome.reason, outcome.payload,
            None, outcome.reply_record, None)
        s = opened(FixedSource(outcome))
        self.assert_refused_to_watch_only(s, s.request_proposal(request_id='req-new', prompt='synthetic'))

    def test_s2_request_record_without_a_prompt_digest_is_refused(self):
        s = opened(FixedSource(accepted(inline_payload('x'))))
        self.assert_refused_to_watch_only(s, s.request_proposal(request_id='req-new', prompt='synthetic'))

    # S1: a malformed registry never costs the operator the stop.
    def test_s1_pointer_that_is_not_text_is_a_projection_error(self):
        for pointer in (None, 0, [], {}):
            with self.subTest(pointer=repr(pointer)):
                with self.assertRaises(projections.ProjectionError):
                    projections.derive({'value': 1}, {'kind': 'value_at', 'pointer': pointer})

    def test_s1_stop_survives_nonsense_pointers(self):
        for pointer in (0, [], {}):
            with self.subTest(pointer=repr(pointer)), tempfile.TemporaryDirectory(prefix='pb5-ptr-') as d:
                code, restored = stop_with_registry(Path(d), source_bytes=b'{"value": 1}',
                    derivation={'kind': 'value_at', 'pointer': pointer})
                self.assertEqual(cli.EXIT_OK, code)
                self.assertTrue(restored.stopped)

    def test_s1_stop_survives_a_nul_in_a_source_path(self):
        with tempfile.TemporaryDirectory(prefix='pb5-nul-') as d:
            code, restored = stop_with_registry(Path(d), source_bytes=None, path='a\x00b.json')
            self.assertEqual(cli.EXIT_OK, code)
            self.assertTrue(restored.stopped)

    def test_s1_stop_survives_a_deeply_nested_source(self):
        deep = b'[' * 100000 + b']' * 100000
        with tempfile.TemporaryDirectory(prefix='pb5-deep-') as d:
            code, restored = stop_with_registry(Path(d), source_bytes=deep)
            self.assertEqual(cli.EXIT_OK, code)
            self.assertTrue(restored.stopped)
            self.assertEqual('session_stopped', restored.records()[-1]['kind'])

    # S4: every reader of the contract pin fails with the pin's own error.
    def test_s4_malformed_contract_pin_is_a_pin_error_and_a_closed_outcome(self):
        real = json.loads(pin.PIN_PATH.read_text(encoding='utf-8'))

        def altered(change):
            document = copy.deepcopy(real)
            change(document)
            return json.dumps(document)

        def own_schema(text):
            def change(document):
                document['vendored'][pin.SCHEMA_KEY]['sha256'] = hashlib.sha256(text.encode()).hexdigest()
            return change, text

        schema_without_defs = own_schema('{}')
        schema_unparseable = own_schema('not json')
        cases = {
            'pin-absent': (None, None, pin.pinned_head),
            'vendored-is-a-list': (altered(lambda d: d.__setitem__('vendored', [])), None,
                                   lambda: pin.read_vendored(pin.SCHEMA_KEY)),
            'entry-without-sha256': (altered(lambda d: d['vendored'][pin.SCHEMA_KEY].pop('sha256')), None,
                                     lambda: pin.read_vendored(pin.SCHEMA_KEY)),
            'no-head-sha': (altered(lambda d: d['source'].pop('head_sha')), None, pin.pinned_head),
            'repository-not-text': (altered(lambda d: d['source'].__setitem__('repository', 7)), None,
                                    pin.pinned_head),
            'pull-request-is-text': (altered(lambda d: d['source'].__setitem__('pull_request', '341')), None,
                                     pin.pinned_head),
            'pull-request-is-bool': (altered(lambda d: d['source'].__setitem__('pull_request', True)), None,
                                     pin.pinned_head),
            'schema-without-defs': (altered(schema_without_defs[0]), schema_without_defs[1], pin.schema_defs),
            'schema-unparseable': (altered(schema_unparseable[0]), schema_unparseable[1], pin.schema_defs),
        }
        for name, (pin_text, schema_text, probe) in cases.items():
            with self.subTest(defect=name), tempfile.TemporaryDirectory(prefix='pb5-pin-') as directory:
                folder = Path(directory)
                shutil.copytree(pin.PACKAGE_DIR / 'contract', folder / 'contract')
                pin_path = folder / 'PIN.json'
                if pin_text is not None:
                    pin_path.write_text(pin_text, encoding='utf-8')
                if schema_text is not None:
                    (folder / pin.SCHEMA_KEY).write_text(schema_text, encoding='utf-8')
                clear_contract_caches()
                try:
                    with mock.patch.object(pin, 'PACKAGE_DIR', folder), mock.patch.object(pin, 'PIN_PATH', pin_path):
                        with self.assertRaises(pin.PinError):
                            probe()
                        result, port = decide(inline_payload('broken'))
                        self.assertEqual('fail_closed', result.disposition)
                        self.assertEqual('contract_drift', result.reason)
                        self.assertEqual(0, port.submissions)
                finally:
                    clear_contract_caches()

    # Answer to the first read: the identities are compared as they are.
    def test_s2_request_record_for_another_request_id_is_refused(self):
        outcome = correlated(inline_payload('x'))
        outcome = DecisionOutcome(outcome.disposition, outcome.reason, outcome.payload,
            {'id': 'req-old', 'prompt_digest': prompt_digest('synthetic')}, outcome.reply_record, None)
        s = opened(FixedSource(outcome))
        self.assert_refused_to_watch_only(s, s.request_proposal(request_id='req-new', prompt='synthetic'))

    def test_s2_ids_and_digests_are_compared_exactly_not_loosely(self):
        right = prompt_digest('synthetic')
        cases = {
            'digest-in-upper-case': ('req-new', {'id': 'req-new', 'prompt_digest': right.upper()},
                                     {'request_id': 'req-new', 'output_digest': DIGEST}),
            'request-record-id-an-int': ('123', {'id': 123, 'prompt_digest': right},
                                         {'request_id': '123', 'output_digest': DIGEST}),
            'reply-request-id-an-int': ('123', {'id': '123', 'prompt_digest': right},
                                        {'request_id': 123, 'output_digest': DIGEST}),
        }
        for name, (request_id, request_record, reply_record) in cases.items():
            with self.subTest(case=name):
                outcome = DecisionOutcome('accepted', 'ok', inline_payload('x'), request_record, reply_record, None)
                s = opened(FixedSource(outcome))
                self.assert_refused_to_watch_only(
                    s, s.request_proposal(request_id=request_id, prompt='synthetic'))

    # S3: a string JSON can write but UTF-8 cannot is not strict JSON either.
    def test_s3_a_lone_surrogate_is_quarantined_not_a_source_error(self):
        payload = inline_payload('size')
        payload['thesis'] = 'broken \ud800 text'
        self.assertFalse(is_strict_json(payload))
        result, _ = decide(copy.deepcopy(payload))
        self.assertEqual('needs_human_review', result.disposition)
        self.assertEqual('consumer_revalidation_failed', result.reason)
        s = opened(FixedSource(correlated(payload)))
        result = s.request_proposal(request_id='req-new', prompt='synthetic')
        self.assertFalse(result.ok)
        self.assertEqual(session.PAYLOAD_INVALID, result.code)
        self.assertEqual([], s.proposals())
        self.assertTrue(s.records()[-1]['data']['quarantined'])

    # S1: the public readers and the loader's mapping.
    def test_s1_read_cited_bytes_refuses_a_nul_path_with_a_projection_error(self):
        with tempfile.TemporaryDirectory(prefix='pb5-cited-') as d:
            for repository in ('owner/repo', projections.VAULT_REPOSITORY):
                with self.subTest(repository=repository), self.assertRaises(projections.ProjectionError):
                    projections.read_cited_bytes(repository, 'a\x00b.json', root=Path(d), vault_root=Path(d))

    def test_s1_a_symlink_loop_in_a_source_path_is_a_projection_error(self):
        with tempfile.TemporaryDirectory(prefix='pb5-loop-') as d:
            root = Path(d)
            try:
                os.symlink('loop-b', root / 'loop-a')
                os.symlink('loop-a', root / 'loop-b')
            except OSError:
                self.skipTest('symlinks are not available')
            with self.assertRaises(projections.ProjectionError):
                projections.read_cited_bytes('owner/repo', 'loop-a', root=root)

    def test_s1_a_value_utf8_cannot_encode_is_a_projection_error(self):
        entry = projections.make_entry(projection_id='sample', label='Sample', value=1, unit='cells',
            repository=projections.VAULT_REPOSITORY, commit='a' * 40, path='source.json',
            sha256='0' * 64, derivation={'kind': 'value_at', 'pointer': ''})
        document = projections.registry_document([entry])
        document['projections'][0]['label'] = 'broken \ud800'
        with tempfile.TemporaryDirectory(prefix='pb5-utf8-') as d:
            registry = Path(d) / 'registry.json'
            registry.write_text(json.dumps(document), encoding='utf-8')
            with self.assertRaises(projections.ProjectionError):
                projections.load_registry(registry, root=Path(d), vault_root=Path(d))

    def test_s1_a_root_that_is_not_a_path_is_a_projection_error(self):
        entry = projections.make_entry(projection_id='sample', label='Sample', value=1, unit='cells',
            repository=projections.VAULT_REPOSITORY, commit='a' * 40, path='source.json',
            sha256='0' * 64, derivation={'kind': 'value_at', 'pointer': ''})
        with tempfile.TemporaryDirectory(prefix='pb5-root-') as d:
            registry = Path(d) / 'registry.json'
            registry.write_text(json.dumps(projections.registry_document([entry])), encoding='utf-8')
            with self.assertRaises(projections.ProjectionError):
                projections.load_registry(registry, root=5, vault_root=Path(d))


if __name__ == '__main__':
    unittest.main(verbosity=2)
