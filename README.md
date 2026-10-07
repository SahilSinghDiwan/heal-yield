# heal-yield

**A measurement harness for self-healing test loops.** Point it at a pinned open-source
Python package; it generates tests, runs them, repairs the failures, and publishes
**first-pass vs post-heal numbers for that run** — including the runs that went badly.

The generate → run → repair loop is commodity. This tool is not the loop. It is the
**ledger**: every candidate gets exactly one terminal disposition from a closed enum, the
dispositions sum to N, and every published number is recomputed from stored artifacts by
`heal-yield verify` — no API key, no Docker, no re-execution, no trust.

The generator is a **subprocess contract**, not a Python entry point, so the harness can
measure a tool it did not ship.

---

## Status: no measured run exists yet

Read this before reading anything else in this repository.

The harness is built and its test suite is green against an offline fixture generator. **No
paid model run has been executed.** There are therefore **no results** here — not a
provisional table, not an illustrative figure, not a number to be refined later. The table
under [Results](#results) says `not yet measured` in every cell, and it will keep saying that
until a run exists and the artifact SHA it came from can be cited.

Two limits are **declared-as-unset** for the same reason:

| Value | Meaning | Status |
|---|---|---|
| Per-run USD ceiling | tripping it truncates the run, loudly | **not yet measured** |
| Per-candidate time limit | exceeding it is disposition `timeout` | **not yet measured** |

Both are meant to be fixed from an *observed* run. None has been observed, so the tool has
**no default for either** — `heal-yield run` requires both on the command line and exits
rather than choosing for you. See [METRICS.md](METRICS.md), *Declared-as-unset values*.

Mutation score (metric 7) is **deferred to increment 2** and declared as deferred, not
omitted.

---

## The definitions come before the numbers

Every number this tool publishes is defined in **[METRICS.md](METRICS.md)**, which is
normative, and each definition is enforced by `heal-yield verify`. The one-line summary:

| # | Metric | Definition (short) |
|---|---|---|
| 1 | Generated candidates (N) | distinct **test functions** emitted before any filtering; a file that fails to collect still contributes every function in it, and N never shrinks |
| 2 | First-pass build rate | `builds₀ / N` — collect succeeds with the candidate added, **zero repair attempts allowed** |
| 3 | First-pass green rate | `passes₀ / N` — passed the terminal 5-of-5 flake gate **having required zero repair rounds** |
| 4 | Post-heal green rate | `passes_k / N` after at most **k = 3** repair rounds, published **per round** |
| 5 | **Heal yield** | `(passes_k − passes₀) / (N − passes₀)` — of everything that failed first pass, what the loop rescued. **Suppressed entirely below a denominator of 10.** |
| 6 | Net coverage delta | line and branch **separately** plus a combined figure; never one blended "coverage" number |
| 7 | Mutation score | **deferred to increment 2**, declared |
| 8 | Cost | USD + tokens for the run, `$ / surviving test`, `$ / net-new covered line`, and **per-repair-round attribution** |
| 9 | Disposition ledger | one terminal disposition per candidate from a **closed enum**; the report asserts `dispositions sum to N` and `verify` re-checks it |
| 10 | Assertion-weakening guard | candidates rejected for deleting a test, weakening an assertion to a tautology, or adding `skip`/`xfail` — **reported even when zero** |

A candidate **passes** only by passing **5 of 5 consecutive executions** with no code change.
The gate is applied once, to the survivor pool, at the end — so a candidate can pass at round
0, enter the pool, and still fail the terminal gate. It is then **not** a first-pass success.

## Results

| Repo | N | First-pass green | Post-heal green | Heal yield | Cost | Run SHA |
|---|---|---|---|---|---|---|
| `cachetools` | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | — |
| `packaging` | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | — |
| `textdistance` | not yet measured | not yet measured | not yet measured | not yet measured | not yet measured | — |

The three targets were **declared before any run**, along with nine more, in
[`heal_yield/data/manifest.yaml`](heal_yield/data/manifest.yaml) — that declaration is the
anti-cherry-picking mechanism. Adding a repo is a versioned manifest bump with a stated
reason, not a command-line flag. Module lists are derived mechanically and never hand-picked:
**at most** 5 modules, by statement count, after excluding `__init__.py`, `__main__.py`,
compat and version shims, anything under a test directory, and any module under 20
statements. "At most" matters — for `cachetools` the rule yields **4** modules, not 5: the
package has five source files and the largest, `__init__.py`, is excluded by the rule. Every
run stores the whole selection in `modules.json`, including each rejected module and why.

Statement counts come from `heal_yield/modules.py`'s own AST walk, which approximates but is
not identical to coverage.py's; the tool also skips `conftest.py`, `setup.py` and `testing/`,
`.tox/`, `.venv/`, `build/`, `docs/` directories. The manifest's rule text says exactly this
as of v1.0.1 (v1.0.0 wrongly said the counts came from coverage.py; it was amended before any
run existed). `modules.json` records what was actually applied.

Target modules' existing tests are removed before a
run so every target starts from the same baseline; **this is not a claim to improve on the
maintainers' suites**.

When results exist, each row will carry the `heal-yield-runs` commit SHA it was recomputed
from, and any reader will be able to reproduce the recomputation offline.

## Install

```
git clone https://github.com/SahilSinghDiwan/heal-yield && cd heal-yield
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

## Run it for free, right now

The fixture generator is deterministic, offline and costs nothing. This exercises the whole
harness — generation, repair rounds, the flake gate, the guard, the ledger, the report:

```
heal-yield run --repo cachetools --ceiling-usd 1 --per-candidate-timeout 60 --install
```

A fixture run is marked `fixture: true` and is **mechanically barred from being a headline
repetition**. It describes the harness, not a model.

Then recompute every number it printed, from the stored artifacts, with nothing re-executed:

```
heal-yield verify --run <the run directory it named>
```

## The first real run — one command

This is the only command that spends money, and it has not been run.

```
ANTHROPIC_API_KEY=... heal-yield run \
  --repo cachetools \
  --generator "python -m heal_yield.generators.anthropic_gen --model claude-sonnet-4-5" \
  --ceiling-usd <the most you are willing to lose> \
  --per-candidate-timeout <seconds> \
  --install \
  --yes-spend
```

Notes, in the order they will bite:

- `--yes-spend` is mandatory for any generator that is not the fixture. Without it the
  command exits 2 before cloning anything.
- `--ceiling-usd` and `--per-candidate-timeout` have **no defaults** and must be named. Both
  are declared-as-unset above; whatever you pass is recorded in `metadata.yaml` and rendered
  in the report as **your choice, not a heal-yield recommendation**.
- The ceiling is hard. Tripping it publishes the run in full with `status: truncated`, and a
  truncated run can never be one of the 5 repetitions behind a headline claim.
- A generator that exits non-zero — no API key, a rate limit, a model id the API no longer
  serves — ends the run with `status: generator-failed` and exit code 1. It is written in
  full and is equally barred from a headline claim. The reference generator does **not**
  retry: a transient API error ends the run, and whatever was spent before it stays spent
  and stays in the ledger.
- The model is chosen by the generator's own `--model`, inside `--generator`. The id
  published in `metadata.yaml` and `report.md` is the one the API reports having served,
  read from the generator's usage records — not a flag on `heal-yield run`.
- Run artifacts land under `$HEAL_YIELD_RUNS` (default `../heal-yield-runs/runs`). That
  repository's layout and its append-only policy are specified in
  [RUNS-REPO.md](RUNS-REPO.md); **it has not been created**.
- Repeat with `--repo packaging` and `--repo textdistance` for the other two v1 targets.

## Measure your own tool

The generator seam is a command line, so any tool that can write files into a directory can
be measured. `heal-yield` invokes it once per module per round:

```
<your command> --module <dotted.path>
               --source-file <path to the module under test>
               --repo <path to the checkout>
               --out-dir <where to write test_*.py>
               --round <0..k>
               --meta-out <path to write a JSON usage record>
               [--feedback-file <captured build/test output from the previous round>]
```

It must write zero or more `test_*.py` files into `--out-dir`, write `--meta-out` as JSON
with at least `{"model", "prompt_tokens", "completion_tokens", "usd"}`, and exit 0. Prompts,
retries and model choice are yours. The harness measures; it does not prescribe.

## Verify a run someone else published

```
heal-yield verify --run runs/<dir>
```

Seven independent checks, reported separately because they fail for different reasons:
**integrity** (do the artifacts still hash to `SHA256SUMS`?), **ledger** (one enum
disposition each, summing to N), **evidence** (re-read each execution's raw pytest output —
every repair round's and each of the flake gate's five — and re-derive the outcome), **disposition-evidence** (is each disposition entailed by that
candidate's own rounds — `passed` requires a five-pass gate record), **arithmetic** (recompute
the metrics block), **coverage** (re-read the stored coverage XML), and
**headline-eligibility**.

`verify` never imports the target package, never starts pytest and never contacts a model. If
it ever needed to, the tool's central claim would be false.

## Tests

```
python -m pytest -q
```

62 tests, entirely offline, against the fixture generator — including an end-to-end run
through the loop, the ledger invariant, the assertion-weakening guard, and the refusal to
invent either unset limit. The paid generator is exercised with its one network call
replaced; no test makes a request.

## Scope

Python and pytest only. One model at temperature 0.0; model comparison is increment 2. CLI
only — no GitHub Action. Not a test generator you should adopt: a **measurement harness that
ships with a reference loop**.

## License

MIT.
