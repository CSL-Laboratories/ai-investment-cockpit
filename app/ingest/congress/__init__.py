"""RW-16 — congressional periodic transaction reports (PTRs) as an ingest feed.

WHAT IS IN HERE
---------------
``provider.py``     the seam: one ``DisclosureProvider`` shape for two unrelated filing systems,
                    plus the injected transport, the throttle and the provisioning gates.
``ptr_parse.py``    PURE parsers (string -> values). No I/O, ever — so every reading decision is
                    replayable against a fixture, byte for byte, without a network.
``house_clerk.py``  the House Clerk provider (per-year ZIP + XML index + one PDF per report).
``senate_efd.py``   the Senate eFD provider (terms-page handshake, then a JSON search POST).
``poller.py``       the driver: fault-isolates the chambers, dedups, stores the evidence and
                    emits one ``MarketEvent`` per readable transaction.

THE TRUST BOUNDARY — A PTR IS AN UNTRUSTED DOCUMENT
----------------------------------------------------
Everything this package reads is a document the cockpit did not author and cannot predict. A
periodic transaction report is typed by a congressional staffer, is sometimes handwritten and
scanned, is published by a small government file server with no API contract, and its asset
description is FREE TEXT that ends up in front of a model downstream. So the whole package is
written to the untrusted-input rules rather than to the "parse a vendor's JSON" rules:

  * every free-text field that survives into a ``copytrade.models.Disclosure`` has already been
    through ``ingest.normalize.escape_untrusted`` at the parse boundary (SEC-02/M3), not at some
    later display site a future caller might bypass;
  * every structured field is pattern-validated and a row that cannot be read CONFIDENTLY is a
    COUNTED refusal with a named reason — never a guess, never a lenient fallback. The failure
    mode of a lenient parser here is not a missing row, it is a confidently wrong row heading
    toward the order path;
  * document ids are ASCII-pattern-gated before they are interpolated into a URL path or into
    ``Disclosure.raw_ref``, because a "cleaned" id points at a DIFFERENT document than the index
    meant and ``raw_ref`` is carried into prompt-visible citation text;
  * raw bytes are hashed and kept separate from anything derived (Invariant 8), so a row's
    ``content_hash`` chains back to the fetched document and a claim can be re-checked rather
    than trusted.

Nothing in this package is on the order path, and no model is on this path: disclosures are
EVIDENCE. Turning evidence into a proposal is ``copytrade/`` s job, and reaching a broker still
requires risk-pass -> mode -> approval -> ``OrderGateway`` like everything else (Invariant 1).

NO IMPORT SIDE EFFECTS. This file deliberately re-exports nothing and imports nothing: the
submodules pull in SQLAlchemy, the parsers and the transport adapters at very different times,
and a convenience re-export here would make ``import ingest.congress`` drag the whole feed —
and its import-order sensitivities — into any module that only wanted one parser.
"""
