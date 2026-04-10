# Code Review System

Multi-agent code review CLI that fuses deterministic static analysis (Ruff, Semgrep, Bandit, ESLint) with LLM-powered specialist agents (Syntax, Logic, Security, Git History) orchestrated via LangGraph — all informed by an AST-driven knowledge graph built with `tree-sitter` and `networkx`.

## Architecture

```
Tier 1 — Static Analysis    Ruff · Semgrep · Bandit · ESLint (parallel)
Tier 2 — Context Assembly    File collection → AST parsing → Knowledge Graph (call chains, imports, inheritance, rationale comments)
Tier 3 — AI Agents           Prefilter → Syntax · Logic · Security · Git History (parallel) → Orchestrator
Output                       Terminal (Rich) · JSON · GitHub payload · Web dashboard
```

**Knowledge Graph** — `tree-sitter` parses each file into an AST; `networkx` stores modules, functions, classes, call-graph edges, import dependencies, inheritance, and developer-intent rationale comments (`# WHY:`, `# HACK:`, `# SECURITY:`, etc.). Agents receive topology-aware subgraphs instead of raw file dumps, reducing token usage and improving context quality.

## Requirements

- Python 3.11+
- **Local mode** (default): [LM Studio](https://lmstudio.ai/) running locally — no API keys needed
- **Remote mode**: API keys for NVIDIA NIM, Groq, and Cerebras (free tiers)
- Optional: `ruff`, `semgrep`, `bandit`, `eslint` on PATH for full Tier 1 signal

## Install

```bash
# Development
pip install -e ".[dev]"

# With external analyzers
pip install -e ".[full]"
```

## Configure

Copy `.env.example` → `.env` and adjust:

```env
LLM_MODE=local                                    # "local" or "remote"
LMSTUDIO_BASE_URL=http://localhost:1234/v1
LMSTUDIO_HEAVY_MODEL=qwen2.5-coder-14b-instruct
LMSTUDIO_LIGHT_MODEL=mistral-7b-instruct-v0.3
SEVERITY_THRESHOLD=medium                          # low | medium | high | critical

# Remote mode only
NVIDIA_API_KEY=nvapi-...
GROQ_API_KEY=gsk_...
CEREBRAS_API_KEY=csk-...
```

## Usage

```bash
# Review current directory
code-review review --path . --severity medium

# JSON output
code-review review --path . --format json

# Commit-aware review (compares SHA against its parent)
code-review review --path . --repo . --sha HEAD

# GitHub payload (prints JSON, does not submit)
code-review review --path . --format github --gh-owner ORG --gh-repo REPO --gh-pr 123

# Disable web dashboard
code-review review --path . --dashboard-port 0
```

A live web dashboard starts on port `9120` by default during each review.

## Run Tests

```bash
python -m pytest -q
```

## Exit Codes

| Code | Meaning |
|------|---------|
| `0`  | No findings after filtering |
| `1`  | Findings present |
| `2`  | Invalid CLI usage |
