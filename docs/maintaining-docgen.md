# Maintaining the docgen engine

For whoever develops the documentation engine itself. If you only want to *use*
docgen on a solution, read [`using-docgen.md`](using-docgen.md) instead.

> **Status.** The engine currently lives inside this repo at
> [`scripts/docgen/`](../scripts/docgen/). It is destined to move to a standalone
> `pbi-docgen` repository — see [Split-day runbook](#split-day-runbook). This
> guide is written for the post-split world and moves with the engine unchanged;
> the design reasoning behind it is in
> [`docgen-distribution-design.md`](docgen-distribution-design.md).

---

## 1. Clone layout

```
c:\dev\pbi-docgen\          engine repo — you edit here (git at the root)
c:\dev\pbi-dev\fhb-weekly\  a real solution — your integration test bed
```

The engine repo mirrors the consumer layout, so the package sits at
`pbi-docgen\scripts\docgen\`. That means git is at the workspace root (VS Code
behaves normally) **and** the run command is byte-identical to a consumer's.

The outer folder name is irrelevant — Python only ever sees `scripts.docgen`.

---

## 2. The development loop

The engine no longer has to live inside the solution it documents. Point it at
one with `--repo-root`:

```powershell
Set-Location c:\dev\pbi-docgen
# edit scripts\docgen\renderers_reports.py
python -m scripts.docgen.doctor   --repo-root c:\dev\pbi-dev\fhb-weekly
python -m scripts.docgen.generate --repo-root c:\dev\pbi-dev\fhb-weekly
python -m scripts.docgen.validate --repo-root c:\dev\pbi-dev\fhb-weekly
```

Nothing is copied. Code in `pbi-docgen` writes docs into `fhb-weekly`.

Two things to know:

- **This dirties the solution repo.** You are regenerating its docs with
  unreleased code. Run `git checkout model-docs model-docs-txt` in fhb-weekly to
  discard, or leave them until you release.
- **Running the engine against its own repo will report `BLOCKER`.** That is
  correct — the engine repo contains no solution. Always pass `--repo-root`.

`--repo-root` beats the `DOCGEN_REPO_ROOT` environment variable, which in turn
beats automatic detection. See [§6](#6-how-the-repo-root-is-found).

### Definition of done for an engine change

1. `generate` twice — the second run must report `0 changed`. Perpetual churn is
   a bug, not a quirk.
2. `validate` — all gates PASS.
3. `git diff` in the solution repo shows only what you intended.

---

## 3. Cutting a release

Consumers vendor the engine with `git subtree`, which pulls from a **flattened
`dist` branch** containing the package contents at its root. That branch does
not update itself — publishing it is the release.

```powershell
Set-Location c:\dev\pbi-docgen

# 1. Bump the version
#    scripts\docgen\__init__.py  ->  __version__ = "1.1.0"
git commit -am "Release 1.1.0"
git push

# 2. Publish the flattened payload consumers subtree-pull from
git subtree split --prefix=scripts/docgen -b dist
git push origin dist --force

# 3. Tag it
git tag v1.1.0
git push origin v1.1.0

# 4. Dogfood: update this repo's own vendored copy the way a consumer would
Set-Location c:\dev\pbi-dev\fhb-weekly
git subtree pull --prefix scripts/docgen https://github.com/<org>/pbi-docgen dist --squash
python -m scripts.docgen.generate
python -m scripts.docgen.validate
```

**Forgetting step 2 is the classic failure** — `main` moves, `dist` doesn't, and
every consumer silently stays on the old engine with no error to tell them.
Step 4 catches it, which is exactly why fhb-weekly consumes the engine through
the same mechanism as everyone else.

`git subtree split` re-derives the branch from history, so `--force` on the push
is expected and safe.

---

## 4. What ships to consumers

Only `scripts/docgen/**` travels through the `dist` branch.

| Path | Ships? | Why |
|---|---|---|
| `scripts/docgen/*.py` | Yes | The engine |
| `scripts/docgen/README.md` | Yes | Internals, useful once installed |
| `scripts/docgen/documentation_req.md` | Yes | The output contract — ships *with* the engine so the two can never drift |
| `README.md` (repo root) | No | Front door; read on the repo page |
| `docs/using-docgen.md` | No | Install guide — you read it *before* you have the code |
| `docs/maintaining-docgen.md` | No | This file |
| `docs/docgen-distribution-design.md` | No | Distribution rationale |
| `docs/agent-knowledge-base-design.md` | No | Output-format rationale |

The rule: anything needed *before* installing lives outside the package;
anything needed *after* installing ships inside it.

### Deliberately not part of the engine

- **`generateDocumentation` prompt** — retired. It wrapped two commands, assumed
  GitHub Copilot specifically, and `doctor` now covers the preflight
  deterministically. It had already drifted out of date.
- **`addMeasureDescriptions` prompt** — belongs with the health / authoring
  toolchain, not here: it *writes* to TMDL, and docgen is strictly read-only
  over source. Running it before first generating docs is a sensible workflow,
  but it is a separate one.
- **`pbi_health` / `pbi_repair`** — see Decision 12 in the distribution design.

---

## 5. Versioning

`__version__` in [`scripts/docgen/__init__.py`](../scripts/docgen/__init__.py)
is the single source of truth, reported by `doctor` so any consumer can answer
"which engine is this repo on?".

- **Patch** — bug fix, output unchanged or strictly corrected.
- **Minor** — new cards / sections / config keys, backwards compatible.
- **Major** — output contract changes, or a config key is removed or renamed.

Because `documentation_req.md` ships inside the package, the contract and the
engine that satisfies it are always the same version. Update both together.

---

## 6. How the repo root is found

`md.find_repo_root()` resolves in strict priority order:

1. the `--repo-root` argument
2. the `DOCGEN_REPO_ROOT` environment variable
3. walk upward for `model-docs/.docgen.toml`
4. walk upward for `.git`
5. fall back to the package's grandparent directory

The config marker deliberately outranks `.git` so that an engine clone nested
*inside* a solution resolves to the solution root rather than to itself.
`doctor` prints both the resolved root and which rule produced it — check that
first whenever paths look wrong.

Because of this, the engine works at any depth. `scripts/docgen/` is the
documented convention purely so every instruction reads
`python -m scripts.docgen.generate`, not because the code requires it.

---

## Split-day runbook

One-time migration of the engine out of this repo. Preserves engine history.

```powershell
# 1. Extract the engine's own history into a branch
Set-Location c:\dev\pbi-dev\fhb-weekly
git subtree split --prefix=scripts/docgen -b docgen-only

# 2. Create an empty `pbi-docgen` repo in the org, then seed it.
#    The split branch has the package at its ROOT, which is exactly the `dist`
#    shape, so push it to `dist`. `main` gets the mirror layout added on top.
git push https://github.com/<org>/pbi-docgen docgen-only:dist

# 3. Clone it and build out the mirror layout on `main`:
#      scripts/docgen/**            (the package)
#      docs/using-docgen.md         (the consumer guide)
#      docs/maintaining-docgen.md   (this file)
#      docs/docgen-distribution-design.md
#      docs/agent-knowledge-base-design.md
#      README.md                    (new — see below)
#      .github/instructions/docgen.instructions.md

# 4. Re-attach this repo as a subtree consumer.
#    `subtree add` REFUSES if the prefix already exists, so remove it first.
Set-Location c:\dev\pbi-dev\fhb-weekly
git rm -r scripts/docgen
git commit -m "Replace vendored docgen with subtree"
git subtree add --prefix scripts/docgen https://github.com/<org>/pbi-docgen dist --squash

# 5. Prove nothing moved
python -m scripts.docgen.generate
python -m scripts.docgen.validate
```

### The root `README.md` to write at step 3

It cannot be drafted here, because this repo's root README belongs to the
solution. It should carry:

- One line on what docgen is and what it produces.
- A quickstart: the `git subtree add` command, then `doctor`, then `generate`.
- The engine's guarantees — deterministic, read-only over source, stdlib only,
  Python 3.11+.
- Links to `docs/using-docgen.md` (consumers), `docs/maintaining-docgen.md`
  (maintainers), and `docs/docgen-distribution-design.md` (why it is like this).
