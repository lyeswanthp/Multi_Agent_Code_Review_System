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
- **Local mode** (default): [Ollama](https://ollama.com) running locally — no API keys needed.
- **Remote mode**: API keys for NVIDIA NIM, Groq, and Cerebras free tiers.
- Optional but recommended on PATH for full Tier 1 signal:
  - `ruff`, `semgrep`, `bandit`, `eslint`

## Install

```bash
pip install -e ".[dev]"
```

## Configure

Copy `.env.example` to `.env` and adjust:

```env
# "local" (Ollama, default) or "remote" (cloud APIs)
LLM_MODE=local

# Local mode — pull models first: ollama pull qwen2.5-coder:14b && ollama pull llama3.1:8b
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_HEAVY_MODEL=qwen2.5-coder:14b
OLLAMA_LIGHT_MODEL=llama3.1:8b

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

