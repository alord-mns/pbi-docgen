# Briefing: automating docgen with a GitHub Action

## How to use this document

This is a **self-contained briefing** for an AI assistant (e.g. a Microsoft 365
Copilot custom agent) that has **no prior context** about this project. It
captures the implementation decisions and constraints needed to advise on
wrapping the `docgen` documentation engine in a GitHub Action so that a Power BI
solution's documentation regenerates automatically.

It deliberately restates background that a reader with no history would lack. If
the agent also has the repository's own docs (`using-docgen.md`,
`maintaining-docgen.md`, `docgen-distribution-design.md`), treat those as the
authority on the engine itself; treat this document as the authority on the
GitHub Action.

---

## 1. What docgen is (one paragraph)

`docgen` is a deterministic, standard-library-only Python 3.11+ engine that reads
a Power BI solution's source files — TMDL semantic model, PBIR reports, exported
dataflow JSON, SQL views, orchestration workflow JSON, unpacked canvas Power
Apps, and an exported Power BI App metadata JSON — and generates a Markdown
"card" knowledge base under `model-docs/`, plus a plain-text mirror under
`model-docs-txt/` and a generated agent system prompt (`agent-instructions.md`).
There is **no AI at runtime**: identical inputs produce byte-identical output.
The engine is strictly read-only over the source; it only writes under
`model-docs/` and `model-docs-txt/`.

The three commands that matter:

- `python -m scripts.docgen.doctor` — read-only preflight; reports readiness.
- `python -m scripts.docgen.generate` — regenerate the documentation.
- `python -m scripts.docgen.validate` — run quality gates; non-zero exit on failure.

## 2. How the engine reaches each repo (this shapes the Action)

The engine lives in its own repository (`pbi-docgen`) and is **vendored** into
each solution repository with `git subtree`, landing at `scripts/docgen/`. It is
committed *into* the solution repo, not fetched at build time.

**Consequence for CI, and it is the single most important design fact:** because
the engine is already in the repo, the workflow needs **no cross-repository
checkout and no extra token**. It is just "check out this repo, run Python."
Fetching `pbi-docgen` at runtime was considered and rejected — it would require
cross-repo authentication (a PAT or GitHub App for a private engine repo) and
would let an engine change silently rewrite documentation across every solution
with nobody choosing to upgrade. Vendored-and-pinned is simpler and safer.

## 3. The goal

Regenerate a solution's documentation automatically whenever its source changes,
so `model-docs/` never drifts from the model. The documentation is deterministic,
which makes this safe: a diff means the source genuinely changed.

## 4. The key decision: "auto-commit" vs "check" model

There are two ways to run this, and the user must pick one. This is the most
important choice; present both.

### Option A — Check model (recommended default)

CI regenerates and **fails the build if the result differs from what is
committed**. It never commits anything itself. Developers run `generate` locally
and commit `model-docs/` as part of their change.

```
generate  ->  git diff --exit-code model-docs model-docs-txt  ->  fail if dirty
```

- **Pros:** no bot commits; no loop risk at all; the documentation diff appears
  in the pull request for human review; simplest permissions (read-only).
- **Cons:** developers must remember to run `generate` before pushing. The build
  tells them clearly when they forgot.

### Option B — Auto-commit model

CI regenerates and **commits the result back** to the branch. Developers never
touch `model-docs/`.

```
generate  ->  validate  ->  commit model-docs + model-docs-txt back
```

- **Pros:** fully automatic; developers cannot forget.
- **Cons:** the repo accumulates bot commits; needs write permission; needs loop
  prevention (see §6); the review benefit is lost because docs change after the
  PR is merged.

**Recommendation:** start with the **check model**. It is less machinery, keeps
the doc diff reviewable, and the determinism guarantee makes the
"is it up to date?" check reliable. Move to auto-commit only if contributors
routinely forget to regenerate.

## 5. Trigger: only fire on real source changes

Trigger on push (and optionally pull_request) to the main branch, filtered to the
**source paths the engine actually reads**, and **excluding the output folders**.

The paths depend on the solution's `[paths]` config in `model-docs/.docgen.toml`.
The engine's default layout is:

```yaml
on:
  push:
    branches: [main]
    paths:
      - 'pbi/**'                 # TMDL model + PBIR reports + Power BI App export
      - 'dataflows/**'
      - 'sql/**'
      - 'orchestration/**'
      - 'power-apps/**'
      - 'model-docs/.docgen.toml'
```

Some repos use a different layout (for example a `src/` folder instead of `pbi/`).
**The trigger paths must match that repo's actual `[paths]` values**, not the
defaults. Always inspect `model-docs/.docgen.toml` first.

Critically, **do not** put `model-docs/**` or `model-docs-txt/**` in the trigger
paths — those are outputs, and triggering on them would cause a loop in the
auto-commit model.

## 6. Loop prevention (auto-commit model only)

Two independent safeguards, use both:

1. **Path filter** (above) excludes the output folders, so the bot's own commit —
   which only touches `model-docs/` and `model-docs-txt/` — does not match the
   trigger.
2. **The default `GITHUB_TOKEN` does not trigger further workflows.** Commits
   pushed using the built-in token are deliberately prevented by GitHub from
   starting new workflow runs. (Note: this protection is **lost** if you commit
   using a personal access token or GitHub App token — another reason to prefer
   the built-in token, and to keep the path filter as belt-and-braces.)

