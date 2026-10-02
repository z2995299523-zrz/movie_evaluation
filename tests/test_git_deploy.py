"""Deployment guards tested entirely against temporary files; never live data."""
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from deploy import git_deploy


def archive(name, *, link=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        entry = tarfile.TarInfo(name)
        if link:
            entry.type = tarfile.SYMTYPE
            entry.linkname = "../../outside"
            tar.addfile(entry)
        else:
            content = b"source code\n"
            entry.size = len(content)
            tar.addfile(entry, io.BytesIO(content))
    return output.getvalue()


class GitDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_requires_exact_commit_instead_of_moving_branch(self):
        for value in ("main", "abc1234", "A" * 40, "a" * 40 + ";false", "../current"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                git_deploy.Deployment(value)
        self.assertEqual(git_deploy.Deployment("a" * 40).commit, "a" * 40)

    def test_git_archive_cannot_escape_release(self):
        for name in ("../outside", "/etc/app.env", "web/../../outside", "web\\outside"):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                git_deploy.extract_source(archive(name), self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_git_archive_rejects_symlinks(self):
        with self.assertRaises(RuntimeError):
            git_deploy.extract_source(archive("web/link", link=True), self.root)
        self.assertFalse((self.root / "web/link").exists())

    def test_prepared_source_tampering_is_detected(self):
        manifest = git_deploy.extract_source(archive("app.py"), self.root)
        commit = "a" * 40
        (self.root / "git-release.json").write_text(json.dumps({"commit": commit, "files": manifest}))
        deployment = git_deploy.Deployment(commit)
        deployment.release = self.root
        deployment.check_source()
        (self.root / "app.py").write_text("modified code")
        with self.assertRaisesRegex(RuntimeError, "source changed"):
            deployment.check_source()

    def test_schema_guard_allows_line_endings_and_rejects_sql_change(self):
        left, right = self.root / "old", self.root / "new"
        for directory in (left, right):
            (directory / "web").mkdir(parents=True)
        (left / "web/schema.sql").write_bytes(b"CREATE TABLE x(id INTEGER);\r\n")
        (right / "web/schema.sql").write_bytes(b"CREATE TABLE x(id INTEGER);\n")
        self.assertTrue(git_deploy.same_schema(left, right))
        (right / "web/schema.sql").write_bytes(b"DROP TABLE x;\n")
        self.assertFalse(git_deploy.same_schema(left, right))

    def test_activation_refuses_a_concurrently_changed_current_release(self):
        releases = self.root / "releases"
        first, changed, candidate = [releases / name for name in ("first", "changed", "candidate")]
        for directory in (first, changed, candidate):
            directory.mkdir(parents=True)
        current = Mock()
        current.resolve.return_value = changed
        deployment = git_deploy.Deployment("a" * 40)
        deployment.release = candidate
        with patch.object(git_deploy, "ROOT", self.root), patch.object(git_deploy, "CURRENT", current), \
             patch.object(deployment, "load", return_value={"oldRelease": str(first)}), \
             patch.object(deployment, "check_source"), patch.object(git_deploy, "run") as commands:
            with self.assertRaisesRegex(RuntimeError, "changed"):
                deployment.activate()
            commands.assert_not_called()
        current.resolve.assert_called_once_with(strict=True)

    def test_rollback_refuses_incompatible_database_schema(self):
        releases = self.root / "releases"
        old, candidate = releases / "old", releases / "candidate"
        for directory in (old, candidate):
            (directory / "web").mkdir(parents=True)
        (old / "web/schema.sql").write_text("old schema")
        (candidate / "web/schema.sql").write_text("new schema")
        current = Mock()
        current.resolve.return_value = candidate
        deployment = git_deploy.Deployment("a" * 40)
        deployment.release = candidate
        with patch.object(git_deploy, "ROOT", self.root), patch.object(git_deploy, "CURRENT", current), \
             patch.object(deployment, "load", return_value={"oldRelease": str(old)}), \
             patch.object(deployment, "check_source"), patch.object(git_deploy, "run") as commands:
            with self.assertRaisesRegex(RuntimeError, "different schemas"):
                deployment.activate(rollback=True)
            commands.assert_not_called()
        current.resolve.assert_called_once_with(strict=True)

    def test_failed_activation_restores_code_without_restoring_database(self):
        old, candidate = self.root / "releases/old", self.root / "releases/candidate"
        for directory in (old, candidate):
            directory.mkdir(parents=True)
        current = Mock()
        current.resolve.return_value = old
        deployment = git_deploy.Deployment("a" * 40)
        deployment.release = candidate
        with patch.object(git_deploy, "ROOT", self.root), patch.object(git_deploy, "DATA", self.root / "data"), \
             patch.object(git_deploy, "CURRENT", current), patch.object(git_deploy, "same_schema", return_value=True), \
             patch.object(deployment, "load", return_value={"oldRelease": str(old)}), \
             patch.object(deployment, "check_source"), patch.object(deployment, "report"), \
             patch.object(deployment, "postcheck", side_effect=RuntimeError("privacy check failed")), \
             patch.object(git_deploy, "run", return_value="123"), \
             patch.object(git_deploy.subprocess, "run", side_effect=[Mock(returncode=0), Mock(returncode=3)]), \
             patch.object(git_deploy, "snapshot", return_value={"users": [(1, "unchanged")]}), \
             patch.object(git_deploy, "wait_ready"), patch.object(git_deploy, "switch") as pointer, \
             patch("web.backup.create_backup"), patch("web.backup.verify_backup", return_value={"recordCount": 40}), \
             patch("web.backup.restore_backup") as restore, \
             patch("web.storage.ArchiveDatabase", return_value=Mock(path=self.root / "data/db/app.sqlite3", migration_backup=None)):
            with self.assertRaisesRegex(RuntimeError, "privacy check failed"):
                deployment.activate()
            self.assertEqual(pointer.call_args_list, [call(candidate), call(old)])
            restore.assert_not_called()


if __name__ == "__main__":
    unittest.main()
