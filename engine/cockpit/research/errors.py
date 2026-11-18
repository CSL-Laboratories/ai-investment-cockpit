"""The one refusal type shared by ``cockpit.research``, ``cockpit.decisions`` and ``cockpit.limits``."""

from __future__ import annotations

__all__ = ["Refused"]


class Refused(ValueError):
    """A value, file or step was refused. ``code`` is stable and machine-readable;
    ``detail`` is for people and never carries a key, a signature or an account secret."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
