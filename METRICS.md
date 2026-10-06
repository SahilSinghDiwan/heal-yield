# METRICS.md — normative definitions

> Normative. Every definition here is enforced by `heal-yield verify`, which
> recomputes each number from stored artifacts with no re-execution. The README
> carries a one-line summary table above its first number and links here.

Every number `heal-yield` publishes is defined here, and every one is recomputed
by `heal-yield verify` from stored raw artifacts — no re-run, no API key, no
Docker, no trust required.

## The unit: what a candidate is

**A candidate is one generated test function.** Not one file.

- One `test_*` function = one candidate. A `@pytest.mark.parametrize` family
  collapses to the single function that declares it.
- If a generated file fails to **collect**, every test function in it counts as
  a failed candidate with disposition `collect-error`. The file is not discarded
  and **N does not shrink**.

`N` — the count of candidates emitted by the model before any filtering — is the
denominator for every percentage below, and it is always printed next to the
percentage.

## The flake gate

A candidate **passes** only if it passes **5 of 5 consecutive executions** with
no code change. Any failure in any of the five means it did not pass; it is
recorded as flaky, not as failing.

The gate is applied **once, to the survivor pool, at the end of the run**.
Intermediate repair rounds use a single execution for their pass/fail signal,
because a candidate that flakes mid-repair is re-prompted either way. This is a
~4× reduction in executions and it changes nothing about what the published
numbers mean — but it has one consequence, stated here rather than buried:

> A candidate may pass once at round 0, enter the survivor pool, and then fail
> the terminal 5-run gate. It is **not** a first-pass success.

## The metrics

**1. Generated candidates (N).** Distinct test functions emitted before any
filtering. The denominator for everything below.

**2. First-pass build rate** — `builds₀ / N`. A candidate builds if the project's
unmodified collect command succeeds with the candidate added and nothing else
changed. **Zero repair attempts are allowed in this number.**

**3. First-pass green rate** — `passes₀ / N`, where `passes₀` is the count of
candidates that **passed the terminal 5-run gate having required zero repair
rounds**. Computed at the end of the run, attributed to round 0.

**4. Post-heal green rate** — `passes_k / N` after at most **k = 3** repair
rounds, each round being one re-prompt carrying the captured build/test output.
Same terminal 5-run gate. `k` is published, and so is the **per-round
attribution** — successes from round 0 / 1 / 2 / 3.

**5. Heal yield** — `(passes_k − passes₀) / (N − passes₀)`.

> Of everything that failed on first pass, the fraction the repair loop actually
> rescued. This is the headline number and the reason the tool exists. When
> `N − passes₀` is small the ratio is unstable; the raw numerator and
> denominator are always printed alongside it, and it is suppressed entirely
> below a denominator of 10.

**6. Net coverage delta** — line coverage and branch coverage reported
**separately and as a combined line+branch figure**, measured before vs after
merging only the surviving tests. Never a single blended "coverage" number:
in Python, branch coverage does not subsume line coverage.

Baseline is measured with the target module's existing tests removed (see
`baseline_rule` in the manifest). **This is not a claim to improve on the
maintainers' test suites.**

**7. Mutation score** — *deferred to increment 2, declared here as deferred.*
Coverage without mutation score is the metric ACH and Diffblue both moved past;
shipping v1 without it is a known, stated gap, not an oversight.

**8. Cost** — for the whole run: USD, prompt tokens, completion tokens. Plus the
derived **$ per net-new-covered-line** and **$ per surviving test**. Plus
**per-repair-round cost attribution** — what round 1, 2 and 3 each cost, so a
reader can see whether `k = 3` pays for itself.

Cost is enforced by a hard per-run USD ceiling. A tripped ceiling is a loud,
recorded outcome, never a silent one — see *Truncated runs*.

**9. Disposition ledger.** Every candidate has **exactly one terminal
disposition**, drawn from a closed enum. The report prints the assertion
`dispositions sum to N ✓` and `verify` re-checks it. There is never a second,
independent counter.

