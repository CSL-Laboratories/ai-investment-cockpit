"""RW-16 — the congressional copy-trading CONFIG surface, checked where the operator is.

Every ``COCKPIT_CONGRESS_*`` name is a knob on a path that PROPOSES ORDERS off a signal that
is, by law, up to 45 days old. That combination makes the config the first safety boundary, so
this module pins four properties rather than merely "the fields exist":

  * **The documented default is the shipped default.** ``.env.example`` is how an operator
    provisions; a comment that says 45 while the class says 30 is a control they think they
    have. Each default is asserted against the class AND the template is asserted to boot
    verbatim with the feature OFF.
  * **A bad value fails at STARTUP, next to the file the operator just saved.** The copy engine
    would raise at PLAN time — hours or days later, when the failure looks like "the feature
    does nothing" rather than "line 3 is wrong". Worse, some malformed values are not loud even
    then: ``parse_follow_list(";;;")`` returns an EMPTY allowlist, and an empty allowlist
    follows NOBODY by design, so "I typo'd the separators" and "I configured nobody" are
    indistinguishable downstream. That is the control test below, and it is why the eager
    validator has to exist.
  * **Every numeric knob is bounded in BOTH directions.** A staleness gate that accepts 3650
    is not a gate; a copy fraction that accepts 5 is not a fraction.
  * **``COCKPIT_CONGRESS_USER_AGENT`` is NON-SECRET and must stay bootable.** It is a name and
    an email published to the remote host on every request (Inv 4 governs credentials, not
    contact strings). Asserted against the REAL ``assert_no_secrets_in_env_file`` — never
    against ``_SECRETISH_ALLOWED``, because the allowlist is an implementation detail of the
    guard and asserting it would pass even if the guard stopped calling it. The pass is made
    causal by a counter-control: a credential-SHAPED congress name is still refused.
"""
from __future__ import annotations

import os
import re
from decimal import Decimal
from pathlib import Path

import pytest

from config.settings import (
    EnvFileSecretError,
    Settings,
    assert_no_secrets_in_env_file,
)
from copytrade.policy import CopyPolicy, PolicyError, parse_follow_list, policy_from_settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"

SECRET_VALUE = "sk-live-do-not-log-me-0123456789"


def write_env(tmp_path, body: str) -> Path:
    p = tmp_path / ".env"
    p.write_text(body, encoding="utf-8")
    return p


def settings_from(tmp_path, monkeypatch, *lines: str) -> Settings:
    """Build Settings from a small .env, with the ambient shell provably out of the way.

    The env-FILE path (not a kwarg) on purpose: that is the path an operator actually uses, and
    it is the one where a validator that only runs on direct construction would be missed.

    VARIADIC SINCE RW-17, for one reason. ``COCKPIT_CONGRESS_AUTOEXEC=true`` on its own is a
    deliberate STARTUP REFUSAL — an auto-execution lane with the congress feed off, or with an
    empty follow list, boots clean and can never trade, and the operator (who has stopped
    watching an approval queue on purpose) would wait forever on notifications that were never
    coming. So a probe for that knob carries the minimum COMPANION lines that make the
    configuration legal, and nothing more. See ``_COMPANIONS``.
    """
    env = write_env(tmp_path, "\n".join(lines) + "\n")
    for line in lines:
        monkeypatch.delenv(line.split("=", 1)[0], raising=False)
    return Settings(_env_file=str(env))


