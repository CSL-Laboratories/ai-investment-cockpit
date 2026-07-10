"""W11 — the ALWAYS-ON runtime order-authority tracer (SEC-03 / P-B capability boundary).

The static call-site grep (``tests/test_invariants.py``) matches a SYMBOL, not the socket: a
``getattr`` / stored-``partial`` / MCP-shim / string-keyed / CLASS-level dispatch to a broker order
function passes the grep GREEN because the source text carries no literal ``broker.submit_order(``
call outside ``brokers/`` (backlog SEC-03, L148 — the grep is necessary, not sufficient). This
tracer is the RUNTIME complement that closes P-B: it wraps every broker order method so that ANY
invocation whose live call stack does not pass through ``OrderGateway.submit`` FAILS CLOSED — the
real broker is never entered, and a bypass ``AuditEntry`` is recorded (Inv 6) so the forensic trail
(and, later, the SEC-20 performance gate) sees the attempt.

CLASS-level wrapping (G11 round-1 hardening): the guard is installed on the broker's CLASS method
and keyed to the traced instance — NOT as a per-instance ``setattr`` shadow. Python consults an
instance attribute only for access THROUGH the instance, so ``type(broker).submit_order(broker,…)``
or ``Cls.submit_order.__get__(broker, Cls)(…)`` would dispatch through the class and skip an
instance shadow. Wrapping the class closes that: every route to the method — instance, class,
descriptor-bound, getattr/partial/shim — lands on the guard. The guard is TRANSPARENT for any
UN-traced instance of the same class (it just calls the original), so it is safe to leave installed
process-wide; only instances with an installed handle enforce.

Identity, not name: the sanctioned frame is recognized by CODE-OBJECT IDENTITY
(``OrderGateway.submit.__code__``). A caller cannot forge that by shadowing a function named
``submit`` — only a real frame executing the gateway's ``submit`` body carries that exact code
object.

Fail-closed everywhere: the refusal (raising ``OrderAuthorityViolation``) is the protection; the
bypass audit is best-effort forensics. If the audit append itself raises, or no ``AuditLog`` is
wired, the tracer STILL refuses — it never lets a broker call proceed to "record it later".

Scope of the guarantee: every DISPATCH route to the method — instance, class, descriptor-bound,
getattr/partial/string-shim — lands on the guard and is checked. The guard does NOT defend against
an in-process actor with arbitrary INTROSPECTION or monkeypatch power (reading the guard's
``__closure__`` cells to recover the closed-over original, ``delattr``-ing the handle, replacing the
class attribute). That actor is already TRUSTED composition-level — it holds the gateway's private
broker, and could call ``os``/``subprocess`` directly — and is out of the P-B threat model, whose
adversary is the OUT-OF-PROCESS decision agent (schema-validated JSON, zero broker reference). The
tracer deliberately exposes NO named unwrap handle (G11 round-3), so the original is not reachable
by attribute name; the ``__closure__`` residual is inherent to any Python wrapper.

Residual (accepted, composition-time-only): a reference to the ORIGINAL bound method captured
BEFORE ``install()`` wraps the class bypasses the guard. That requires code running between broker
construction and install — i.e. the TRUSTED composition root, which installs the tracer before the
broker is ever exposed (``main`` / the order-path composition). Untrusted actors (out-of-process
decision agents) hold no broker reference at all.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from brokers.gateway import OrderGateway

# The single sanctioned frame the broker may be reached through. Compared by identity, so a
# same-named impostor function cannot satisfy it.
_SANCTIONED_CODE = frozenset({OrderGateway.submit.__code__})

# The broker order functions the tracer guards. ``submit_order`` is the current stub seam; the
# live Robinhood-Agentic order tool, when wired (W17), registers its order fn name here too.
DEFAULT_GUARDED_METHODS = ("submit_order",)

# The bypass audit event (Inv 6). Kept DISTINCT from the SEC-20 ``BYPASS_EVENTS`` set so this
# package does not reach into the performance-gate scope (that wiring is W16); the event is a
# durable forensic record that a broker order fn was invoked outside the gateway.
EVENT_ORDER_AUTHORITY_BYPASS = "order_authority_bypass"

_MARKER = "_order_tracer_handle"        # per-INSTANCE handle attr — marks a traced broker
# A BOOLEAN sentinel on a class guard — used only for idempotent install. It deliberately does NOT
# hold the pre-guard original (that stays private in the guard's closure): a readable unwrap handle
# would let in-process code call the broker around the guard (G11 round-3).
_GUARD_MARKER = "_is_order_tracer_guard"


class OrderAuthorityViolation(RuntimeError):
    """A broker order function was invoked whose call stack did not pass through
    ``OrderGateway.submit`` — the runtime capability boundary (P-B) refused it before the broker."""


def _dispatched_through_gateway() -> bool:
    """True iff a frame currently executing ``OrderGateway.submit`` is on the call stack above
    the guard. Walks ``f_back`` from the guard's caller and matches by code-object IDENTITY.
    Any anomaly (too-shallow stack) fails CLOSED — an unverifiable stack is treated as a bypass."""
    try:
        frame = sys._getframe(2)   # 0=this fn, 1=the guard, 2=the actual caller
    except ValueError:
        return False
    while frame is not None:
        if frame.f_code in _SANCTIONED_CODE:
            return True
        frame = frame.f_back
    return False


@dataclass
class _TracerHandle:
    """Marks a broker INSTANCE as traced and carries the AuditLog for its bypass records."""

    broker: object
    state: object
    methods: tuple


def _record_bypass(state, method: str, args) -> None:
    """Best-effort forensic AuditEntry (Inv 6). NEVER raises to the caller — the refusal, not the
    audit, is the protection; a failed/absent audit must not turn a refusal into an allow."""
    audit = getattr(state, "audit", None)
    if audit is None:
        return
    tuple_hash = None
    if args:
        first = args[0]
        if isinstance(first, dict):
            tuple_hash = first.get("order_tuple_hash")
    try:
        audit.append(
            EVENT_ORDER_AUTHORITY_BYPASS,
            actor="order_tracer",
            refs={"order_tuple_hash": tuple_hash} if tuple_hash else None,
            payload={
                "method": method,
                "note": "broker order fn invoked outside OrderGateway.submit (SEC-03 runtime tracer)",
            },
        )
    except Exception:
        # Fail closed: swallow any audit error; the caller still gets the refusal.
        pass


def _make_class_guard(name: str, original):
    """A CLASS-level guard for ``name``. It enforces ONLY for instances carrying a tracer handle
    (so untraced instances of the same class are transparent), and refuses any call not dispatched
    through ``OrderGateway.submit`` — regardless of instance/class/descriptor dispatch."""

    def guarded(self, *args, **kwargs):
        handle = getattr(self, _MARKER, None)
        if handle is not None and name in handle.methods:
            if not _dispatched_through_gateway():
                _record_bypass(handle.state, name, args)
                raise OrderAuthorityViolation(
                    f"broker.{name}() invoked outside OrderGateway.submit — order authority "
                    "capability boundary refused (SEC-03 / P-B). The broker was never entered."
                )
        return original(self, *args, **kwargs)

    guarded.__name__ = getattr(original, "__name__", name)
    # ONLY a boolean sentinel is exposed; the pre-guard ``original`` stays in the closure, never as a
    # readable attribute (a named unwrap handle would be a ready in-process bypass — G11 round-3).
    setattr(guarded, _GUARD_MARKER, True)
    return guarded


def install(broker, *, state, methods: tuple[str, ...] = DEFAULT_GUARDED_METHODS) -> _TracerHandle:
    """Install the always-on tracer on ``broker`` (a re-install raises). Each named order method is
    wrapped at the CLASS level once (idempotent across instances of the same class) and this
    instance is marked as traced. The gateway's private broker MUST be traced before the live order
    path serves (W17)."""
    if is_installed(broker):
        raise OrderAuthorityViolation(
            "order tracer already installed on this broker (double-install is a wiring bug)"
        )
    cls = type(broker)
    for name in methods:
        current = cls.__dict__.get(name)
        if not getattr(current, _GUARD_MARKER, False):       # not yet a tracer guard on this class
            original = getattr(cls, name)
            setattr(cls, name, _make_class_guard(name, original))
    handle = _TracerHandle(broker=broker, state=state, methods=tuple(methods))
    setattr(broker, _MARKER, handle)
    return handle


def uninstall(broker) -> None:
    """Un-mark ``broker`` as traced (no-op if not installed). The class guard is left in place but
    is transparent for an un-traced instance, so no order method is ever left partially wrapped."""
    try:
        delattr(broker, _MARKER)
    except AttributeError:
        pass


def is_installed(broker) -> bool:
    """Whether the always-on tracer currently guards this broker's order methods."""
    return getattr(broker, _MARKER, None) is not None


class traced_broker:
    """Context manager: mark the broker traced for the duration of a block, then un-mark it.

    ``with traced_broker(broker, state=st): ...`` — used by tests and any short-lived order
    session; the production install in ``main`` is process-lifetime (never uninstalled)."""

    def __init__(self, broker, *, state, methods: tuple[str, ...] = DEFAULT_GUARDED_METHODS):
        self._broker = broker
        self._state = state
        self._methods = methods

    def __enter__(self):
        return install(self._broker, state=self._state, methods=self._methods)

    def __exit__(self, *exc):
        uninstall(self._broker)
        return False
