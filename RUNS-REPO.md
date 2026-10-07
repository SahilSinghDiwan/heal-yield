# `heal-yield-runs` — the append-only run-artifact repository

Ticket 07 named this repository and fixed one thing about it: run directories are
`runs/<date>-<repo>-<sha>/`. This file is the rest of the design.

**It does not exist yet.** Nothing here has been created on GitHub, and no run has been
committed to it. This is the layout the tool already writes locally (`heal_yield/runstore.py`
is the executable copy of the per-run half) and the policy the repository will adopt when
the first real run is executed.

## Why it is a second repository

The tool changes; a published number must not. Keeping artifacts out of `heal-yield` means a
`git log` of the tool is a history of code, a `git log` of the runs is a history of evidence,
and neither rewrites the other. It also keeps the tool's clone small: one run's `raw/` is
larger than the whole tool.

## Top level

```
heal-yield-runs/
  README.md            what this repo is, and the one command that checks it
  POLICY.md            the append-only rule and the amendment procedure (below)
  INDEX.md             one line per run: dir, repo, sha, status, headline-eligible, date
  index.json           the same, machine-readable; INDEX.md is rendered from it
  manifests/
    1.0.1.yaml         every manifest version ever run, frozen by version
  runs/
    <YYYY-MM-DD>-<repo>-<sha7>[-r<rep>]/
  .github/workflows/verify.yml   runs `heal-yield verify` over every run directory
```

`index.json` is the only file in the repository that is ever rewritten in place, and it is
rewritten only by appending an object. A run's own directory is never touched after its
commit.

## A run directory

Written by `heal_yield.runstore.RunStore`; `heal-yield verify` walks exactly this shape.

```
runs/<YYYY-MM-DD>-<repo>-<sha7>/
  metadata.yaml            run configuration and provenance
  manifest.snapshot.yaml   the exact manifest rows this run used
  modules.json             the mechanical module selection, with its audit trail
  run.json                 SOURCE OF TRUTH: candidates, rounds, dispositions, usage
  report.md                rendered from run.json, never hand-written
  SHA256SUMS               digest of every file under raw/
  raw/
    generated/<module>/round-<n>/<file>.py
    model/<module>/round-<n>/{usage.json,generator.txt,...}
    pytest/<module>/collect-round-<n>.{txt,jsonl}
    pytest/<module>/round-<n>.{txt,jsonl}
    pytest/gate/exec-<1..5>.{txt,jsonl}
    coverage/{baseline,final}.{xml,json}
```

Two rules make the directory auditable rather than merely present:

1. `run.json` names, for every candidate and every round, the raw file its outcome was read
   from. **A number with no path to a file is not publishable.**
2. `SHA256SUMS` covers everything under `raw/`, so `verify` can prove the artifacts it
   recomputed from are the artifacts the run wrote.

The `-r<rep>` suffix appears only when a run is one of the 5 repetitions behind a headline
claim. Repetitions of the same repo at the same SHA on the same date are `-r1 … -r5`.

## Append-only, concretely

- A run directory is **added in one commit and never modified again**. No fixes, no
  re-renders, no tidying.
- A run that is wrong is **superseded, not edited**: the new run is added, and a
  `superseded_by` line is appended to `index.json` for the old one. The old run's files and
  its numbers stay exactly where they are.
- `git push --force` is disallowed on `main` by branch protection. That is the whole
  enforcement mechanism, and it is the only one that is not a promise.
- Truncated runs are committed in full, with `status: truncated`, and `index.json` records
  `headline_eligible: false`. They are never deleted for being embarrassing — publishing the
  bad ones is the product.
- Fixture runs (the stub generator) may be committed for shape, and are marked
  `fixture: true`, which also makes them headline-ineligible.

## How a published number cites this repository

Every number in `heal-yield`'s README carries the `heal-yield-runs` **commit SHA** it was
recomputed from, in the form *"heal yield <value> — recomputed from `heal-yield-runs@<sha>`"*. A
reader checks it out at that SHA and runs:

```
heal-yield verify --run runs/<dir>
```

No API key, no Docker, no re-execution. If the numbers in the README and the numbers `verify`
prints from the cited SHA disagree, the README is wrong.

## Amending the manifest

`manifests/<version>.yaml` is frozen per version. A manifest change is a minor version bump
with a changelog entry naming the reason (see the `policy` block in the manifest itself).
Runs keep pointing at the version they used; nothing is retro-fitted.
