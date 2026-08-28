# Maintaining the docgen engine

For whoever develops the documentation engine itself. If you only want to *use*
docgen on a solution, read [`using-docgen.md`](using-docgen.md) instead.

The design reasoning behind how the engine is packaged and delivered is in
[`docgen-distribution-design.md`](docgen-distribution-design.md); the reasoning
behind the shape of its output is in
[`agent-knowledge-base-design.md`](agent-knowledge-base-design.md).

---

## 1. What you need locally

Two things: this repository, and **any Power BI solution repository to test
against**. The engine contains no Power BI source of its own, so you cannot
exercise a change without pointing it at a real solution.

```
C:\dev\pbi-docgen\      this repo — you edit here
C:\dev\my-solution\     any repo with a Power BI solution in it
```

Use whichever solution you have access to; the paths above are only an example.
A large, messy, real model is far more useful than a small tidy one, because
that is where renderer and parser bugs actually surface.

The package sits at `pbi-docgen\scripts\docgen\`, mirroring where consumers
vendor it. That keeps git at the workspace root (so VS Code behaves normally)
**and** makes the run command identical to a consumer's. The outer folder name
is irrelevant — Python only ever sees `scripts.docgen`.

---

## 2. The development loop

The engine does not have to live inside the solution it documents. Point it at
one with `--repo-root`:

```powershell
Set-Location C:\dev\pbi-docgen
# edit scripts\docgen\renderers_reports.py
python -m scripts.docgen.doctor   --repo-root C:\dev\my-solution
python -m scripts.docgen.generate --repo-root C:\dev\my-solution
python -m scripts.docgen.validate --repo-root C:\dev\my-solution
```

Nothing is copied. Code here writes documentation into the solution repo. This
is how you test a change *before* releasing it — the alternative would be
editing a consumer's vendored copy, which the next `subtree pull` would discard.

Two things to know:

- **This modifies the solution repo.** You are regenerating its documentation
  with unreleased code. Run `git checkout model-docs model-docs-txt` there to
  discard, or leave the changes until you release.
- **Running the engine against this repo will report `BLOCKER`.** That is
  correct — there is no Power BI solution here. Always pass `--repo-root`.

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
Set-Location C:\dev\pbi-docgen

# 1. Bump the version
#    scripts\docgen\__init__.py  ->  __version__ = "1.1.0"
git commit -am "Release 1.1.0"
git push

# 2. Publish the flattened payload consumers subtree-pull from.
#    Delete any local `dist` first: `subtree split` refuses to overwrite a
#    branch that already exists, so this fails on every release after the first.
#    Harmless if it reports "branch 'dist' not found" — you have not split here yet.
git branch -D dist
git subtree split --prefix=scripts/docgen -b dist
git push origin dist

# 3. Tag it
git tag v1.1.0
git push origin v1.1.0

# 4. Prove the release by installing it the way a consumer does
Set-Location C:\dev\my-solution
git subtree pull --prefix scripts/docgen https://github.com/alord-mns/pbi-docgen dist --squash
python -m scripts.docgen.generate
python -m scripts.docgen.validate
```

**Forgetting step 2 is the classic failure** — `main` moves, `dist` doesn't, and
every consumer silently stays on the old engine with no error to tell them.
Step 4 is what catches it: by installing your own release through the same
mechanism everyone else uses, you find a broken release before they do.

`git subtree split` is deterministic, so re-deriving `dist` reproduces the
previous commits identically and appends the new ones. A plain `git push` is
therefore a fast-forward and needs no `--force`. If git rejects the push,
something genuinely unexpected has happened — someone else published to `dist`,
or `main`'s history was rewritten. Investigate rather than forcing; if you are
certain, use `--force-with-lease`, never a bare `--force`.

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

- **Copilot prompts and chat instructions.** Wrappers around `generate` /
  `validate` are specific to one AI tool, duplicate what `doctor` already does
  deterministically, and drift out of date. A consumer who wants them can keep
  their own.
- **Anything that writes to Power BI source.** Backfilling TMDL descriptions,
  for example, improves documentation quality a great deal — but docgen is
  strictly read-only over source, so tooling that edits it belongs elsewhere.
  Running it before generating is a sensible workflow; it is a separate one.
- **Health-check and repair engines** — see Decision 12 in the distribution
  design for why these ship separately.

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
