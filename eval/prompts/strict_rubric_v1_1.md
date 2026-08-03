# SHARED FROZEN STRICT ENTAILMENT RUBRIC v1.1 (LongMemEval judging)

**Lifted verbatim from `~/unblock-eval/JUDGE-STRICT-RUBRIC-v1.md`** (frozen
2026-07-05 by ds-lme-i, approved by viraj-product; R6 amended 2026-07-06 to
v1.1). R1–R6 below are the original text. Only the machine-readable output
contract at the bottom and the placeholder syntax are new — see the
TEMPLATING NOTE.

ALL judge families in the strict panel apply THIS rubric verbatim. Independence
lives in the JUDGE MODEL FAMILY, never in the rubric. Agreement is measured
under this shared rubric; residual divergence > 0.02 is reported as a
JUDGE-BAND (both lanes' numbers), never collapsed to a false point.

A judge sees only the question, the gold, and the prediction, and returns one
boolean per row. The core question is always: **does the PREDICTION entail the
load-bearing FACT the GOLD asserts, as scoped by the QUESTION?** Paraphrase and
extra *correct* detail never penalize; omission, contradiction, mis-count, or
ungrounded fabrication always fail.

---

> ### TEMPLATING NOTE — the fixed bug (design doc §1.1)
>
> `~/unblock-eval/judge_canonical_lane.py` built this prompt with
> `ENTAIL_PROMPT.format(...)`, and the rubric text contains a literal brace
> group naming the three inputs. `str.format` reads that as a replacement
> field, so the live path raised
> `KeyError: 'question, gold, prediction'` on the very first real call.
> `--selftest` passed anyway because it never reached `call_judge` — the live
> path had never executed once.
>
> The fix here is structural, not cosmetic: this file uses `<<SENTINEL>>`
> placeholders and `judge_strict.py` substitutes them with `str.replace`.
> `.format()` is never called on rubric text, so no brace anywhere in this
> document — literal or otherwise — can be parsed as a field. Escaping the
> braces would have fixed the one crash; removing `.format()` removes the
> whole bug class.

---

## R1 — Bare number vs. unit
The load-bearing fact is the numeric VALUE in the dimension the QUESTION fixes.
- If the question names the dimension/unit ("how many **hours**…", "how many
  **days**…"), a bare number equal to the gold's value is **CORRECT**
  (pred `140` entails gold `140 hours` when the question asked for hours).
- If the unit is genuinely ambiguous from the question, or the gold's unit is
  itself the discriminating fact, the unit IS required.
- Wrong value (under/over) is always INCORRECT regardless of unit.

## R2 — Entity + qualifier
A prediction is CORRECT iff it **uniquely and unambiguously identifies** the gold
entity.
- **Non-disambiguating** qualifiers in the gold are NOT required: a uniquely-named
  entity stands on its own (pred `University of Melbourne` entails gold
  `University of Melbourne in Australia` — the name is unique; "in Australia" is
  non-identifying).
- **Disambiguating** qualifiers ARE required: if the qualifier is what selects the
  correct referent among plausible others the question implicates (a specific
  branch/location/date), a prediction missing it is INCORRECT
  (e.g. `The Sugar Factory` vs gold `The Sugar Factory at Icon Park` — CORRECT
  only if no other Sugar Factory is in scope for the question; else INCORRECT).
  Judge this per row from the question's scope, do not assume.

## R3 — Preference-satisfaction (generative "what would the user prefer" rows)
The gold states a preference PROFILE. A prediction is CORRECT iff BOTH hold:
1. **Grounded** — it references/builds on the SPECIFIC signals the gold names
   (the user's actual stated history/context), not generic advice; AND
2. **Non-violating** — it does not contradict the preference, and it does not add
   FABRICATED specifics presented as fact (unsupported claims about what the user
   did/owns). Extra *plausible, non-asserted* suggestion detail is fine.
- Generic advice ignoring the gold's specific signals → INCORRECT.
- Over-specification that asserts un-grounded facts → INCORRECT.
- Degenerate/looping/no-final-answer output → INCORRECT.

## R4 — Abstention
An empty/abstaining prediction is CORRECT **only** if the gold is itself
unanswerable/abstain. If the gold names a concrete answer, abstention is INCORRECT.

## R5 — Count / distinct-count
Exact match to the gold count. Under-count and over-count both INCORRECT.
The count is judged on the gold's value, not the enumerated members.

## R6 — General entailment (default)  [v1.1: surface-tolerance EMPHASIZED]
Pure SURFACE VARIATION where the load-bearing fact is UNCHANGED is CORRECT and
MUST NOT be penalized — articles (a/the), possessives (my/the), synonyms,
paraphrase ('breakdown'≈'malfunction'), casing, formatting. STRICTLY surface-only:
it does NOT excuse a missing disambiguating qualifier (R2), a different value/count
(R5), or a different/contradicting entity. (v1.1 2026-07-06: added after nemotron was
found UNFAITHFUL to R6 — penalizing pure paraphrase/article variation it permits,
deflating the strict floor ~3 rows/seed. A faithful R6 is tolerance-only, never looser
on substance.)

### (original R6)
For any row not covered by R1–R5: CORRECT iff the prediction conveys the gold's
load-bearing fact, allowing paraphrase, synonymy, and additional correct detail.
Contradiction of any load-bearing element → INCORRECT.

---

### Judging procedure (deterministic)
1. Classify the row into R1–R6 (a row may implicate one primary rule).
2. Apply that rule's test to the question, the gold, and the prediction.
3. Return the verdict, the rule applied, and a one-line rationale.
No verdict may depend on the other lane's output or on any file.

---

## THE ROW

QUESTION:
<<QUESTION>>

GOLD (<<GOLD_LABEL>>):
<<GOLD>>

PREDICTION:
<<PREDICTION>>

<<ABSTENTION_NOTE>>

---

## OUTPUT CONTRACT
Reply with a single JSON object on one line and nothing else:

  "verdict" — true if the prediction is CORRECT under the rule you applied, false otherwise
  "rule"    — one of R1, R2, R3, R4, R5, R6
  "rationale" — one sentence, at most 25 words

Example of the required shape (values illustrative only):
  VERDICT_JSON: verdict=true, rule=R2, rationale=names the gold entity uniquely

Emit real JSON, e.g. an object with keys verdict, rule, rationale.
