"""The signed caps and allowlist source. Every key, account and venue here is made up."""

import copy
import os
import stat
import tempfile
import unittest

from cockpit.limits import (LIMITS_SCHEMA, FileHighWater, Limits, LimitsSource, MemoryHighWater,
                            create_high_water, signing_input)
from cockpit.research.canonical import canonical, parse
from cockpit.research.errors import Refused

from .fakesign import KEY, OTHER_KEY, FakeVerifier, key_id, sign

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
NOW = 1_800_000_000_000
ACCOUNT, VENUE = "paper-acct-0001", "fakevenue"
POSIX = hasattr(os, "getuid")


def fixture_bytes():
    with open(os.path.join(FIXTURES, "limits_signed.json"), "rb") as f:
        return f.read()


def fixture_document():
    return parse(fixture_bytes()[:-1])["document"]


def envelope(document, signature=None, key=KEY):
    sig = sign(document, key) if signature is None else signature
    return canonical({"document": document, "signature": sig})


def source(high_water=None, **kw):
    args = dict(verifier=FakeVerifier(), account=ACCOUNT, venue=VENUE, mode="paper",
                high_water=high_water if high_water is not None else MemoryHighWater())
    args.update(kw)
    return LimitsSource(**args)


class StrSub(str):
    pass


class IntSub(int):
    pass


class LimitsFixture(unittest.TestCase):
    def test_signed_fixture_loads(self):
        limits = source().load(fixture_bytes(), now_ms=NOW)
        self.assertIs(type(limits), Limits)
        self.assertEqual((limits.account, limits.venue, limits.mode), (ACCOUNT, VENUE, "paper"))
        self.assertEqual(limits.gateway_caps(), {"max_notional_minor": 50000, "max_quantity": 10})
        self.assertEqual(limits.allowlist, frozenset({"FAKE-001", "FAKE-002"}))
        self.assertEqual((limits.approval_id, limits.approved_by), ("appr-made-up-0001", "operator-made-up"))
        with open(os.path.join(FIXTURES, "limits_document_digest.txt"), encoding="ascii") as f:
            self.assertEqual(limits.digest, f.read().strip())

    def test_fixture_signature_is_over_the_prefixed_canonical_document(self):
        doc = fixture_document()
        self.assertEqual(doc["schema"], LIMITS_SCHEMA)
        self.assertEqual(doc["issuer_key_id"], key_id())
        self.assertTrue(signing_input(doc).startswith(b"cockpit.limits/1\n"))
        self.assertEqual(parse(fixture_bytes()[:-1])["signature"], sign(doc))

    def test_allows_only_exact_strings_on_the_list(self):
        limits = source().load(fixture_bytes(), now_ms=NOW)
        self.assertTrue(limits.allows("FAKE-001"))
        for bad in ("FAKE-003", "fake-001", "FAKE-001 ", StrSub("FAKE-001"), None, b"FAKE-001", ["FAKE-001"]):
            with self.subTest(bad=repr(bad)):
                self.assertFalse(limits.allows(bad))

    def test_limits_are_immutable(self):
        limits = source().load(fixture_bytes(), now_ms=NOW)
        with self.assertRaises(AttributeError):
            limits.max_quantity = 10**6
        self.assertIs(type(limits.allowlist), frozenset)

    def test_is_current(self):
        limits = source().load(fixture_bytes(), now_ms=NOW)
        self.assertTrue(limits.is_current(NOW))
        self.assertFalse(limits.is_current(limits.expires_at))
        self.assertFalse(limits.is_current(limits.not_before - 1))
        self.assertFalse(limits.is_current(float(NOW)))


