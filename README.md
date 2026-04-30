# harness-engineering

Practical, runnable examples of *agentic harness engineering*.

This repo starts with a real demo you can run locally without any API key:

* an approval-gated research agent harness
* typed tool registry
* checkpointed run state
* resumable execution
* human approval gates for risky actions
* per-step tracing
* retry handling for flaky tools
* secret-scan script to help avoid leaking keys into a public repo

## Why this repo exists

Most agent demos focus on prompts. Real systems break somewhere else:

* tool contracts are vague
* retries are missing
* state disappears after interruption
* approvals are bolted on as chat text
* there is no trace of what happened

This repo demonstrates the opposite approach: engineer the harness around the model or tools.

## Demo architecture

The included demo is a small *planner/executor harness*:

1. `search_mock` finds relevant mock source documents
2. `extract_facts` turns them into concise facts
3. `draft_report` writes a markdown draft
4. `finalize_report` is treated as risky and requires explicit human approval before writing to disk

Run state is persisted under `.runs/<run_id>/state.json`.

## Project structure

```text
src/harness_engineering/
  cli.py
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

### 5. Approve and resume

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

## Make targets

```bash
make install
make demo
make demo-interactive
make test
make secrets
```

## Optional provider integration

This starter repo intentionally works without external APIs, but it can also use an OpenAI-compatible endpoint for draft generation.

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

Never hardcode keys. Never commit populated `.env` files.

## Why this is blog-post friendly

This repo gives you code that demonstrates real harness concepts cleanly:

* approvals are explicit workflow state
* resumes are deterministic
* traces are stored
* retries are visible
* risky actions are gated
* the interactive demo makes the approval boundary tangible for readers and screenshots

That makes it a good companion for a practical blog series on harness engineering.

## Next suggested expansions

* swap `search_mock` for a real MCP-backed search tool
* add a web research provider behind an interface
* add policy rules for file/network/tool permissions
* add evaluation fixtures for trace replay
* add a multi-agent planner/reviewer variant
