# harness-engineering

Practical, runnable examples of *agentic harness engineering*.

This repo starts with a real demo you can run locally without any API key:

* an approval-gated research agent harness
* typed tool registry
* explicit tool action categories and policy checks for safe/unsafe operations
* MCP-style tool descriptors and adapter calls
* checkpointed run state
* resumable execution
* human approval gates for risky actions
* per-step tracing with timing and lightweight cost/workload estimates
* persisted trace summaries for observability
* lightweight trace-aware eval fixtures
* explicit memory-layer separation for working context, session state, and retrieval memory
* retry handling for flaky tools
* secret-scan script to help avoid leaking keys into a public repo
* an optional multi-agent mode with explicit planner→executor→reviewer handoffs

## Why this repo exists

Most agent demos focus on prompts. Real systems break somewhere else:

* tool contracts are vague
* retries are missing
* state disappears after interruption
* approvals are bolted on as chat text
* there is no trace of what happened

This repo demonstrates the opposite approach: engineer the harness around the model or tools.

That now includes a small but real policy layer: the harness classifies tool actions (for example `read_only`, `model_generation`, and `filesystem_write`) and checks whether risky side effects are allowed before execution.

## Demo architecture

The included demo is a small *planner/executor/reviewer harness*:

1. a planner creates or confirms the workflow steps
2. `search_mock` finds relevant source documents
3. `extract_facts` turns them into concise facts
4. `draft_report` writes a markdown draft
5. a reviewer checks the draft structure/quality
6. `finalize_report` is treated as risky and requires explicit human approval before writing to disk
7. policy checks verify that filesystem writes stay inside allowed output roots before the write happens

By default this runs as a single harness loop. You can also start it in a small `--multi-agent` mode that keeps the same linear workflow but records explicit role activity and handoffs between planner, executor, and reviewer.

Run state is persisted under `.runs/<run_id>/state.json`.

## Project structure

```text
src/harness_engineering/
  cli.py
  mcp.py
  memory.py
  models.py
  runner.py
  store.py
  tools.py
  tracing.py
tests/
sample_data/
scripts/
```

## Quickstart

### 1. Run without installing anything

This works on locked-down systems too:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli start \
  --topic "Agentic harness engineering" \
  --source-file sample_data/sources.json
```

### Optional: install locally in a virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

### 2. Start a run

```bash
PYTHONPATH=src python3 -m harness_engineering.cli start \
  --topic "Agentic harness engineering" \
  --source-file sample_data/sources.json
```

Expected behavior:

* the harness runs through planning/extraction/drafting
* it pauses before `finalize_report`
* it saves checkpointed state locally
* it tells you how to approve and resume

To start the same demo in explicit multi-agent mode:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli start \
  --topic "Agentic harness engineering" \
  --source-file sample_data/sources.json \
  --multi-agent
```

That mode does **not** create a swarm or a parallel graph runtime. It keeps the same small workflow and adds persisted planner→executor and executor↔reviewer handoffs so you can inspect the role boundaries honestly.

### 3. Try the interactive demo

```bash
PYTHONPATH=src python3 -m harness_engineering.cli interactive \
  --topic "Agentic harness engineering" \
  --source-file sample_data/sources.json
```

This mode shows the draft report, prompts for approval, and either:
* writes the final report immediately if you approve, or
* leaves the run checkpointed for later resume if you decline

### 4. Inspect the latest run

```bash
PYTHONPATH=src python3 -m harness_engineering.cli inspect --latest
```

### 5. Print a run summary and replay-friendly history

The repo now writes a machine-readable summary for every saved run to:

```text
.runs/<run_id>/summary.json
```

You can also inspect that summary from the CLI:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli summary --latest
```

And inspect trace history for replay/debugging:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli history --latest
PYTHONPATH=src python3 -m harness_engineering.cli history --latest --event approval_required
PYTHONPATH=src python3 -m harness_engineering.cli history --latest --tail 5
```

The summary includes:

* current step and status
* whether approval is still required
* total attempts across steps
* counts of pause/resume/approval events
* final artifact paths
* the next commands to run for approval or resume

This is still a lightweight local harness, not a full durable workflow engine, but the summary/history surface makes pause-and-resume behavior easier to inspect and explain.

### 6. Inspect a compact trace summary

Every saved run now also writes a machine-readable trace summary to:

```text
.runs/<run_id>/trace_summary.json
```

