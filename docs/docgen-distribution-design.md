# docgen distribution design — decisions and rationale

Why the documentation engine is packaged and delivered the way it is. Companion
to [`agent-knowledge-base-design.md`](agent-knowledge-base-design.md), which
covers the design of the *output*; this file covers the design of the *product*.

Written because these choices were shaped by constraints that are invisible in
the code. Each decision records the alternatives that were rejected, so they do
not get re-litigated from scratch later.

Practical instructions live elsewhere: [`using-docgen.md`](using-docgen.md) for
consumers, [`maintaining-docgen.md`](maintaining-docgen.md) for maintainers.

---

## The constraints that shaped everything

Three facts about the environment drove most of what follows:

1. **The organisation mandates its own default template for new repositories**,
   and we have no admin rights over org-level GitHub settings.
2. **PowerShell script execution is locked down** on developer machines.
3. **This repo contains confidential business content** — dataflow exports, a
   2,100-measure model, SQL view definitions, SharePoint URLs, workspace GUIDs.

Any distribution mechanism requiring admin rights, shell scripts, or "clone my
solution repo and delete the parts you don't need" was out before we started.

---

## Decision 1 — the engine becomes its own repository

The engine was built inside `fhb-weekly` and is model-agnostic by construction:
no solution-specific logic, all naming in `model-docs/.docgen.toml`. But it was
only *obtainable* by cloning a solution repo.

That fails constraint 3. Handing a colleague this repo to extract `scripts/docgen/`
also hands them every dataflow and SQL view in it. A separate `pbi-docgen` repo
is therefore a confidentiality requirement, not a tidiness preference.

## Decision 2 — not a GitHub template repository

Template repos look like the obvious answer: "Use this template" creates a new
repo under the consumer's own name with no shared history. Rejected for two
reasons, the second more fundamental than the first:

- We cannot publish one (constraint 1).
- **Templating happens at repo-creation time, and consumer repos already exist.**
  Because the org mandates its own template, every consumer already has a repo
  before docgen enters the picture. The need is *"add the engine to an existing
  repo"* — a vendoring problem, not a repo-creation problem.

Recognising this reframed the whole question and made the admin-rights obstacle
irrelevant.

## Decision 3 — git subtree is the delivery mechanism

Consumers install with one command and update with one command:

```
git subtree add  --prefix scripts/docgen <url> dist --squash
git subtree pull --prefix scripts/docgen <url> dist --squash
```

Chosen because git is universally available, needs no admin rights, works into
repos that already exist, and is the only candidate with a genuine update path.

| Rejected | Why |
|---|---|
| **ZIP download** | No update path — you re-download and hand-merge, and consumers can't tell what version they're on. **Retained as the fallback** for anyone without git access: a ZIP has no `.git`, so it also sidesteps every git-identity problem. |
| **Bootstrap script** | Originally proposed as PowerShell, which constraint 2 kills. A Python rewrite would work but becomes redundant once subtree exists, and is another thing to maintain. |
| **Git submodule** | Consumers must understand detached HEADs and `--recurse-submodules`; a forgotten init produces an empty directory and a confusing failure. |
| **pip / internal feed** | Cleanest updates, and the likely end-state if this ever goes org-wide. Rejected *now* because it needs feed publishing rights and breaks the "no install, no virtualenv, just run it" property that makes adoption easy. |

## Decision 4 — always `--squash`

Without it, subtree grafts the engine's entire commit history into the consumer's
repo, so their `git log` fills with commits about a tool they only consume. With
it, each update is a single commit.

## Decision 5 — mirror layout on `main`, flattened `dist` branch for consumers

`git subtree` maps the **source repo's root** onto the target prefix. That forces
an awkward choice:

- *Package at the repo root* — subtree is clean, but the maintainer's clone has
  git two levels below the folder they open, so VS Code doesn't see the repo at
  the workspace root.
- *Package at `scripts/docgen/`* — clone is clean, but `subtree add` would
  produce `scripts/docgen/scripts/docgen/`.

Resolved by decoupling the development layout from the delivery payload. `main`
mirrors the consumer layout; each release publishes a flattened branch:

```
git subtree split --prefix=scripts/docgen -b dist
git push origin dist --force
```

Cost is one extra command per release. The risk is forgetting it — `main` moves,
`dist` doesn't, and consumers silently stay on the old engine — which is why the
release checklist ends by updating this repo through the same subtree pull a
consumer would use.

> **A superseded constraint, recorded so it isn't reintroduced.** An earlier
> version of this design put the package at the repo root and required the
> maintainer's clone to be named exactly `docgen`, because the clone folder
> became the Python module name and `pbi-docgen` contains a hyphen, which is an
> illegal identifier. Decision 5 removes that entirely — the outer folder name
> is now irrelevant, because Python only ever sees `scripts.docgen`.

## Decision 6 — `scripts/docgen/` is the standard vendor path

Nothing in the code requires it (see Decision 8). It is a convention so that
every instruction, in every guide, reads `python -m scripts.docgen.generate`.

Without a convention the module path varies per consumer — someone vendoring to
`tools/docgen/` types `python -m tools.docgen.generate` — and no shared
documentation can be copy-pasted.

## Decision 7 — the output contract ships inside the package

`documentation_req.md` lives in `scripts/docgen/`, not in `model-docs/`, because
only `scripts/docgen/**` travels through the `dist` branch. Keeping it inside is
the only way it reaches consumers via subtree — and it means the contract and
the engine that satisfies it are always the same version and can never drift.
`init` copies it into `model-docs/` on first run.

The general rule: **anything needed before installing lives outside the package;
anything needed after installing ships inside it.**

## Decision 8 — the repo root is discovered, not assumed

The engine previously computed `REPO_ROOT = Path(__file__).resolve().parents[2]`,
duplicated in two modules. That hard-codes the package sitting *exactly* two
levels below the repo root. Vendored anywhere else it silently resolved to the
wrong directory — globs matched nothing, and the error surfaced as a confusing
config complaint rather than a path problem.

Replaced with an ordered search: `--repo-root` → `DOCGEN_REPO_ROOT` → walk up for
`model-docs/.docgen.toml` → walk up for `.git` → the old fallback.

**The config marker deliberately outranks `.git`**, so an engine clone nested
inside a solution resolves to the *solution* root instead of to itself. `doctor`
reports both the resolved root and the rule that produced it, so a mis-vendored
install is diagnosable instead of mysterious.

## Decision 9 — the `--repo-root` flag

Lets the engine run against a solution it does not live inside. This is what
makes the maintainer's development loop possible without copying files back and
forth, and it is the prerequisite for any future pip packaging, where the engine
would not be in the repo at all.

## Decision 10 — the generation stamp is ignored when detecting changes

Every generated file carries a `_Last generated: <date>_` banner, and the
overview carries a `**Documentation regenerated:** <date>` line. `md.write()`
was meant to ignore the banner when deciding whether content changed, but the
regex anchored at line start while the banner renders inside a blockquote
(`> _Last generated: ..._`), so it never matched. Every file was rewritten
whenever the date rolled over.

Harmless when run by hand; fatal for Decision 14, where it would mean a commit
touching every file every day with no substantive change.

**Accepted trade-off:** both stamps are now ignored for comparison, so a file
keeps the date of its last *content* change. The banner effectively means "last
changed" rather than "last generated", and different files legitimately show
different dates. `generation-log.md` retains the true per-run audit trail.

## Decision 11 — fhb-weekly becomes consumer #1

Rather than keeping a privileged copy, this repo vendors the engine by subtree
like everyone else. Its docs are regenerated through the same command a consumer
runs, so the distribution path is exercised on every release instead of rotting
untested.

## Decision 12 — `pbi_health` and `pbi_repair` are not bundled

They complement docgen but ship separately, or not yet at all.

- **Verified**: they import nothing from `docgen`. They carry their own,
  deliberately lighter TMDL/PBIR parsers because they need different things from
  the same files. Separating them therefore costs nothing today, and the
  "extract a shared `pbi-core` library" argument is weak when the duplication is
  intentional.
- **Maturity** — bundling a finished tool with unfinished ones forces the stable
  one to ride their churn.
