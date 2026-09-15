You are writing the summary of a benchmark-authoring evaluation.

Several language models were each asked, in one shot, to write a
`benchmark.json` for a finite-element validation suite from a paper. Every model
got an identical prompt: an authoring guide, the physics modules, the JSON
schema, and the paper's text. A comparator then diffed each result against the
reference spec a human wrote from the same paper, and an analyst grouped those
differences into the decisions behind them.

You are given the scores and every one of those groups. Write the document a
person reads to decide **what to change next**.

## What matters

A mistake one model made is a fact about that model. The same mistake made by
every model on the same case is a fact about the **paper** or about the
**authoring guide** — and only the second kind is actionable, because the guide
is the thing that can be edited. Rank accordingly.

Groups marked `comparator_artifact` are not model failures at all; they are
places the benchmark scores a difference that is not one. Collect them
separately. They are the cheapest score improvement available and they make
every future run more trustworthy.

## Rules

1. Output GitHub-flavoured markdown. No preamble, no sign-off, start at `##`.
2. Be specific and quantitative. "Models often get units wrong" is not worth
   writing; "seven of nineteen cases have at least one time value off by 86400 —
   the guide's units box states seconds but every paper reports days" is.
3. Name models and cases in backticks. Never invent a number that is not below.
4. **Only the diagnosed pairs below are evidence.** The scores table covers the
   whole run; the diagnoses may cover a fraction of it. Never write "every
   model" or "all cases" unless every one of them appears below — say "both
   diagnosed attempts" or name them. If one case was diagnosed, you are writing
   about one case, however tempting the pattern looks.
5. Where you recommend a change to the authoring guide, quote the sentence that
   is missing or misleading if you can infer it, and say what it should say.
6. At most 900 words. Cut the ranking before you cut the specifics.

## The document

Use exactly these sections:

```
## What this run shows
  Three to five sentences. The headline result and the one thing that surprises.

## Failure modes, ranked
  A table: mode | how many (model, case) pairs | sections hit | what it costs.
  Then a short paragraph per mode, in rank order, for the top four or five.

## Change the authoring guide
  A numbered list of concrete edits to CREATE.md or to the prompt, each with the
  evidence that motivates it. Nothing speculative.

## Change the comparator
  Everything marked `comparator_artifact`, grouped by the rule that produced it,
  with what the rule should do instead.

## Per-model notes
  One or two sentences per model. What it is good at, what it reliably fails.
```

## Scores

{SCORES}

{COVERAGE}

## The grouped diagnoses

{GROUPS}

---

Write the document.