You can print the same observability surface from the CLI:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli trace-summary --latest
```

The trace summary rolls up:

* total trace-event count
* event counts by type
* tool counts and retry attempts by tool
* per-tool wall-clock duration totals and averages
* estimated model-input/model-output token volume for draft-generation steps
* latest workflow event
* approval-gate status
* reviewer result
* whether a final artifact exists

This is intentionally small, but it gives operators a faster way to inspect run behavior than reading the full raw trace.

Important limitation: the repo records **engineering estimates**, not provider billing truth. Token counts are derived from character counts and the demo does not invent dollar prices it cannot verify.

### 7. Run lightweight eval fixtures

The repo now includes a tiny eval runner in `src/harness_engineering/evals.py` plus starter fixtures in `sample_data/evals/basic.json`.

Run the default suite:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli evals
```

These evals are trace-aware rather than benchmark-like. They check whether a run reaches expected workflow states and emits required trace events such as:

* `approval_required`
* `approval_granted`
* `run_resumed`
* `run_completed`

That makes them useful as harness evals: they validate runtime behavior, not just text quality.

### 8. Inspect the memory layers

The repo now exports a small memory architecture view in `src/harness_engineering/memory.py`.
For any saved run, it separates:

* `working_context`: what the harness needs right now for the next step
* `session_state`: durable run metadata for pause/resume and operator inspection
* `retrieval_memory`: relevant source documents and extracted facts fetched by query

Inspect the latest run with the default topic-based retrieval query:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli memory --latest
```

Or ask for retrieval results tied to a narrower query:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli memory --latest --query "approval gates" --top-k 3
```

A machine-readable `memory.json` snapshot is also written next to `state.json`, `trace.json`, and `summary.json` for every saved run.

### 9. Inspect multi-agent handoffs

For runs started with `--multi-agent`, the repo now persists a separate `handoffs.json` artifact plus role activity in `state.json`, `summary.json`, and `trace_summary.json`.

Inspect the latest multi-agent handoffs:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli handoffs --latest
```

That output includes:

* handoff count
* current role
* each recorded handoff with `from_role`, `to_role`, `purpose`, and payload summary
* role execution events recorded during planning, execution, and review

This is the repo's practical answer to multi-agent hype: keep roles sharp, keep the workflow small, and make the handoffs inspectable.

### 10. Inspect the pending approval action

Before approving a risky step, you can now inspect a structured pending-action payload:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli pending --latest
```

That output includes:

* the pending action name
* why approval is required
* the proposed output path
* a small draft preview
* the reviewer result that allowed the run to reach the approval gate
* the next CLI commands to inspect, approve, and resume

This makes the approval boundary more explicit than raw `inspect` output and models the kind of operator-facing approval surface a real harness should expose.

### 11. Inspect policy rules and action categories

The repo now includes a small policy engine in `src/harness_engineering/policy.py` plus a checked-in baseline policy file at `policy/default.json`.
By default it:

* classifies each tool by action category
* treats `finalize_report` as a `filesystem_write`
* allows writes only under the current runs directory
* records policy decisions in run artifacts and traces
* resolves relative policy paths from the policy file location, not from whatever shell directory you happened to run from

Inspect the effective built-in policy:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli policy --pretty
```

Inspect the checked-in baseline policy file explicitly:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli policy \
  --policy-file policy/default.json \
  --pretty
```

You can also supply a custom JSON policy file to tighten or relax rules:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli start \
  --topic "Agentic harness engineering" \
  --source-file sample_data/sources.json \
  --policy-file sample_data/policy/restrictive.json
```

With the included restrictive sample policy, the run is expected to fail before approval because the proposed output path is outside the configured allowed write roots.

### 12. Approve and resume

Replace `<run_id>` with the value printed by the `start` command.

```bash
PYTHONPATH=src python3 -m harness_engineering.cli approve <run_id>
PYTHONPATH=src python3 -m harness_engineering.cli resume <run_id>
```

The final markdown report will be written to:

```text
.runs/<run_id>/final_report.md
```

## Tests

```bash
make test
```

or

```bash
python3 -m unittest discover -s tests -v
```

## Secret hygiene for public repos

This repo is meant to stay public-safe.

Rules:

* do not commit `.env`
* only use placeholder variables in `.env.example`
* keep generated run data under ignored directories
* run the secret scan before pushing

Check tracked files for obvious secrets:

```bash
make secrets
```

## Policy model

The current policy layer is intentionally small and local.

Core pieces:

* `Tool.action_category` in `src/harness_engineering/tools.py`
* `PolicyEngine` and `PolicyDecision` in `src/harness_engineering/policy.py`
* `HarnessRunner._execute()` in `src/harness_engineering/runner.py`, which evaluates policy before tool execution
* `cmd_policy()` in `src/harness_engineering/cli.py`, which prints the effective policy

What it enforces today:

* tools can be enabled or disabled by name
* action categories are explicit and inspectable
* filesystem write targets must stay under allowed roots
* policy checks and denials are persisted in traces and summaries

What it does **not** do yet:

* network egress restrictions
* subprocess sandboxing
* OS-level isolation
* user/session identity-based policy
* capability-scoped credentials

That is deliberate: this repo demonstrates harness-level policy gates, not a full system sandbox.

## Make targets

```bash
make install
make demo
make demo-interactive
make test
make secrets
```

## Workflow graph export

The repo now includes a small orchestration-inspection helper in `src/harness_engineering/workflow.py`.

It exports the current harness workflow as either:

* structured JSON with nodes, transitions, approval gates, risky steps, and terminal states
* a Mermaid flowchart string for docs or diagrams

Inspect the workflow as JSON:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli workflow --pretty
```

