"""Judge lanes, unit-tested with CANNED RESPONSES. No API calls, no keys.

Both lanes are tested in BOTH directions -- a judge that always says yes and a
judge that always says no must produce different numbers. A test suite that only
asserts the happy path cannot tell a working judge from a stuck one, which is
the `judge_canonical_lane.py --selftest` failure exactly.
"""

from __future__ import annotations

import pytest

from lme import guards, metrics
from lme.judge_official import (
    OFFICIAL_JUDGE_MODEL,
    OfficialJudge,
    get_anscheck_prompt,
    official_label,
)
from lme.judge_strict import (
    DEFAULT_PANEL,
    StrictPanel,
    load_rubric,
    parse_verdict,
    render_prompt,
    summarize,
)


# ── canned clients ──────────────────────────────────────────────────────────
class _Msg:
    def __init__(self, content): self.content = content


class _Choice:
    def __init__(self, content): self.message = _Msg(content)


class _Resp:
    def __init__(self, content, model): self.choices = [_Choice(content)]; self.model = model


class CannedOpenAI:
    """Mimics `client.chat.completions.create`. Records every prompt it saw."""

    def __init__(self, reply="yes", model=OFFICIAL_JUDGE_MODEL):
        self.reply, self.model, self.prompts = reply, model, []
        self.chat = self

    @property
    def completions(self): return self

    def create(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        r = self.reply(len(self.prompts)) if callable(self.reply) else self.reply
        return _Resp(r, self.model)


class CannedStrict:
    def __init__(self, verdict: bool): self.verdict, self.seen = verdict, []

    def complete(self, prompt: str) -> str:
        self.seen.append(prompt)
        return '{"verdict": %s, "rule": "R6", "rationale": "canned"}' % (
            "true" if self.verdict else "false"
        )


ROWS = [
    {"question_id": "q1", "question_type": "multi-session", "question": "Where?", "gold": "Paris", "prediction": "Paris"},
    {"question_id": "q2_abs", "question_type": "temporal-reasoning", "question": "When?", "gold": "unanswerable", "prediction": ""},
]


# ── official lane ───────────────────────────────────────────────────────────
def test_official_prompt_has_four_variants_plus_abstention():
    default = get_anscheck_prompt("multi-session", "Q", "A", "R")
    temporal = get_anscheck_prompt("temporal-reasoning", "Q", "A", "R")
    ku = get_anscheck_prompt("knowledge-update", "Q", "A", "R")
    pref = get_anscheck_prompt("single-session-preference", "Q", "A", "R")
    abst = get_anscheck_prompt("multi-session", "Q", "A", "R", abstention=True)

    assert len({default, temporal, ku, pref, abst}) == 5
    # the distinguishing clauses, verbatim from upstream
    assert "do not penalize off-by-one errors for the number of days" in temporal
    assert "contains some previous information along with an updated answer" in ku
    assert "Rubric:" in pref and "does not need to reflect all the points" in pref
    assert "correctly identifies the question as unanswerable" in abst
    # single-session-user/-assistant share the default template
    assert get_anscheck_prompt("single-session-user", "Q", "A", "R") == default
    assert get_anscheck_prompt("single-session-assistant", "Q", "A", "R") == default


def test_official_prompt_transcription_is_pinned():
    """THE TRANSCRIPTION PIN.

    Every published LongMemEval number was produced by these exact bytes. A
    well-meaning cleanup -- the double space, the trailing space on the
    abstention branch -- makes our number non-comparable to all of them, which
    is precisely the criticism design doc §2.5 levels at other vendors. This
    test is the thing that catches that edit.

    If upstream genuinely changes, update the constant AND say so out loud in
    the report, because it breaks comparability with every prior score.
    """
    from lme.judge_official import PROMPT_TEMPLATES_SHA256, _probe_templates

    assert _probe_templates() == PROMPT_TEMPLATES_SHA256


def test_official_prompt_preserves_upstream_whitespace_quirks():
    """The quirks are load-bearing for byte-comparability, so assert them."""
    d = get_anscheck_prompt("multi-session", "Q", "A", "R")
    assert "answer no. \n\nQuestion:" in d          # space before the newlines
    t = get_anscheck_prompt("temporal-reasoning", "Q", "A", "R")
    assert "still correct. \n\nQuestion:" in t
    ku = get_anscheck_prompt("knowledge-update", "Q", "A", "R")
    assert "required answer.\n\nQuestion:" in ku    # NO space on this branch


def test_official_prompt_unknown_task_raises():
    with pytest.raises(NotImplementedError):
        get_anscheck_prompt("not-a-real-type", "Q", "A", "R")


def test_official_label_is_substring_check():
    """Upstream rule, replicated warts and all: `'yes' in resp.lower()`."""
    assert official_label("Yes") is True
    assert official_label("yes.") is True
    assert official_label("No") is False
    # upstream quirk we deliberately do NOT fix in lane 1
    assert official_label("yes, but actually no") is True


def test_official_judge_both_directions():
    """A stuck judge must be visible: all-yes and all-no give different scores."""
    yes = OfficialJudge(client=CannedOpenAI("yes"))
    no = OfficialJudge(client=CannedOpenAI("no"))
    vy = yes.judge_rows(ROWS)
    vn = no.judge_rows(ROWS)
    assert [v.label for v in vy] == [True, True]
    assert [v.label for v in vn] == [False, False]


def test_official_judge_applies_abstention_override_by_id_substring():
    j = OfficialJudge(client=(c := CannedOpenAI("yes")))
    verdicts = j.judge_rows(ROWS)
    assert verdicts[0].is_abstention is False
    assert verdicts[1].is_abstention is True
    # the `_abs` row got the abstention template despite being temporal-reasoning
    assert "unanswerable" in c.prompts[1]
    assert "off-by-one" not in c.prompts[1]


def test_official_judge_rejects_swapped_model():
    """T2 on the judge: a silent swap to gpt-4o-mini is the 'lax judge family'
    half of the 0.9010 phantom."""
    j = OfficialJudge(client=CannedOpenAI("yes", model="gpt-4o-mini"))
    with pytest.raises(guards.GuardViolation) as exc:
        j.judge_rows(ROWS[:1])
    assert exc.value.trap == "T2"


# ── strict lane ─────────────────────────────────────────────────────────────
def test_strict_rubric_renders_with_braces_in_every_field():
    """THE REGRESSION TEST FOR THE FIXED KeyError.

    `judge_canonical_lane.py` used `.format()` on rubric text containing a
    literal brace group and crashed with KeyError on its first real call. Here
    the values themselves are full of braces -- which is realistic, since
    predictions routinely contain JSON and code.
    """
    out = render_prompt(
        load_rubric(),
        question="What did {user} say about {topic}?",
        gold='{"answer": "Paris", "n": {1,2}}',
        prediction="He said {'city': 'Paris'} — see {ref}",
        is_abstention=False,
    )
    assert "{user}" in out and '{"answer": "Paris"' in out and "{'city': 'Paris'}" in out
    # every real sentinel substituted (the doc prose mentions `<<SENTINEL>>`
    # itself, which is deliberately NOT one of them)
    from lme.judge_strict import SENTINELS

    assert not [s for s in SENTINELS if s in out]


def test_strict_rubric_abstention_note_is_conditional():
    r = load_rubric()
    a = render_prompt(r, question="q", gold="g", prediction="p", is_abstention=True)
    b = render_prompt(r, question="q", gold="g", prediction="p", is_abstention=False)
    assert "ABSTENTION row" in a and "Apply R4" in a
    assert "ABSTENTION row" not in b
    assert "explanation of unanswerability" in a and "correct answer" in b


def test_parse_verdict_handles_shapes_and_refuses_to_guess():
    assert parse_verdict('{"verdict": true, "rule": "R2"}') is True
    assert parse_verdict('{"verdict": false, "rule": "R5"}') is False
    assert parse_verdict('here you go: {"verdict": true, "rule":"R1"} done') is True
    # An unparseable reply is an ABSTAINING VOTE (None), never a silent False --
    # silently voting False would deflate the strict floor and look like rigour.
    assert parse_verdict("I cannot comply.") is None
    assert parse_verdict("") is None


def test_strict_panel_both_directions_and_floor():
    clients_yes = {m: CannedStrict(True) for m in DEFAULT_PANEL}
    p = StrictPanel(clients=clients_yes, votes=3, reader_model="claude-sonnet-4-6-20260514")
    s = summarize(p.judge_rows(ROWS))
    assert s["maj3"] == 1.0 and s["floor"] == 1.0 and s["undecided"] == 0

    clients_no = {m: CannedStrict(False) for m in DEFAULT_PANEL}
    p2 = StrictPanel(clients=clients_no, votes=3, reader_model="claude-sonnet-4-6-20260514")
    s2 = summarize(p2.judge_rows(ROWS))
    assert s2["maj3"] == 0.0 and s2["floor"] == 0.0


def test_strict_panel_floor_is_min_over_families():
    """One dissenting family drops the FLOOR but not the majority."""
    clients = {m: CannedStrict(True) for m in DEFAULT_PANEL}
    clients[DEFAULT_PANEL[2]] = CannedStrict(False)
    p = StrictPanel(clients=clients, votes=3, reader_model="claude-sonnet-4-6-20260514")
    s = summarize(p.judge_rows(ROWS))
    assert s["maj3"] == 1.0
    assert s["floor"] == 0.0
    assert s["per_family"][DEFAULT_PANEL[2]] == 0.0


def test_strict_panel_enforces_family_exclusion():
    """T7: a panel that includes the reader's own family is a self-grade."""
    with pytest.raises(guards.GuardViolation) as exc:
        StrictPanel(
            panel=["claude-opus-4-1-20250805", "gpt-4o-2024-08-06", "gemini-1.5-pro-002"],
            clients={},
            reader_model="claude-sonnet-4-6-20260514",
        )
    assert exc.value.trap == "T7"


def test_strict_panel_rejects_single_family_panel():
    with pytest.raises(guards.GuardViolation):
        StrictPanel(panel=["gpt-4o-2024-08-06", "gpt-4o-mini", "gpt-4.1"], clients={})


def test_strict_panel_refuses_unregistered_client():
    """No silent routing to 'whatever is importable'."""
    p = StrictPanel(clients={DEFAULT_PANEL[0]: CannedStrict(True)}, votes=1)
    with pytest.raises(RuntimeError, match="no client registered"):
        p.judge_rows(ROWS[:1])


# ── the two lanes together ──────────────────────────────────────────────────
def test_lane_band_reports_a_band_not_a_point_on_divergence():
    band = metrics.LaneBand(official=0.72, strict_maj3=0.61, strict_floor=0.55)
    assert not band.dual_confirmed
    assert "JUDGE-BAND" in band.render() and "NOT a point estimate" in band.render()

    tight = metrics.LaneBand(official=0.72, strict_maj3=0.71, strict_floor=0.68)
    assert tight.dual_confirmed
    assert "DUAL-CONFIRMED" in tight.render()


def test_compute_agreement():
    assert metrics.compute_agreement([True, False, True], [True, False, True]) == 1.0
    assert metrics.compute_agreement([True, True], [True, False]) == 0.5
    with pytest.raises(ValueError):
        metrics.compute_agreement([True], [True, False])
