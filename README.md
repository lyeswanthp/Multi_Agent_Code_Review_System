# Code Review System

Multi-agent code review CLI that combines deterministic tools (Ruff, Semgrep, Bandit, ESLint) with LLM-based analysis (Syntax, Logic, Security, Git History) and a final orchestration step.

## What It Does

- Runs linters/SAST in parallel and parses structured findings.
- Builds shared code context once (changed files + direct imports).
- Executes 4 specialist agents in parallel via LangGraph.
- Synthesizes results, deduplicates noise, and outputs a final review.
- Supports commit-aware git analysis for regression/history signals.

## Requirements

- Python 3.11+
- **Local mode** (default): [LM Studio](https://lmstudio.ai/) running locally — no API keys needed.
- **Remote mode**: API keys for NVIDIA NIM, Groq, and Cerebras free tiers.
- Optional but recommended on PATH for full Tier 1 signal:
  - `ruff`, `semgrep`, `bandit`, `eslint` (Node.js needed for ESLint)

## Install

Recommended for end users (isolated CLI install):

```bash
pipx install code-review-system
```

Install with optional extra tooling (stronger deterministic signal):

```bash
pipx install "code-review-system[full]"
```

Local development install:

```bash
pip install -e ".[dev]"
```

## Configure

Copy `.env.example` to `.env` and adjust:

```env
# "local" (LM Studio, default) or "remote" (cloud APIs)
LLM_MODE=local

# Local mode (LM Studio)
LMSTUDIO_BASE_URL=http://localhost:1234/v1
LMSTUDIO_HEAVY_MODEL=qwen2.5-coder-14b-instruct
LMSTUDIO_LIGHT_MODEL=mistral-7b-instruct-v0.3

# Remote mode — fill these in when LLM_MODE=remote
NVIDIA_API_KEY=nvapi-...
GROQ_API_KEY=gsk_...
CEREBRAS_API_KEY=csk-...
SEVERITY_THRESHOLD=medium
```

## Usage

### Local folder review

```bash
code-review review --path . --severity medium
```

### JSON output

```bash
code-review review --path . --format json
```

### Commit-aware review (with git history overlap)

```bash
code-review review --path . --repo . --sha HEAD
```

Notes:
- `--sha` reviews that commit against its parent.
- To review your latest modifications with history context, commit them first (even as WIP), then review `HEAD`.

### GitHub payload output (no API post)

```bash
code-review review --path . --format github --gh-owner YOUR_ORG --gh-repo YOUR_REPO --gh-pr 123
```

This builds and prints a GitHub review payload JSON; it does not submit it automatically.

## Run Tests

```bash
python -m pytest -q
```

## Exit Codes

- `0`: no findings after filtering
- `1`: findings present
- `2`: invalid CLI usage (for example, missing required GitHub options in `--format github`)