# ---------------------------------------------------------------------------------------
# the table: env name, attribute, DOCUMENTED default, a non-default raw value, its parse
# ---------------------------------------------------------------------------------------
CONGRESS_SETTINGS = [
    ("COCKPIT_CONGRESS_ENABLED", "congress_enabled", False, "true", True),
    ("COCKPIT_CONGRESS_USER_AGENT", "congress_user_agent", "",
     "Cockpit ops@example.com", "Cockpit ops@example.com"),
    ("COCKPIT_CONGRESS_RATE_PER_SEC", "congress_rate_per_sec", 0.5, "1.5", 1.5),
    ("COCKPIT_CONGRESS_FOLLOW", "congress_follow", "", "Pelosi, Nancy", "Pelosi, Nancy"),
    ("COCKPIT_CONGRESS_MAX_AGE_DAYS", "congress_max_age_days", 45, "20", 20),
    ("COCKPIT_CONGRESS_STALENESS_DECAY", "congress_staleness_decay",
     Decimal("0.5"), "0.25", Decimal("0.25")),
    ("COCKPIT_CONGRESS_COPY_FRACTION", "congress_copy_fraction",
     Decimal("0.02"), "0.01", Decimal("0.01")),
    ("COCKPIT_CONGRESS_MAX_NOTIONAL_PER_TRADE", "congress_max_notional_per_trade",
     Decimal("500"), "250", Decimal("250")),
    ("COCKPIT_CONGRESS_MIN_NOTIONAL_PER_TRADE", "congress_min_notional_per_trade",
     Decimal("25"), "10", Decimal("10")),
    ("COCKPIT_CONGRESS_MIN_AMOUNT_LOW", "congress_min_amount_low",
     Decimal("1001"), "15001", Decimal("15001")),
    ("COCKPIT_CONGRESS_OPTIONS_POLICY", "congress_options_policy",
     "underlying", "ignore", "ignore"),
    ("COCKPIT_CONGRESS_OPTIONS_HAIRCUT", "congress_options_haircut",
     Decimal("0.25"), "0.5", Decimal("0.5")),
    ("COCKPIT_CONGRESS_OPTIONS_MIN_DAYS_TO_EXPIRY", "congress_options_min_days_to_expiry",
     30, "60", 60),
    ("COCKPIT_CONGRESS_MIRROR_SELLS", "congress_mirror_sells", True, "false", False),
    ("COCKPIT_CONGRESS_MAX_UNIVERSE_SYMBOLS", "congress_max_universe_symbols", 40, "10", 10),
    ("COCKPIT_CONGRESS_UNIVERSE_TTL_DAYS", "congress_universe_ttl_days", 120, "30", 30),
    ("COCKPIT_CONGRESS_SENATE_ENABLED", "congress_senate_enabled", False, "true", True),
    # ---- RW-17: the zero-touch PAPER auto-execution lane -------------------------------
    ("COCKPIT_CONGRESS_AUTOEXEC", "congress_autoexec", False, "true", True),
    ("COCKPIT_CONGRESS_AUTOEXEC_WINDOW_SECONDS", "congress_autoexec_window_seconds",
     300, "600", 600),
    ("COCKPIT_CONGRESS_AUTOEXEC_TICKET_MAX_AGE_SECONDS",
     "congress_autoexec_ticket_max_age_seconds", 21_600, "3600", 3600),
    ("COCKPIT_CONGRESS_AUTOEXEC_MAX_DAILY_NOTIONAL", "congress_autoexec_max_daily_notional",
     Decimal("500"), "1000", Decimal("1000")),
    ("COCKPIT_CONGRESS_AUTOEXEC_MAX_TRADES_PER_DAY", "congress_autoexec_max_trades_per_day",
     3, "5", 5),
    ("COCKPIT_CONGRESS_AUTOEXEC_MAX_OPEN_POSITIONS", "congress_autoexec_max_open_positions",
     10, "4", 4),
    ("COCKPIT_CONGRESS_AUTOEXEC_MAX_NOTIONAL_PER_SYMBOL_PER_DAY",
     "congress_autoexec_max_notional_per_symbol_per_day",
     Decimal("250"), "100", Decimal("100")),
]
_IDS = [row[1] for row in CONGRESS_SETTINGS]

# The knobs that cannot legally be set ALONE, with the minimum companions that make them legal.
# Only the master switch needs any: every rail below it is inert while the lane is off, which is
# itself the property being relied on here.
_COMPANIONS = {
    "COCKPIT_CONGRESS_AUTOEXEC": ("COCKPIT_CONGRESS_ENABLED=true",
                                  "COCKPIT_CONGRESS_FOLLOW=Pelosi, Nancy"),
}


def test_the_table_below_covers_every_congress_setting_there_is():
    """A knob added without a row here would ship untested, undocumented and unbounded.

    This is the anti-drift control for the whole module: every other test in it is
    parametrized off ``CONGRESS_SETTINGS``, so a table that silently falls behind
    ``Settings`` would turn all of them green while covering less.
    """
    declared = {name for name in Settings.model_fields if name.startswith("congress_")}
    covered = {row[1] for row in CONGRESS_SETTINGS}
    assert declared == covered, (
        f"untested congress setting(s): {sorted(declared - covered)}; "
        f"stale row(s): {sorted(covered - declared)}"
    )


