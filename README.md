# pbi-docgen

A portable, deterministic documentation engine for Power BI solutions. It reads
your PBIP source — TMDL, PBIR, dataflow JSON, SQL exports, orchestration
workflows, canvas Power Apps — and generates a card-based knowledge base
designed to be retrieved by an AI agent, plus the agent system prompt to go
with it.

**There is no generative AI in the engine.** Same source in, byte-identical
documentation out.

## What it produces

| File | Contents |
|---|---|
| `00-overview.md` | Solution summary, architecture, glossary, dependencies |
| `01-model-and-metrics.md` | One card per metric concept, measure, and table |
| `02-data-pipeline.md` | Data sources, dataflows, orchestration, refresh runbook |
| `03-reports.md` | Report and page cards with slicers, filters, and metrics |
| `04-source-code.md` | The literal SQL and Power Query behind each entity *(when present)* |
| `05-power-apps.md` | Canvas Power Apps and their write-back targets *(when present)* |
| `agent-instructions.md` | The agent system prompt, generated for **your** solution |

## Quickstart

Run these inside the repository that already holds your Power BI solution:

```powershell
# 1. Install the engine
git subtree add --prefix scripts/docgen https://github.com/alord-mns/pbi-docgen dist --squash

# 2. Scaffold config (detects your existing layout; never overwrites)
python -m scripts.docgen.init

# 3. Fill in model-docs/.docgen.toml, then check you are ready
python -m scripts.docgen.doctor

# 4. Generate and validate
python -m scripts.docgen.generate
python -m scripts.docgen.validate
```

Update later with a single command:

```powershell
git subtree pull --prefix scripts/docgen https://github.com/alord-mns/pbi-docgen dist --squash
```

## What it needs

- **Python 3.11+ and nothing else.** Standard library only, no virtualenv.
- **No Power BI service access, no credentials, no network.** The engine is
  strictly read-only over your source files.
- Your solution stored as files (PBIP format).

## Documentation

| Guide | For |
|---|---|
| [docs/using-docgen.md](docs/using-docgen.md) | **Using it** — install, configure, run, publish, troubleshoot |
| [docs/maintaining-docgen.md](docs/maintaining-docgen.md) | **Developing it** — dev loop, release process, versioning |
| [docs/docgen-distribution-design.md](docs/docgen-distribution-design.md) | Why it is packaged and delivered this way |
| [docs/agent-knowledge-base-design.md](docs/agent-knowledge-base-design.md) | Why the output is shaped this way |
| [scripts/docgen/README.md](scripts/docgen/README.md) | Engine internals |
| [scripts/docgen/documentation_req.md](scripts/docgen/documentation_req.md) | The output contract and quality gates |

## Branches

- **`main`** — development. Mirrors the consumer layout (`scripts/docgen/`).
- **`dist`** — what consumers subtree from. A flattened copy of the package,
  republished on each release. Do not develop against it.
