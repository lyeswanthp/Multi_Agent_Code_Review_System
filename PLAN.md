# Code Review System — Implementation Plan

## Phase 0: Documentation Discovery (Complete)

### Verified APIs & Packages

| Package | Import | Verified |
|---------|--------|----------|
| `langgraph` | `from langgraph.graph import StateGraph, START, END` | Yes |
| `openai` | `from openai import AsyncOpenAI` | Yes |
| `langchain-core` | `from langchain_core.messages import HumanMessage, SystemMessage` | Yes |
| `gitpython` | `from git import Repo` | Yes |
| `pydantic` | `from pydantic import BaseModel` | Yes |

### Verified Model Endpoints (Multi-Provider Free Tier)

| Provider | Endpoint | Rate Limit | Key Prefix |
|----------|----------|------------|------------|
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | 40 req/min | `nvapi-` |
| Groq | `https://api.groq.com/openai/v1` | 14.4K req/day | `gsk_` |
| Cerebras | `https://api.cerebras.ai/v1` | 14.4K req/day | `csk-` |

All three providers are OpenAI-compatible (`/v1/chat/completions`). Single `openai.AsyncOpenAI(base_url=..., api_key=...)` client works for all.

### Agent-to-Provider Mapping

| Agent | Provider | Model | Why |
|-------|----------|-------|-----|
| Syntax | Groq | `llama-3.3-70b-versatile` | Fast, lightweight — only interprets linter output |
| Logic | NVIDIA NIM | `mistralai/devstral-2-123b-instruct-2512` | Strongest code reasoning, 256K context |
| Security | Cerebras | `qwen3-32b` | Fast inference + good security reasoning |
| Git History | Groq | `llama-3.1-8b-instant` | Lightweight — just summarizes file-level diffs |
| Orchestrator | NVIDIA NIM | `nvidia/llama-3_3-nemotron-super-49b-v1` | Best tool-calling & synthesis |

### Verified Linter CLI Commands

| Tool | Command | Output |
|------|---------|--------|
| Ruff | `ruff check --output-format json <path>` | JSON array of findings |
| Semgrep | `semgrep --json --config auto <path>` | JSON with `results` array |
| Bandit | `bandit -r <path> -f json` | JSON with `results` array |
| ESLint | `eslint --format json <path>` | JSON array per file |

### Known Caveats

1. **NVIDIA NIM bottleneck**: 40 req/min shared across models — with 2 NIM calls per review (Logic + Orchestrator), max ~20 reviews/min. Groq and Cerebras handle the other agents to avoid this bottleneck.
2. **Provider fallback**: On any provider failure (rate limit, timeout), log a warning and return empty findings for that agent — never crash the entire review.

---

## Phase 1: Project Skeleton & Data Models

**Goal**: Set up monorepo structure, Pydantic models, and configuration.

### Project Structure
```
code-review-system/
├── pyproject.toml                # Single package, all deps
├── .env.example                  # API key template
├── packages/
│   └── core/
│       └── code_review/
│           ├── __init__.py
│           ├── config.py         # Settings via pydantic-settings
│           ├── llm_client.py     # Unified OpenAI-compatible client (Groq/NIM/Cerebras)
│           ├── models.py         # Finding, ReviewResult, AgentPayload
│           ├── state.py          # ReviewState TypedDict for LangGraph
│           ├── agents/
│           │   ├── __init__.py
│           │   ├── syntax.py
│           │   ├── logic.py
│           │   ├── security.py
│           │   ├── git_history.py
│           │   └── orchestrator.py
│           ├── tools/
│           │   ├── __init__.py
│           │   ├── ruff_runner.py
│           │   ├── semgrep_runner.py
│           │   ├── bandit_runner.py
│           │   ├── eslint_runner.py
│           │   └── git_diff.py
│           ├── graph.py          # LangGraph StateGraph wiring
│           ├── context.py        # Tier 2: context assembly
│           ├── noise_filter.py   # Post-processing dedup
│           └── output/
│               ├── __init__.py
│               ├── base.py       # OutputAdapter ABC
│               ├── terminal.py   # CLI output
│               └── github.py     # PR review comments
├── cli/
│   ├── __init__.py
│   └── main.py                   # Typer CLI entrypoint
├── rules/
│   └── default/
│       ├── syntax.md
│       ├── logic.md
│       ├── security.md
│       └── git_history.md
└── tests/
    ├── __init__.py
    ├── test_models.py
    ├── test_tools.py
    ├── test_agents.py
    └── test_graph.py
```