# ---------------------------------------------------------------------------------------
# defaults + parsing
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("env,attr,default,raw,parsed", CONGRESS_SETTINGS, ids=_IDS)
def test_every_congress_setting_has_its_documented_default(env, attr, default, raw, parsed):
    """The unconfigured value. ``Settings()`` reads no COCKPIT_* here (root conftest wipes
    them), so this is the class's own default and not the developer's shell."""
    del env, raw, parsed
    value = getattr(Settings(), attr)
    assert value == default
    assert type(value) is type(default), f"{attr} parsed as {type(value)}, expected {type(default)}"


@pytest.mark.parametrize("env,attr,default,raw,parsed", CONGRESS_SETTINGS, ids=_IDS)
def test_every_congress_setting_parses_from_the_env_file(tmp_path, monkeypatch,
                                                         env, attr, default, raw, parsed):
    """Durability: the value reaches Settings from the FILE, with no shell export — the RW-10
    fault this whole config layer exists to prevent, now for 17 more names."""
    assert parsed != default, f"{attr}: the probe value must differ from the default"
    lines = (f"{env}={raw}", *_COMPANIONS.get(env, ()))
    assert getattr(settings_from(tmp_path, monkeypatch, *lines), attr) == parsed


@pytest.mark.parametrize("env,attr,default,raw,parsed", CONGRESS_SETTINGS, ids=_IDS)
def test_a_blank_congress_value_means_unset_not_a_parse_error(tmp_path, monkeypatch,
                                                              env, attr, default, raw, parsed):
    """``cp .env.example .env`` leaves blanks. A blank must fall back to the default, never
    take the process down over a knob the operator has not reached yet."""
    del raw, parsed
    assert getattr(settings_from(tmp_path, monkeypatch, f"{env}="), attr) == default


@pytest.mark.parametrize("env,attr,default,raw,parsed", CONGRESS_SETTINGS, ids=_IDS)
def test_every_congress_setting_is_documented_in_the_env_template(env, attr, default,
                                                                  raw, parsed):
    """Per-name version of the suite-wide template check, so a miss names the FIELD.

    A commented line counts: a commented default is documentation, which is the thing being
    guarded. An operator cannot set a control they cannot know exists.
    """
    del attr, default, raw, parsed
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.search(rf"^\s*#?\s*{env}\s*=", text, flags=re.MULTILINE), \
        f"{env} is not documented in app/.env.example"


@pytest.mark.parametrize("env,attr,default,raw,parsed", CONGRESS_SETTINGS, ids=_IDS)
def test_the_template_documents_the_value_the_class_actually_uses(env, attr, default,
                                                                  raw, parsed):
    """A template that says 45 while the class says 30 hands the operator a control they do
    not have. Only lines that carry a VALUE are checked; a deliberately blank operator input
    (``COCKPIT_CONGRESS_FOLLOW=``) documents "unset", which is the default already asserted
    above."""
    del attr, raw, parsed
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    match = re.search(rf"^\s*#?\s*{env}\s*=\s*(\S+)\s*$", text, flags=re.MULTILINE)
    if match is None:
        return                                  # documented as blank/unset; covered elsewhere
    documented = match.group(1)
    if isinstance(default, bool):
        assert documented.lower() in {"true", "false"}
        assert (documented.lower() == "true") is default
    elif isinstance(default, Decimal):
        assert Decimal(documented) == default
    elif isinstance(default, float):
        assert float(documented) == pytest.approx(default)
    elif isinstance(default, int):
        assert int(documented) == default
    else:
        assert documented == default


def test_a_verbatim_copy_of_the_template_boots_with_the_feature_OFF(tmp_path, monkeypatch):
    """THE provisioning path, and the posture it must land in.

    RW-16 fetches two .gov sites AND proposes orders, so a fresh operator who copied the
    template and has not read the docs must get NOTHING: no fetch, no follow, no proposals.
    """
    env = write_env(tmp_path, ENV_EXAMPLE.read_text(encoding="utf-8"))
    for key in [k for k in os.environ if k.startswith("COCKPIT_")]:
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=str(env))
    assert s.congress_enabled is False
    assert s.congress_senate_enabled is False
    assert s.congress_user_agent == ""
    assert s.congress_follow == ""
    assert policy_from_settings(s).follows("pelosi-nancy") is False


