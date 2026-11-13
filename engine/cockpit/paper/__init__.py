"""Cockpit paper order path (build-order item 9): one OrderGateway behind the Recursion core.

Paper only. Orders reach the book only through ``gateway.OrderGateway``, after the
core's permission, approval and authority-profile checks. Kalshi has use switches and a
kill path here, and no order path. Coinbase and Gemini have kill paths (mock transports)
and no order path; ``killswitch.KillSwitch`` halts once and reaches every venue kill.
"""
