"""The decision record: one append-only, owner-only, hash-chained file per research case.

It is the model-risk log as well (contract line 1693, PROPOSED: "each proposal logs its
prompt, output and model version"): every model output is written with its prompt, its
output, the model id and version and the route record it came from, before anything is
read out of it.

The file is ``<directory>/<case_id>.jsonl``. Each line is one row in canonical bytes
(``cockpit.research.canonical``) followed by ``\\n``::

    {"schema": "cockpit.decision_record/1", "seq": n, "prev": <digest of line n-1>,
     "kind": ..., "at_ms": <int>, "body": {...}}

Row 0 is ``case_opened``; its ``prev`` is ``GENESIS``. The kinds and their order:

* ``case_opened``: the case and its digest; exactly once, first.
* ``model_output``: route record id, model id and version, prompt and output (each as the
  UTF-8 bytes in base64 with their SHA-256, so any text a model writes is kept exactly).
  Allowed until a proposal is accepted.
* ``proposal``: the proposal that one earlier ``model_output`` (named by its ``seq``) holds,
  with its digest. The output's text is parsed again with ``parse_proposal`` against the
  case, on every append and every replay, and must give exactly this proposal
  (``provenance_mismatch`` otherwise), so a proposal is never attributed to a model output
  that did not produce it. At most one per record.
* ``proposal_refused``: the refusal code for one earlier ``model_output``.
* ``answer``: an operator's ``approve`` or ``veto`` of the accepted proposal: the approval
  id, the effect digest, the named principal who answered, the reason, and the model id,
  version and prompt and output digests the proposal came from. After it the record is
  final.

Nothing edits a row: a row is only ever appended, every row names the digest of the one
before, and opening a record re-reads and re-checks every row, so an edited, removed,
reordered or torn row is refused rather than read. No row names the last row, so the
record also keeps its head (the last row's digest and the row count) in
``<directory>/<case_id>.head``, written after every append, and ``open`` refuses a record
whose last row is not that head (``head_mismatch``): a dropped or rewritten final veto is
refused, not read as a record that can take a fresh answer. A caller that keeps
``head()`` somewhere else can pass it to ``open(expect_head=...)`` as well. Same-uid code
can still rewrite the file, its chain and its head file together; the record is tamper
evidence against that, not a boundary.

A restart keeps the record because it is the file. Every append runs under an exclusive
``flock`` on the record (``open`` takes a shared one), and under it re-checks that the file
is the one opened, holds exactly the bytes this handle knows, and that the head file still
names this handle's head, so a second writer or an outside edit, even one of the same
length, is refused (``record_changed``), not appended to. An append that would take the
file past the size ``open`` reads (``_MAX_FILE``) is refused before it is written
(``too_large``). A write or a sync that fails is refused (``write_failed``) and the row is
cut back off, so the record stays readable; if the head file was already replaced when a
directory sync failed, this handle refuses every later append (``record_unusable``) and a
reopen reads the record as written.

The sized intent and the approval request (later slices: the gateway sizes, the core
draws the approval) are not rows yet; ``answer`` is recorded here only as data, and no
code here answers, approves or vetoes anything.

Owner-only: the directory must be a real directory (not a link), owned by this user and
closed to group and others; the record and its head file are created ``0600`` with
``O_EXCL`` and opened with ``O_NOFOLLOW``. These checks need POSIX owners (``os.getuid``)
and the process lock needs ``fcntl``; where either is missing (native Windows) ``create``,
``open`` and every append refuse with ``platform_unsupported`` rather than run without them.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import secrets
import stat
import threading
from dataclasses import dataclass, field, replace
from typing import Callable

try:  # POSIX advisory lock: serialises writers in other processes too; absent on Windows
    import fcntl
except ImportError:  # pragma: no cover - platform without fcntl
    fcntl = None

from cockpit.research.canonical import DIGEST_FORM, MAX_INT, canonical, parse
from cockpit.research.errors import Refused
from cockpit.research.schema import (Proposal, ResearchCase, case_from_value, parse_proposal,
                                     proposal_from_value)

__all__ = ["ANSWERS", "GENESIS", "HEAD_SCHEMA", "KINDS", "MAX_TEXT_BYTES", "RECORD_SCHEMA", "DecisionRecord"]

RECORD_SCHEMA = "cockpit.decision_record/1"
HEAD_SCHEMA = "cockpit.decision_record_head/1"
GENESIS = "sha256:" + "0" * 64
KINDS = ("case_opened", "model_output", "proposal", "proposal_refused", "answer")
ANSWERS = ("approve", "veto")
MAX_TEXT_BYTES = 1 << 20
MAX_REASON = 2_000
_MAX_FILE = 64 * MAX_TEXT_BYTES
_MAX_HEAD = 4096

_ROW_FIELDS = frozenset({"schema", "seq", "prev", "kind", "at_ms", "body"})
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}")
_CASE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_MODEL = re.compile(r"[A-Za-z0-9_.:@/\[\]-]{1,128}")
_PRINCIPAL = re.compile(r"[a-z0-9][a-z0-9_.:@-]{0,127}")
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")

_POSIX = hasattr(os, "getuid")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


@dataclass(frozen=True)
class _State:
    case: ResearchCase | None = None
    seq_next: int = 0
    last_digest: str = GENESIS
    last_at: int = 0
    outputs: tuple = ()  # (seq, model dict, output text) of every model_output row
    consumed: frozenset = field(default_factory=frozenset)
    proposal: tuple | None = None  # (digest, model dict)
    final: bool = False


def _exact(value: object, fields: frozenset, where: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise Refused("row_invalid", where)
    return value


def _match(value: object, pattern: re.Pattern, where: str) -> str:
    if type(value) is not str or not pattern.fullmatch(value):
        raise Refused("row_invalid", where)
    return value


def _seq_ref(value: object, state: _State, where: str) -> int:
    if type(value) is not int or not 0 <= value < state.seq_next:
        raise Refused("row_invalid", where)
    return value


def _text_blob(text: object, where: str) -> dict:
    """Exact text to ``{"sha256", "utf8_b64"}``. Refuses non-``str``, lone surrogates, oversize."""
    if type(text) is not str:
        raise Refused("wrong_type", where)
    try:
        raw = text.encode("utf-8")
    except UnicodeEncodeError:
        raise Refused("text_invalid", f"{where}: lone surrogate") from None
    if len(raw) > MAX_TEXT_BYTES:
        raise Refused("too_large", where)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "utf8_b64": base64.b64encode(raw).decode("ascii")}


def _check_blob(value: object, where: str) -> str:
    value = _exact(value, frozenset({"sha256", "utf8_b64"}), where)
    sha = _match(value["sha256"], _HEX64, f"{where}.sha256")
    b64 = value["utf8_b64"]
    if type(b64) is not str:
        raise Refused("row_invalid", f"{where}.utf8_b64")
    try:
        raw = base64.b64decode(b64.encode("ascii"), validate=True)
        text = raw.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise Refused("row_invalid", f"{where}.utf8_b64") from None
    if base64.b64encode(raw).decode("ascii") != b64 or len(raw) > MAX_TEXT_BYTES:
        raise Refused("row_invalid", f"{where}.utf8_b64")
    if hashlib.sha256(raw).hexdigest() != sha:
        raise Refused("row_invalid", f"{where}.sha256")
    return text


def _next_state(state: _State, row: object) -> _State:
    """Check one decoded row against the record so far; return the state after it.

    The one place the row rules live: ``open`` replays every stored row through it and
    every append passes through it before a byte is written."""
    row = _exact(row, _ROW_FIELDS, "row")
    if row["schema"] != RECORD_SCHEMA or type(row["schema"]) is not str:
        raise Refused("row_invalid", "schema")
    if type(row["seq"]) is not int or row["seq"] != state.seq_next:
        raise Refused("chain_broken", "seq")
    if type(row["prev"]) is not str or row["prev"] != state.last_digest:
        raise Refused("chain_broken", "prev")
    at = row["at_ms"]
    if type(at) is not int or not 0 <= at <= MAX_INT:
        raise Refused("row_invalid", "at_ms")
    if at < state.last_at:
        raise Refused("clock_regressed")
    if state.final:
        raise Refused("record_final")
    kind = row["kind"]
    body = row["body"]
    if type(kind) is not str or kind not in KINDS:
        raise Refused("row_invalid", "kind")
    if (kind == "case_opened") != (state.seq_next == 0):
        raise Refused("row_order", kind)
    nxt = replace(state, seq_next=state.seq_next + 1, last_at=at)

    if kind == "case_opened":
        body = _exact(body, frozenset({"case", "case_digest", "digest_form"}), "case_opened")
        case = case_from_value(body["case"])
        if body["digest_form"] != DIGEST_FORM or body["case_digest"] != case.digest():
            raise Refused("row_invalid", "case_digest")
        return replace(nxt, case=case)

    if kind == "model_output":
        if state.proposal is not None:
            raise Refused("row_order", "model_output after the proposal")
        body = _exact(body, frozenset({"route_record_id", "model_id", "model_version", "prompt",
                                       "output"}), "model_output")
        _match(body["route_record_id"], _ID, "route_record_id")
        _check_blob(body["prompt"], "prompt")
        output = _check_blob(body["output"], "output")
        model = {
            "model_id": _match(body["model_id"], _MODEL, "model_id"),
            "model_version": _match(body["model_version"], _MODEL, "model_version"),
            "prompt_sha256": body["prompt"]["sha256"],
            "output_sha256": body["output"]["sha256"],
        }
        return replace(nxt, outputs=state.outputs + ((state.seq_next, model, output),))

    if kind in ("proposal", "proposal_refused"):
        if state.proposal is not None:
            raise Refused("row_order", "a proposal is already accepted")
        fields = {"proposal": frozenset({"from_output", "proposal", "proposal_digest"}),
                  "proposal_refused": frozenset({"from_output", "code"})}[kind]
        body = _exact(body, fields, kind)
        source = _seq_ref(body["from_output"], state, "from_output")
        outputs = {seq: (model, text) for seq, model, text in state.outputs}
        if source not in outputs or source in state.consumed:
            raise Refused("row_order", "from_output names no unanswered model_output")
        consumed = state.consumed | {source}
        if kind == "proposal_refused":
            _match(body["code"], _CODE, "code")
            return replace(nxt, consumed=consumed)
        proposal = proposal_from_value(body["proposal"], case=state.case)
        if body["proposal_digest"] != proposal.digest():
            raise Refused("row_invalid", "proposal_digest")
        model, text = outputs[source]
        try:
            produced = parse_proposal(text, case=state.case)
        except Refused as exc:
            raise Refused("provenance_mismatch", f"the output holds no proposal ({exc.code})") from None
        if produced != proposal:
            raise Refused("provenance_mismatch", "the output holds a different proposal")
        return replace(nxt, consumed=consumed, proposal=(proposal.digest(), model))

    # answer
    if state.proposal is None:
        raise Refused("row_order", "answer before a proposal")
    body = _exact(body, frozenset({"proposal_digest", "approval_id", "effect_digest", "answered_by",
                                   "answer", "reason", "model"}), "answer")
    if body["proposal_digest"] != state.proposal[0]:
        raise Refused("digest_mismatch", "proposal_digest")
    _match(body["approval_id"], _ID, "approval_id")
    _match(body["effect_digest"], _DIGEST, "effect_digest")
    _match(body["answered_by"], _PRINCIPAL, "answered_by")
    if type(body["answer"]) is not str or body["answer"] not in ANSWERS:
        raise Refused("row_invalid", "answer")
    reason = body["reason"]
    if type(reason) is not str or not reason.isascii() or len(reason) > MAX_REASON \
            or any(not (ch.isprintable() or ch in "\n\t") for ch in reason):
        raise Refused("row_invalid", "reason")
    if type(body["model"]) is not dict or body["model"] != state.proposal[1]:
        raise Refused("row_invalid", "model")
    return replace(nxt, final=True)


def _require_platform() -> None:
    """Owner checks and the process lock are this record's contract; without them, refuse."""
    if not _POSIX or fcntl is None:
        raise Refused("platform_unsupported", "needs POSIX owners and fcntl")


