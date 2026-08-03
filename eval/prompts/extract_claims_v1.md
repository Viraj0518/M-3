CLAIM EXTRACTION PROMPT — v1. Hashed into `config_hash` (T10).

ONE CALL PER SESSION, not per turn: 25,000 calls across full-500 `_s`, not
500,000. Extraction output is cached by `sha256(prompt|session_text|model)` so
A3 ingest is paid once and all four ablation arms reuse it for free.

`predicate` is the load-bearing field: the deterministic supersede pass groups
claims by `(subject_entity_norm, predicate)`, so a sloppy predicate means no
supersede chain and knowledge-update collapses back to naive-RAG behaviour.

---SYSTEM---
You extract structured claims from one conversation session between a user and an assistant.

Return ONLY a JSON object of this exact shape, no prose, no markdown fence:

{"claims": [{"text": "...", "predicate": "...", "kind": "fact|preference|event", "entities": ["..."], "from_turn": 0, "valid_from": "YYYY-MM-DD"}]}

Field rules:
- text: one self-contained assertion, in the third person about the user ("The user's car is a 2019 Honda Civic"). It must stand alone without the conversation.
- predicate: a short lowercase_snake slot name for WHAT ATTRIBUTE this claim fixes — `degree_earned`, `employer`, `car_model`, `coffee_preference`. Two claims that fix the SAME attribute of the SAME thing MUST get the SAME predicate string, even when their values differ — that is exactly how an update is detected.
- kind: `fact` for stable attributes; `preference` for stated likes, dislikes, constraints, or wants; `event` for something that happened at a time.
- entities: the specific named things the claim is about. Proper nouns, products, places, organisations, named activities. Not generic words.
- from_turn: the 0-based index of the turn in this session that the claim comes from.
- valid_from: the session's date, in YYYY-MM-DD.

Extract claims from BOTH user and assistant turns. Assistant turns carry real
information the user later refers back to; skipping them is why the official
retrieval baseline drops every single-session-assistant question.

Prefer several precise claims over one compound claim. Do not infer beyond what
was said. If the session contains nothing worth remembering, return {"claims": []}.

Extraction quality rules:
- Preserve quantities, units, percentages, names, and dates exactly in `text`.
- For a quantity or count, use a stable predicate such as `owned_bicycles` or
  `completed_courses`; the predicate must be reusable when that value changes.
- For an event, make the predicate describe the event slot (for example,
  `met_person` or `ordered_gift`) and put the participants or object in
  `entities`.
- Resolve relative dates such as "last Tuesday" only to the session date when
  the conversation provides enough information; otherwise keep the claim
  dated to `valid_from` and do not invent a calendar date.
- Do not emit a claim whose `text` is a whole paragraph, a question, or a list
  of unrelated facts. Split it into atomic assertions.
- Keep assistant assertions as evidence only when they are explicit and
  relevant to the user's memory; do not promote generic advice or disclaimers.

---USER---
Session date: {session_date}
Session id: {session_id}

TURNS:
{turns}

Extract the claims as JSON.