Render the same workflow as Mermaid:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli workflow --format mermaid
```

This is intentionally a graph/export view of the **current** runner, not a full graph runtime. The live orchestration still happens in `src/harness_engineering/runner.py`.

## MCP-style tool adapter

The repo now includes a small MCP-ready adapter layer in `src/harness_engineering/mcp.py`.

It does three things:

* converts internal `Tool` definitions into MCP-style tool descriptors
* validates tool-call arguments against the registry's declared schema
* returns MCP-style call results with both `content` and `structuredContent`

List the default tool descriptors:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli mcp-tools --pretty
```

Call a tool through the adapter:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli mcp-call extract_facts '{"matches": []}'
```

This does **not** make the repo a full MCP server. It creates a provider-neutral interface boundary so the harness can expose its tools in a protocol-friendly shape while still keeping orchestration, retries, approvals, and state in the harness.

## Optional provider integration

This starter repo intentionally works without external APIs, but it can also use an OpenAI-compatible endpoint for planning, review, and draft generation.

The provider-loading path is intentionally small and inspectable:

* `load_dotenv()` in `src/harness_engineering/provider.py` reads a repo-local `.env`
* `load_model_config()` in `src/harness_engineering/provider.py` prefers repo-local `HARNESS_*` variables before inherited shell variables
* `doctor_check()` in `src/harness_engineering/provider.py` validates `/models` and a minimal chat round-trip before you rely on local-model behavior in the demo

Supported env vars:

* `MODEL_PROVIDER` (`mock` or `openai_compatible`)
* `MODEL_NAME`
* `OPENAI_BASE_URL`
* `OPENAI_API_KEY`
* `HARNESS_MODEL_PROVIDER`
* `HARNESS_MODEL_NAME`
* `HARNESS_OPENAI_BASE_URL`
* `HARNESS_OPENAI_API_KEY`
* `ANTHROPIC_API_KEY`
* `GOOGLE_API_KEY`

Example local `.env` (do not commit it):

```dotenv
HARNESS_MODEL_PROVIDER=openai_compatible
HARNESS_MODEL_NAME=gemma4
HARNESS_OPENAI_BASE_URL=http://127.0.0.1:8080/v1
HARNESS_OPENAI_API_KEY=your-local-key
```

The `HARNESS_*` names are useful when your shell already exports unrelated provider keys.

Check provider/model connectivity:

```bash
PYTHONPATH=src python3 -m harness_engineering.cli doctor
```

A healthy local OpenAI-compatible setup should return `status: "ok"` and echo `MODEL_OK`.
If you leave the repo in mock mode, `doctor` should return `status: "mock"` and skip network checks.

Never hardcode keys. Never commit populated `.env` files.

## Why this is blog-post friendly

This repo gives you code that demonstrates real harness concepts cleanly:

* approvals are explicit workflow state
* resumes are deterministic
* traces are stored
* per-run trace summaries are persisted next to state and trace files, including timing and workload estimates
* lightweight eval fixtures can assert expected workflow states and trace events
* per-run summaries are persisted next to state and trace files
* pending approval actions are inspectable from the CLI with operator-friendly details
* working/session/retrieval memory layers are inspectable from the CLI
* replay/debug history is inspectable from the CLI
* retries are visible
* risky actions are gated
* policy decisions are explicit, persisted, and inspectable
* filesystem writes are constrained to allowed output roots by policy
* the interactive demo makes the approval boundary tangible for readers and screenshots
* optional local-model planning and review make the harness feel more agentic without requiring cloud APIs
* multi-agent mode records explicit role handoffs without pretending the system is more autonomous than it is

That makes it a good companion for a practical blog series on harness engineering.

## Next suggested expansions

* expose the adapter over a real JSON-RPC MCP server transport
* swap `search_mock` for a real MCP-backed search tool
* add a web research provider behind an interface
* attach token-budget metrics to working-context construction
* extend policy beyond filesystem writes into network/tool/subprocess permissions
* extend eval fixtures into replayable regression suites with timing/cost thresholds
* extend the current multi-agent mode with verifier-specific tools or stricter handoff contracts
