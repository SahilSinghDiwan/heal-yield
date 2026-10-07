"""The reference generator. Calls a real model and costs real money.

Run it through the harness, never directly:

    heal-yield run --repo cachetools \\
        --generator "python -m heal_yield.generators.anthropic_gen --model <dated-model-id>"

`--model` may also be given as `HEAL_YIELD_MODEL`. `ANTHROPIC_API_KEY` must be
set; this generator refuses to run without an explicit key, because it spends
money.

One model, temperature 0.0. The model id written to the usage record -- and
from there to `metadata.yaml` -- is the one the API reports having served, so
it carries the date suffix even when an alias was requested. The
full request and response are written to the run's artifact directory, because
a transcript nobody can read is not evidence.

Uses `urllib` rather than an SDK on purpose: the artifact repo should be
re-readable years from now without resolving a dependency tree.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

from ._contract import parse_args, read_feedback, read_source, write_meta

API_BASE = "https://api.anthropic.com"
API_VERSION = "2023-06-01"

#: USD per million tokens. Recorded in the run so a reader can re-derive every
#: dollar figure; a price that changed after the run does not silently rewrite
#: a published number.
PRICING: Dict[str, Tuple[float, float]] = {
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
DEFAULT_MODEL = "claude-sonnet-4-5"

SYSTEM = (
    "You write pytest tests. You are given one Python module. Emit a single "
    "complete pytest file that exercises it. Rules: import the module under "
    "test by its dotted path; no network, no filesystem writes outside tmp_path, "
    "no sleeps; every test function must assert something specific about "
    "behaviour; do not use pytest.skip or xfail; do not test private helpers "
    "through monkeypatching their internals. Output the file inside one ```python "
    "code fence and nothing else."
)


def pricing_for(model: str) -> Tuple[float, float]:
    """The (input, output) USD-per-million-token row for a model id.

    Matched by prefix so a dated id (`claude-sonnet-4-5-20250929`) finds the
    row of its alias. `price` and the usage record both go through here, so
    the rate that was charged and the rate that is written down cannot differ.
    """
    for prefix, row in PRICING.items():
        if model.startswith(prefix):
            return row
    raise SystemExit(
        "no price on record for model %r. Refusing to publish a cost of zero: "
        "add it to heal_yield/generators/anthropic_gen.py PRICING first." % model
    )


def price(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = pricing_for(model)
    return (prompt_tokens * pin + completion_tokens * pout) / 1_000_000.0


def build_prompt(module: str, source: str, feedback: str, round_index: int) -> str:
    if round_index == 0:
        return (
            "Module under test: `%s`\n\nSource:\n```python\n%s\n```\n\n"
            "Write the pytest file." % (module, source)
        )
    return (
        "Module under test: `%s`\n\nSource:\n```python\n%s\n```\n\n"
        "Your previous tests failed. Here is the captured pytest output:\n\n"
        "```\n%s\n```\n\n"
        "Repair the failing tests. Keep the tests that passed unchanged. Do not "
        "delete a failing test, weaken an assertion to something trivially true, "
        "or add skip/xfail -- those are rejected by the harness and counted "
        "against the run. Emit the complete corrected file."
        % (module, source, feedback[-12000:])
    )


def check_gateway(model: str, base_url: str, unmetered: bool) -> None:
    """Refuse the two ways a non-Anthropic endpoint could mislabel a run.

    A router that exposes `claude-*` ids as slots auto-routed to some free model
    echoes the requested id back as the served model, so a run would be
    published as Claude when no Claude ever answered. And an unpriced model may
    only run when the operator says out loud that its cost is not measured.
    """
    if base_url.rstrip("/") != API_BASE:
        if model.startswith("claude-"):
            raise SystemExit(
                "refusing model %r on %s: a claude-* id on a non-Anthropic endpoint "
                "may be a routing slot, and the response would label the run with a "
                "model that never answered. Name the real model." % (model, base_url)
            )
        if not unmetered:
            raise SystemExit(
                "%s is not api.anthropic.com; pass --unmetered to record cost as "
                "unmetered rather than invent a price." % base_url
            )


def call_model(model: str, prompt: str, api_key: str, max_tokens: int = 8000,
               base_url: str = API_BASE) -> Dict:
    body = json.dumps(
        {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_code(text: str) -> Optional[str]:
    fences = re.findall(r"```(?:python)?\n(.*?)```", text, re.S)
    if fences:
        return fences[0]
    if text.strip().startswith(("import ", "from ", "def test")):
        return text
    return None


def _extend(parser) -> None:
    parser.add_argument(
        "--model",
        default=os.environ.get("HEAL_YIELD_MODEL", DEFAULT_MODEL),
        help="dated model id; must have a row in PRICING or the run refuses to start",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("HEAL_YIELD_BASE_URL", API_BASE),
        help="Messages-API endpoint (default: api.anthropic.com)",
    )
    parser.add_argument(
        "--unmetered",
        action="store_true",
        help="record cost as unmetered (0.0, flagged) instead of pricing the model; "
             "only for a free endpoint, and only with a non-claude-* model id",
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv, extend=_extend)
    model = args.model

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.stderr.write(
            "ANTHROPIC_API_KEY is not set. This generator spends money; it will not "
            "run without an explicit key.\n"
        )
        return 2

    # Before the request, not after it: an unpriced model must refuse to start,
    # not be billed and then fail to record what it cost.
    check_gateway(model, args.base_url, args.unmetered)
    if not args.unmetered:
        pricing_for(model)

    source = read_source(args.repo, args.source_file)
    feedback = read_feedback(args.feedback_file)
    prompt = build_prompt(args.module, source, feedback, args.round)

    artifact_dir = os.environ.get("HEAL_YIELD_ARTIFACT_DIR", args.out_dir)
    os.makedirs(artifact_dir, exist_ok=True)
    with open(os.path.join(artifact_dir, "request.json"), "w") as fh:
        json.dump({"model": model, "temperature": 0.0, "system": SYSTEM,
                   "prompt": prompt}, fh, indent=2)

    try:
        response = call_model(model, prompt, api_key, base_url=args.base_url)
    except urllib.error.HTTPError as exc:
        sys.stderr.write("model call failed: %s %s\n" % (exc.code, exc.read()[:2000]))
        write_meta(args.meta_out, model, 0, 0, 0.0, {"error": "http %s" % exc.code})
        return 1

    with open(os.path.join(artifact_dir, "response.json"), "w") as fh:
        json.dump(response, fh, indent=2)

    usage = response.get("usage", {})
    prompt_tokens = int(usage.get("input_tokens", 0))
    completion_tokens = int(usage.get("output_tokens", 0))
    usd = 0.0 if args.unmetered else price(model, prompt_tokens, completion_tokens)

    text = "".join(
        block.get("text", "") for block in response.get("content", [])
        if block.get("type") == "text"
    )
    code = extract_code(text)
    if code:
        os.makedirs(args.out_dir, exist_ok=True)
        name = "test_%s.py" % args.module.replace(".", "_")
        with open(os.path.join(args.out_dir, name), "w") as fh:
            fh.write(code)

    # The id the API says it served, which carries the date suffix even when an
    # alias was requested. That is the one METRICS.md requires in metadata.yaml.
    served_model = response.get("model") or model
    write_meta(
        args.meta_out, served_model, prompt_tokens, completion_tokens, usd,
        {"round": args.round, "emitted": bool(code), "requested_model": model,
         "stop_reason": response.get("stop_reason"),
         "base_url": args.base_url,
         "cost_basis": "unmetered" if args.unmetered else "priced",
         "pricing_usd_per_mtok": None if args.unmetered else list(pricing_for(model))},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