class Signatures(unittest.TestCase):
    def refused(self, raw, code, src=None):
        with self.assertRaises(Refused) as ctx:
            (src or source()).load(raw, now_ms=NOW)
        self.assertEqual(ctx.exception.code, code, ctx.exception)

    def test_unsigned(self):
        self.refused(canonical({"document": fixture_document()}), "unsigned")
        self.refused(canonical(fixture_document()), "unsigned")

    def test_malformed_signatures(self):
        good = sign(fixture_document())
        for bad in ("", "0", good.upper(), good + "0", "zz" * 32, " " + good, "ab" * 513):
            with self.subTest(bad=bad[:12]):
                self.refused(envelope(fixture_document(), bad), "signature_malformed")
        for bad in (None, 1, [good], {"hex": good}, True):
            with self.subTest(bad=repr(bad)[:12]):
                self.refused(canonical({"document": fixture_document(), "signature": bad}), "signature_malformed")

    def test_wrong_signatures(self):
        doc = fixture_document()
        self.refused(envelope(doc, sign(doc, OTHER_KEY)), "signature_invalid")
        self.refused(envelope(doc, "00" * 32), "signature_invalid")
        self.refused(envelope(doc, sign(doc)[:-2]), "signature_invalid")
        raw = fixture_bytes()
        bumped = raw.replace(b'"max_notional_minor":50000', b'"max_notional_minor":5000000')
        self.assertNotEqual(bumped, raw)
        self.refused(bumped, "signature_invalid")
        widened = raw.replace(b'"allowlist":["FAKE-001","FAKE-002"]', b'"allowlist":["FAKE-001","FAKE-002","FAKE-003"]')
        self.assertNotEqual(widened, raw)
        self.refused(widened, "signature_invalid")

    def test_signature_by_another_key_with_its_own_key_id(self):
        doc = fixture_document()
        doc["issuer_key_id"] = key_id(OTHER_KEY)
        self.refused(envelope(doc, sign(doc, OTHER_KEY)), "wrong_key")
        for bad in (None, 1, key_id().upper(), key_id() + " "):
            doc = fixture_document()
            doc["issuer_key_id"] = bad
            with self.subTest(bad=repr(bad)):
                self.refused(canonical({"document": doc, "signature": "00"}), "wrong_key")

    def test_only_an_exact_true_from_the_verifier_counts(self):
        class Truthy:
            key_id = key_id()

            def __init__(self, answer):
                self.answer = answer

            def verify(self, message, signature):
                if isinstance(self.answer, BaseException):
                    raise self.answer
                return self.answer

        for answer in (1, "yes", [True], object(), RuntimeError("boom"), ValueError("x"), None):
            with self.subTest(answer=repr(answer)):
                self.refused(fixture_bytes(), "signature_invalid", source(verifier=Truthy(answer)))

    def test_verifier_is_captured_at_construction(self):
        verifier = FakeVerifier()
        src = source(verifier=verifier)
        verifier.key_id = key_id(OTHER_KEY)
        verifier.verify = lambda message, signature: True
        doc = fixture_document()
        self.refused(envelope(doc, "00" * 32), "signature_invalid", src)
        self.assertEqual(src.load(fixture_bytes(), now_ms=NOW).limits_id, "limits-made-up-0001")

    def test_bad_verifiers(self):
        for bad in (None, object(), type("V", (), {"key_id": "k"})(), type("V", (), {"verify": print})(),
                    type("V", (), {"key_id": 1, "verify": print})(), type("V", (), {"key_id": "K!", "verify": print})()):
            with self.subTest(bad=repr(bad)[:30]):
                with self.assertRaises(Refused) as ctx:
                    source(verifier=bad)
                self.assertEqual(ctx.exception.code, "verifier_invalid")


class Scope(unittest.TestCase):
    def refused(self, raw, code, src=None, now=NOW):
        with self.assertRaises(Refused) as ctx:
            (src or source()).load(raw, now_ms=now)
        self.assertEqual(ctx.exception.code, code, ctx.exception)

    def test_for_another_account_venue_or_mode(self):
        for key, value in (("account", "paper-acct-0002"), ("venue", "othervenue")):
            doc = fixture_document()
            doc["scope"][key] = value
            with self.subTest(key=key):
                self.refused(envelope(doc), "scope_mismatch")
        self.refused(fixture_bytes(), "scope_mismatch", source(account="paper-acct-0002"))
        self.refused(fixture_bytes(), "scope_mismatch", source(venue="othervenue"))

    def test_live_is_not_authorised(self):
        doc = fixture_document()
        doc["scope"]["mode"] = "live"
        self.refused(envelope(doc), "live_not_authorised")
        for mode in ("live", "Paper", "paper ", "auto"):
            with self.subTest(mode=mode):
                with self.assertRaises(Refused) as ctx:
                    source(mode=mode)
                self.assertIn(ctx.exception.code, ("live_not_authorised", "invalid_value"))

    def test_window(self):
        doc = fixture_document()
        self.refused(fixture_bytes(), "not_yet_valid", now=doc["not_before"] - 1)
        self.refused(fixture_bytes(), "expired", now=doc["expires_at"])
        source().load(fixture_bytes(), now_ms=doc["not_before"])
        doc["expires_at"] = doc["not_before"]
        self.refused(envelope(doc), "invalid_value")
        for bad in (1.0 * NOW, True, None, -1, 2**53, IntSub(NOW)):
            with self.subTest(now=repr(bad)):
                self.refused(fixture_bytes(), "clock_invalid", now=bad)

    def test_operator_approval_dated_after_now(self):
        doc = fixture_document()
        doc["operator_approval"]["approved_at"] = NOW + 1
        self.refused(envelope(doc), "invalid_value")


