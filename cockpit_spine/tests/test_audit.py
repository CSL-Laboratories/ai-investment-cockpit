from __future__ import annotations

import copy
import unittest

from cockpit_spine.audit import GENESIS, AuditChain, AuditError, verify_chain


def make(n: int = 3) -> AuditChain:
    chain = AuditChain()
    for i in range(n):
        chain.append("k", {"i": i})
    return chain


class AuditChainTests(unittest.TestCase):
    def test_intact_chain_verifies_and_head_matches(self) -> None:
        chain = make()
        self.assertEqual(chain.head, verify_chain(chain.records()))
        self.assertEqual(GENESIS, verify_chain([]))

    def test_every_kind_of_tampering_is_caught(self) -> None:
        records = make(4).records()

        def edit(bad: list) -> None:
            bad[1]["data"]["i"] = 99

        def delete(bad: list) -> None:
            del bad[1]

        def reorder(bad: list) -> None:
            bad[1], bad[2] = bad[2], bad[1]

        def relink(bad: list) -> None:
            bad[2]["prev"] = GENESIS

        def extra_key(bad: list) -> None:
            bad[0]["note"] = "x"

        def resequence(bad: list) -> None:
            bad[0]["seq"] = 2

        for mutate in (edit, delete, reorder, relink, extra_key, resequence):
            bad = copy.deepcopy(records)
            mutate(bad)
            with self.assertRaises(AuditError, msg=mutate.__name__):
                verify_chain(bad)

    def test_a_prefix_is_still_a_valid_chain(self) -> None:
        records = make(4).records()
        self.assertEqual(records[1]["hash"], verify_chain(records[:2]))

    def test_non_list_and_non_dict_inputs_are_refused(self) -> None:
        for bad in (None, {}, "x", [1], [None]):
            with self.assertRaises(AuditError):
                verify_chain(bad)

    def test_records_returned_are_copies(self) -> None:
        chain = make(1)
        chain.records()[0]["data"]["i"] = 5
        self.assertEqual(0, chain.records()[0]["data"]["i"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