# ---------------------------------------------------------------------------------------
# bounds — both directions, on every numeric knob
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("env,field,bad", [
    # a politeness floor that can be raised to a flood is not a floor
    ("COCKPIT_CONGRESS_RATE_PER_SEC", "congress_rate_per_sec", "0"),
    ("COCKPIT_CONGRESS_RATE_PER_SEC", "congress_rate_per_sec", "-1"),
    ("COCKPIT_CONGRESS_RATE_PER_SEC", "congress_rate_per_sec", "5.1"),
    ("COCKPIT_CONGRESS_RATE_PER_SEC", "congress_rate_per_sec", "500"),
    # a staleness gate that accepts a decade is not a gate
    ("COCKPIT_CONGRESS_MAX_AGE_DAYS", "congress_max_age_days", "0"),
    ("COCKPIT_CONGRESS_MAX_AGE_DAYS", "congress_max_age_days", "-1"),
    ("COCKPIT_CONGRESS_MAX_AGE_DAYS", "congress_max_age_days", "366"),
    ("COCKPIT_CONGRESS_MAX_AGE_DAYS", "congress_max_age_days", "3650"),
    ("COCKPIT_CONGRESS_STALENESS_DECAY", "congress_staleness_decay", "-0.1"),
    ("COCKPIT_CONGRESS_STALENESS_DECAY", "congress_staleness_decay", "1.1"),
    # a copy "fraction" above 1 is a typo, not a configuration
    ("COCKPIT_CONGRESS_COPY_FRACTION", "congress_copy_fraction", "0"),
    ("COCKPIT_CONGRESS_COPY_FRACTION", "congress_copy_fraction", "-0.5"),
    ("COCKPIT_CONGRESS_COPY_FRACTION", "congress_copy_fraction", "1.5"),
    # the per-intent notional band
    ("COCKPIT_CONGRESS_MAX_NOTIONAL_PER_TRADE", "congress_max_notional_per_trade", "0"),
    ("COCKPIT_CONGRESS_MAX_NOTIONAL_PER_TRADE", "congress_max_notional_per_trade", "-1"),
    ("COCKPIT_CONGRESS_MIN_NOTIONAL_PER_TRADE", "congress_min_notional_per_trade", "-1"),
    ("COCKPIT_CONGRESS_MIN_AMOUNT_LOW", "congress_min_amount_low", "-1"),
    # a zero haircut emits intents worth nothing: a silent no-op dressed as a feature
    ("COCKPIT_CONGRESS_OPTIONS_HAIRCUT", "congress_options_haircut", "0"),
    ("COCKPIT_CONGRESS_OPTIONS_HAIRCUT", "congress_options_haircut", "-0.25"),
    ("COCKPIT_CONGRESS_OPTIONS_HAIRCUT", "congress_options_haircut", "1.5"),
    # ``1`` is the exact value ``CopyPolicy`` forbids outright. ``le=1`` accepted it here, so the
    # disagreement surfaced as a runtime PolicyError swallowed by paper_execution's copy-universe
    # guard — an empty universe and every promoted symbol refused at submit — rather than as a
    # config error at boot. Anything above MAX_OPTIONS_HAIRCUT lets share quantization collapse
    # an options-derived order onto the equity one.
    ("COCKPIT_CONGRESS_OPTIONS_HAIRCUT", "congress_options_haircut", "1"),
    ("COCKPIT_CONGRESS_OPTIONS_HAIRCUT", "congress_options_haircut", "0.75"),
    ("COCKPIT_CONGRESS_OPTIONS_MIN_DAYS_TO_EXPIRY", "congress_options_min_days_to_expiry", "-1"),
    # 0 admits a 0-DTE contract as a fresh multi-week long: the near-expiry gate off by config.
    ("COCKPIT_CONGRESS_OPTIONS_MIN_DAYS_TO_EXPIRY", "congress_options_min_days_to_expiry", "0"),
    ("COCKPIT_CONGRESS_OPTIONS_MIN_DAYS_TO_EXPIRY", "congress_options_min_days_to_expiry", "366"),
    # the deterministic allowlist must stay small enough to reason about
    ("COCKPIT_CONGRESS_MAX_UNIVERSE_SYMBOLS", "congress_max_universe_symbols", "0"),
    ("COCKPIT_CONGRESS_MAX_UNIVERSE_SYMBOLS", "congress_max_universe_symbols", "201"),
    ("COCKPIT_CONGRESS_UNIVERSE_TTL_DAYS", "congress_universe_ttl_days", "0"),
    ("COCKPIT_CONGRESS_UNIVERSE_TTL_DAYS", "congress_universe_ttl_days", "1096"),
    # a switch is a switch
    ("COCKPIT_CONGRESS_ENABLED", "congress_enabled", "maybe"),
    ("COCKPIT_CONGRESS_SENATE_ENABLED", "congress_senate_enabled", "sometimes"),
    ("COCKPIT_CONGRESS_MIRROR_SELLS", "congress_mirror_sells", "occasionally"),
])
def test_an_out_of_bounds_congress_value_is_refused_at_startup(tmp_path, monkeypatch,
                                                               env, field, bad):
    """Refused from the FILE, and the error NAMES the field so the operator knows which line."""
    with pytest.raises(Exception) as exc:
        settings_from(tmp_path, monkeypatch, f"{env}={bad}")
    assert field in str(exc.value)


