# Migrating the benchmark pipeline to DeepSeek Harness (dsh)

Working checklist. Tick stages off in order; each has a **gate** that must pass before
the next starts. Nothing here requires a period where the current pipeline is broken.

Verified against `deepseek-ai/deepseek-harness` @ `master` (pushed 2026-08-21).

## Why dsh changes the design

dsh is built on **Cordis** — "everything is a plugin". Services claim `ctx.<key>`
(`ctx.tools`, `ctx.llm`, `ctx.sessions`, `ctx.fs`, `ctx.attachments`,
`ctx.systemPrompt`), and typed events (`turn/start`, `agent/pre-step`,
`tools/pre-execute`, `tool/result`, `fs/*`) are documented extension points.

That means the phase discipline currently written as prose in `WORKER.md` can become
**structure**: a phase gets its own tool scope, its own mounted skill sections, and its
own turn budget, and the harness enforces it instead of asking the agent to.

The session log is the source of truth — the runtime invariant is *"model-visible
means logged"*, and the log is forkable and resumable. That replaces `STATUS.json`
as state and deletes the `STATUS.attempt<N>.json` crash-preservation hack.

## Model routing — the one architectural choice

Shipped adapters are `llm-deepseek` and `llm-pi-ai` only. **There is no Anthropic
main-route adapter.** Three ways to keep the current model:

| Route | What it is | Cost | Risk |
|---|---|---|---|
| **A. `subagent-claude-code`** | dsh orchestrates; Claude Code runs as a subagent (`@anthropic-ai/claude-agent-sdk@0.3.220`, shipped in `packages/subagent/subagent-claude-code`) | lowest | coarser phase scoping — the subagent owns its own tool loop |
| **B. custom `LlmAdapter`** | `ctx.llm.registerAdapter(providers, adapter)` — a documented public API | medium | you own streaming, retries, and the **modality declaration** that gates `read_image` |
| **C. OpenAI-compatible proxy** | LiteLLM in front, reuse an existing adapter via `DEEPSEEK_BASE_URL` | low | extra moving part; modality metadata may not survive the shim |

**Decision: A first, B later.** The subagent bridge lets orchestration, state, tools and
policy migrate while the model path stays known-good. Write the adapter once the rest
is stable.

---

## Stage 0 — Repo reorganisation

Harness-independent, and both pipelines read these files — doing it later means
migrating twice.

- [ ] `authoring/` — CREATE.md (core), MAINTAINING.md, `modules/`, ANALYSIS.md,
      EXTRACT.md, DEBUGGING-PLAYBOOK.md
- [ ] `worker/tools/` — check_benchmark.py, plot_digitize.py (all pipeline tools live together, beside pdf_render/status/handoff)
- [ ] `tests/` — the 4 skill-bound contract tests (absolute imports), plus
      `test_corpus_lints.py`
- [ ] `benchmark.schema.json` **stays in THCM-Fem** — it is the solver's input
      contract and must change atomically with solver code
- [ ] repoint `worker/SKILLs/*` symlinks inward, one at a time
- [ ] update THCM-Fem `README.md` (5 refs) and the `config.sh` "must never be forked"
      comment

**Gate:** `run_queue.sh --preflight` green · contract tests green in both repos ·
`test_corpus_lints.py` passing or explicitly xfailed with reasons.

## Stage 1 — Groundwork that pays off either way

- [x] **`worker/tools/plot_digitize.py`** — `axes` / `calibrate` / `series` / `verify`.
      Replaces the browser digitizer, which does not currently work. Biggest single
      fragility removal, and no dsh decision depends on it.
- [x] **`pdf_render.py --max-side`** clamp (default 1600, `0` disables). Verified: a
      300-dpi A4 page renders 2481x3308 and is downscaled to 1200x1600 with a note.
- [x] **`worker/SKILLs/EXTRACT.md`** written as the routing skill: vector → colour →
      markers → curve → human handoff, with the browser digitizer demoted to one
      sub-route (`EXTRACT-WEB.md`). Wired as `{{SKILL_EXTRACT}}` + `{{DIGITIZE}}`;
      WORKER.md Phase 3 rewritten around it, `verify` made mandatory.

**Stage 1 complete.**

