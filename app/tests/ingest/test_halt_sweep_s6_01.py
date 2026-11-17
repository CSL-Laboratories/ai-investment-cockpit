"""EDGAR pipeline: an SEC halt during a run is a counted failure and revokes every grant.

Both tests patch the pipeline's collaborators, so nothing touches a database or the network.
"""
from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from filings.edgar.fetch import SecFairAccessHalt


class HaltSweepS6Item01(unittest.TestCase):
    def test_01_companyfacts_halt_revokes_all_completion_grants(self):
        from ingest import edgar_pipeline as pipeline
        from filings import delta as delta_module
        from filings.edgar import submissions_poll

        ciks = ["0000000001", "0000000002"]
        filings = [SimpleNamespace(cik=cik, accession_no=f"acc-{i}")
                   for i, cik in enumerate(ciks)]

        class Poller:
            def __init__(self, *args, **kwargs):
                pass

            def baseline(self, cik):
                return False

            def poll(self, cik):
                return SimpleNamespace(emitted=0, errors=[])

        with ExitStack() as stack:
            stack.enter_context(patch.object(submissions_poll, "SubmissionsPoller", Poller))
            stack.enter_context(patch.object(pipeline, "_filing_audit_hook", return_value=None))
            stack.enter_context(patch.object(pipeline, "_extraction_candidates", return_value=filings))
            stack.enter_context(patch.object(pipeline, "_prior_same_form", return_value=None))
            stack.enter_context(patch.object(pipeline, "_facts_by_accession", side_effect=[
                ({}, None), SecFairAccessHalt("offline://companyfacts", 429),
            ]))
            stack.enter_context(patch.object(delta_module, "compute_deltas", return_value=[]))
            stack.enter_context(patch.object(pipeline, "_persist_deltas", return_value=0))
            stack.enter_context(patch.object(pipeline, "_record_extraction_state"))
            grants = stack.enter_context(patch.object(pipeline, "_record_poll_completions"))
            result = pipeline.run_edgar_pipeline(
                None, settings=SimpleNamespace(sec_max_new_filings_per_poll=25),
                ciks=ciks, fetcher=object(), raw_store=object(), audit_log=object(),
            )
        self.assertFalse(result.ok)
        granted_ciks = [cik for call in grants.call_args_list for cik in call.args[3]]
        self.assertEqual(granted_ciks, [])

    def test_01_first_contact_halt_is_counted_as_failure(self):
        from ingest import edgar_pipeline as pipeline
        from filings.edgar import submissions_poll

        class Poller:
            def __init__(self, *args, **kwargs):
                pass

            def baseline(self, cik):
                raise SecFairAccessHalt("offline://submissions", 429)

        with patch.object(submissions_poll, "SubmissionsPoller", Poller), patch.object(
            pipeline, "_filing_audit_hook", return_value=None
        ):
            result = pipeline.run_edgar_pipeline(
                None, settings=SimpleNamespace(sec_max_new_filings_per_poll=25),
                ciks=["0000000001"], fetcher=object(), raw_store=object(), audit_log=object(),
            )
        self.assertFalse(result.ok)
        self.assertTrue(result.poll_errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