## 7. Permissions, secrets, runner

- **Permissions:** check model needs only `contents: read`. Auto-commit needs
  `permissions: contents: write`.
- **Secrets:** none. The engine is offline — no Power BI service access, no
  credentials, no network. This is a genuine simplification versus most CI.
- **Runner:** `ubuntu-latest`. The engine is pure standard-library Python and is
  OS-independent; Linux is cheapest.
- **Python:** 3.11 or newer (the engine uses `tomllib`, standard from 3.11). Use
  `actions/setup-python` with `python-version: '3.11'`. No `pip install` step —
  there are no third-party dependencies.
- **Concurrency:** add a `concurrency` group keyed on the branch so rapid pushes
  don't run overlapping jobs.

## 8. Why the diffs are trustworthy (do not skip this)

The engine is deterministic, and one specific fix makes CI viable: the daily
"last generated" date stamp in each file is **ignored when deciding whether a
file changed**. Without that, every run on a new calendar day would rewrite every
file and, in the auto-commit model, produce a daily no-change commit. With it, a
changed file means the *content* changed. Ensure the vendored engine is recent
enough to include this behaviour (it has been present since the engine became
location-independent; `doctor` prints the engine version).

## 9. `generation-log.md`

`model-docs/generation-log.md` is an append-only audit trail; the engine adds one
entry per `generate` run, **including no-op runs**. It lives under `model-docs/`,
so:

- In the **check model** it is one of the files compared — a no-op run still
  appends a line, so the "is it up to date?" check should either ignore this file
  or accept that running `generate` always touches it. The simplest robust check
  is to compare only the card files, or to run `generate` and then check whether
  anything *other than* `generation-log.md` changed.
- In the **auto-commit model** it means a committed line per run. Acceptable as
  an audit trail, but be aware of it.

This is the one wrinkle worth deciding explicitly.

## 10. Rolling the workflow out to many repos

The workflow file must live inside each solution repo (`.github/workflows/`).
Options, in order of preference:

1. **Have the engine's `init` scaffold it.** `python -m scripts.docgen.init`
   already scaffolds folders and config; it can also drop in a ready workflow
   file. Every new consumer then gets a correct workflow with nothing to copy.
2. **Reusable workflow.** The real logic lives once in the engine repo's
   `.github/workflows/` and each consumer has a tiny caller pinned to a tag.
   Caveat: calling a reusable workflow **from a private repo in another repo**
   can require an organisation setting that the team may not control — verify
   access before relying on this.
3. **Manual copy.** Fine for a handful of repos; drifts at scale.

## 11. Explicitly out of scope for the Action

- **Publishing the knowledge base to Microsoft 365 / SharePoint** for the Q&A
  agent is a separate downstream step, not part of regeneration. It can be a
  later workflow stage, but keep it decoupled.
- **The Power BI App metadata export** (the JSON the engine reads to document the
  distribution App) is produced out of band by a separate Power Automate flow or
  REST call and committed to the repo. The Action documents it once present; it
  does not fetch it.

## 12. Prerequisites checklist

Before wiring up the Action, the repo must already have:

- [ ] The engine vendored at `scripts/docgen/` (via `git subtree add`).
- [ ] A populated `model-docs/.docgen.toml` (run `python -m scripts.docgen.init`
      once, then fill it in).
- [ ] A green local run of `generate` then `validate`.
- [ ] The source laid out at the paths declared in `[paths]`.

## 13. Decisions to put to the user

1. **Check model or auto-commit model?** (§4 — recommend check.)
2. **What are this repo's actual source paths?** (Read `[paths]`; the trigger must
   match — §5.)
3. **How is `generation-log.md` handled in the up-to-date check?** (§9.)
4. **Also validate on pull requests**, so a PR fails before merge if docs are
   stale or gates break? (Recommended: yes, a read-only validate job on PRs.)
5. **Roll-out mechanism** across repos? (§10.)

## 14. Reference: a concrete check-model workflow

Provided as a starting point; adapt the trigger paths to the repo's `[paths]`.

```yaml
name: docs

on:
  push:
    branches: [main]
    paths:
      - 'pbi/**'
      - 'dataflows/**'
      - 'sql/**'
      - 'orchestration/**'
      - 'power-apps/**'
      - 'model-docs/.docgen.toml'
  pull_request:
    paths:
      - 'pbi/**'
      - 'dataflows/**'
      - 'sql/**'
      - 'orchestration/**'
      - 'power-apps/**'
      - 'model-docs/.docgen.toml'

permissions:
  contents: read

concurrency:
  group: docs-${{ github.ref }}
  cancel-in-progress: true

jobs:
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Regenerate documentation
        run: python -m scripts.docgen.generate
      - name: Fail if documentation is out of date
        run: |
          # Ignore the append-only run log; compare only real output.
          git checkout -- model-docs/generation-log.md || true
          if ! git diff --quiet -- model-docs model-docs-txt; then
            echo "::error::model-docs is out of date. Run 'python -m scripts.docgen.generate' and commit the result."
            git --no-pager diff --stat -- model-docs model-docs-txt
            exit 1
          fi
      - name: Validate quality gates
        run: python -m scripts.docgen.validate
```

For the **auto-commit** variant, change `permissions` to `contents: write`,
remove the "fail if out of date" step, and after `validate` add a step that
commits `model-docs` and `model-docs-txt` back using the built-in token and a
bot identity — only when there is something to commit.
