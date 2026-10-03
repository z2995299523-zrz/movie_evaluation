"""Deploy a pinned Git commit to the existing schema-v2 Ubuntu installation.

Called by the root-owned movie-review-deploy launcher, under its flock lock.
Routine releases deliberately refuse database/schema and systemd changes.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path, PurePosixPath

ROOT = Path("/opt/movie-review")
REPO = ROOT / "repo.git"
CURRENT = ROOT / "current"
DATA = Path("/var/lib/movie-review")
BACKUPS = Path("/var/backups/movie-review")
UV = ROOT / "tools/uv"
UV_HASH = ROOT / "tools/uv.sha256"
ORIGIN = "https://github.com/z2995299523-zrz/movie_evaluation.git"
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def run(args, *, cwd=None, env=None, binary=False):
    if str(args[0]) == str(UV):
        verify_deployment_tool()
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env,
                            capture_output=True, text=not binary, timeout=300)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[-2500:]}")
    return result.stdout if binary else result.stdout.strip()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def check_root_owned_path(path, *, executable=False):
    """Reject tool replacement through writable files, parents, or symlinks."""
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError("Deployment tool path must be absolute and normalized")
    for entry in (*reversed(path.parents), path):
        try:
            metadata = os.lstat(entry)
        except OSError as error:
            raise RuntimeError("Deployment tool path is unavailable: " + str(entry)) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError("Deployment tool path contains a symlink: " + str(entry))
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise RuntimeError("Deployment tool path must be root-owned and not group/world writable: " + str(entry))
        if entry != path and not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("Deployment tool parent is not a directory: " + str(entry))
        if entry == path and (not stat.S_ISREG(metadata.st_mode) or
                              (executable and not metadata.st_mode & stat.S_IXUSR)):
            raise RuntimeError("Deployment tool must be a regular " + ("executable" if executable else "file"))


def verify_deployment_tool():
    check_root_owned_path(UV, executable=True)
    check_root_owned_path(UV_HASH)
    expected = UV_HASH.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256(UV) != expected:
        raise RuntimeError("Deployment tool SHA-256 does not match its approved root-owned digest")
    return expected


def same_schema(left, right):
    # Git and older Windows ZIP releases can have different line endings.
    return (left / "web/schema.sql").read_text(encoding="utf-8") == (right / "web/schema.sql").read_text(encoding="utf-8")


def snapshot(path, *, sessions=False):
    """Keep full business/security rows in memory, never in deployment logs."""
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA user_version").fetchone()[0] != 2:
            raise RuntimeError("Routine deployment requires schema v2; use a separate migration procedure")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Database integrity check failed")
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: db.execute('SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid').fetchall()
                for name in tables if sessions or name not in ("sessions", "login_failures")}


def extract_source(content, target):
    """Accept only Git regular files/directories, with no path escape or links."""
    manifest = {}
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or "\\" in member.name:
                raise RuntimeError("Unsafe Git archive path")
            destination = target.joinpath(*name.parts)
            if not destination.resolve().is_relative_to(target.resolve()):
                raise RuntimeError("Git archive escaped the release directory")
            if member.isdir():
                destination.mkdir(mode=0o755, parents=True, exist_ok=True)
            elif member.isfile():
                if member.name in manifest or destination.exists():
                    raise RuntimeError("Duplicate Git archive path")
                destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                with archive.extractfile(member) as source, destination.open("xb") as output:
                    while block := source.read(1024 * 1024):
                        output.write(block)
                destination.chmod(0o755 if member.mode & 0o111 else 0o644)
                manifest[member.name] = {"sha256": sha256(destination), "bytes": destination.stat().st_size}
            else:
                raise RuntimeError("Git archive contains a link or special file")
    return manifest


def fetch(path):
    try:
        with HTTP.open("http://127.0.0.1:8000" + path, timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def wait_ready():
    for _ in range(60):
        try:
            if fetch("/readyz")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(.25)
    raise RuntimeError("Website readiness check failed")


def switch(target):
    temporary = CURRENT.with_name("current-git-" + secrets.token_hex(4))
    temporary.symlink_to(target, target_is_directory=True)
    os.replace(temporary, CURRENT)


class Deployment:
    def __init__(self, commit):
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("Use the full 40-character lowercase commit SHA")
        self.commit = commit
        self.release = ROOT / "releases" / ("git-" + commit)
        self.evidence = ROOT / "deployments" / commit

    def report(self, name, value):
        self.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
        value = {"gitCommit": self.commit, **value}
        (self.evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)

    def load(self, name):
        return json.loads((self.evidence / name).read_text(encoding="utf-8"))

    def check_source(self, target=None):
        target = target or self.release
        manifest_path = target / "git-release.json"
        if not manifest_path.is_file():
            raise RuntimeError("Missing Git release provenance")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if target == self.release and manifest["commit"] != self.commit:
            raise RuntimeError("Git release commit mismatch")
        for name, entry in manifest["files"].items():
            if sha256(target / name) != entry["sha256"]:
                raise RuntimeError("Release source changed after preparation: " + name)

    def prepare(self):
        tool_digest = verify_deployment_tool()
        if run(["git", "--git-dir", REPO, "remote", "get-url", "origin"]) != ORIGIN:
            raise RuntimeError("Source origin does not match the approved repository")
        run(["git", "--git-dir", REPO, "merge-base", "--is-ancestor", self.commit, "refs/remotes/origin/main"])
        snapshot(DATA / "db/app.sqlite3")
        old = CURRENT.resolve(strict=True)
        if self.release.exists():
            self.check_source()
            self.load("prepared.json")
            if CURRENT.resolve() == self.release:
                raise RuntimeError("This commit is already active; use verify")
            raise RuntimeError("This commit is already prepared; continue with trial/activate")
        self.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.umask(0o022)
        self.release.mkdir(mode=0o755)
        content = run(["git", "--git-dir", REPO, "archive", "--format=tar", self.commit], binary=True)
        manifest = extract_source(content, self.release)
        if not same_schema(old, self.release):
            raise RuntimeError("Schema SQL changed; a separately rehearsed migration is required")
        for name in ("movie-review.service", "movie-review-backup.service", "movie-review-backup.timer"):
            if (old / "deploy" / name).read_text() != (self.release / "deploy" / name).read_text():
                raise RuntimeError("systemd templates changed; review/install separately before deploying")
        run([UV, "venv", "--python", "/usr/bin/python3", self.release / ".venv"])
        python = self.release / ".venv/bin/python"
        run([UV, "pip", "sync", "--python", python, self.release / "requirements-web.lock"])
        run([UV, "pip", "check", "--python", python])
        qa = self.evidence / "qa-venv"
        run([UV, "venv", "--python", "/usr/bin/python3", qa])
        run([UV, "pip", "sync", "--python", qa / "bin/python", self.release / "requirements-web.lock"])
        run([UV, "pip", "install", "--python", qa / "bin/python", "httpx==0.28.1"])
        output = run([qa / "bin/python", self.release / "deploy/check_release.py", "web"], cwd=self.release)
        checks = json.loads(output.splitlines()[-1])
        run(["runuser", "-u", "movie-review", "--", python, "-c", "import fastapi,pydantic,uvicorn"], cwd=self.release)
        (self.release / "git-release.json").write_text(json.dumps({"commit": self.commit, "origin": ORIGIN, "files": manifest}, indent=2))
        os.umask(0o077)
        self.report("prepared.json", {"release": str(self.release), "oldRelease": str(old),
                                     "files": len(manifest), "schemaUnchanged": True, "tests": checks,
                                     "deploymentToolSha256": tool_digest})

    def trial(self):
        from web.backup import create_backup, restore_backup, verify_backup
        from web.storage import ArchiveDatabase
        self.load("prepared.json")
        self.check_source()
        archive = BACKUPS / f"pre-git-trial-{self.commit[:12]}-{time.time_ns()}.zip"
        create_backup(DATA, archive)
        manifest = verify_backup(archive)
        target = self.evidence / ("trial-" + str(time.time_ns()))
        restore_backup(archive, target)
        before = snapshot(target / "db/app.sqlite3")
        database = ArchiveDatabase(target / "db/app.sqlite3")
        if database.migration_backup or snapshot(database.path) != before:
            raise RuntimeError("New code changes business content or triggers migration")
        roundtrip = target.parent / (target.name + ".zip")
        create_backup(target, roundtrip)
        restored = target.parent / (target.name + "-restored")
        restore_backup(roundtrip, restored)
        if snapshot(restored / "db/app.sqlite3") != before:
            raise RuntimeError("Backup does not round-trip the full business data")
        for name, digest in manifest["mediaSha256"].items():
            if sha256(restored / name) != digest:
                raise RuntimeError("Restored media hash mismatch")
        self.report("trial.json", {"backup": str(archive), "restoreVerified": True,
                                  "recordCount": manifest["recordCount"], "mediaCount": len(manifest["mediaSha256"]),
                                  "migrationRequired": False, "businessFieldsExact": True})

    def postcheck(self, target):
        wait_ready()
        if CURRENT.resolve() != target:
            raise RuntimeError("Active release pointer mismatch")
        for path in ("/api/auth/me", "/api/users", "/api/reviews", "/api/exports/backup"):
            status, headers, _ = fetch(path)
            if status != 401 or headers.get("Cache-Control") != "no-store":
                raise RuntimeError("Unauthenticated privacy check failed: " + path)
        for url, name in (("/", "index.html"), ("/web/client.js", "web/client.js"), ("/web/account.css", "web/account.css")):
            status, headers, content = fetch(url)
            # Homepage is adapted by FastAPI; verify the unchanged static resources exactly.
            if status != 200 or headers.get("Cache-Control") != "no-store":
                raise RuntimeError("Website resource check failed: " + url)
            if url != "/" and hashlib.sha256(content).hexdigest() != sha256(target / name):
                raise RuntimeError("Served resource differs from Git release: " + name)
        run(["systemctl", "is-active", "movie-review", "nginx", "sing-box",
             "movie-review-backup.timer", "movie-review-certbot-renew.timer"])
        return {"ready": 200, "privateApis": 401, "staticResourcesExact": True, "servicesAndTimersActive": True}

    def activate(self, *, rollback=False):
        from web.backup import create_backup, verify_backup
        from web.storage import ArchiveDatabase
        self.load("trial.json")
        self.check_source()
        old = CURRENT.resolve(strict=True)
        expected = self.release if rollback else Path(self.load("prepared.json")["oldRelease"])
        target = Path(self.load("deployment.json")["oldRelease"]) if rollback else self.release
        if old != expected or not target.is_relative_to(ROOT / "releases") or not target.is_dir():
            raise RuntimeError("Active or target release changed; refuse activation")
        if not same_schema(old, target):
            raise RuntimeError("Refuse code-only switch across different schemas")
        if (target / "git-release.json").is_file():
            self.check_source(target)
        timer_active = subprocess.run(["systemctl", "is-active", "--quiet", "movie-review-backup.timer"]).returncode == 0
        proxy_pid = run(["systemctl", "show", "sing-box", "--property=MainPID", "--value"])
        archive = BACKUPS / f"pre-git-{'rollback' if rollback else 'final'}-{self.commit[:12]}-{time.time_ns()}.zip"
        stopped = False
        try:
            if timer_active:
                run(["systemctl", "stop", "movie-review-backup.timer"])
            for _ in range(60):
                if subprocess.run(["systemctl", "is-active", "--quiet", "movie-review-backup.service"]).returncode != 0:
                    break
                time.sleep(.5)
            else:
                raise RuntimeError("Scheduled backup is still running")
            start = time.monotonic()
            run(["systemctl", "stop", "movie-review.service"])
            stopped = True
            before = snapshot(DATA / "db/app.sqlite3", sessions=True)
            media = {str(p.relative_to(DATA)): sha256(p) for p in (DATA / "media").rglob("*") if p.is_file()}
            create_backup(DATA, archive)
            manifest = verify_backup(archive)
            database = ArchiveDatabase(DATA / "db/app.sqlite3")
            if database.migration_backup or snapshot(database.path, sessions=True) != before:
                raise RuntimeError("Business data or existing sessions changed before startup")
            if any(sha256(DATA / name) != digest for name, digest in media.items()):
                raise RuntimeError("Production media changed")
            switch(target)
            run(["systemctl", "start", "movie-review.service"])
            wait_ready()
            seconds = round(time.monotonic() - start, 2)
            if timer_active:
                run(["systemctl", "start", "movie-review-backup.timer"])
            checks = self.postcheck(target)
            if run(["systemctl", "show", "sing-box", "--property=MainPID", "--value"]) != proxy_pid:
                raise RuntimeError("Existing proxy process changed")
            self.report("rollback.json" if rollback else "deployment.json",
                        {"release": str(target), "oldRelease": str(old), "backup": str(archive),
                         "databaseReplaced": False, "migrationPerformed": False, "businessFieldsExactBeforeStart": True,
                         "existingSessionsPreserved": True, "mediaFilesPreserved": len(media),
                         "recordCount": manifest["recordCount"], "proxyPidUnchanged": True,
                         "activationSeconds": seconds, "checks": checks})
        except Exception as failure:
            if stopped:
                run(["systemctl", "stop", "movie-review.service"])
                switch(old)
                run(["systemctl", "start", "movie-review.service"])
                wait_ready()
                self.report("automatic-rollback.json", {"release": str(old), "databaseReplaced": False, "reason": str(failure)})
            raise
        finally:
            if timer_active:
                run(["systemctl", "start", "movie-review-backup.timer"])

    def verify(self):
        from web.backup import restore_backup, verify_backup
        target = CURRENT.resolve(strict=True)
        allowed = {self.release}
        if (self.evidence / "rollback.json").is_file():
            allowed.add(Path(self.load("rollback.json")["release"]))
        if target not in allowed:
            raise RuntimeError("Current release differs from this deployment")
        if (target / "git-release.json").exists():
            self.check_source(target)
        checks = self.postcheck(target)
        started = time.time_ns()
        run(["systemctl", "start", "movie-review-backup.service"])
        status = run(["systemctl", "show", "movie-review-backup.service", "--property=Result", "--property=ExecMainStatus"])
        if "Result=success" not in status or "ExecMainStatus=0" not in status:
            raise RuntimeError("Formal backup service failed")
        archive = max(BACKUPS.glob("movie-review-*.zip"), key=lambda p: p.stat().st_mtime_ns)
        if archive.stat().st_mtime_ns < started:
            raise RuntimeError("No fresh formal backup was created")
        manifest = verify_backup(archive)
        restored = self.evidence / ("post-release-restored-" + str(time.time_ns()))
        restore_backup(archive, restored)
        snapshot(restored / "db/app.sqlite3")
        with closing(sqlite3.connect(restored / "db/app.sqlite3")) as db:
            if db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]:
                raise RuntimeError("Restored backup contains active sessions")
        self.report("verification.json", {"release": str(target), "backup": str(archive), "restoreVerified": True,
                                         "recordCount": manifest["recordCount"], "mediaCount": len(manifest["mediaSha256"]),
                                         "restoredSessions": 0, "checks": checks})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["all", "prepare", "trial", "activate", "verify", "rollback"])
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Run via sudo movie-review-deploy")
    os.umask(0o077)
    deployment = Deployment(args.commit)
    os.environ["MOVIE_REVIEW_DATA_DIR"] = str(deployment.evidence / "never-production-default")
    runtime = deployment.release / ".venv/bin/python"
    if args.phase not in ("all", "prepare") and Path(sys.executable) != runtime:
        print(run([runtime, __file__, args.phase, "--commit", args.commit]), flush=True)
        return
    if args.phase in ("all", "prepare"):
        deployment.prepare()
    sys.path.insert(0, str(deployment.release))
    if args.phase == "all":
        # Use the prepared runtime, without installing web dependencies in system Python.
        for phase in ("trial", "activate", "verify"):
            print(run([deployment.release / ".venv/bin/python", __file__, phase, "--commit", args.commit]), flush=True)
    elif args.phase != "prepare":
        if args.phase == "rollback":
            deployment.activate(rollback=True)
            deployment.verify()
        else:
            getattr(deployment, args.phase)()


if __name__ == "__main__":
    main()
