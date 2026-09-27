"""The benchmark manifest: the anti-cherry-picking mechanism.

The full set is declared before any run and pinned to tagged-release SHAs.
Reading it is deliberately strict -- a manifest that silently tolerates a
missing SHA would let a run quote a repo nobody can check out.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import yaml

DEFAULT_MANIFEST = os.path.join(os.path.dirname(__file__), "data", "manifest.yaml")

REQUIRED_REPO_KEYS = ("name", "repo", "tag", "sha", "license", "tier", "v1_run")


class ManifestError(Exception):
    pass


class Repo(object):
    def __init__(self, data: Dict):
        missing = [k for k in REQUIRED_REPO_KEYS if k not in data]
        if missing:
            raise ManifestError("repo %r is missing %s" % (data.get("name"), ", ".join(missing)))
        if len(str(data["sha"])) != 40:
            raise ManifestError("repo %r: sha is not a full 40-character commit id"
                                % data["name"])
        self.data = data

    def __getattr__(self, item):
        try:
            return self.data[item]
        except KeyError:
            raise AttributeError(item)

    def to_dict(self) -> Dict:
        return dict(self.data)


class Manifest(object):
    def __init__(self, data: Dict):
        self.data = data
        if "manifest_version" not in data:
            raise ManifestError("manifest has no manifest_version")
        self.repos: List[Repo] = [Repo(r) for r in data.get("repos", [])]
        if not self.repos:
            raise ManifestError("manifest declares no repos")

    @property
    def version(self) -> str:
        return str(self.data["manifest_version"])

    @property
    def policy(self) -> Dict:
        return self.data.get("policy", {})

    def get(self, name: str) -> Repo:
        for r in self.repos:
            if r.name == name:
                return r
        raise ManifestError(
            "%r is not in the declared set. The set is the anti-cherry-picking mechanism; "
            "adding a repo is a versioned manifest change, not a command-line flag." % name
        )

    def v1_repos(self) -> List[Repo]:
        return [r for r in self.repos if r.v1_run]

    def headline(self) -> List[Repo]:
        return [r for r in self.repos if r.tier == "headline"]

    def anchors(self) -> List[Repo]:
        return [r for r in self.repos if r.tier == "anchor"]

    def snapshot(self, names: List[str]) -> Dict:
        """The exact rows a run used, for the run directory."""
        return {
            "manifest_version": self.version,
            "declared": self.data.get("declared"),
            "module_selection_rule": self.data.get("module_selection_rule"),
            "baseline_rule": self.data.get("baseline_rule"),
            "policy": self.policy,
            "repos": [self.get(n).to_dict() for n in names],
        }


def load(path: Optional[str] = None) -> Manifest:
    path = os.path.abspath(path or DEFAULT_MANIFEST)
    if not os.path.exists(path):
        raise ManifestError("no manifest at %s" % path)
    with open(path) as fh:
        return Manifest(yaml.safe_load(fh))