@pytest.mark.parametrize("value", ["0.5", "1", "0"])
def test_the_staleness_decay_endpoints_are_ADMITTED(value):
    """Not a weakened bound — the closed interval [0, 1] is the documented one, and a test
    that only checked rejections would pass on a validator that refused everything."""
    assert Settings(congress_staleness_decay=value).congress_staleness_decay == Decimal(value)


def test_an_inverted_notional_band_is_refused_at_startup():
    """min > max clamps every intent below the floor and drops it: the feature looks configured
    and silently never trades. ``CopyPolicy.__post_init__`` catches it too, but not until the
    first disclosure is planned — days after the edit."""
    with pytest.raises(Exception) as exc:
        Settings(congress_min_notional_per_trade="600")     # default max is 500
    assert "congress_min_notional_per_trade" in str(exc.value)
    assert "congress_max_notional_per_trade" in str(exc.value)


def test_the_notional_band_may_touch_at_its_edges():
    """Control for the test above: min == max is a legal (if useless) fixed-size configuration
    and must not be swept up by an off-by-one in the comparison."""
    s = Settings(congress_min_notional_per_trade="500", congress_max_notional_per_trade="500")
    assert s.congress_min_notional_per_trade == s.congress_max_notional_per_trade


# ---------------------------------------------------------------------------------------
# the follow allowlist — the value whose failure mode is SILENCE
# ---------------------------------------------------------------------------------------
def follow_entries(count: int) -> str:
    """``count`` DISTINCT members.

    The surnames must differ in LETTERS, not digits: ``normalize_filer`` strips everything
    outside ``[a-z ,.-]``, so "Member0, First" and "Member1, First" are one person and a naive
    generator silently produces a one-entry list (which is how the entry-ceiling control here
    first passed for the wrong reason).
    """
    return ";".join(f"Smith{chr(65 + i // 26)}{chr(65 + i % 26)}, First" for i in range(count))


MALFORMED_FOLLOW = [
    ";;;",                          # separators and nothing else
    " ; ",
    "123456",                       # digits normalize to nothing
    "!!!",
    "Pelosi, Nancy; @@@",           # one good entry, one unparseable
    follow_entries(51),             # over MAX_FOLLOW_ENTRIES
    "x" * 2049,                     # over the raw-text bound
]


@pytest.mark.parametrize("raw", MALFORMED_FOLLOW)
def test_a_malformed_follow_list_fails_at_STARTUP(tmp_path, monkeypatch, raw):
    """Not at trade time. The operator is still looking at the file they just saved."""
    with pytest.raises(Exception) as exc:
        settings_from(tmp_path, monkeypatch, f"COCKPIT_CONGRESS_FOLLOW={raw}")
    assert "congress_follow" in str(exc.value)


@pytest.mark.parametrize("raw", MALFORMED_FOLLOW + ["Pelosi, Nancy; ###"])
def test_a_malformed_follow_list_is_refused_on_direct_construction_too(raw):
    """The same refusal without the dotenv layer.

    Both seams matter and they are not the same seam: ``#`` is a COMMENT to a dotenv parser, so
    a trailing "###" never reaches the validator from a file at all. Pinning direct
    construction keeps the validator honest for every non-file caller (tests, tools, a future
    config UI) rather than resting on what the file parser happened to strip.
    """
    with pytest.raises(Exception) as exc:
        Settings(congress_follow=raw)
    assert "congress_follow" in str(exc.value)


