"""Bounded read transport over an injected client, for the staged artifact adapters (CSL-259).

OFFLINE MECHANICS ONLY; this is not a GitHub client. ``ReadTransport`` supplies the five
read methods that ``candidate_artifact_adapters.consume`` calls (``READ_METHODS``):
arguments are validated before the client is touched, every body is read through bounded
``read(size)`` calls, metadata is strict JSON, and ordinary ``Exception`` failures become
``{'unavailable': reason}`` replies instead of ``{'ok': value}``. Control-flow
``BaseException`` signals propagate; cleanup is attempted for recognized streams. Nothing here
reads credentials or the environment, builds URLs, follows redirects, imports an HTTP
library, uploads, POSTs or retries. There is no CLI and no workflow wiring, and
``disabled_entry`` in both existing modules ignores this class. It is CI-development
tooling and makes no Recursion runtime language choice.

Injected client protocol, the only call this module makes on the client::

    client.open(operation, args) -> (status, stream)

``operation`` is one of ``READ_METHODS``. ``args`` is the tuple of that method's public
arguments after validation, in signature order; ``download_artifact`` passes
``max_bytes`` so the client can bound its own work too. There is no keyword forwarding
and no other entry point, so the client can only be asked for one of five fixed reads.
The reply must be a 2-tuple: ``status`` a plain ``int`` equal to 200 (403, 404, 410, 429
and every other value are unavailable), ``stream`` an object with ``read(size)``
returning at most ``size`` bytes (``b''`` at EOF) and ``close()``. Every stream in a
2-tuple is closed, on success and on every failure. Two-element lists and tuple
subclasses are rejected but their stream slot is still closed; other malformed reply
shapes have no stream under this protocol. A failing ``close()`` is itself unavailable.

Payloads: ``get_ref`` is a JSON object ``{"ref": "refs/heads/<branch>", "object":
{"type": "commit", "sha": <40-hex>}}``; ``get_workflow_run`` a JSON object whose integer
``id`` is the requested run; ``list_workflow_runs``/``list_artifacts`` a JSON page with
exactly ``items`` (at most MAX_PAGE_ITEMS objects with distinct positive integer ``id``)
and boolean ``has_next``; ``download_artifact`` the raw archive, exactly ``max_bytes``
long. Completion is only ever ``has_next: false``: a short page is not complete and an
empty page may not continue. Cross-page duplicates, the page bound, complete before/after
snapshots, expiry, digest, source binding and the no-fallback rule stay with the
existing consumer and validator.

Trust. The client, its statuses, streams and every metadata byte are caller-supplied and
NOT authenticated here; attributes a stream carries (lengths, headers, flags) are never
consulted, so no claim can raise ``max_bytes``. The bound is over what ``read(size)``
hands back: at most the cap is kept, plus one discarded ``read(1)`` EOF/overflow probe. A
hostile in-process client can still allocate whatever it likes before returning or inside
``read``; this module only rejects what it is handed.
"""
from __future__ import annotations

import json
import math

import candidate_artifact_adapters as ad
import candidate_artifact_journal as aj

# Bounded read(size) request size; no body is ever read with read() or by iteration.
READ_CHUNK = 64 * 1024
# Ceiling for one metadata reply. A caller may lower it per transport, never raise it.
# The archive cap is the consumer's ``max_bytes``, at most aj.MAX_ARCHIVE_CEILING.
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_ID = 2 ** 63 - 1
INVALID_ARGUMENTS = 'invalid read arguments'


