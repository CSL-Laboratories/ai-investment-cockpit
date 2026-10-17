# Synthetic validator controls

These invented input bytes, candidate, baseline outputs, host, configuration,
floor-result receipts and observations exercise the manifest validator only.
No benchmark was run and no floor test was executed. The commits, host, timings,
configuration and passing floor receipts are synthetic; acceptance by the
validator proves structural checks, not provenance of a real run, empirical
evidence or qualification. O8's measured tier-3 result count remains zero.

`benchmark.json` is the otherwise valid comparison used to isolate negative
controls. `unbound-fixture-baseline.json` deliberately repeats an invented fixture
digest; `baseline-units-mismatched.json` deliberately uses the wrong throughput
unit. Their references carry the actual SHA-256 of these control files so a failed
digest check cannot mask the intended semantic failure.