| Disposition | Meaning |
|---|---|
| `passed` | survived the terminal 5-run gate |
| `collect-error` | its file failed to collect |
| `build-failure` | failed the project's collect/build command |
| `still-failing` | failed after all `k` repair rounds |
| `still-flaky` | failed ≥1 of 5 in the terminal gate |
| `no-progress-abort` | identical failure signature across two rounds |
| `no-coverage-increase` | passed but covered nothing new |
| `timeout` | exceeded the per-candidate time limit |
| `no-output` | the model emitted nothing usable |
| `weakened-rejected` | rejected by the assertion-weakening guard |

**10. Assertion-weakening guard.** Count of candidates rejected because the
repair deleted a test, weakened an assertion to a tautology, or added a
`skip`/`xfail`. **Reported even when it is zero** — it is the named
false-positive failure mode of autonomous repair.

### Never-healed

The same enum, filtered to candidates that entered at least one repair round and
did not survive: `still-failing`, `still-flaky`, `no-progress-abort`,
`weakened-rejected`. Published as a counted breakdown. A curated gallery of the
actual diffs and final failure output is a later increment, not v1.

## Truncated runs

If the USD ceiling trips mid-run, the run is published in full with
`status: truncated`. N is whatever was generated, every percentage is computed
over that N, and the truncation is stamped on every table.

**A truncated run can never be one of the 5 repetitions behind a headline
claim.** All five reps must be `status: complete`. The exclusion is mechanical
and stated, not discretionary.

## Generator failures

A generator that does not exit 0, or that outlives its timeout, ends the run
with `status: generator-failed`. That is a failure of the generator or of its
environment — a missing API key, a rate limit, a retired model id — and it is
**not** disposition `no-output`, which means the model was reached and emitted
nothing usable.

The run is written in full, with every call made before the failure still in
the cost ledger, and N is whatever was generated. Like a truncated run it
**can never be one of the 5 repetitions behind a headline claim**, for the same
mechanical reason: its status is not `complete`.

## Declared-as-unset values

Two limits in this document are **declared-as-unset**. They are named here,
they have a defined meaning, and they have **no value**:

| Value | Where it applies | Status |
|---|---|---|
| Per-run USD ceiling | metric 8, *Truncated runs* | **not yet measured** |
| Per-candidate time limit | disposition `timeout` | **not yet measured** |

Both are meant to be fixed from an **observed run**, and no run has been
observed. So there is no default anywhere in the tool: `heal-yield run`
**requires** `--ceiling-usd` and `--per-candidate-timeout` on the command line
and exits rather than choosing for you, and `LoopConfig` raises `UnsetLimit` if
either is omitted. A default here would within one release be quoted as a
measured recommendation, which is exactly the failure mode this whole document
exists to prevent.

Whatever a caller passes is recorded verbatim in `metadata.yaml` and rendered
in `report.md` as an **operator choice, not a heal-yield recommendation**.
These rows become measured values only when a run has been executed and the
numbers written down here — not before.

## Mutation score (metric 7) is deferred

Stated once more here so it is not read as an omission: metric 7 is **deferred
to increment 2** and ships undefined. No mutation tool and no operator set have
been chosen. Coverage without mutation score is a known, declared gap.

## Run configuration and provenance

Each run directory carries `metadata.yaml`: model ID with date suffix,
temperature (0.0), `k`, seed where the stack supports it, timeouts, the exact
build/test/coverage commands, and the manifest version and repo SHA.

Run artifacts live in the separate **`heal-yield-runs`** repository, append-only.
Every number published in the tool's README carries the **`heal-yield-runs`
commit SHA** it was recomputed from. `heal-yield verify --run <path>` works
against a local clone.

JSON is the source of truth; Markdown is rendered from it and never hand-written.

## Leakage

These packages are public and in training data. Pinning to tagged releases does
not change that. Mitigation is disclosure, plus mutation score once it ships —
a coverage number is far easier to memorise than a killed mutant.

## Attempt semantics

One attempt per target for every headline number. No `pass@k`. If a selection
module ever picks among multiple candidates, it will be labelled `Best@k` and
the selecting module will not touch the evaluation tests.
