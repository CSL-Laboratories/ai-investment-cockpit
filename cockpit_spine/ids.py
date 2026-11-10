"""The one shape of an identifier that may be displayed or recorded as an actor.

Views, the session and the service share this, so a value the session records is
a value every surface can show. A purely numeric string is not an identifier:
numbers on a screen must come from a projection.
"""

from __future__ import annotations

import re

_ID = re.compile(r"^[A-Za-z0-9:/#._@\-]{1,200}$")
_NUMERIC_ONLY = re.compile(r"^[0-9.,+\-]+$")


def is_id(text: object) -> bool:
    return isinstance(text, str) and bool(_ID.match(text)) and not _NUMERIC_ONLY.match(text)
