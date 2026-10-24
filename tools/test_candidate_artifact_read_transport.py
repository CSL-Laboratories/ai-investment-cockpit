"""Offline tests for the bounded injected read transport (CSL-259 follow-up).

Everything is synthetic. The injected client is an in-memory fake that serves JSON bytes
and the produced ZIP over the neutral fixture through fake streams; the real consumer
(``candidate_artifact_adapters.consume``) and validator run unchanged on top. A passing
suite proves bounded, fail-closed mechanics against these fakes only: it observes no
GitHub behaviour, authenticates nothing and approves no activation.

Client protocol under test (see the module docstring):
    client.open(operation, args) -> (status, stream); stream.read(size); stream.close()

Run: python -X utf8 -B tools/test_candidate_artifact_read_transport.py
"""
from __future__ import annotations

import ast
import importlib
import json
import sys
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
base = importlib.import_module('test_candidate_artifact_adapters')  # also puts scripts/ci on sys.path
aj, ad = base.aj, base.ad
rt = importlib.import_module('candidate_artifact_read_transport')

POLICY = base.POLICY
REPO = POLICY['repository']
WORKFLOW_ID = POLICY['workflow_id']
SNAPSHOT = base.FIXTURE['snapshot']
RUN_ID = SNAPSHOT['run']['id']
ARTIFACT_ID = next(m['id'] for m in SNAPSHOT['artifacts'] if m['name'] == POLICY['artifact_name'])
INVALID = 'invalid read arguments'
METADATA_CALLS = (('get_ref', (REPO, 'main')), ('list_workflow_runs', (REPO, WORKFLOW_ID, 1)),
                  ('get_workflow_run', (REPO, RUN_ID)), ('list_artifacts', (REPO, RUN_ID, 1)))


def json_bytes(value):
    return json.dumps(value).encode('utf-8')


class BytesSub(bytes):
    pass


class FakeStream:
    """Readable binary stream over fixed bytes. Records requested read sizes, any
    unbounded read or iteration (``violations``) and ``close``. ``chunk`` trickles at
    most that many bytes per read; ``claims`` are attributes a client might attach
    (lengths, headers, trust flags) that the transport must never consult."""

    def __init__(self, data, chunk=None, **claims):
        self.data, self.pos, self.chunk = data, 0, chunk
        self.reads, self.violations, self.closed = [], [], False
        self.__dict__.update(claims)

    def read(self, size=-1):
        if not (type(size) is int and size >= 0):
            self.violations.append(('read', size))
            size = len(self.data)
        self.reads.append(size)
        size = size if self.chunk is None else min(size, self.chunk)
        out = self.data[self.pos:self.pos + size]
        self.pos += len(out)
        return out

    def __iter__(self):
        self.violations.append('iter')
        return iter([self.data])

    def close(self):
        self.closed = True


class InfiniteStream(FakeStream):
    """Never reaches EOF: every bounded read is filled. ``delivered`` counts bytes handed out."""

    def __init__(self, **claims):
        super().__init__(b'', **claims)
        self.delivered = 0

    def read(self, size=-1):
        if not (type(size) is int and size >= 0):
            self.violations.append(('read', size))
            size = 1 << 20
        self.reads.append(size)
        self.delivered += size
        return b'\x00' * size


class ScriptedStream(FakeStream):
    """Every read returns ``script(size)``: wrong types, oversized chunks or exceptions."""

    def __init__(self, script):
        super().__init__(b'')
        self.script = script

    def read(self, size=-1):
        self.reads.append(size)
        return self.script(size)


class CloseFails(FakeStream):
    def close(self):
        super().close()
        raise OSError('close failed')


