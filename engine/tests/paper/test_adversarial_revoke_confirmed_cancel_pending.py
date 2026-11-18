"""Adversarial: revoke confirmed while cancel pending must reach a distinguishable state.

Once ``revoke_grant`` / ``revoke_key`` is ``confirmed`` with the cancel leg still pending, every
later ``kill()`` re-runs the cancel against a revoked credential (PermissionError), so the venue
reports the same retryable ``pending`` forever. A monitor polling for ``confirmed`` cannot tell
"retry may help" from "credential dead, orders unverifiable". This test requires the later kills
to expose a typed pending cancel state (``pending_credential_revoked``) rather than the identical
plain ``pending``; the cancel stays pending and is still retried. Mocks only.
"""

from __future__ import annotations

import unittest

from cockpit.paper.coinbase import CoinbaseKill
from cockpit.paper.gemini import GeminiKill
from cockpit.paper.kalshi import KalshiKill
from cockpit.paper.revoke import RevokeReply


class _Venue:
    """Cancel/list time out while the credential is live; PermissionError once revoked."""

    def __init__(self) -> None:
        self.live = True

    def _call(self):
        if not self.live:
            raise PermissionError("unauthorized")
        raise TimeoutError("venue slow")

    # Gemini / Kalshi
    def cancel_all(self):
        self._call()

    # Gemini / Kalshi / Coinbase (Coinbase passes a cursor)
    def list_open_orders(self, cursor=None):
        self._call()

    def cancel_order(self, order_id):
        self._call()

    # Gemini / Coinbase
    def revoke_oauth_grant(self):
        self.live = False
        return RevokeReply(200)

    def probe_api_call(self):
        return "authorized" if self.live else "unauthorized"

    def probe_refresh_token_exchange(self):
        return "authorized" if self.live else "unauthorized"

    # Kalshi
    def revoke_api_key(self):
        self.live = False
        return {"accepted": True}

    def probe_authenticated(self):
        return "authorized" if self.live else "unauthorized"


VENUES = (
    ("gemini", GeminiKill, "cancel_all", "revoke_grant"),
    ("coinbase", CoinbaseKill, "cancel_orders", "revoke_grant"),
    ("kalshi", KalshiKill, "cancel_all", "revoke_key"),
)


class RevokeConfirmedCancelPendingTests(unittest.TestCase):
    def test_later_kills_distinguish_terminal_from_retryable_pending(self) -> None:
        for name, cls, cancel_key, revoke_key in VENUES:
            with self.subTest(venue=name):
                kill = cls(lambda: None, _Venue())
                first = kill.kill()
                # Kill 1: cancel timed out while live (retryable), revoke confirmed.
                self.assertEqual(first[revoke_key], "confirmed")
                self.assertEqual(first[cancel_key], "pending")
                self.assertEqual(first["detail"].get(cancel_key), "error:TimeoutError"
                                 if name != "coinbase" else "list_error:TimeoutError")
                later = [kill.kill(), kill.kill()]
                for n, out in enumerate(later, start=2):
                    self.assertEqual(out[revoke_key], "confirmed")
                    self.assertNotEqual(
                        (out["status"], out[cancel_key]), ("pending", "pending"),
                        "%s kill %d after a confirmed revoke is the same retryable pending as kill 1 "
                        "(detail %s=%r); no terminal cancel_unverifiable-like state exists"
                        % (name, n, cancel_key, out["detail"].get(cancel_key)))
                    self.assertNotEqual(out["status"], "confirmed")  # never a false confirm


if __name__ == "__main__":
    unittest.main()
