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
* ``proposal``: a proposal parsed from one earlier ``model_output`` (by its ``seq``), with
  its digest. At most one per record.
* ``proposal_refused``: the refusal code for one earlier ``model_output``.
* ``answer``: an operator's ``approve`` or ``veto`` of the accepted proposal: the approval
  id, the effect digest, the named principal who answered, the reason, and the model id,
  version and prompt and output digests the proposal came from. After it the record is
  final.

Nothing edits a row: a row is only ever appended, every row names the digest of the one
before, and opening a record re-reads and re-checks every row, so an edited, removed,
reordered or torn row is refused rather than read. The last row is the exception: no
row names it, so an edit of it is seen only by a caller that kept ``head()`` elsewhere
and passes it to ``open(expect_head=...)``. Same-uid code can rewrite the whole file and
its chain; the record is tamper evidence against that, not a boundary. A restart keeps the record because it
is the file. Each append re-checks that the file is still the one opened, at the length
last seen, so a second writer or an outside edit is refused, not interleaved. Where the
platform has ``fcntl``, that check and the write run under an exclusive ``flock`` on the
file, so two processes appending at once cannot both pass it; without ``fcntl`` (Windows)
two processes can, and the next ``open`` then refuses the record (two rows, one ``seq``).

The sized intent and the approval request (later slices: the gateway sizes, the core
draws the approval) are not rows yet; ``answer`` is recorded here only as data, and no
code here answers, approves or vetoes anything.