class FakeClient:
    """Injected read client over the synthetic REST state of a ``base.Scenario``.

    ``open(operation, args)`` serves metadata as JSON bytes in the module's payload grammar
    and the produced archive for downloads, each through a fresh tracked FakeStream.
    ``replies[operation]`` (args -> reply) overrides one operation; ``on_download`` mutates
    the state after the download, like the reference fake transport."""

    def __init__(self, state, archive):
        self.state, self.archive, self.page_size, self.chunk = state, archive, 100, None
        self.opened, self.streams, self.replies, self.on_download = [], [], {}, None

    def track(self, stream):
        self.streams.append(stream)
        return stream

    def stream(self, data, **claims):
        return self.track(FakeStream(data, **claims))

    def open(self, operation, args):
        self.opened.append((operation, args))
        if operation in self.replies:
            return self.replies[operation](args)
        reply = (200, self.track(FakeStream(self.body(operation, args), chunk=self.chunk)))
        if operation == 'download_artifact' and self.on_download is not None:
            self.on_download(self.state)
        return reply

    def body(self, operation, args):
        if operation == 'download_artifact':
            return self.archive
        if operation == 'get_ref':
            return json_bytes({'ref': 'refs/heads/' + args[1], 'node_id': 'MDM6',
                               'object': {'type': 'commit', 'sha': self.state['ref']}})
        if operation == 'get_workflow_run':
            return json_bytes(self.state['run_by_id'].get(args[1], {}))
        items = self.state['runs'] if operation == 'list_workflow_runs' else self.state['artifacts'].get(args[1], [])
        start = (args[2] - 1) * self.page_size
        return json_bytes({'items': items[start:start + self.page_size],
                           'has_next': start + self.page_size < len(items)})

    def operations(self):
        return [operation for operation, _ in self.opened]

    def reset(self):
        self.opened.clear()
        self.replies.clear()


class Case(unittest.TestCase):
    def setUp(self):
        self.scenario = base.Scenario()
        self.client = FakeClient(self.scenario.state, self.scenario.archive)
        self.transport = rt.ReadTransport(self.client)

    def tearDown(self):
        self.assertStreamsClosed()

    def assertStreamsClosed(self):
        # Every stream handed to the transport was closed and only ever read with a bounded size.
        for stream in self.client.streams:
            self.assertTrue(stream.closed, 'stream left open')
            self.assertEqual(stream.violations, [])

    def fresh(self):
        """New scenario and client, after checking the old client's streams."""
        self.assertStreamsClosed()
        self.setUp()

    def consume(self):
        s = self.scenario
        return ad.consume(self.transport, s.policy, s.candidate, s.previous, base.BEFORE_AT, base.AFTER_AT)

    def serve(self, operation, data, status=200, **claims):
        self.client.replies[operation] = lambda _args: (status, self.client.stream(data, **claims))

    def assertUnavailable(self, reply, reason):
        self.assertEqual(reply, {'unavailable': reason})
        self.assertIsNotNone(aj.REASON.fullmatch(reason), reason)


