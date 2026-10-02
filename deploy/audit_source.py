"""Audit staged blobs without printing their contents or touching private data."""
from __future__ import annotations

import re
import subprocess
from pathlib import PurePosixPath


def git(*args):
    return subprocess.check_output(["git", *args])


def main():
    names = git("ls-files", "-z").decode("utf-8").split("\0")
    patterns = [rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
                rb"gh[pousr]_[A-Za-z0-9]{30,}", rb"github_pat_[A-Za-z0-9_]{40,}", rb"AKIA[A-Z0-9]{16}"]
    problems = []
    count = 0
    for name in filter(None, names):
        path = PurePosixPath(name)
        if (set(path.parts) & {"media", "backups", "feils", ".venv", ".runtime-web", ".runtime-test", "build", "dist"}
                or path.suffix.lower() in {".pem", ".key", ".sqlite", ".sqlite3", ".db", ".zip", ".exe", ".pfx", ".p12"}
                or path.name.startswith(".env") or path.suffix == ".env" or path.name.startswith("movie-reviews")
                or (name.startswith("deploy/") and name.endswith("-record.md"))
                or name.startswith("影评网站上线方案/")):
            problems.append(name + ": private/local artifact")
            continue
        content = git("show", ":" + name)
        if len(content) > 5 * 1024 * 1024:
            problems.append(name + ": unexpectedly large source file")
        if any(re.search(pattern, content) for pattern in patterns):
            problems.append(name + ": credential signature")
        count += 1
    if problems:
        raise SystemExit("Source audit failed:\n" + "\n".join(problems))
    print(f"Source audit passed: {count} files; no excluded artifacts or credential signatures")


if __name__ == "__main__":
    main()