### Tasks
1. Create `pyproject.toml` with dependencies:
   - `langgraph`, `langchain-core`, `openai`, `httpx`
   - `pydantic`, `pydantic-settings`
   - `gitpython`, `typer`, `rich`, `pyyaml`
   - Dev: `pytest`, `pytest-asyncio`, `ruff`
2. Create `models.py` — Pydantic models:
   - `Finding(severity, file, line, message, agent, suggestion)`
   - `ReviewResult(findings, summary, metadata)`
   - `AgentPayload(diff, files, linter_output, git_overlap)`
3. Create `state.py` — LangGraph shared state:
   - `ReviewState(TypedDict)` with `Annotated[list[Finding], operator.add]` reducer
4. Create `config.py` — environment-based config:
   - Per-agent: model name, provider base_url, API key env var
   - Severity threshold, fallback behavior
5. Create `llm_client.py` — unified OpenAI-compatible client:
   - `get_client(provider) -> AsyncOpenAI` with correct base_url/api_key
   - `call_agent(provider, model, messages) -> str` with graceful fallback
   - On failure: log warning, return empty result (never crash)
6. Create `.env.example` with keys for all 3 providers

### Verification
- [ ] `pip install -e .` succeeds
- [ ] `python -c "from code_review.models import Finding"` works
- [ ] All model imports resolve

### Anti-patterns
- Do NOT install `langchain` (full package) — only `langchain-core`
- Do NOT install provider-specific LangChain packages — use `openai` SDK for all providers
- Do NOT hardcode API keys — use env vars via pydantic-settings

---

## Phase 2: Tier 1 — Deterministic Tool Runners

**Goal**: Implement parallel linter/SAST execution with structured JSON output.

### Tasks
1. Create `tools/ruff_runner.py` — `run_ruff(path) -> list[Finding]`
   - Shell out: `ruff check --output-format json <path>`
   - Parse JSON → Finding objects
2. Create `tools/semgrep_runner.py` — `run_semgrep(path) -> list[Finding]`
   - Shell out: `semgrep --json --config auto <path>`
   - Parse `results` array → Finding objects
3. Create `tools/bandit_runner.py` — `run_bandit(path) -> list[Finding]`
   - Shell out: `bandit -r <path> -f json`
   - Parse `results` array → Finding objects
4. Create `tools/eslint_runner.py` — `run_eslint(path) -> list[Finding]`
   - Shell out: `eslint --format json <path>`
   - Parse JSON → Finding objects
5. Create `tools/git_diff.py`:
   - `get_changed_files(repo, sha) -> set[str]`
   - `get_file_overlap(repo, current_sha) -> set[str]` (current ∩ previous)
   - `get_diff(repo, sha) -> str`
6. Create parallel runner: `async def run_all_tools(path, repo) -> ToolResults`
   - Use `asyncio.gather` to run all tools concurrently

### Verification
- [ ] Each runner returns valid `Finding` objects from a test file
- [ ] `git_diff.get_file_overlap()` returns correct intersection
- [ ] Parallel runner completes faster than sequential

### Anti-patterns
- Do NOT let tools raise on linter errors (linter findings ≠ exceptions)
- Do NOT parse linter output with regex — use JSON mode

---

## Phase 3: Tier 2 — Context Assembly

**Goal**: Single-read file loader that builds shared state for all agents.