class Replay(unittest.TestCase):
    def doc(self, sequence, cap=50000):
        doc = fixture_document()
        doc["sequence"] = sequence
        doc["caps"]["max_notional_minor"] = cap
        return doc

    def test_rollback_and_conflict_in_memory(self):
        src = source()
        src.load(envelope(self.doc(2)), now_ms=NOW)
        src.load(envelope(self.doc(2)), now_ms=NOW)  # the same file again is the same limits
        for raw, code in ((fixture_bytes(), "rollback"), (envelope(self.doc(2, cap=60000)), "sequence_conflict")):
            with self.subTest(code=code):
                with self.assertRaises(Refused) as ctx:
                    src.load(raw, now_ms=NOW)
                self.assertEqual(ctx.exception.code, code)
        self.assertEqual(src.load(envelope(self.doc(3)), now_ms=NOW).sequence, 3)

    def test_replay_floor_survives_a_restart_in_its_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "limits-high-water.json")
            create_high_water(path)
            if POSIX:
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode) & 0o077, 0)
            source(FileHighWater(path)).load(envelope(self.doc(5)), now_ms=NOW)
            again = source(FileHighWater(path))  # a new process, the same file
            with self.assertRaises(Refused) as ctx:
                again.load(envelope(self.doc(4)), now_ms=NOW)
            self.assertEqual(ctx.exception.code, "rollback")
            again.load(envelope(self.doc(5)), now_ms=NOW)
            self.assertEqual(sorted(os.listdir(tmp)),  # no temp file left
                             ["limits-high-water.json", "limits-high-water.json.lock"])
            with self.assertRaises(Refused) as ctx:
                create_high_water(path)
            self.assertEqual(ctx.exception.code, "high_water_exists")

    def test_scopes_have_separate_floors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hw")
            create_high_water(path)
            source(FileHighWater(path)).load(envelope(self.doc(9)), now_ms=NOW)
            doc = self.doc(1)
            doc["scope"]["account"] = "paper-acct-0002"
            limits = source(FileHighWater(path), account="paper-acct-0002").load(envelope(doc), now_ms=NOW)
            self.assertEqual(limits.sequence, 1)

    def test_missing_or_tampered_floor_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hw")
            with self.assertRaises(Refused) as ctx:
                source(FileHighWater(path)).load(fixture_bytes(), now_ms=NOW)
            self.assertEqual(ctx.exception.code, "high_water_missing")
            create_high_water(path)
            for bad in (b"", b"{}", b"not json", canonical({"schema": "x", "entries": {}}),
                        canonical({"schema": "cockpit.limits_high_water/1", "entries": {"k": {"sequence": 0,
                                                                                               "digest": "x"}}})):
                with self.subTest(bad=bad[:20]):
                    with open(path, "wb") as f:
                        f.write(bad)
                    os.chmod(path, 0o600)
                    with self.assertRaises(Refused) as ctx:
                        source(FileHighWater(path)).load(fixture_bytes(), now_ms=NOW)
                    self.assertEqual(ctx.exception.code, "high_water_invalid")

    @unittest.skipUnless(POSIX, "POSIX owners")
    def test_floor_file_open_to_others_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hw")
            create_high_water(path)
            os.chmod(path, 0o644)
            with self.assertRaises(Refused) as ctx:
                source(FileHighWater(path)).load(fixture_bytes(), now_ms=NOW)
            self.assertEqual(ctx.exception.code, "high_water_not_owner_only")

    def test_a_refused_file_does_not_move_the_floor(self):
        src = source()
        doc = self.doc(7)
        doc["scope"]["account"] = "paper-acct-0002"
        with self.assertRaises(Refused):
            src.load(envelope(doc), now_ms=NOW)
        self.assertEqual(src.load(envelope(self.doc(6)), now_ms=NOW).sequence, 6)


