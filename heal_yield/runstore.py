"""The run directory: layout, writing, and reading back.

This is the layout of the append-only `heal-yield-runs` repository. It is
fixed here rather than described in prose because `verify` walks it.

    runs/<YYYY-MM-DD>-<repo>-<sha7>[-r<rep>]/
      metadata.yaml            run configuration and provenance
      manifest.snapshot.yaml   the exact manifest rows this run used
      modules.json             the mechanical module selection, with its audit trail
      run.json                 SOURCE OF TRUTH: candidates, rounds, dispositions, usage
      report.md                rendered from run.json, never hand-written
      SHA256SUMS               digest of every file under raw/
      raw/
        generated/<module>/round-<n>/<file>.py     what the generator emitted
        model/<module>/round-<n>/{usage.json,generator.txt,...}
        pytest/<module>/collect-round-<n>.{txt,jsonl}
        pytest/<module>/round-<n>.{txt,jsonl}
        pytest/gate/exec-<1..5>.{txt,jsonl}
        coverage/{baseline,final}.{xml,json}

Two rules make the directory auditable rather than merely present:

  * `run.json` names, for every candidate and every round, the raw file its
    outcome was read from. A number with no path to a file is not publishable.
  * `SHA256SUMS` covers everything under `raw/`, so `verify` can prove the
    artifacts it recomputed from are the artifacts the run wrote.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, Iterator, List, Optional

RUN_JSON = "run.json"
METADATA = "metadata.yaml"
MANIFEST_SNAPSHOT = "manifest.snapshot.yaml"
MODULES_JSON = "modules.json"
REPORT_MD = "report.md"
CHECKSUMS = "SHA256SUMS"
RAW = "raw"


def run_dir_name(date: str, repo: str, sha: str, repetition: Optional[int] = None) -> str:
    name = "%s-%s-%s" % (date, repo, sha[:7])
    if repetition is not None:
        name += "-r%d" % repetition
    return name


class RunStore(object):
    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    # -- paths ---------------------------------------------------------
    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def raw(self, *parts: str) -> str:
        return os.path.join(self.root, RAW, *parts)

    def generated_dir(self, module: str, round_index: int) -> str:
        return self.raw("generated", _slug(module), "round-%d" % round_index)

    def model_dir(self, module: str, round_index: int) -> str:
        return self.raw("model", _slug(module), "round-%d" % round_index)

    def pytest_prefix(self, module: str, name: str) -> str:
        return self.raw("pytest", _slug(module), name)

    def gate_prefix(self, execution: int) -> str:
        return self.raw("pytest", "gate", "exec-%d" % execution)

    def coverage_path(self, which: str, ext: str) -> str:
        return self.raw("coverage", "%s.%s" % (which, ext))

    # -- io ------------------------------------------------------------
    def ensure(self) -> None:
        for sub in ("generated", "model", "pytest", "coverage"):
            os.makedirs(self.raw(sub), exist_ok=True)

    def write_json(self, name: str, payload: Dict) -> str:
        p = self.path(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        return p

    def read_json(self, name: str) -> Dict:
        with open(self.path(name)) as fh:
            return json.load(fh)

    def write_text(self, name: str, text: str) -> str:
        p = self.path(name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    # -- integrity -----------------------------------------------------
    def iter_raw_files(self) -> Iterator[str]:
        base = self.path(RAW)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for fn in sorted(filenames):
                yield os.path.relpath(os.path.join(dirpath, fn), self.root)

    def write_checksums(self) -> str:
        lines: List[str] = []
        for rel in self.iter_raw_files():
            lines.append("%s  %s" % (_sha256_file(self.path(rel)), rel))
        return self.write_text(CHECKSUMS, "\n".join(lines) + ("\n" if lines else ""))

    def verify_checksums(self) -> Dict[str, List[str]]:
        """Compare stored digests to what is on disk.

        Returns the three ways this can go wrong separately -- changed,
        missing, unlisted -- because they mean different things: a changed
        file is tampering or a rerun, a missing one is an incomplete commit,
        and an unlisted one is an artifact written after the run was sealed.
        """
        stored: Dict[str, str] = {}
        cpath = self.path(CHECKSUMS)
        if os.path.exists(cpath):
            with open(cpath) as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    digest, rel = line.split("  ", 1)
                    stored[rel] = digest
        on_disk = set(self.iter_raw_files())
        changed, missing = [], []
        for rel, digest in sorted(stored.items()):
            full = self.path(rel)
            if not os.path.exists(full):
                missing.append(rel)
            elif _sha256_file(full) != digest:
                changed.append(rel)
        unlisted = sorted(on_disk - set(stored))
        return {"changed": changed, "missing": missing, "unlisted": unlisted}


def _slug(module: str) -> str:
    return module.replace("/", "_").replace(os.sep, "_")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