### Tasks
1. Create `context.py`:
   - `assemble_context(path, repo, tool_results) -> ReviewState`
   - Read each changed file ONCE → `{filepath: content}` dict
   - Parse all tool JSON outputs (already in ToolResults)
   - Compute git overlap set (already from Tier 1)
   - Resolve import chains for Logic Agent (follow `import` statements 1 level deep)
   - Package into `ReviewState` dict
2. Implement import resolver:
   - For Python: regex parse `import X` / `from X import Y` → resolve to file paths
   - For JS/TS: parse `import ... from '...'` → resolve relative paths
   - Only 1 level deep, only within the repo

### Verification
- [ ] `ReviewState` contains all changed file contents (read once)
- [ ] Import resolver finds direct dependencies
- [ ] No file is read more than once (add a counter/log)

### Anti-patterns
- Do NOT use AST parsing for import resolution — regex is sufficient for 1-level
- Do NOT read files that aren't in the changed set or their direct imports

---

## Phase 4: Tier 3 — LangGraph Agent Graph

**Goal**: Wire up 4 parallel agents + orchestrator in LangGraph.

### Tasks
1. Create `agents/syntax.py`:
   - Input: linter findings from state
   - Provider: **Groq** | Model: `llama-3.3-70b-versatile`
   - Job: Interpret linter JSON → human-readable prose, prioritize
   - System prompt from `rules/default/syntax.md`
2. Create `agents/logic.py`:
   - Input: full diff + file contents + import context
   - Provider: **NVIDIA NIM** | Model: `mistralai/devstral-2-123b-instruct-2512`
   - Job: Semi-formal reasoning — premises, execution paths, conclusions
   - System prompt from `rules/default/logic.md`
3. Create `agents/security.py`:
   - Input: Semgrep/Bandit findings + file contents
   - Provider: **Cerebras** | Model: `qwen3-32b`
   - Job: Triage SAST findings, separate real threats from false positives
   - System prompt from `rules/default/security.md`
4. Create `agents/git_history.py`:
   - Input: overlap files + diffs between current and previous commit
   - Provider: **Groq** | Model: `llama-3.1-8b-instant`
   - Job: Flag repeated changes, regressions, incomplete refactors
   - System prompt from `rules/default/git_history.md`
   - **Skip entirely** if overlap set is empty
5. Create `agents/orchestrator.py`:
   - Input: all agent findings merged via reducer
   - Provider: **NVIDIA NIM** | Model: `nvidia/llama-3_3-nemotron-super-49b-v1`
   - Job: Synthesize, deduplicate, rank, produce final summary
6. Create `graph.py` — LangGraph wiring:
   ```python
   builder = StateGraph(ReviewState)
   # Fan-out from START → 4 agents in parallel (equal depth)
   # Fan-in → orchestrator → END
   ```
   - Use `Annotated[list[Finding], operator.add]` reducer
   - All 4 agents are direct START → agent → orchestrator (equal depth, no fan-in caveat)
   - Each agent uses `llm_client.call_agent()` with its assigned provider/model

### Verification
- [ ] Graph compiles: `graph = builder.compile()`
- [ ] With mock LLM responses, fan-out executes all 4 agents
- [ ] Orchestrator receives merged findings from all agents
- [ ] Git History agent skips when overlap is empty
- [ ] Each agent uses its assigned model

### Anti-patterns
- Do NOT use a single model for all agents — the whole point is specialization
- Do NOT let agents read files or call tools — they only consume pre-assembled state
- Do NOT crash on provider failure — log warning, return empty findings for that agent
- DO surface auth errors clearly (missing/invalid API key) — those are config bugs, not transient failures

---

## Phase 5: Rule Files & Prompt Engineering

**Goal**: Create markdown rule files with YAML frontmatter for each agent.

### Tasks
1. Create `rules/default/syntax.md` — formatting/style rules
2. Create `rules/default/logic.md` — semi-formal reasoning template
3. Create `rules/default/security.md` — OWASP Top 10 focus, triage instructions
4. Create `rules/default/git_history.md` — regression/pattern detection
5. Create `rules/loader.py`:
   - Parse YAML frontmatter (`agent`, `trigger`, `globs`, `severity_default`)
   - Return markdown body as system prompt
   - Support glob-based activation