def test_the_startup_check_catches_what_plan_time_would_swallow_SILENTLY():
    """THE reason the eager validator exists, stated as a control.

    ``parse_follow_list(";;;")`` does not raise — it returns an EMPTY allowlist, and an empty
    allowlist follows NOBODY by design. So at plan time "I mistyped the separators" and "I
    deliberately configured nobody" produce the identical, silent outcome. Startup is the only
    place the distinction survives, which is why it has to be made here.
    """
    assert parse_follow_list(";;;") == frozenset()           # silent at plan time...
    with pytest.raises(Exception) as exc:                    # ...refused at startup
        Settings(congress_follow=";;;")
    assert "congress_follow" in str(exc.value)
    assert "EMPTY" in str(exc.value) or "empty" in str(exc.value)


def test_a_TRULY_empty_follow_list_is_legal_and_follows_nobody():
    """The other side of the control above: blank is a legitimate setting (ingest without
    copying), so the validator must not turn "off" into a boot failure."""
    s = Settings(congress_follow="")
    assert s.congress_follow == ""
    assert policy_from_settings(s).follow == frozenset()
    assert policy_from_settings(s).follows("pelosi-nancy") is False


def test_a_valid_follow_list_survives_the_round_trip_to_the_policy():
    """``normalize_congress_follow`` returns canonical RAW TEXT, not slugs, because
    ``policy_from_settings`` re-parses this exact field. A validator that stored slugs would
    make the setting unreadable to the only thing that reads it."""
    s = Settings(congress_follow=" Pelosi, Nancy P. ; Tuberville, Tommy ")
    assert parse_follow_list(s.congress_follow) == {"pelosi-nancy", "tuberville-tommy"}
    policy = policy_from_settings(s)
    assert policy.follows("pelosi-nancy") and policy.follows("tuberville-tommy")
    assert policy.follows("greene-marjorie") is False


def test_duplicate_spellings_of_one_member_collapse_to_one_entry():
    """Two spellings normalize to one slug, so keeping both would misreport the follow COUNT
    in every log line and status view. The FIRST spelling seen survives."""
    s = Settings(congress_follow="Pelosi, Nancy; PELOSI, NANCY P.; Hon. Nancy Pelosi")
    assert s.congress_follow == "Pelosi, Nancy"
    assert parse_follow_list(s.congress_follow) == {"pelosi-nancy"}


def test_the_follow_entry_ceiling_admits_the_documented_maximum():
    """Control for the over-limit rejection: 50 entries is legal, 51 is not."""
    ok = follow_entries(50)
    assert len(parse_follow_list(Settings(congress_follow=ok).congress_follow)) == 50


def test_digits_in_a_follower_name_are_DROPPED_not_significant():
    """A sharp edge worth stating: ``normalize_filer`` keeps only ``[a-z ,.-]``, so a name
    distinguished solely by a digit is not distinguished at all. Config authors (and the test
    above) need to know that "Member1" and "Member2" are one follow entry, not two."""
    s = Settings(congress_follow="Member1, First; Member2, First")
    assert parse_follow_list(s.congress_follow) == {"member-first"}


# ---------------------------------------------------------------------------------------
# the options policy — an enum in a string field
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("underlying", "underlying"),
    ("ignore", "ignore"),
    ("UNDERLYING", "underlying"),
    ("  Ignore  ", "ignore"),
])
def test_a_legal_options_policy_is_accepted_and_canonicalized(raw, expected):
    assert Settings(congress_options_policy=raw).congress_options_policy == expected


@pytest.mark.parametrize("raw", [
    "underlyng",          # the one-character typo this check exists for
    "copy_contract",      # the value a reader might expect to exist; it deliberately does not
    "contract",
    "none",
    "ignore_all",
    "0",
])
def test_an_invalid_options_policy_is_refused_at_startup(tmp_path, monkeypatch, raw):
    """``CopyPolicy.__post_init__`` would raise at PLAN time, turning a one-character config
    error into a feature that looks configured and silently never runs."""
    with pytest.raises(Exception) as exc:
        settings_from(tmp_path, monkeypatch, f"COCKPIT_CONGRESS_OPTIONS_POLICY={raw}")
    assert "congress_options_policy" in str(exc.value)