Owner-only: the directory must be a real directory (not a link), owned by this user and
closed to group and others; the file is created ``0600`` with ``O_EXCL`` and opened with
``O_NOFOLLOW``. Those checks run where the platform has POSIX owners (``os.getuid``);
on Windows the ACL is not checked here (see the lane's questions).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
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
from cockpit.research.schema import (Proposal, ResearchCase, case_from_value, proposal_from_value)

__all__ = ["ANSWERS", "GENESIS", "KINDS", "MAX_TEXT_BYTES", "RECORD_SCHEMA", "DecisionRecord"]

RECORD_SCHEMA = "cockpit.decision_record/1"
GENESIS = "sha256:" + "0" * 64
KINDS = ("case_opened", "model_output", "proposal", "proposal_refused", "answer")
ANSWERS = ("approve", "veto")
MAX_TEXT_BYTES = 1 << 20
MAX_REASON = 2_000
_MAX_FILE = 64 * MAX_TEXT_BYTES

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
    outputs: tuple = ()  # (seq, model dict) of model_output rows no proposal row has consumed
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
        _check_blob(body["output"], "output")
        model = {
            "model_id": _match(body["model_id"], _MODEL, "model_id"),
            "model_version": _match(body["model_version"], _MODEL, "model_version"),
            "prompt_sha256": body["prompt"]["sha256"],
            "output_sha256": body["output"]["sha256"],
        }
        return replace(nxt, outputs=state.outputs + ((state.seq_next, model),))

    if kind in ("proposal", "proposal_refused"):
        if state.proposal is not None:
            raise Refused("row_order", "a proposal is already accepted")
        fields = {"proposal": frozenset({"from_output", "proposal", "proposal_digest"}),
                  "proposal_refused": frozenset({"from_output", "code"})}[kind]
        body = _exact(body, fields, kind)
        source = _seq_ref(body["from_output"], state, "from_output")
        models = dict(state.outputs)
        if source not in models or source in state.consumed:
            raise Refused("row_order", "from_output names no unanswered model_output")
        consumed = state.consumed | {source}
        if kind == "proposal_refused":
            _match(body["code"], _CODE, "code")
            return replace(nxt, consumed=consumed)
        proposal = proposal_from_value(body["proposal"], case=state.case)
        if body["proposal_digest"] != proposal.digest():
            raise Refused("row_invalid", "proposal_digest")
        return replace(nxt, consumed=consumed, proposal=(proposal.digest(), models[source]))

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


def _check_dir(directory: object) -> str:
    if type(directory) is not str or not directory:
        raise Refused("path_invalid")
    try:
        st = os.lstat(directory)
    except FileNotFoundError:
        raise Refused("directory_missing") from None
    if not stat.S_ISDIR(st.st_mode):
        raise Refused("directory_invalid")
    if _POSIX and (st.st_uid != os.getuid() or st.st_mode & 0o077):
        raise Refused("directory_not_owner_only")
    return directory


def _check_file(st: os.stat_result) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise Refused("record_not_regular_file")
    if _POSIX and (st.st_uid != os.getuid() or st.st_mode & 0o077):
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


class DecisionRecord:
    """One case's decision record. Make one with ``create``; reopen it with ``open``."""

    __slots__ = ("_path", "_clock", "_lock", "_state", "_lines", "_size", "_ident")

    def __init__(self, *args, **kwargs) -> None:
        raise TypeError("use DecisionRecord.create or DecisionRecord.open")

    @classmethod
    def _new(cls, path: str, clock: Callable[[], int]) -> "DecisionRecord":
        if not callable(clock):
            raise Refused("clock_invalid")
        self = object.__new__(cls)
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._state = _State()
        self._lines = []
        self._size = 0
        self._ident = None
        return self

    @classmethod
    def create(cls, directory: str, case: ResearchCase, *, clock: Callable[[], int]) -> "DecisionRecord":
        """Create the record for ``case`` with its ``case_opened`` row. Refuses if it exists."""
        if type(case) is not ResearchCase:
            raise Refused("wrong_type", "case")
        case = case_from_value(case.value())
        path = _path(_check_dir(directory), case.case_id)
        self = cls._new(path, clock)
        row, line, after = self._build("case_opened", {"case": case.value(), "case_digest": case.digest(),
                                                       "digest_form": DIGEST_FORM})
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY | _CLOEXEC, 0o600)
        except FileExistsError:
            raise Refused("record_exists") from None
        try:
            st = os.fstat(fd)
            _check_file(st)
            self._ident = (st.st_dev, st.st_ino)
            _write_all(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        _sync_dir(directory)
        self._commit(line, after)
        return self

    @classmethod
    def open(cls, directory: str, case_id: str, *, clock: Callable[[], int],
             expect_head: str | None = None) -> "DecisionRecord":
        """Reopen a record (after a restart): read and re-check every row.

        With ``expect_head`` (a ``head()`` kept somewhere else), the last row must be that one;
        without it an edit of the last row cannot be seen, because no later row names it."""
        if expect_head is not None and (type(expect_head) is not str or not _DIGEST.fullmatch(expect_head)):
            raise Refused("invalid_value", "expect_head")
        path = _path(_check_dir(directory), case_id)
        self = cls._new(path, clock)
        try:
            fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _BINARY | _CLOEXEC)
        except FileNotFoundError:
            raise Refused("record_missing") from None
        except OSError:
            raise Refused("record_not_regular_file") from None
        try:
            st = os.fstat(fd)
            _check_file(st)
            if st.st_size > _MAX_FILE:
                raise Refused("too_large", "record")
            data = _read_all(fd)
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
        """Record the proposal parsed from model output ``from_output``. Returns its seq."""
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

    def _append(self, kind: str, body: dict) -> int:
        with self._lock:
            row, line, after = self._build(kind, body)
            try:
                fd = os.open(self._path, os.O_WRONLY | os.O_APPEND | _NOFOLLOW | _BINARY | _CLOEXEC)
            except FileNotFoundError:
                raise Refused("record_missing") from None
            except OSError:
                raise Refused("record_not_regular_file") from None
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)  # held until close; the length check runs under it
                st = os.fstat(fd)
                _check_file(st)
                if (st.st_dev, st.st_ino) != self._ident or st.st_size != self._size:
                    raise Refused("record_changed")
                _write_all(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
            self._commit(line, after)
            return row["seq"]


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise Refused("write_failed")
        view = view[n:]


def _read_all(fd: int) -> bytes:
    chunks = []
    while True:
        chunk = os.read(fd, 1 << 20)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _sync_dir(directory: str) -> None:
    if not _POSIX:
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