class PublicMethodTests(Case):
    def test_surface_is_exactly_the_adapter_read_methods(self):
        public = sorted(name for name in dir(rt.ReadTransport) if not name.startswith('_'))
        self.assertEqual(public, sorted(ad.READ_METHODS))

    def test_each_method_reads_through_one_open_with_validated_arguments(self):
        t, archive = self.transport, self.scenario.archive
        self.assertEqual(t.get_ref(REPO, 'main'), {'ok': POLICY['policy_commit']})
        runs = t.list_workflow_runs(REPO, WORKFLOW_ID, 1)['ok']
        self.assertEqual(([r['id'] for r in runs['items']], runs['has_next']),
                         ([r['id'] for r in SNAPSHOT['workflow_runs']], False))
        self.assertEqual(t.get_workflow_run(REPO, RUN_ID), {'ok': self.scenario.state['run_by_id'][RUN_ID]})
        self.assertEqual(t.list_artifacts(REPO, RUN_ID, 1),
                         {'ok': {'items': self.scenario.state['artifacts'][RUN_ID], 'has_next': False}})
        self.assertEqual(t.download_artifact(REPO, ARTIFACT_ID, len(archive)), {'ok': archive})
        self.assertEqual(self.client.opened, [
            ('get_ref', (REPO, 'main')), ('list_workflow_runs', (REPO, WORKFLOW_ID, 1)),
            ('get_workflow_run', (REPO, RUN_ID)), ('list_artifacts', (REPO, RUN_ID, 1)),
            ('download_artifact', (REPO, ARTIFACT_ID, len(archive)))])

    def test_boundary_arguments_reach_the_client(self):
        self.assertEqual(self.transport.list_artifacts(REPO, RUN_ID, ad.MAX_LIST_PAGES),
                         {'ok': {'items': [], 'has_next': False}})
        self.assertUnavailable(self.transport.get_workflow_run(REPO, rt.MAX_ID), 'run payload malformed')
        self.assertUnavailable(self.transport.download_artifact(REPO, ARTIFACT_ID, aj.MAX_ARCHIVE_CEILING),
                               'stream ended short of the authenticated size')
        self.assertEqual(len(self.client.opened), 3)

    def test_invalid_arguments_never_reach_the_client(self):
        bad_repos = ['', 'example-org', 'a/b/c', '../x', 'x/..', './x', 'a b/c', 'a/b\n', 1, None, b'a/b']
        bad_branches = ['', '..', 'a..b', 'a//b', 'main/', 'main.lock', '-main', 7, None]
        bad_ids = [0, -1, True, False, 1.0, '1', None, rt.MAX_ID + 1]
        bad_pages = [0, -1, ad.MAX_LIST_PAGES + 1, True, 1.0, '1', None]
        bad_caps = [0, -1, True, 1.5, '1', None, aj.MAX_ARCHIVE_CEILING + 1]
        calls = []
        for r in bad_repos:
            calls += [('get_ref', (r, 'main')), ('list_workflow_runs', (r, WORKFLOW_ID, 1)),
                      ('get_workflow_run', (r, RUN_ID)), ('list_artifacts', (r, RUN_ID, 1)),
                      ('download_artifact', (r, ARTIFACT_ID, 10))]
        calls += [('get_ref', (REPO, b)) for b in bad_branches]
        for i in bad_ids:
            calls += [('list_workflow_runs', (REPO, i, 1)), ('get_workflow_run', (REPO, i)),
                      ('list_artifacts', (REPO, i, 1)), ('download_artifact', (REPO, i, 10))]
        for p in bad_pages:
            calls += [('list_workflow_runs', (REPO, WORKFLOW_ID, p)), ('list_artifacts', (REPO, RUN_ID, p))]
        calls += [('download_artifact', (REPO, ARTIFACT_ID, c)) for c in bad_caps]
        for name, args in calls:
            with self.subTest(name=name, args=args):
                self.assertUnavailable(getattr(self.transport, name)(*args), INVALID)
        self.assertEqual(self.client.opened, [])

    def test_metadata_cap_can_only_be_tightened(self):
        for cap in (0, -1, True, 1.5, '1', None, rt.MAX_METADATA_BYTES + 1):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                rt.ReadTransport(self.client, max_metadata_bytes=cap)
        self.assertEqual(self.client.opened, [])