**Gate: PASSED 2026-08-23.** Bazant 2004 Fig. 5 re-digitized with
`--mode markers --ring --recover-hidden --min-hole 100`: 10 markers, every one
within 0.3% of the hand extraction, and `verify` shows each replotted point
inside its data circle. Recall is 10 of 13 -- the three partially occluded by the
curve need threshold relaxation and a look at the `verify` overlay, which is what
that step exists for. Tuning note: `--min-hole` is the discriminator between a
data ring (~138 px enclosed) and a letter counter in an annotation (~69 px).

## Stage 2 — Spike

### FINDINGS 2026-08-23 (read-only, from source — nothing installed)

The spike paid for itself before running a single agent turn. Four corrections to
this plan:

**1. `pip install deepseek-harness` gets the WRONG package.** PyPI's
`deepseek-harness` is a third-party client by "Henry Zhang" ("Protocol-aware client
for DeepSeek V4-Pro/V4-Flash/Vision"). It imports as `deepseek_harness` — the **same
module name** as the official SDK — but exposes only `chat()` / `stream_chat()`. No
`run`, no `session_id`, no `cordis`. The official distribution is
**`deepseek-harness-sdk`** (`python/sdk/`), which depends on
`deepseek-harness-runtime-bin`; neither resolves on public PyPI, which is why the
install is done from a clone.

**2. Cordis plugins are TypeScript, not Python.** The Stage 3 sketch listed
`harness/plugins/*.py`. Wrong: plugins live in `packages/*/src/*.ts` and use
`defineTool`. The Python SDK is a *client* that launches a Node runtime subprocess
(`runtime_bin`, `DSH_CWD`/`DSH_SESSION_ROOT`/`DSH_CORDIS_CONFIG` env). Plugin work is
TS regardless of what drives it.

**3. `Session` is a first-class object — Stage 4 is easier than assumed.**

```python
h = DeepSeekHarness(provider=..., model=..., cwd=..., session_root=..., cordis=...)
s = h.start_session("bench-<slug>")     # explicit, reusable
s.run(phase1_task); s.run(phase2_task)  # same durable session
```

Phases-as-scoped-runs is a supported shape, not a convention we impose.

**4. Routing to a non-DeepSeek model is easier than Route C implied.**
`DeepSeekHarnessConfig` carries **`base_url`** and **`api_key`** directly, plus an
`env` dict for injecting `DEEPSEEK_BASE_URL`. An OpenAI-compatible proxy in front of
another model is a config change, not a plugin.

### What `run()` returns — this is the Stage 5 foundation

```python
RunResult(session_id, final_response, finish_reason, events, notifications, session_root)
```

`events` is the session event stream (`session.event` notifications filtered to this
session), and there is an `on_notification` callback for live progress. So
`result_writer.py` projects `RESULT.md` and `phases.json` straight off `RunResult`
— no separate state file, exactly as Stage 5 wants. `finish_reason` gives the
`done`/`failed` split for free.

`run()` also accepts `list[JsonObject]` content blocks, not just a string
(`normalize_input` wraps a str as `[{"type":"text","text":...}]`) — worth testing
whether an image block can be passed directly instead of going through `read_image`.

### LIVE FINDINGS — runtime actually started (no API key needed for any of this)

`deepseek-harness-sdk` + `deepseek-harness-runtime-bin` 0.1.1rc1 install into the
project venv. The runtime is a 212 MB standalone JSON-RPC binary
(`dsh-jsonrpc-agent-pkg-linux-x64`), resolved via
`deepseek_harness_runtime.resolve_bundled_launch_args()`.

**It starts and runs the whole turn pipeline without credentials** — the model call
fails with `finish_reason: "error"`, but every event up to it fires:
`turn/start → step/start → user/message → session/title → request/header →
request/context → assistant/chunk → step/end → turn/end`. That is enough to inspect
the composition, which is how everything below was measured.

**`request/header` is the observability hook.** Its payload carries the assembled
**system prompt** and the **full tool schema list** — so a CI check can assert the
composition instead of discovering it mid-run. `request/context` reports
`contextWindow: 1000000`, `maxTokens: 256000`.

#### The default composition is 5 tools

| tool | params |
|---|---|
| `bash` | `command`, `description`, `timeoutMs`, `workdir`, **`run_in_background`** |
| `job_list` | – |
| `job_output` | `job_id`, **`wait`**, `timeout_ms` |
| `job_kill` | `job_id`, `reason` |
| `skill` | `name` |

#### Three planned plugins are now unnecessary

- **`run_solver` — DROP IT.** `bash` takes `run_in_background: true` and `timeoutMs`,
  and the job tools manage the rest. The system prompt already instructs: *"You are
  notified in-session when a job finishes — do not busy-poll or sleep on one."* The
  3-hour solver is `bash(run_in_background)` + `job_output(wait=true)`. The 300 s cap
  was never the obstacle.
- **`skill_sections` — probably DROP IT.** There is a native `skill` tool: *"Load the
  full instructions for an available skill. Call this with the exact skill name from
  the session skill catalog."* That is exactly the CREATE.md core + routed `modules/`
  design, natively supported. Register the modules as skills and the routing table
  becomes a catalog lookup. **Verify next:** how a skill catalog is declared in the
  cordis config, and whether a 40 KB skill body is accepted.
- **`read_image` is NOT in the default composition** — confirmed empirically, matching
  `docs/tool-catalog.md`'s `ctx.attachments` gate. The preflight assertion in Stage 3
  is therefore mandatory, and it can read `request/header` to do it.

#### What the runtime binary actually bundles

The 212 MB runtime is a self-contained pkg; `dsh-*` plugin packages are compiled in
and composed by config. Everything the plan needs is present:

`dsh-attachment-local` · `dsh-tool-fs` · `dsh-fs-observation-policy` · `dsh-fs-sandbox`
· `dsh-sandbox-policy` · `dsh-skill` · `dsh-skill-filesystem` · `dsh-tool-skill` ·
`dsh-tool-jobs` · `dsh-tool-bash` · `dsh-tool-bash-persistent` ·
`dsh-tool-str-replace-editor` · `dsh-session-persistence-jsonl` ·
`dsh-compaction-basic` · `dsh-user-approval` · `dsh-hooks-claude-code`

**A. `dsh-llm-mock-server` IS bundled — the spike is no longer credential-blocked.**
Every "needs an API key" item can be exercised against the mock route: tool
registration, `read_image` gating, per-phase tool scoping, write confinement, skill
loading, and the full `RunResult.events` projection. Only the *real* image
dimension/byte limits and genuine model behaviour need a live route.

**B. `dsh-subagent-claude-code` is NOT bundled.** Only `dsh-subagent-acp`,
`-fork-in-process`, `-spawn-in-process`. So **Route A as written is unavailable** in
the SDK runtime — the claude-code subagent exists in the monorepo but is not compiled
into the published binary. Revised routing:

| Route | Status |
|---|---|
| ~~A. `subagent-claude-code`~~ | **not in the runtime binary** — would need a custom runtime build |
| **C. OpenAI-compatible proxy** | **now the cheapest path.** `DeepSeekHarnessConfig` takes `base_url` + `api_key` directly, and `dsh-llm-deepseek` reads `apiKeyEnv` — point both at a proxy |
| B. custom `LlmAdapter` | still the clean long-term answer; TypeScript, needs a runtime build |
| — | `dsh-subagent-acp` may bridge to any ACP-speaking agent — worth a look before committing to B |

**C. `dsh-skill-filesystem` exists — the CREATE.md/modules split maps natively.**
Skills are loaded from the filesystem and surfaced through the `skill` tool ("Load the
full instructions for an available skill... from the session skill catalog"). The core
+ routed-modules design becomes a catalog directory, not a prompt-assembly plugin.

#### Still open

- [ ] real `maxImageDimension` / `maxImagePixels` / byte limits (needs the attachment
      store mounted + an image-capable route)
- [ ] whether an image content block can be passed straight to `run()` instead of
      going through `read_image`
- [ ] `ctx.tools` per-phase scoping, and `tools/pre-execute` write confinement — both
      need a custom cordis config
- [ ] skill catalog declaration + 40 KB body limit

### Still to verify (needs the SDK installed + a route)

`worker/harness/spike.py` proving six things:

- [ ] `read_image` is **registered** after mounting `dsh-attachment-local` — assert
      it is in the tool list, not that it "works"
- [ ] a 300-dpi page render is refused and a figure crop is accepted — find the real
      `maxImageDimension` / `maxImagePixels` / byte limits empirically
- [ ] a custom `defineTool` runs `thcm` past the built-in bash **300 s** cap via
      spawn-and-poll
- [ ] `ctx.tools` scoping actually hides a tool from a phase
- [ ] `tools/pre-execute` rejects a write outside the workdir
- [ ] 40 KB of CREATE.md mounts as a prompt section (NOT through
      `str_replace_editor`, which caps output at 16,000 chars)

**Gate:** all six green, or the design changes before any real code exists.

## Stage 3 — The harness package

```
worker/harness/
  profile.yaml            bundles + patches; restate the app config IN FULL
  plugins/
    benchmark_tools.py    check_benchmark · run_solver · digitize · render_pdf
    phase_scope.py        per-phase ctx.tools scope
    workdir_policy.py     tools/pre-execute write confinement
    skill_sections.py     mounts CREATE.md + the routed modules
    result_writer.py      session log -> RESULT.md + phases.json
  run.py                  SDK entry, one call per paper
  preflight.py            asserts read_image registered + route declares image input
```

⚠️ A Cordis config patch **replaces the whole app config** — the shipped
`examples/acp-agent/image.cordis.yml` restates it for exactly this reason.

**Gate:** `dsh --profile bench --dump-config` shows the expected tree ·
`preflight.py` fails loudly when the attachment store is missing.

## Stage 4 — Phases as scoped runs

`WORKER.md`'s 7 phases become N `harness.run()` calls sharing one `session_id`
(documented to preserve shell state and continue the durable conversation). Each phase
gets its own tool scope, turn budget and mounted skill sections.

**Gate:** one paper end to end producing a `benchmark.json` that passes
`check_benchmark.py` with zero ERRORs.

## Stage 5 — The UX redesign

```
AutoBench/<slug>/
  paper.pdf
  RESULT.md          <- the one file a human reads
  benchmark/         <- the deliverable
  .run/              <- machine-owned; dot-prefixed so ls and explorers hide it
    session.jsonl  phases.json  figures/  output/
```

Three stops, defined by the reader's next action:

| State | Means | You |
|---|---|---|
| `done` | ran and was analysed — **including** "not benchmarkable because X" | read `RESULT.md` |
| `needs-you` | blocked on data or a decision only a human has | `RESULT.md` names file + folder |
| `failed` | the pipeline broke | `RESULT.md` names phase + error |

- [ ] `infeasible` folds into `done` (it is a complete answer, not a failure)
- [ ] `stuck` + `crashed` fold into `failed`; `parked` becomes `needs-you`
- [ ] `key_result` gets a hard length cap (today's are 700-char paragraphs in a table cell)
- [ ] state is **projected** from the session log, not separately maintained

**Gate:** someone else can open `AutoBench/<slug>/` and say what happened without asking.

## Stage 6 — Cutover

- [ ] dual-run 3 papers on both harnesses; diff `benchmark.json` and the scorecards
- [ ] flip the default
- [ ] keep `claude -p` behind a flag for one cycle
- [ ] delete it

---

## Mapping

| Today | dsh |
|---|---|
| `claude -p "$prompt"` | `harness.run(task, session_id=)` |
| `--max-turns 400` | per-phase budget |
| `--allowedTools` | `ctx.tools` scope per phase |
| `--permission-mode acceptEdits` | `dsh-base` sandbox/approval policy |
| `--add-dir FEM_ROOT` | fs provider roots |
| `--output-format json` | session log (`ctx.sessions`) |
| `STATUS.json` + `status.py` | projected from the session log |
| `handoff.py` / `_needs_human/` | `tools/pre-execute` policy + `needs-you` |
| `mcp__claude-in-chrome` | `plot_digitize` tools (browser optional) |
| `Read(pages=)` on PDFs | `pdf_render` + `read_image` |
| Bash -> `thcm` | custom `run_solver` tool (bash caps at 300 s) |
| `{{PLACEHOLDER}}` substitution | prompt sections via `ctx.systemPrompt` |

## Risks

| Risk | Mitigation | Stage |
|---|---|---|
| No Anthropic main route | subagent bridge first, adapter later | 0 |
| `read_image` silently unregistered (no `ctx.attachments`) | preflight assertion | 2/3 |
| Image dimension/pixel/byte gates | crop workflow + `--max-side` | 1/2 |
| bash 300 s vs 3 h solver | custom polling tool | 2 |
| Cordis patch replaces whole app config | restate config; `--dump-config` in CI | 3 |
| Cordis learning curve | the spike is the tuition | 2 |
| POSIX-only (no Windows) | WSL is fine | — |
