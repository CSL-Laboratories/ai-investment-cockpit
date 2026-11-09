"""Q44 BudgetVector: no opt-in never spends; reservation defaults to 0."""

from __future__ import annotations

import unittest

from engine.budget.vector import MAX_MINOR, BudgetVector, MockMeter, Refused, RouteRecord, charge


def _opted(route_id: str = "route-1") -> RouteRecord:
    return RouteRecord(route_id, metered_spend_allowed=True)


class BudgetVectorTests(unittest.TestCase):
    def test_reservation_defaults_to_zero(self) -> None:
        vector = BudgetVector(user_cap_minor=500)
        self.assertEqual(vector.dollar_reservation_minor, 0)
        self.assertEqual(vector.spent_minor, 0)

    def test_route_opt_in_defaults_off(self) -> None:
        route = RouteRecord("route-1")
        self.assertIs(route.metered_spend_allowed, False)

    def test_no_opt_in_never_spends(self) -> None:
        route = RouteRecord("route-1", metered_spend_allowed=False)
        vector = BudgetVector(user_cap_minor=10_000, dollar_reservation_minor=10_000)
        meter = MockMeter()
        with self.assertRaises(Refused) as caught:
            meter.charge(route, vector, 1)
        self.assertEqual(caught.exception.code, "metered_spend_not_opted_in")
        self.assertEqual(meter.rows, [])
        self.assertEqual(vector.dollar_reservation_minor, 10_000)
        self.assertEqual(vector.spent_minor, 0)

    def test_default_reservation_refuses_a_charge(self) -> None:
        meter = MockMeter()
        vector = BudgetVector(user_cap_minor=100)
        with self.assertRaises(Refused) as caught:
            meter.charge(_opted(), vector, 1)
        self.assertEqual(caught.exception.code, "over_reservation")
        self.assertEqual(meter.rows, [])

    def test_charge_debits_reservation_and_counts_toward_cap(self) -> None:
        meter = MockMeter()
        vector = BudgetVector(user_cap_minor=100, dollar_reservation_minor=100)
        after = meter.charge(_opted(), vector, 40)
        self.assertEqual(after.dollar_reservation_minor, 60)
        self.assertEqual(after.spent_minor, 40)
        self.assertEqual(vector.spent_minor, 0)
        self.assertEqual(meter.rows, [{
            "route_id": "route-1",
            "amount_minor": 40,
            "reservation_after": 60,
            "spent_after": 40,
            "seq": 1,
        }])
        after = meter.charge(_opted(), after, 60)
        self.assertEqual(after.dollar_reservation_minor, 0)
        self.assertEqual(after.spent_minor, 100)
        self.assertEqual(len(meter.rows), 2)
        self.assertEqual(meter.rows[1]["seq"], 2)

    def test_second_charge_cannot_cross_the_user_cap(self) -> None:
        meter = MockMeter()
        vector = meter.charge(
            _opted(),
            BudgetVector(user_cap_minor=100, dollar_reservation_minor=1_000),
            40,
        )
        with self.assertRaises(Refused) as caught:
            meter.charge(_opted(), vector, 61)
        self.assertEqual(caught.exception.code, "over_user_cap")
        self.assertEqual(len(meter.rows), 1)
        self.assertEqual(vector.spent_minor, 40)

    def test_single_charge_over_cap_or_reservation(self) -> None:
        vector = BudgetVector(user_cap_minor=40, dollar_reservation_minor=100)
        with self.assertRaises(Refused) as caught:
            charge(_opted(), vector, 41)
        self.assertEqual(caught.exception.code, "over_user_cap")
        vector = BudgetVector(user_cap_minor=100, dollar_reservation_minor=40)
        with self.assertRaises(Refused) as caught:
            charge(_opted(), vector, 41)
        self.assertEqual(caught.exception.code, "over_reservation")

    def test_non_integers_are_refused_and_not_clamped(self) -> None:
        vector = BudgetVector(user_cap_minor=10**6, dollar_reservation_minor=10**6)
        route = _opted()
        for amount in (0, -5, 1.0, 1e9, float("nan"), float("inf"), True, False, None, "1"):
            with self.assertRaises(Refused) as caught:
                charge(route, vector, amount)
            self.assertEqual(caught.exception.code, "amount_invalid", amount)
        self.assertEqual(vector.dollar_reservation_minor, 10**6)

    def test_canonical_range(self) -> None:
        self.assertEqual(MAX_MINOR, 2**53 - 1)
        vector = BudgetVector(user_cap_minor=MAX_MINOR, dollar_reservation_minor=MAX_MINOR)
        after, row = charge(_opted(), vector, MAX_MINOR)
        self.assertEqual(row["amount_minor"], MAX_MINOR)
        self.assertEqual(after.dollar_reservation_minor, 0)
        with self.assertRaises(Refused) as caught:
            BudgetVector(user_cap_minor=MAX_MINOR + 1, dollar_reservation_minor=0)
        self.assertEqual(caught.exception.code, "amount_invalid")

    def test_construction_refuses_a_negative_or_spent_over_cap(self) -> None:
        with self.assertRaises(Refused) as caught:
            BudgetVector(user_cap_minor=-1)
        self.assertEqual(caught.exception.code, "amount_invalid")
        with self.assertRaises(Refused) as caught:
            BudgetVector(user_cap_minor=10, dollar_reservation_minor=-1)
        self.assertEqual(caught.exception.code, "amount_invalid")
        with self.assertRaises(Refused) as caught:
            BudgetVector(user_cap_minor=10, spent_minor=11)
        self.assertEqual(caught.exception.code, "invalid_budget")

    def test_opt_in_must_be_bool_and_route_id_bounded(self) -> None:
        with self.assertRaises(Refused) as caught:
            RouteRecord("route-1", metered_spend_allowed=1)  # type: ignore[arg-type]
        self.assertEqual(caught.exception.code, "opt_in_invalid")
        with self.assertRaises(Refused) as caught:
            RouteRecord("")
        self.assertEqual(caught.exception.code, "route_invalid")

    def test_refused_charge_does_not_append(self) -> None:
        meter = MockMeter()
        with self.assertRaises(Refused):
            meter.charge(RouteRecord("route-1"), BudgetVector(user_cap_minor=5), 1)
        self.assertEqual(meter.rows, [])

    def test_module_has_no_venue_or_network_path(self) -> None:
        import engine.budget.vector as vector

        with open(vector.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for token in ("socket", "urllib", "http", "alpaca", "kalshi", "coinbase", "gemini"):
            self.assertNotIn(token, source.lower())


if __name__ == "__main__":
    unittest.main()