class ConsumerThroughTransportTests(Case):
    def test_happy_path_matches_the_reference_transport(self):
        result = self.consume()
        self.assertEqual(result, self.scenario.consume())
        for key, value in base.FIXTURE['expected'].items():
            self.assertEqual(result[key], value, key)
        self.assertEqual(self.client.operations(), base.FULL_SEQUENCE)
        size = len(self.scenario.archive)
        self.assertIn(('download_artifact', (REPO, ARTIFACT_ID, size)), self.client.opened)
        download = self.client.streams[base.FULL_SEQUENCE.index('download_artifact')]
        self.assertLessEqual(sum(download.reads), size + 1)

    def test_trickling_streams_still_validate(self):
        self.client.chunk = 7
        self.assertTrue(self.consume()['diagnostic_valid'])
        self.assertTrue(all(len(s.reads) > 2 for s in self.client.streams))

    def test_full_pagination_reads_every_page_and_never_infers_completion(self):
        self.client.page_size = 1
        self.assertTrue(self.consume()['diagnostic_valid'])
        pages = [(name, args[2]) for name, args in self.client.opened if name.startswith('list_')]
        self.assertEqual(pages, [('list_workflow_runs', 1), ('list_workflow_runs', 2),
                                 ('list_artifacts', 1), ('list_artifacts', 2)] * 2)

    def test_page_cap_exhaustion_is_incomplete_and_never_downloads(self):
        endless = (('list_workflow_runs', (REPO, WORKFLOW_ID), SNAPSHOT['workflow_runs'][1], 300000100),
                   ('list_artifacts', (REPO, RUN_ID), SNAPSHOT['artifacts'][1], 400000100))
        for operation, prefix, template, first_id in endless:
            with self.subTest(operation):
                self.fresh()
                self.client.replies[operation] = lambda args, template=template, first_id=first_id: (
                    200, self.client.stream(json_bytes({'items': [base.noisy({**template, 'id': first_id + args[2]})],
                                                        'has_next': True})))
                with self.assertRaisesRegex(aj.ProvenanceError, 'incomplete bounded listing'):
                    self.consume()
                pages = [args[2] for name, args in self.client.opened if name == operation]
                self.assertEqual(pages, list(range(1, ad.MAX_LIST_PAGES + 1)))
                self.assertNotIn('download_artifact', self.client.operations())
                opened = len(self.client.opened)
                self.assertUnavailable(getattr(self.transport, operation)(*prefix, ad.MAX_LIST_PAGES + 1), INVALID)
                self.assertEqual(len(self.client.opened), opened)

    def test_cross_page_duplicate_is_rejected_by_the_consumer(self):
        run = self.scenario.state['runs'][0]
        self.client.replies['list_workflow_runs'] = lambda args: (
            200, self.client.stream(json_bytes({'items': [run], 'has_next': args[2] == 1})))
        with self.assertRaisesRegex(aj.ProvenanceError, 'duplicate or malformed listing identity'):
            self.consume()
        self.assertNotIn('download_artifact', self.client.operations())

    def test_malformed_or_unpaginated_listing_never_reaches_download(self):
        items = self.scenario.state['artifacts'][RUN_ID]
        for body, reason in (({'items': items}, 'listing page malformed'),
                             ({'items': items, 'has_next': None}, 'listing page malformed'),
                             ({'items': [], 'has_next': True}, 'listing page empty continuation'),
                             ({'items': items + items, 'has_next': False},
                              'listing page item identity malformed or duplicate')):
            with self.subTest(reason=reason):
                self.client.reset()
                self.serve('list_artifacts', json_bytes(body))
                with self.assertRaisesRegex(aj.ProvenanceError, 'evidence unavailable: ' + reason):
                    self.consume()
                self.assertEqual(self.client.operations(), base.FULL_SEQUENCE[:4])

    def test_expired_or_oversized_artifact_is_rejected_before_download(self):
        for change, reason in (({'expired': True}, 'artifact: expired'),
                               ({'expires_at': '2026-10-16T11:59:00Z'}, 'expired or inconsistent timestamps'),
                               ({'size_in_bytes': POLICY['bounds']['max_archive_bytes'] + 1}, 'size out of bounds')):
            with self.subTest(reason=reason):
                self.fresh()
                self.scenario.journal_artifact().update(change)
                with self.assertRaisesRegex(aj.ProvenanceError, reason):
                    self.consume()
                self.assertEqual(self.client.operations(), base.FULL_SEQUENCE[:4])

    def test_snapshot_moving_after_download_is_rejected_after_one_download(self):
        moves = ((lambda state: state.update(ref=base.OTHER_SHA), 'default branch moved'),
                 (lambda state: self.scenario.journal_artifact().update(updated_at='2026-10-16T11:59:00Z'),
                  'snapshot moved between the before and after observations'))
        for move, reason in moves:
            with self.subTest(reason=reason):
                self.fresh()
                self.client.on_download = move
                with self.assertRaisesRegex(aj.ProvenanceError, reason):
                    self.consume()
                self.assertEqual(self.client.operations().count('download_artifact'), 1)
                self.assertEqual(self.client.operations()[:5], base.FULL_SEQUENCE[:5])

    def test_every_method_blocks_on_unavailable_status_without_retry(self):
        prefixes = {'download_artifact': 'download '}
        for status in (403, 404, 410, 429, 500):
            for index, operation in enumerate(base.FULL_SEQUENCE[:5]):
                with self.subTest(status=status, operation=operation):
                    self.client.reset()
                    self.serve(operation, b'{}', status=status)
                    pattern = 'evidence unavailable: ' + prefixes.get(operation, '') + f'http status {status}'
                    with self.assertRaisesRegex(aj.ProvenanceError, pattern):
                        self.consume()
                    self.assertEqual(self.client.operations(), base.FULL_SEQUENCE[:index + 1])

    def test_download_cap_is_the_authenticated_size_not_any_stream_claim(self):
        archive, size = self.scenario.archive, len(self.scenario.archive)
        cases = {
            'endless stream claiming the right length': lambda args: (
                200, self.client.track(InfiniteStream(length=args[2], headers={'Content-Length': str(args[2])}))),
            'one trailing byte under an honest-looking length': lambda args: (
                200, self.client.stream(archive + b'\x00', length=size, authenticated=True)),
            'inflated length claim with an extra tail': lambda args: (
                200, self.client.stream(archive + b'PK' * 1000, length=10 ** 9)),
        }
        for label, reply in cases.items():
            with self.subTest(label):
                self.client.reset()
                self.client.replies['download_artifact'] = reply
                with self.assertRaisesRegex(aj.ProvenanceError, 'download stream exceeds the byte cap'):
                    self.consume()
                self.assertEqual(self.client.operations(), base.FULL_SEQUENCE[:5])
                self.assertEqual(sum(self.client.streams[-1].reads), size + 1)

    def test_cap_follows_authenticated_metadata_in_both_directions(self):
        size = len(self.scenario.archive)
        for claimed, reason in ((size - 1, 'download stream exceeds the byte cap'),
                                (size + 1, 'download stream ended short of the authenticated size')):
            with self.subTest(claimed=claimed):
                self.client.reset()
                self.scenario.journal_artifact()['size_in_bytes'] = claimed
                with self.assertRaisesRegex(aj.ProvenanceError, reason):
                    self.consume()
                self.assertIn(('download_artifact', (REPO, ARTIFACT_ID, claimed)), self.client.opened)
                self.assertEqual(self.client.operations(), base.FULL_SEQUENCE[:5])
                self.assertLessEqual(self.client.streams[-1].pos, claimed + 1)

    def test_short_or_empty_download_is_unavailable(self):
        for body in (self.scenario.archive[:-1], b''):
            with self.subTest(length=len(body)):
                self.client.reset()
                self.serve('download_artifact', body)
                with self.assertRaisesRegex(aj.ProvenanceError, 'download stream ended short of the authenticated size'):
                    self.consume()
                self.assertEqual(self.client.operations(), base.FULL_SEQUENCE[:5])

    def test_stream_claims_neither_rescue_nor_block_correct_bytes(self):
        self.serve('download_artifact', self.scenario.archive, length=10 ** 9,
                   headers={'Content-Length': '1'}, authenticated=False)
        self.assertTrue(self.consume()['diagnostic_valid'])


