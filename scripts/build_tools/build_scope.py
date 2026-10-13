# Generated from ststats/staruniv/scripts/build_tools/build_scope.py; do not edit.
"""Decide deployment scope; the Build workflow still tests every main push."""
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def needs_build(paths, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in patterns)


def main():
    if sys.argv[1:] == ["--verify"]:
        lock = Path(".github/build-tools.json")
        if lock.exists():
            manifest = json.loads(lock.read_text(encoding="utf-8"))
            for name, expected in manifest["sha256"].items():
                source = (Path(__file__).resolve().parent / name).read_text(encoding="utf-8")
                if hashlib.sha256(source.encode()).hexdigest() != expected:
                    raise SystemExit(f"modified generated build tool: {name}")
        return
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    before = event.get("before", "")
    deploy = os.environ["GITHUB_EVENT_NAME"] != "push" or not before.strip("0")
    if not deploy:
        # A force-push can remove the previous commit from a fresh checkout.
        # Without a baseline, build everything rather than guessing the diff.
        baseline = subprocess.run(
            ["git", "cat-file", "-e", f"{before}^{{commit}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if baseline.returncode:
            print("Previous commit unavailable; running a full build.")
            deploy = True
    if not deploy:
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", "-z", before, os.environ["GITHUB_SHA"]]
        ).decode("utf-8").split("\0")
        patterns = json.loads(Path(".github/build-paths.json").read_text(encoding="utf-8"))
        deploy = needs_build(changed, patterns)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"deploy={str(deploy).lower()}\n")


if __name__ == "__main__":
    main()