def _check_dir(directory: object) -> str:
    if type(directory) is not str or not directory:
        raise Refused("path_invalid")
    try:
        st = os.lstat(directory)
    except FileNotFoundError:
        raise Refused("directory_missing") from None
    if not stat.S_ISDIR(st.st_mode):
        raise Refused("directory_invalid")
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Refused("directory_not_owner_only")
    return directory


def _check_file(st: os.stat_result) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise Refused("record_not_regular_file")
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Refused("record_not_owner_only")


def _path(directory: str, case_id: object) -> str:
    if type(case_id) is not str or not _CASE_ID.fullmatch(case_id):
        raise Refused("invalid_value", "case_id")
    return os.path.join(directory, case_id + ".jsonl")


def _clock_value(clock: Callable[[], int]) -> int:
    at = clock()
    if type(at) is not int or not 0 <= at <= MAX_INT:
        raise Refused("clock_invalid")
    return at


def _line(digest_line: bytes) -> str:
    return "sha256:" + hashlib.sha256(digest_line).hexdigest()


def _fsync_file(fd: int) -> None:
    os.fsync(fd)


def _fsync_dir(directory: str) -> None:
    fd = os.open(directory, os.O_RDONLY | _CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise OSError("no progress writing")
        view = view[n:]


def _read_bounded(fd: int, limit: int) -> bytes:
    """Read from offset 0 up to ``limit + 1`` bytes, so a caller can tell 'more than limit'."""
    chunks, total = [], 0
    while total <= limit:
        chunk = os.pread(fd, min(1 << 20, limit + 1 - total), total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _head_bytes(case_id: str, digest: str, rows: int) -> bytes:
    return canonical({"schema": HEAD_SCHEMA, "case_id": case_id, "head": digest, "rows": rows})


def _read_head(path: str, case_id: str) -> tuple:
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _BINARY | _CLOEXEC)
    except FileNotFoundError:
        raise Refused("head_missing") from None
    except OSError:
        raise Refused("head_invalid") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Refused("head_invalid")
        if st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise Refused("head_not_owner_only")
        raw = _read_bounded(fd, _MAX_HEAD)
    finally:
        os.close(fd)
    try:
        value = parse(raw)
    except Refused:
        raise Refused("head_invalid") from None
    if type(value) is not dict or set(value) != {"schema", "case_id", "head", "rows"} \
            or value["schema"] != HEAD_SCHEMA or value["case_id"] != case_id \
            or type(value["head"]) is not str or not _DIGEST.fullmatch(value["head"]) \
            or type(value["rows"]) is not int or value["rows"] < 1:
        raise Refused("head_invalid")
    return value["head"], value["rows"]


def _replace_head(path: str, raw: bytes) -> bool:
    """Write ``raw`` to a temporary file and move it over the head file, then sync the folder.

    Returns normally when all of it held. On failure raises ``OSError`` or ``Refused`` with
    ``.replaced`` set on the exception: whether the head file was already replaced."""
    directory = os.path.dirname(path)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.{secrets.token_hex(8)}.tmp")
    replaced = False
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY | _CLOEXEC, 0o600)
        try:
            _write_all(fd, raw)
            _fsync_file(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        replaced = True
        _fsync_dir(directory)
    except (OSError, Refused) as exc:
        if not replaced:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        exc.replaced = replaced
        raise
    return True


class DecisionRecord:
    """One case's decision record. Make one with ``create``; reopen it with ``open``."""

    __slots__ = ("_path", "_head_path", "_case_id", "_clock", "_lock", "_state", "_lines", "_size",
                 "_ident", "_broken")

    def __init__(self, *args, **kwargs) -> None:
        raise TypeError("use DecisionRecord.create or DecisionRecord.open")

    @classmethod
    def _new(cls, path: str, case_id: str, clock: Callable[[], int]) -> "DecisionRecord":
        if not callable(clock):
            raise Refused("clock_invalid")
        self = object.__new__(cls)
        self._path = path
        self._head_path = path[: -len(".jsonl")] + ".head"
        self._case_id = case_id
        self._clock = clock
        self._lock = threading.Lock()
        self._state = _State()
        self._lines = []
        self._size = 0
        self._ident = None
        self._broken = False
        return self

    @classmethod
    def create(cls, directory: str, case: ResearchCase, *, clock: Callable[[], int]) -> "DecisionRecord":
        """Create the record for ``case`` with its ``case_opened`` row. Refuses if it exists."""
        _require_platform()
        if type(case) is not ResearchCase:
            raise Refused("wrong_type", "case")
        case = case_from_value(case.value())
        path = _path(_check_dir(directory), case.case_id)
        self = cls._new(path, case.case_id, clock)
        row, line, after = self._build("case_opened", {"case": case.value(), "case_digest": case.digest(),
                                                       "digest_form": DIGEST_FORM})
        if os.path.lexists(self._head_path):
            raise Refused("record_exists", "a head file is already there")
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY | _CLOEXEC, 0o600)
        except FileExistsError:
            raise Refused("record_exists") from None
        except OSError as exc:
            raise Refused("write_failed", exc.strerror or "") from None
        done = False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            st = os.fstat(fd)
            _check_file(st)
            self._ident = (st.st_dev, st.st_ino)
            _write_all(fd, line)
            _fsync_file(fd)
            _replace_head(self._head_path, _head_bytes(case.case_id, _line(line), 1))
            done = True
        except OSError as exc:
            raise Refused("write_failed", exc.strerror or "") from None
        finally:
            if not done:  # leave nothing behind: a later create must be able to start again
                for leftover in (path, self._head_path):
                    try:
                        os.unlink(leftover)
                    except OSError:
                        pass
            os.close(fd)
        self._commit(line, after)
        return self

    @classmethod
    def open(cls, directory: str, case_id: str, *, clock: Callable[[], int],
             expect_head: str | None = None) -> "DecisionRecord":
        """Reopen a record (after a restart): read and re-check every row and the head file.

        With ``expect_head`` (a ``head()`` kept somewhere else) the last row must be that one too."""
        _require_platform()
        if expect_head is not None and (type(expect_head) is not str or not _DIGEST.fullmatch(expect_head)):
            raise Refused("invalid_value", "expect_head")
        path = _path(_check_dir(directory), case_id)
        self = cls._new(path, case_id, clock)
        try:
            fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _BINARY | _CLOEXEC)
        except FileNotFoundError:
            raise Refused("record_missing") from None
        except OSError:
            raise Refused("record_not_regular_file") from None
        try:
            st = os.fstat(fd)
            _check_file(st)
            fcntl.flock(fd, fcntl.LOCK_SH)  # no writer between the record's bytes and its head
            data = _read_bounded(fd, _MAX_FILE)
            if len(data) > _MAX_FILE:
                raise Refused("too_large", "record")
            head, head_error = None, None
            if data:
                try:
                    head = _read_head(self._head_path, case_id)
                except Refused as exc:  # reported after the rows, which say more when both are wrong
                    head_error = exc
        finally:
            os.close(fd)
        self._ident = (st.st_dev, st.st_ino)
        if not data:
            raise Refused("record_empty")
        if not data.endswith(b"\n"):
            raise Refused("record_torn")
        for line in data[:-1].split(b"\n"):
            line = line + b"\n"
            row = parse(line[:-1])
            self._state = _next_state(self._state, row)
            self._state = replace(self._state, last_digest=_line(line))
            self._lines.append(line)
        self._size = len(data)
        if self._state.case is None or self._state.case.case_id != case_id:
            raise Refused("row_invalid", "case_id")
        if head_error is not None:
            raise head_error
        if head != (self._state.last_digest, len(self._lines)):
            raise Refused("head_mismatch")
        if expect_head is not None and self._state.last_digest != expect_head:
            raise Refused("head_mismatch")
        return self

    # ---- appends -------------------------------------------------------------------------

    def log_model_output(self, *, route_record_id: str, model_id: str, model_version: str,
                         prompt: str, output: str) -> int:
        """Write one model output with its prompt and model (the model-risk log). Returns its seq."""
        body = {"route_record_id": route_record_id, "model_id": model_id, "model_version": model_version,
                "prompt": _text_blob(prompt, "prompt"), "output": _text_blob(output, "output")}
        return self._append("model_output", body)

    def record_proposal(self, proposal: Proposal, *, from_output: int) -> int:
        """Record the proposal that model output ``from_output`` holds. Returns its seq."""
        if type(proposal) is not Proposal:
            raise Refused("wrong_type", "proposal")
        value = proposal.value()
        digest = proposal_from_value(value).digest()
        return self._append("proposal", {"from_output": from_output, "proposal": value,
                                         "proposal_digest": digest})

    def record_proposal_refused(self, code: str, *, from_output: int) -> int:
        """Record that model output ``from_output`` gave no acceptable proposal."""
        return self._append("proposal_refused", {"from_output": from_output, "code": code})

    def record_answer(self, *, proposal_digest: str, approval_id: str, effect_digest: str,
                      answered_by: str, answer: str, reason: str) -> int:
        """Record an operator's approve or veto as data. The record is final after it."""
        with self._lock:
            model = self._state.proposal[1] if self._state.proposal is not None else {}
        return self._append("answer", {"proposal_digest": proposal_digest, "approval_id": approval_id,
                                       "effect_digest": effect_digest, "answered_by": answered_by,
                                       "answer": answer, "reason": reason, "model": dict(model)})

    # ---- reads ---------------------------------------------------------------------------

    @property
    def path(self) -> str:
        return self._path

    @property
    def case(self) -> ResearchCase:
        return self._state.case

    @property
    def final(self) -> bool:
        return self._state.final

    def rows(self) -> list:
        """Every row, decoded afresh from the bytes written (callers get copies)."""
        with self._lock:
            lines = list(self._lines)
        return [parse(line[:-1]) for line in lines]

    def head(self) -> str:
        """The digest of the last row: what the next row will name as ``prev``."""
        with self._lock:
            return self._state.last_digest

    @staticmethod
    def text(blob: dict) -> str:
        """The exact text of a ``prompt`` or ``output`` blob from ``rows()``."""
        return _check_blob(blob, "blob")

    # ---- internals -----------------------------------------------------------------------

    def _build(self, kind: str, body: dict) -> tuple:
        """The row, its line and the record's state after it; nothing is written yet."""
        row = {"schema": RECORD_SCHEMA, "seq": self._state.seq_next, "prev": self._state.last_digest,
               "kind": kind, "at_ms": _clock_value(self._clock), "body": body}
        try:
            line = canonical(row) + b"\n"
        except Refused as exc:
            raise Refused("row_invalid", exc.code) from None
        # Check the row as it will be read back, not the caller's objects.
        after = _next_state(self._state, parse(line[:-1]))
        return row, line, after

    def _commit(self, line: bytes, after: _State) -> None:
        self._state = replace(after, last_digest=_line(line))
        self._lines.append(line)
        self._size += len(line)

    def _cut_back(self, fd: int) -> None:
        """Take a failed row back off the file, so the record stays what this handle knows."""
        try:
            os.ftruncate(fd, self._size)
            os.fsync(fd)
        except OSError:
            self._broken = True

    def _append(self, kind: str, body: dict) -> int:
        with self._lock:
            if self._broken:
                raise Refused("record_unusable", "an earlier append failed after its head was replaced")
            _require_platform()
            row, line, after = self._build(kind, body)
            if self._size + len(line) > _MAX_FILE:
                raise Refused("too_large", "the append would pass what open reads")
            try:
                fd = os.open(self._path, os.O_RDWR | os.O_APPEND | _NOFOLLOW | _BINARY | _CLOEXEC)
            except FileNotFoundError:
                raise Refused("record_missing") from None
            except OSError:
                raise Refused("record_not_regular_file") from None
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)  # held until close: checks and writes run under it
                st = os.fstat(fd)
                _check_file(st)
                if (st.st_dev, st.st_ino) != self._ident or st.st_size != self._size:
                    raise Refused("record_changed")
                if _read_bounded(fd, self._size) != b"".join(self._lines):
                    raise Refused("record_changed", "contents")
                try:
                    if _read_head(self._head_path, self._case_id) != (self._state.last_digest, len(self._lines)):
                        raise Refused("record_changed", "head")
                except Refused as exc:
                    raise Refused("record_changed", exc.code) from None
                try:
                    _write_all(fd, line)
                    _fsync_file(fd)
                except OSError as exc:
                    self._cut_back(fd)
                    raise Refused("write_failed", exc.strerror or "") from None
                head = _head_bytes(self._case_id, _line(line), len(self._lines) + 1)
                try:
                    _replace_head(self._head_path, head)
                except (OSError, Refused) as exc:
                    if getattr(exc, "replaced", False):
                        self._broken = True  # the head names the new row; the record holds it
                    else:
                        self._cut_back(fd)
                    raise Refused("write_failed", getattr(exc, "strerror", None) or "head") from None
            finally:
                os.close(fd)
            self._commit(line, after)
            return row["seq"]