class _Unavailable(Exception):
    """A stable reason inside the validator's REASON grammar."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _check(condition, reason):
    if not condition:
        raise _Unavailable(reason)


def _error_reason(prefix, error):
    reason = (prefix + ': ' + type(error).__name__)[:80]
    return reason if aj.REASON.fullmatch(reason) else prefix


def _guarded(work):
    """Normalize ordinary exceptions without retrying; BaseException signals propagate."""
    try:
        return {'ok': work()}
    except _Unavailable as exc:
        return {'unavailable': exc.reason}
    except Exception as exc:  # noqa: BLE001 - injected client failures must fail closed
        return {'unavailable': _error_reason('transport error', exc)}


# --- argument grammar, checked before the client is touched -----------------

def _repository(value):
    return (isinstance(value, str) and aj.REPOSITORY.fullmatch(value) is not None
            and not any(seg in ('.', '..') for seg in value.split('/')))


def _branch(value):
    # The rule validate_policy applies to default_branch.
    return (isinstance(value, str) and aj.BRANCH.fullmatch(value) is not None and '..' not in value
            and '//' not in value and not value.endswith(('/', '.lock')))


def _ident(value):
    return type(value) is int and 0 < value <= MAX_ID  # bool is not int here


def _page(value):
    return type(value) is int and 1 <= value <= ad.MAX_LIST_PAGES


# --- bounded stream reads ------------------------------------------------------

def _chunk(stream, size):
    chunk = stream.read(size)
    _check(type(chunk) in (bytes, bytearray), 'stream returned non-bytes')
    _check(len(chunk) <= size, 'stream returned an oversized chunk')
    return chunk


def _read(stream, cap, exact):
    """At most ``cap`` bytes through read(size) calls, then one read(1) probe that must hit
    EOF and is discarded. With ``exact`` the body must be exactly ``cap`` bytes long."""
    body = bytearray()
    while len(body) < cap:
        chunk = _chunk(stream, min(READ_CHUNK, cap - len(body)))
        if not chunk:
            _check(not exact, 'stream ended short of the authenticated size')
            return bytes(body)
        body += chunk
    _check(not _chunk(stream, 1), 'stream exceeds the byte cap')
    return bytes(body)


def _close(stream):
    close = getattr(stream, 'close', None)
    if callable(close):
        close()


# --- metadata payloads -----------------------------------------------------------

def _strict_json(data):
    def pairs(items):
        _check(len({key for key, _ in items}) == len(items), 'metadata duplicate JSON key')
        return dict(items)

    def finite(text):
        value = float(text)
        _check(math.isfinite(value), 'metadata non-finite JSON number')
        return value

    def constant(_name):
        raise _Unavailable('metadata non-finite JSON number')

    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_float=finite,
                           parse_constant=constant)
    except (ValueError, RecursionError):  # includes UnicodeDecodeError and JSONDecodeError
        raise _Unavailable('metadata JSON malformed') from None
    pending = [(value, 1)]
    while pending:
        node, depth = pending.pop()
        if isinstance(node, (dict, list)):
            _check(depth <= aj.MAX_JSON_DEPTH, 'metadata JSON nesting exceeds bound')
            children = node.values() if isinstance(node, dict) else node
            pending.extend((child, depth + 1) for child in children)
    return value


def _ref_sha(doc, branch):
    target = doc.get('object') if isinstance(doc, dict) else None
    _check(isinstance(target, dict) and doc.get('ref') == 'refs/heads/' + branch
           and target.get('type') == 'commit' and isinstance(target.get('sha'), str)
           and aj.SHA.fullmatch(target['sha']) is not None, 'ref payload malformed')
    return target['sha']


def _run(doc, run_id):
    _check(isinstance(doc, dict) and type(doc.get('id')) is int and doc['id'] == run_id, 'run payload malformed')
    return doc


def _listing_page(doc):
    """Exactly {items, has_next}. Completion is never inferred from a short page or from
    missing pagination; an empty page may not claim a continuation."""
    _check(isinstance(doc, dict) and set(doc) == {'items', 'has_next'}
           and isinstance(doc['items'], list) and type(doc['has_next']) is bool, 'listing page malformed')
    items = doc['items']
    _check(len(items) <= ad.MAX_PAGE_ITEMS, 'listing page oversized')
    _check(items or not doc['has_next'], 'listing page empty continuation')
    ids = [item.get('id') if isinstance(item, dict) else None for item in items]
    _check(all(_ident(i) for i in ids) and len(set(ids)) == len(ids),
           'listing page item identity malformed or duplicate')
    return {'items': items, 'has_next': doc['has_next']}


# --- the transport -------------------------------------------------------------------

class ReadTransport:
    """The adapter's five read methods over ``client.open`` (see the module docstring)."""

    def __init__(self, client, max_metadata_bytes=MAX_METADATA_BYTES):
        if not (type(max_metadata_bytes) is int and 0 < max_metadata_bytes <= MAX_METADATA_BYTES):
            raise ValueError('max_metadata_bytes may only tighten MAX_METADATA_BYTES')
        self._client, self._max_metadata = client, max_metadata_bytes

    def _open(self, operation, args, cap, exact):
        """One client open, read under ``cap`` and closed whatever happens. No retry."""
        try:
            reply = self._client.open(operation, args)
        except Exception as exc:  # noqa: BLE001 - normalize arbitrary injected client failures
            raise _Unavailable(_error_reason('client error', exc)) from None
        # A malformed two-element list or tuple subclass still identifies a stream
        # for cleanup. Only an exact tuple may yield successful evidence.
        stream = reply[1] if isinstance(reply, (tuple, list)) and len(reply) == 2 else None
        body_succeeded = False
        try:
            _check(type(reply) is tuple and stream is not None, 'client reply malformed')
            status = reply[0]
            _check(type(status) is int, 'client status malformed')
            _check(status == 200, 'http status ' + str(status) if 100 <= status <= 599 else 'client status malformed')
            _check(callable(getattr(stream, 'read', None)) and callable(getattr(stream, 'close', None)),
                   'client stream malformed')
            body = _read(stream, cap, exact)
            body_succeeded = True
            return body
        finally:
            try:
                _close(stream)
            except Exception:  # preserve any primary read/status failure
                if body_succeeded:
                    raise

    def _metadata(self, operation, args):
        return _strict_json(self._open(operation, args, self._max_metadata, exact=False))

    def get_ref(self, repository, branch):
        if not (_repository(repository) and _branch(branch)):
            return {'unavailable': INVALID_ARGUMENTS}
        return _guarded(lambda: _ref_sha(self._metadata('get_ref', (repository, branch)), branch))

    def list_workflow_runs(self, repository, workflow_id, page):
        if not (_repository(repository) and _ident(workflow_id) and _page(page)):
            return {'unavailable': INVALID_ARGUMENTS}
        return _guarded(lambda: _listing_page(self._metadata('list_workflow_runs', (repository, workflow_id, page))))

    def get_workflow_run(self, repository, run_id):
        if not (_repository(repository) and _ident(run_id)):
            return {'unavailable': INVALID_ARGUMENTS}
        return _guarded(lambda: _run(self._metadata('get_workflow_run', (repository, run_id)), run_id))

    def list_artifacts(self, repository, run_id, page):
        if not (_repository(repository) and _ident(run_id) and _page(page)):
            return {'unavailable': INVALID_ARGUMENTS}
        return _guarded(lambda: _listing_page(self._metadata('list_artifacts', (repository, run_id, page))))

    def download_artifact(self, repository, artifact_id, max_bytes):
        """Exactly ``max_bytes`` bytes (the consumer's authenticated size) or unavailable.
        Nothing the client or stream claims about length changes the cap."""
        if not (_repository(repository) and _ident(artifact_id) and type(max_bytes) is int
                and 0 < max_bytes <= aj.MAX_ARCHIVE_CEILING):
            return {'unavailable': INVALID_ARGUMENTS}
        return _guarded(lambda: self._open('download_artifact', (repository, artifact_id, max_bytes),
                                           max_bytes, exact=True))