class StreamAndReplyTests(Case):
    def test_endless_download_is_stopped_after_the_cap_and_one_probe(self):
        cap = 3 * rt.READ_CHUNK + 5
        stream = self.client.track(InfiniteStream(length=1, headers={'Content-Length': '1'}, authenticated=True))
        self.client.replies['download_artifact'] = lambda _args: (200, stream)
        self.assertUnavailable(self.transport.download_artifact(REPO, ARTIFACT_ID, cap), 'stream exceeds the byte cap')
        self.assertEqual(stream.delivered, cap + 1)
        self.assertLessEqual(max(stream.reads), rt.READ_CHUNK)
        self.assertEqual(len(self.client.opened), 1)

    def test_endless_metadata_is_stopped_at_the_metadata_cap(self):
        for transport, cap in ((rt.ReadTransport(self.client, max_metadata_bytes=1000), 1000),
                               (self.transport, rt.MAX_METADATA_BYTES)):
            for name, args in METADATA_CALLS:
                with self.subTest(name=name, cap=cap):
                    stream = self.client.track(InfiniteStream())
                    self.client.replies[name] = lambda _args, stream=stream: (200, stream)
                    self.assertUnavailable(getattr(transport, name)(*args), 'stream exceeds the byte cap')
                    self.assertEqual(stream.delivered, cap + 1)

    def test_metadata_exactly_at_the_cap_is_read_and_one_byte_over_is_not(self):
        body = json_bytes(self.scenario.state['run_by_id'][RUN_ID])
        self.serve('get_workflow_run', body)
        exact = rt.ReadTransport(self.client, max_metadata_bytes=len(body))
        self.assertEqual(exact.get_workflow_run(REPO, RUN_ID)['ok']['id'], RUN_ID)
        tight = rt.ReadTransport(self.client, max_metadata_bytes=len(body) - 1)
        self.assertUnavailable(tight.get_workflow_run(REPO, RUN_ID), 'stream exceeds the byte cap')
        self.assertLessEqual(self.client.streams[-1].pos, len(body))

    def test_full_page_is_accepted_and_one_more_item_is_oversized(self):
        items = [{'id': i} for i in range(1, ad.MAX_PAGE_ITEMS + 2)]
        self.serve('list_artifacts', json_bytes({'items': items[:-1], 'has_next': False}))
        self.assertEqual(len(self.transport.list_artifacts(REPO, RUN_ID, 1)['ok']['items']), ad.MAX_PAGE_ITEMS)
        self.serve('list_artifacts', json_bytes({'items': items, 'has_next': False}))
        self.assertUnavailable(self.transport.list_artifacts(REPO, RUN_ID, 1), 'listing page oversized')

    def test_malformed_pages_are_unavailable_after_one_open(self):
        item = {'id': 1}
        cases = {
            'listing page malformed': [[], None, {'items': [item]}, {'has_next': False},
                                       {'items': [item], 'has_next': 0}, {'items': [item], 'has_next': 'false'},
                                       {'items': {}, 'has_next': False},
                                       {'items': [item], 'has_next': False, 'total_count': 1}],
            'listing page empty continuation': [{'items': [], 'has_next': True}],
            'listing page item identity malformed or duplicate': [
                {'items': [item, item], 'has_next': False}, {'items': [{'id': True}], 'has_next': False},
                {'items': [{}], 'has_next': False}, {'items': [[1]], 'has_next': False},
                {'items': [{'id': '1'}], 'has_next': True}],
        }
        for reason, bodies in cases.items():
            for body in bodies:
                for name, args in (('list_workflow_runs', (REPO, WORKFLOW_ID, 1)), ('list_artifacts', (REPO, RUN_ID, 1))):
                    with self.subTest(name=name, body=body):
                        self.client.reset()
                        self.serve(name, json_bytes(body))
                        self.assertUnavailable(getattr(self.transport, name)(*args), reason)
                        self.assertEqual(len(self.client.opened), 1)

    def test_oversized_wrong_type_and_failing_reads_are_unavailable(self):
        size = len(self.scenario.archive)

        def boom(_size):
            raise OSError('reset')

        cases = ((lambda n: b'x' * (n + 1), 'stream returned an oversized chunk'),
                 (lambda n: b'x' * n if n > 1 else b'yy', 'stream returned an oversized chunk'),
                 (lambda n: 'x' * n, 'stream returned non-bytes'),
                 (lambda n: None, 'stream returned non-bytes'),
                 (lambda n: memoryview(b'x'), 'stream returned non-bytes'),
                 (lambda n: [b'x'], 'stream returned non-bytes'),
                 (lambda n: BytesSub(b'x'), 'stream returned non-bytes'),
                 (lambda n: b'x' * n if n > 1 else 0, 'stream returned non-bytes'),
                 (boom, 'transport error: OSError'))
        for script, reason in cases:
            with self.subTest(reason=reason):
                self.client.reset()
                stream = self.client.track(ScriptedStream(script))
                self.client.replies['download_artifact'] = lambda _args, stream=stream: (200, stream)
                self.assertUnavailable(self.transport.download_artifact(REPO, ARTIFACT_ID, size), reason)
                self.assertEqual(len(self.client.opened), 1)

    def test_bytearray_chunks_are_accepted_and_returned_as_bytes(self):
        archive = self.scenario.archive
        self.serve('download_artifact', bytearray(archive), chunk=1000)
        reply = self.transport.download_artifact(REPO, ARTIFACT_ID, len(archive))
        self.assertIs(type(reply['ok']), bytes)
        self.assertEqual(reply['ok'], archive)

    def test_close_failure_and_client_errors_are_unavailable(self):
        stream = self.client.track(CloseFails(self.scenario.archive))
        self.client.replies['download_artifact'] = lambda _args: (200, stream)
        self.assertUnavailable(self.transport.download_artifact(REPO, ARTIFACT_ID, len(self.scenario.archive)),
                               'transport error: OSError')

        def refuse(_args):
            raise RuntimeError('boom')

        self.client.replies['get_ref'] = refuse
        self.assertUnavailable(self.transport.get_ref(REPO, 'main'), 'client error: RuntimeError')
        self.assertUnavailable(rt.ReadTransport(object()).get_ref(REPO, 'main'), 'client error: AttributeError')

    def test_non_200_or_malformed_status_is_unavailable_without_reading(self):
        size = len(self.scenario.archive)
        cases = [(s, f'http status {s}') for s in (403, 404, 410, 429, 500, 503, 201, 204, 206, 301, 302)]
        cases += [(s, 'client status malformed') for s in (True, False, '200', 200.0, None, 99, 600, -200)]
        for status, reason in cases:
            with self.subTest(status=status):
                self.serve('download_artifact', self.scenario.archive, status=status, authenticated=True)
                self.assertUnavailable(self.transport.download_artifact(REPO, ARTIFACT_ID, size), reason)
                self.assertEqual(self.client.streams[-1].reads, [])

    def test_unusual_exception_names_still_produce_valid_reasons(self):
        for name in ('\u041e\u0448\u0438\u0431\u043a\u0430', '<bad,error>'):
            error = type(name, (Exception,), {})

            def fail(*_args, error=error):
                raise error('synthetic failure')

            for operation in ('open', 'read', 'close'):
                with self.subTest(name=name, operation=operation):
                    self.client.reset()
                    if operation == 'open':
                        self.client.replies['download_artifact'] = fail
                    else:
                        stream = self.client.track(FakeStream(self.scenario.archive))
                        if operation == 'read':
                            stream.read = fail
                        else:
                            def close(stream=stream, error=error):
                                stream.closed = True
                                raise error('synthetic close failure')
                            stream.close = close
                        self.client.replies['download_artifact'] = lambda _args, stream=stream: (200, stream)
                    reason = 'client error' if operation == 'open' else 'transport error'
                    self.assertUnavailable(self.transport.download_artifact(
                        REPO, ARTIFACT_ID, len(self.scenario.archive)), reason)
                    self.assertEqual(len(self.client.opened), 1)

    def test_primary_size_failure_survives_a_failing_close(self):
        archive = self.scenario.archive
        for payload, reason in (
                (archive[:-1], 'stream ended short of the authenticated size'),
                (archive + b'x', 'stream exceeds the byte cap')):
            with self.subTest(reason=reason):
                self.client.reset()
                stream = self.client.track(CloseFails(payload))
                self.client.replies['download_artifact'] = lambda _args, stream=stream: (200, stream)
                self.assertUnavailable(self.transport.download_artifact(
                    REPO, ARTIFACT_ID, len(archive)), reason)
                self.assertTrue(stream.closed)
                self.assertEqual(len(self.client.opened), 1)

    def test_malformed_replies_and_streams_are_unavailable(self):
        loose = FakeStream(b'{}')
        for reply in (None, 200, [200, loose], (200,), (200, loose, 'extra'), {'status': 200, 'stream': loose},
                      (200, None)):
            with self.subTest(reply=reply):
                self.client.replies['get_ref'] = lambda _args, reply=reply: reply
                self.assertUnavailable(self.transport.get_ref(REPO, 'main'), 'client reply malformed')
        self.assertEqual(loose.reads, [])
        read_only = mock.Mock(spec=['read'])
        broken = self.client.stream(b'{}')
        broken.read = 'not callable'
        for stream in (b'{}', object(), read_only, broken):
            with self.subTest(stream=stream):
                self.client.replies['get_ref'] = lambda _args, stream=stream: (200, stream)
                self.assertUnavailable(self.transport.get_ref(REPO, 'main'), 'client stream malformed')
        read_only.read.assert_not_called()

    def test_metadata_json_is_strict(self):
        cases = {
            'metadata duplicate JSON key': [b'{"id": 1, "id": 1}', b'{"a": {"b": 1, "b": 2}, "id": 1}'],
            'metadata non-finite JSON number': [b'{"id": NaN}', b'{"x": Infinity}', b'{"x": -Infinity}',
                                                b'{"x": 1e999}', b'{"x": [-1e400]}'],
            'metadata JSON malformed': [b'', b'{', b'{"id": 1} {}', b"{'id': 1}", b'\xff\xfe{}',
                                        b'\xef\xbb\xbf{}', b'[' * 100000],
        }
        for reason, bodies in cases.items():
            for body in bodies:
                with self.subTest(body=body[:24]):
                    self.client.reset()
                    self.serve('get_workflow_run', body)
                    self.assertUnavailable(self.transport.get_workflow_run(REPO, RUN_ID), reason)
                    self.assertEqual(len(self.client.opened), 1)

    def test_well_formed_metadata_nesting_has_an_explicit_bound(self):
        for depth in (aj.MAX_JSON_DEPTH, aj.MAX_JSON_DEPTH + 1):
            with self.subTest(depth=depth):
                nested = 0
                for _ in range(depth - 1):
                    nested = [nested]
                doc = {'id': RUN_ID, 'unused': nested}
                self.serve('get_workflow_run', json_bytes(doc))
                reply = self.transport.get_workflow_run(REPO, RUN_ID)
                if depth == aj.MAX_JSON_DEPTH:
                    self.assertEqual(reply, {'ok': doc})
                else:
                    self.assertUnavailable(reply, 'metadata JSON nesting exceeds bound')

    def test_malformed_two_element_replies_still_close_the_stream(self):
        reply_type = namedtuple('Reply', 'status stream')
        for wrap in (reply_type, lambda status, stream: [status, stream]):
            with self.subTest(wrap=wrap):
                self.client.reset()
                stream = self.client.track(FakeStream(self.scenario.archive))
                reply = wrap(200, stream)
                self.client.replies['download_artifact'] = lambda _args, reply=reply: reply
                self.assertUnavailable(self.transport.download_artifact(
                    REPO, ARTIFACT_ID, len(self.scenario.archive)), 'client reply malformed')
                self.assertTrue(stream.closed)
                self.assertEqual(stream.reads, [])
                self.assertEqual(len(self.client.opened), 1)

    def test_ref_and_run_payloads_must_bind_the_request(self):
        sha = POLICY['policy_commit']
        for body in ({'ref': 'refs/heads/other', 'object': {'type': 'commit', 'sha': sha}},
                     {'ref': 'refs/heads/main', 'object': {'type': 'tag', 'sha': sha}},
                     {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': sha.upper()}},
                     {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': sha[:39]}},
                     {'ref': 'refs/heads/main', 'object': [sha]}, {'ref': 'refs/heads/main'}, [sha], sha):
            with self.subTest(body=body):
                self.serve('get_ref', json_bytes(body))
                self.assertUnavailable(self.transport.get_ref(REPO, 'main'), 'ref payload malformed')
        run = self.scenario.state['run_by_id'][RUN_ID]
        for body in ({**run, 'id': RUN_ID + 1}, {**run, 'id': str(RUN_ID)},
                     {k: v for k, v in run.items() if k != 'id'}, [run]):
            with self.subTest(body=type(body).__name__):
                self.serve('get_workflow_run', json_bytes(body))
                self.assertUnavailable(self.transport.get_workflow_run(REPO, RUN_ID), 'run payload malformed')


class DisabledAndHygieneTests(Case):
    def test_disabled_entries_ignore_the_transport_client_and_callbacks(self):
        callback = mock.Mock()
        for entry, disabled in ((ad.disabled_entry, ad.DISABLED), (aj.disabled_entry, aj.DISABLED)):
            result = entry({'payload': 'x'}, self.transport, self.client, transport=self.transport,
                           client=self.client, on_success=callback, callbacks=[callback])
            self.assertEqual(result, disabled)
        self.assertEqual(self.client.opened, [])
        callback.assert_not_called()
        self.assertEqual(ad.DISABLED['reason'],
                         'v1 adapters are offline preparation; no transport, upload, dispatch or activation route exists')

    def test_module_has_no_network_environment_process_or_cli_surface(self):
        source = (ROOT / 'scripts/ci/candidate_artifact_read_transport.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or '').split('.')[0])
        self.assertLessEqual(imported, {'__future__', 'json', 'math', 'candidate_artifact_adapters',
                                        'candidate_artifact_journal'})
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertFalse(names & {'open', 'eval', 'exec', 'compile', '__import__', 'input', 'print'})
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertFalse(attrs & {'environ', 'getenv', 'urlopen', 'request', 'post', 'put', 'patch', 'delete',
                                  'upload'})
        self.assertFalse(hasattr(rt, 'main'))
        self.assertNotIn('__main__', source)

    def test_existing_modules_and_workflows_are_not_wired_to_the_transport(self):
        paths = [ROOT / 'scripts/ci/candidate_artifact_adapters.py', ROOT / 'scripts/ci/candidate_artifact_journal.py']
        paths += sorted((ROOT / '.github/workflows').glob('*.y*ml'))
        for path in paths:
            self.assertNotIn('candidate_artifact_read_transport', path.read_text(encoding='utf-8'), str(path))


if __name__ == '__main__':
    unittest.main()