class DocumentFields(unittest.TestCase):
    def refused(self, doc, code):
        with self.assertRaises(Refused) as ctx:
            source().load(envelope(doc), now_ms=NOW)
        self.assertEqual(ctx.exception.code, code, ctx.exception)

    def test_missing_and_unknown_fields(self):
        for key in fixture_document():
            if key == "issuer_key_id":
                continue
            doc = fixture_document()
            del doc[key]
            with self.subTest(missing=key):
                self.refused(doc, "missing_field")
        for where in (None, "scope", "caps", "operator_approval"):
            doc = fixture_document()
            (doc if where is None else doc[where])["extra"] = 1
            with self.subTest(unknown=where):
                self.refused(doc, "unknown_field")
        doc = fixture_document()
        doc["caps"]["max_position_minor"] = 1
        self.refused(doc, "unknown_field")

    def test_schema(self):
        for bad in ("cockpit.limits/2", "", None, 1):
            doc = fixture_document()
            doc["schema"] = bad
            with self.subTest(bad=repr(bad)):
                self.refused(doc, "schema_mismatch")

    def test_caps(self):
        for key in ("max_notional_minor", "max_quantity"):
            for bad, code in ((0, "out_of_range"), (-1, "out_of_range"), (True, "wrong_type"), ("1", "wrong_type"),
                              (None, "wrong_type"), ([1], "wrong_type")):
                doc = fixture_document()
                doc["caps"][key] = bad
                with self.subTest(key=key, bad=repr(bad)):
                    self.refused(doc, code)

    def test_numbers_that_canonical_bytes_cannot_hold_are_refused_before_the_signature(self):
        raw = fixture_bytes()
        for literal in (b"50000.0", b"5e4", b"NaN", b"Infinity", b"9007199254740992", b"-0", b"050000"):
            bad = raw.replace(b'"max_notional_minor":50000', b'"max_notional_minor":' + literal)
            with self.subTest(literal=literal):
                with self.assertRaises(Refused) as ctx:
                    source().load(bad, now_ms=NOW)
                self.assertEqual(ctx.exception.code, "noncanonical_value")

    def test_allowlist(self):
        for bad, code in (([], "invalid_length"), (["FAKE-001", "FAKE-001"], "duplicate_value"),
                          (["fake-001"], "invalid_value"), ([1], "wrong_type"), ("FAKE-001", "wrong_type"),
                          ([["FAKE-001"]], "wrong_type"), (["FAKE-%d" % i for i in range(1001)], "invalid_length")):
            doc = fixture_document()
            doc["allowlist"] = bad
            with self.subTest(bad=repr(bad)[:30]):
                self.refused(doc, code)

    def test_ids_and_text(self):
        cases = (
            (("limits_id",), "Limits"), (("currency",), "usd"), (("currency",), "USDT"),
            (("scope", "account"), "Paper"), (("scope", "account"), "a|b"), (("scope", "venue"), "Venue"),
            (("operator_approval", "approved_by"), ""), (("operator_approval", "approval_id"), "A"),
        )
        for path, bad in cases:
            doc = fixture_document()
            target = doc
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = bad
            with self.subTest(path=path, bad=bad):
                self.refused(doc, "invalid_value")

    def test_sequence(self):
        for bad, code in ((0, "out_of_range"), (-1, "out_of_range"), (True, "wrong_type"), ("1", "wrong_type")):
            doc = fixture_document()
            doc["sequence"] = bad
            with self.subTest(bad=repr(bad)):
                self.refused(doc, code)


class RawBytes(unittest.TestCase):
    def refused(self, raw, code):
        with self.assertRaises(Refused) as ctx:
            source().load(raw, now_ms=NOW)
        self.assertEqual(ctx.exception.code, code)

    def test_only_exact_canonical_bytes(self):
        raw = fixture_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        source().load(raw[:-1], now_ms=NOW)
        for bad in (raw.decode("ascii"), bytearray(raw), memoryview(raw), None):
            with self.subTest(type=type(bad).__name__):
                self.refused(bad, "not_bytes")
        for bad in (raw + b"\n", b" " + raw, raw.replace(b'{"document":', b'{ "document":'),
                    b"\xef\xbb\xbf" + raw, raw.replace(b"\n", b"\r\n")):
            with self.subTest(bad=bad[:12]):
                self.refused(bad, "noncanonical_value")
        self.refused(b" " * (256 * 1024 + 1), "too_large")

    def test_duplicate_keys(self):
        raw = fixture_bytes()
        dup = raw.replace(b'{"document":', b'{"document":{},"document":', 1)
        with self.assertRaises(Refused) as ctx:
            source().load(dup, now_ms=NOW)
        self.assertIn(ctx.exception.code, ("duplicate_key", "noncanonical_value"))

    def test_envelope_shape(self):
        doc = fixture_document()
        self.refused(canonical({"document": doc, "signature": sign(doc), "extra": 1}), "envelope_invalid")
        self.refused(canonical([doc]), "envelope_invalid")
        self.refused(canonical({"document": [doc], "signature": "00"}), "envelope_invalid")
        self.refused(canonical({"signature": "00"}), "envelope_invalid")

    def test_non_ascii_and_lone_surrogates_cannot_be_signed_or_loaded(self):
        doc = fixture_document()
        doc["limits_id"] = "limits-" + chr(0xE9)
        with self.assertRaises(Refused):
            sign(doc)
        raw = fixture_bytes().replace(b"limits-made-up-0001", b"limits-made-up-\\ud800")
        self.refused(raw, "noncanonical_value")
        raw = fixture_bytes().replace(b"limits-made-up-0001", "limits-made-up-".encode() + bytes([0xC3, 0xA9]))
        self.refused(raw, "noncanonical_value")


if __name__ == "__main__":
    unittest.main()
