You are authoring ONE benchmark specification for a finite-element validation suite.

You are given: the authoring guide, the physics modules it routes to, the JSON
schema the file must satisfy, a short scope identifying the intended validation
case, and the full text of one paper. Produce the `benchmark.json` for that case.

## Rules

1. **Output exactly one JSON object and nothing else.** No prose before or after,
   no markdown fence, no explanation. The whole reply must parse as JSON.
2. Follow the authoring guide's units box exactly. Getting a unit wrong is the
   single most common failure and it is not recoverable downstream.
3. Every value carries a `_comment` provenance tag, as the guide describes
   (`PAPER_VALUE`, `INFERRED`, `ASSUMED`, `MISSING`, ...). Never invent a number
   and tag it `PAPER_VALUE`.
4. Build the value table (parameter -> value -> unit -> where in the paper)
   in your head first, then write the JSON from it.
5. The paper text was extracted automatically, so figures are absent and tables
   may be reflowed. Take numbers from the running text and the tables you can
   read. Where the paper only gives a quantity in a figure, tag it `MISSING`
   rather than guessing a value.
6. `results.reference_curves` may name CSV files that you cannot produce — name
   them as the guide describes and leave the extraction to a later step.

## The authoring guide

{CREATE}

## Physics modules

{MODULES}

## The schema your output must satisfy

```json
{SCHEMA}
```

## Case scope

The paper may describe several analyses. Use this scope to identify the specific
case and outputs this benchmark must reproduce.

{SCOPE}

## The paper

{PAPER}

---

Reply with the `benchmark.json` object only.
