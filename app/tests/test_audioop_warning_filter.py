"""The pytest audioop filter ignores one third-party warning and nothing else.

discord.py is not imported. The probe emits warnings with warn_explicit so the
result does not depend on a discord.py release.
"""

from __future__ import annotations

import warnings

_AUDIOOP = "'audioop' is deprecated and slated for removal in Python 3.13"
_FILTER = (
    "ignore:'audioop' is deprecated and slated for removal in Python 3\\.13\\Z"
    ":DeprecationWarning:discord\\.player\\Z"
)


def _emit(message: str, category: type[Warning], module: str, lineno: int) -> None:
    warnings.warn_explicit(
        message,
        category,
        filename="<audioop-filter-probe>",
        lineno=lineno,
        module=module,
    )


def test_audioop_filter_suppresses_only_the_discord_player_deprecation(pytestconfig):
    """Pytest's configured filter drops the exact warning and records the rest.

    The ``filterwarnings`` equality is an intentional tripwire. Adding a second
    filter fails this test on purpose, so a broader ignore cannot land quietly.
    """
    assert list(pytestconfig.getini("filterwarnings")) == [_FILTER]

    # Nested catch_warnings records whatever the live pytest filters let through.
    # Do not call simplefilter(): that would be checked before the ini ignore.
    with warnings.catch_warnings(record=True) as caught:
        _emit(_AUDIOOP, DeprecationWarning, "discord.player", 30)
        _emit(_AUDIOOP, DeprecationWarning, "other.module", 31)
        _emit(_AUDIOOP, DeprecationWarning, "discord.player.sub", 32)
        _emit(_AUDIOOP, DeprecationWarning, "discord.players", 33)
        _emit(_AUDIOOP + " extra", DeprecationWarning, "discord.player", 34)
        _emit("cockpit deprecation stays visible", DeprecationWarning, "discord.player", 35)
        _emit(_AUDIOOP, UserWarning, "discord.player", 36)

    seen = {(str(item.message), item.category, item.lineno) for item in caught}
    assert (_AUDIOOP, DeprecationWarning, 30) not in seen
    assert (_AUDIOOP, DeprecationWarning, 31) in seen
    assert (_AUDIOOP, DeprecationWarning, 32) in seen
    assert (_AUDIOOP, DeprecationWarning, 33) in seen
    assert (_AUDIOOP + " extra", DeprecationWarning, 34) in seen
    assert ("cockpit deprecation stays visible", DeprecationWarning, 35) in seen
    assert (_AUDIOOP, UserWarning, 36) in seen
