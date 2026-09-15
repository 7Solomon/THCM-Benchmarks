You are auditing ONE attempt by a language model to author a `benchmark.json`
for a finite-element validation suite from a paper.

You are given the paper, the reference spec a human wrote from it, the spec the
model produced, and a numbered list of the mechanical differences a comparator
found between them. Your job is to explain what the model actually got wrong and
why — in a form a person can read in thirty seconds.

The model under audit is `{MODEL}`. The case is `{CASE}`.

## What you are looking for

The comparator reports one difference per differing key. That is the wrong unit
for a person: one decision by the author — modelling the wrong problem, reading
the time axis in the wrong unit, never writing a required key — shows up as many
differences. **Group the findings by the decision behind them.** A group of one
is fine when the mistake really is isolated.

For each group, decide what it actually is:

- `model_error` — the model misread the paper, invented a value, or ignored the
  authoring guide. The reference is right.
- `comparator_artifact` — the two specs say the same thing in different words,
  and the comparator cannot see it. A synonym, an equivalent formulation, a
  defensible modelling alternative, a key that carries a schema default.
- `paper_ambiguous` — the paper genuinely does not state this, or states it only
  in a figure. The model had no way to get it right from the text it was given.

Be honest about `comparator_artifact`. It is the most useful verdict you can
give, because it is the one that fixes the benchmark rather than the model — but
only if you apply it when it is true and not when it is convenient.

## Rules

1. **Output exactly one JSON object and nothing else.** No prose around it, no
   markdown fence.
2. Every finding index appears in exactly one group. Do not invent an index, and
   do not leave one out — if a finding defeats you, put it in a group of its own
   and say so in `why`.
3. `why` is at most 30 words and says something a person could not read off the
   finding list. "the value differs" is worthless. "the paper gives k in
   W/(m·K) in Table 2 but the model took the value from the abstract, which is
   per hour" is the job.
4. When the paper settles it, cite where — section, table or equation number.
5. `fix` is one short imperative aimed at whoever maintains the pipeline: a
   change to the authoring guide, to the prompt, to the comparator, or
   "nothing — the model simply has to read better".

## Output shape

```json
{
  "verdict": "one sentence: did this spec describe the same experiment?",
  "groups": [
    {
      "finding_indices": [0, 3, 4],
      "title": "a short plain-English noun phrase naming the decision — 'crack model read from the wrong table', never an identifier like 'crack_model_choice'",
      "cause": "model_error | comparator_artifact | paper_ambiguous",
      "why": "<=30 words, specific, cites the paper where it settles it",
      "fix": "one imperative"
    }
  ]
}
```

## The comparator's findings

{FINDINGS}

## What a mechanical grouping already suggests

These are produced by a rule, not by judgement. Use them, correct them, or
ignore them — they are a starting point, not an answer.

{CLUSTERS}

## The reference spec (what a human wrote from this paper)

```json
{REFERENCE}
```

## The candidate spec (what `{MODEL}` produced)

```json
{CANDIDATE}
```

## The paper

{PAPER}

---

Reply with the JSON object only.