def test_the_refusal_message_lists_the_policies_that_DO_exist():
    """An error that says "invalid" and stops has told the operator nothing they can act on."""
    with pytest.raises(Exception) as exc:
        Settings(congress_options_policy="underlyng")
    message = str(exc.value)
    assert "underlying" in message and "ignore" in message


def test_the_settings_defaults_ARE_the_policy_defaults():
    """One feature, two places that state a default (``Settings`` and ``CopyPolicy``). If they
    drift, the documented number and the enforced number stop being the same number — and the
    engine's is the one that trades."""
    assert policy_from_settings(Settings()) == CopyPolicy()


def test_policy_from_settings_never_falls_back_to_a_policy_that_trades():
    """Fail closed: a settings object carrying a bad policy raises rather than defaulting.

    ``Settings`` cannot produce this (it validates eagerly — that is the point of everything
    above), so the probe is a stand-in object. This pins the SECOND line of defence: if a
    future caller builds a policy from something other than ``Settings``, it still refuses.
    """
    class NotSettings:
        congress_options_policy = "underlyng"

    with pytest.raises(PolicyError):
        policy_from_settings(NotSettings())


# ---------------------------------------------------------------------------------------
# Inv 4 — the contact string is CONFIG, not a credential
# ---------------------------------------------------------------------------------------
def _documented_congress_lines() -> list[str]:
    """Every ``COCKPIT_CONGRESS_*`` line in the shipped template, UNCOMMENTED.

    An operator provisions by uncommenting, so the guard has to survive exactly that edit. A
    documented line that boots only while it stays commented out is not documentation.
    """
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return [f"{name}={value.strip()}" for name, value in
            re.findall(r"^\s*#?\s*(COCKPIT_CONGRESS_[A-Z0-9_]+)\s*=(.*)$", text,
                       flags=re.MULTILINE)]


def test_the_declared_contact_string_does_not_trip_the_real_env_secret_guard(tmp_path):
    """It is a name and an email, sent to house.gov on every request by design — the same
    shape and purpose as COCKPIT_SEC_USER_AGENT. Asserted against the REAL guard: a test that
    checked ``_SECRETISH_ALLOWED`` instead would stay green even if the guard stopped
    consulting it, which is precisely the failure that matters here (the cockpit refusing to
    boot over a contact string)."""
    env = write_env(tmp_path, "COCKPIT_CONGRESS_USER_AGENT=Lucas Guerra lucas@example.com\n")
    assert_no_secrets_in_env_file(env)          # must not raise


def test_no_documented_congress_line_makes_the_cockpit_refuse_to_boot(tmp_path):
    """All 17 at once, uncommented, straight out of the template."""
    lines = _documented_congress_lines()
    assert len(lines) >= 17, f"expected the full congress block, found {len(lines)} lines"
    assert_no_secrets_in_env_file(write_env(tmp_path, "\n".join(lines) + "\n"))


@pytest.mark.parametrize("key", [
    "COCKPIT_CONGRESS_API_KEY",
    "COCKPIT_CONGRESS_TOKEN",
    "COCKPIT_CONGRESS_SECRET",
    "COCKPIT_CONGRESS_EFD_PASSWORD",
    "COCKPIT_CONGRESS_SIGNING_KEY",
])
def test_a_credential_shaped_congress_name_is_STILL_refused(tmp_path, key):
    """The counter-control that makes the two passes above causal rather than vacuous: the
    guard is live for this prefix, and it is the SHAPE of the user-agent name that clears it,
    not the guard being asleep. (These names do not exist today — neither .gov source has an
    API key. The point is that inventing one in .env would fail closed.)"""
    with pytest.raises(EnvFileSecretError):
        assert_no_secrets_in_env_file(write_env(tmp_path, f"{key}={SECRET_VALUE}\n"))


def test_the_guard_error_still_never_quotes_the_value(tmp_path):
    """An exception that quotes the secret turns a safety check into a disclosure. Re-asserted
    on the congress prefix because this is the block most likely to gain a credential-shaped
    name later (a Senate EFD login has been discussed and deliberately not built)."""
    env = write_env(tmp_path, f"COCKPIT_CONGRESS_API_KEY={SECRET_VALUE}\n")
    with pytest.raises(EnvFileSecretError) as exc:
        assert_no_secrets_in_env_file(env)
    assert "COCKPIT_CONGRESS_API_KEY" in str(exc.value)
    assert SECRET_VALUE not in str(exc.value)
    assert "sk-live" not in str(exc.value)
