"""Minimal daemon-served operator surface (build-order item 10); no Tauri shell or execution path.

``surface.Application`` is a WSGI app over ``client.ControlClient``; ``inventory`` holds
the route rubric. No order path, no own Stop, no socket binding.
"""