### Verification
- [ ] Loader parses all 4 default rule files
- [ ] Frontmatter fields are correctly extracted
- [ ] Rule body is passed as system prompt to correct agent

### Anti-patterns
- Do NOT use `yaml.load()` — use `yaml.safe_load()`
- Keep rules under 150 lines each

---

## Phase 6: Noise Filter & Output

**Goal**: Post-processing deduplication and output adapters.

### Tasks
1. Create `noise_filter.py`:
   - Deduplicate findings (same file + line range + category)
   - Merge overlapping line ranges
   - Filter below severity threshold (from config)
   - Sort by severity → file → line
2. Create `output/base.py` — `OutputAdapter` ABC:
   - `emit_finding(finding)`, `emit_summary(result)`, `emit_progress(agent, status)`
3. Create `output/terminal.py` — Rich-based CLI output:
   - Color-coded severity, file:line references, grouped by file
4. Create `output/github.py` — GitHub PR review adapter:
   - Batch findings into `POST /repos/{owner}/{repo}/pulls/{pr}/reviews`
   - Map findings to inline comments with file + line

### Verification
- [ ] Duplicate findings are merged
- [ ] Severity filter removes low-priority items
- [ ] Terminal output renders correctly
- [ ] GitHub adapter produces valid API payload

---

## Phase 7: CLI Entry Point

**Goal**: Typer-based CLI that ties everything together.

### Tasks
1. Create `cli/main.py`:
   - `code-review review --path <dir> [--severity medium] [--format terminal|json]`
   - `code-review review --repo <path> --sha <commit>` (git mode)
   - Wire up: Tier 1 → Tier 2 → Tier 3 → Noise Filter → Output
2. Add `[project.scripts]` to `pyproject.toml`:
   - `code-review = "cli.main:app"`
3. Add progress indicators via Rich

### Verification
- [ ] `code-review review --path ./test-fixtures` produces output
- [ ] `--severity` flag filters correctly
- [ ] `--format json` outputs valid JSON
- [ ] Exit code reflects findings (0 = clean, 1 = findings)

---

## Phase 8: Testing & Integration

**Goal**: Test suite covering all tiers.

### Tasks
1. Create test fixtures: small Python/JS files with known issues
2. Unit tests for each tool runner (mock subprocess)
3. Unit tests for context assembly (verify single-read)
4. Unit tests for each agent (mock LLM responses)
5. Integration test: full pipeline with mock LLMs
6. Integration test: full pipeline with real NIM endpoints (marked slow)

### Verification
- [ ] `pytest` passes all tests
- [ ] Coverage on core modules > 80%
- [ ] Real NIM integration test works with valid API key

---

## Phase 9: GitHub Action & Web App (Future)

**Goal**: Additional deployment targets (deferred until CLI is solid).

### Tasks (GitHub Action)
1. Create `github-action/action.yml`
2. Create `github-action/Dockerfile`
3. Create `github-action/entrypoint.py` — reads PR diff, runs engine, posts review

### Tasks (Web App)
1. Create `web/app.py` — FastAPI + SSE
2. Create `web/frontend/` — simple HTML/JS that renders SSE stream
3. Implement streaming: each agent's findings arrive as SSE events

---

## Dependency Summary

```
langgraph>=0.2.0
langchain-core>=0.3.0
openai>=1.0
httpx>=0.27
pydantic>=2.0
pydantic-settings>=2.0
gitpython>=3.1
typer>=0.9
rich>=13.0
pyyaml>=6.0
# Dev
pytest>=8.0
pytest-asyncio>=0.23
ruff>=0.5
```

## Execution Order

Phases 1-3 are foundation (no AI needed, can test independently).
Phase 4 is the core AI layer.
Phase 5 controls agent behavior.
Phases 6-7 make it usable.
Phase 8 validates everything.
Phase 9 is future work.