- **Risk profile** — docgen is strictly read-only; `pbi_repair` *modifies* PBIR
  files. Bundling a mutating tool with a read-only one raises the review bar for
  the whole package and could deter teams from adopting the safe part.

Revisit when they are finished. If parser duplication turns out to hurt in
practice, one `pbi-tooling` repo containing three independently-runnable
packages beats three repos plus a shared library.

## Decision 13 — no synthetic test fixture

A minimal fake PBIP committed to the engine repo was considered, to let the
engine be developed without a real solution. Rejected: engine changes are driven
by significant issues found on real models, which a toy fixture will not
surface, and it would need maintaining forever.

The existing harness is stronger anyway — 11 quality gates, the idempotency
check, and `git diff` against a 2,100-measure production model.

## Decision 14 — regenerate in CI from the vendored engine

The goal is for docs to refresh automatically whenever the solution changes.
This reinforces vendoring: with the engine committed, the workflow is simply
`python -m scripts.docgen.generate` — no cross-repo checkout and no token.

Checking out `pbi-docgen` at workflow runtime was rejected: consumers would
silently track `main`, so an engine change could rewrite every document without
anyone choosing to upgrade. Pinning would be mandatory, at which point vendoring
is simpler and pinned by construction.

Shape: trigger on pushes touching `src/**`, `dataflows/**`, `sql/**`,
`orchestration/**`, `power-apps/**` and `model-docs/.docgen.toml`; run `generate`
then `validate`; fail the build if any gate fails; commit the regenerated docs
back. The path filter also prevents the docs commit from re-triggering the
workflow. Decision 10 is a hard prerequisite.

## Decision 15 — the agent system prompt is generated, not templated

A knowledge base alone does not make a working agent. The other half is the
system prompt describing how the cards are shaped and how to route a question —
which is a product of *this engine's output format*, not of any solution. The
original was hand-written and carried one solution's name.

It is now generated, because generation buys three things a static template
cannot:

- **Presence-awareness** — it describes only the card types and files this run
  actually produced, instead of listing everything and hedging with "when
  attached".
- **Real worked examples** — the concept and metric names in the examples are
  taken from actual cards in that model, so a user following an example
  literally gets a hit. A config-supplied name carries no such guarantee.
- **No drift** — the hand-written original had to be manually edited when the
  Power App card type shipped. That class of mistake is now impossible.

It is emitted to `model-docs/agent-instructions.md` but deliberately kept *out*
of `kb_outputs`, so it gets no plain-text twin and is never mistaken for a card
file. It must not be uploaded with the knowledge base or it pollutes retrieval.

Because it is regenerated every run, consumers adapt it **where they deploy it**
— in the agent's configuration — leaving the repo copy canonical.

## Decision 16 — `init` detects before it scaffolds

Most consumers already have their solution laid out before they hear about
docgen, so imposing this engine's folder names would be wrong. `init` searches
for the PBIP markers `*.SemanticModel` / `*.Report` first — reliable because
those are format conventions, not naming choices — and writes a `[paths]` block
pointing at whatever it finds. Folders are only scaffolded for a genuinely empty
repo, which also avoids littering a real repo with empty directories that git
would not track anyway.

`init` is non-destructive and re-runnable, per the repo convention that tools are
add-only unless destructive behaviour is explicitly opted into. It will never
overwrite an existing `.docgen.toml`: that file can represent hours of curation
and is the one thing in the pipeline the engine cannot regenerate.

## Decision 17 — the input layout is configurable; the output layout is not

All seven input artefact types resolve through `[paths]` globs, so the engine
adapts to any repo layout without code changes. `model-docs/` is the single
fixed convention, for two reasons rather than inertia:

1. **Chicken-and-egg.** `model-docs/.docgen.toml` is the marker the repo-root
   search looks for. A configurable config path would mean reading the config to
   discover where the config lives.
2. **Safety.** The sweep *deletes* every non-protected file under the output
   directories. If that path were user-supplied, a typo (`docs` for
   `model-docs`) would silently destroy hand-written documentation.

One input constraint is not a path at all and cannot be configured away: **SQL
export filenames must match the view names they define**, because that filename
is the join key for the two-hop source trace.
