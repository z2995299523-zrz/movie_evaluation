"""Deployment guards tested entirely against temporary files; never live data."""
import io
import json
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
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

    def tool_metadata(self, path, *, owner=0, mode=None):
        if mode is None:
            mode = stat.S_IFREG | 0o755 if path.name == "uv" else stat.S_IFDIR | 0o755
        return SimpleNamespace(st_uid=owner, st_mode=mode)

    def test_deployment_tool_checks_every_parent_without_following_links(self):
        tool = PurePosixPath("/opt/movie-review/tools/uv")
        with patch.object(git_deploy.os, "lstat", side_effect=self.tool_metadata) as metadata:
            git_deploy.check_root_owned_path(tool, executable=True)
        self.assertEqual(metadata.call_args_list,
                         [call(PurePosixPath(path)) for path in
                          ("/", "/opt", "/opt/movie-review", "/opt/movie-review/tools", str(tool))])

    def test_deployment_tool_rejects_replaceable_parent_or_file(self):
        tool = PurePosixPath("/opt/movie-review/tools/uv")
        for target in (*tool.parents, tool):
            for owner, permissions in ((1000, 0o755), (0, 0o775), (0, 0o757)):
                def metadata(path):
                    kind = stat.S_IFREG if path == tool else stat.S_IFDIR
                    return self.tool_metadata(path, owner=owner if path == target else 0,
                                              mode=kind | (permissions if path == target else 0o755))
                with self.subTest(target=str(target), owner=owner, mode=permissions), \
                     patch.object(git_deploy.os, "lstat", side_effect=metadata), \
                     self.assertRaisesRegex(RuntimeError, "root-owned"):
                    git_deploy.check_root_owned_path(tool, executable=True)

    def test_deployment_tool_rejects_symlinks_including_parent(self):
        tool = PurePosixPath("/opt/movie-review/tools/uv")
        for target in (tool.parent, tool):
            def metadata(path):
                if path == target:
                    return self.tool_metadata(path, mode=stat.S_IFLNK | 0o755)
                return self.tool_metadata(path)
            with self.subTest(target=str(target)), patch.object(git_deploy.os, "lstat", side_effect=metadata), \
                 self.assertRaisesRegex(RuntimeError, "symlink"):
                git_deploy.check_root_owned_path(tool, executable=True)

    def test_deployment_tool_rejects_nonexecutable_special_and_missing_files(self):
        tool = PurePosixPath("/opt/movie-review/tools/uv")
        for mode in (stat.S_IFREG | 0o644, stat.S_IFIFO | 0o755, stat.S_IFDIR | 0o755):
            def metadata(path):
                return self.tool_metadata(path, mode=mode) if path == tool else self.tool_metadata(path)
            with self.subTest(mode=mode), patch.object(git_deploy.os, "lstat", side_effect=metadata), \
                 self.assertRaisesRegex(RuntimeError, "regular executable"):
                git_deploy.check_root_owned_path(tool, executable=True)
        with patch.object(git_deploy.os, "lstat", side_effect=FileNotFoundError), \
             self.assertRaisesRegex(RuntimeError, "unavailable"):
            git_deploy.check_root_owned_path(tool, executable=True)

    def test_deployment_tool_digest_detects_tampering_and_unsafe_digest_file(self):
        tool, approved = self.root / "uv", self.root / "uv.sha256"
        tool.write_bytes(b"independently verified executable")
        approved.write_text(git_deploy.sha256(tool) + "\n", encoding="ascii")
        def metadata(path):
            mode = stat.S_IFREG | 0o755 if path == tool else \
                   stat.S_IFREG | 0o644 if path == approved else stat.S_IFDIR | 0o755
            return self.tool_metadata(path, mode=mode)
        with patch.object(git_deploy, "UV", tool), patch.object(git_deploy, "UV_HASH", approved), \
             patch.object(git_deploy.os, "lstat", side_effect=metadata):
            self.assertEqual(git_deploy.verify_deployment_tool(), approved.read_text().strip())
            tool.write_bytes(b"replaced executable")
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                git_deploy.verify_deployment_tool()
            tool.write_bytes(b"independently verified executable")
            approved.write_text("invalid digest\n")
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                git_deploy.verify_deployment_tool()
            def unsafe_digest(path):
                result = metadata(path)
                if path == approved:
                    result.st_uid = 1000
                return result
            with patch.object(git_deploy.os, "lstat", side_effect=unsafe_digest), \
                 self.assertRaisesRegex(RuntimeError, "root-owned"):
                git_deploy.verify_deployment_tool()

    def test_untrusted_tool_is_rejected_before_subprocess_or_prepare_mutation(self):
        with patch.object(git_deploy, "verify_deployment_tool", side_effect=RuntimeError("untrusted tool")), \
             patch.object(git_deploy.subprocess, "run") as process, \
             patch.object(git_deploy, "snapshot") as database:
            with self.assertRaisesRegex(RuntimeError, "untrusted tool"):
                git_deploy.run([git_deploy.UV, "--version"])
            with self.assertRaisesRegex(RuntimeError, "untrusted tool"):
                git_deploy.Deployment("a" * 40).prepare()
            process.assert_not_called()
            database.assert_not_called()

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
