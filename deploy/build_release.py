"""Build a source-only web release from the current working tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "app.py", "index.html", "graph_export.html", "README.md", "requirements-web.txt", "requirements-web.lock",
    "web/__init__.py", "web/main.py", "web/storage.py", "web/schema.sql", "web/manage.py", "web/backup.py", "web/client.js", "web/account.css",
    "deploy/Caddyfile.example", "deploy/movie-review.service", "deploy/movie-review-backup.service",
    "deploy/movie-review-backup.timer", "deploy/README.md", "deploy/build_release.py", "docs/DATABASE_MODEL.md",
    "tests/test_web.py", "tests/test_storage_migration.py", "tests/browser_smoke.mjs",
    "tests/test_git_deploy.py", "deploy/GIT_RELEASE.md", "deploy/git_deploy.py",
    "deploy/movie-review-deploy.sh", "deploy/check_release.py", "deploy/audit_source.py",
    "deploy/publish.ps1", "deploy/deploy.ps1",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name in FILES:
        path = ROOT / name
        content = path.read_bytes()
        manifest[name] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in FILES:
            archive.write(ROOT / name, name)
        archive.writestr("release-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({"output": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                      "files": len(manifest), "bytes": output.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
