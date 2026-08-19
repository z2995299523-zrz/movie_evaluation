import base64
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import (
    BACKUP_FORMAT,
    BACKUP_FORMAT_VERSION,
    DataValidationError,
    DesktopApi,
    MAX_IMAGE_BYTES,
    ReviewStore,
    SettingsStore,
    _dropped_image_path,
    _json_bytes,
    expose_desktop_api,
    main,
    normalize_reviews,
)


VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def sample_review(**changes):
    value = {
        "id": "review_1",
        "title": "一部电影",
        "director": "导演",
        "date": "2026-08-19",
        "rating": 5,
        "tags": ["剧情", "剧情", "经典"],
        "comment": "值得记录。",
        "createdAt": 1_700_000_000_000,
        "updatedAt": 1_700_000_000_000,
    }
    value.update(changes)
    return value


class NormalizeReviewsTests(unittest.TestCase):
    def test_normalizes_valid_review_and_deduplicates_tags(self):
        result = normalize_reviews([sample_review()])
        self.assertEqual(result[0]["title"], "一部电影")
        self.assertEqual(result[0]["tags"], ["剧情", "经典"])
        self.assertEqual(result[0]["rating"], 5)

    def test_rejects_invalid_date(self):
        with self.assertRaisesRegex(DataValidationError, "观影日期"):
            normalize_reviews([sample_review(date="2026-02-30")])

    def test_rejects_out_of_range_rating(self):
        with self.assertRaisesRegex(DataValidationError, "评分"):
            normalize_reviews([sample_review(rating=6)])

    def test_replaces_unsafe_or_duplicate_ids(self):
        result = normalize_reviews([sample_review(id="bad'id"), sample_review(id="review_1")])
        self.assertNotIn("'", result[0]["id"])
        self.assertNotEqual(result[0]["id"], result[1]["id"])

    def test_accepts_safe_optional_image_metadata(self):
        image = {
            "path": f"media/{'a' * 64}.png",
            "name": "剧照.png",
            "mime": "image/png",
            "size": 123,
        }
        result = normalize_reviews([sample_review(image=image)])
        self.assertEqual(result[0]["image"], image)

    def test_rejects_unsafe_or_inconsistent_image_metadata(self):
        with self.assertRaisesRegex(DataValidationError, "路径不安全"):
            normalize_reviews([sample_review(image={"path": "../secret.png", "name": "x.png", "mime": "image/png", "size": 1})])
        with self.assertRaisesRegex(DataValidationError, "类型与路径不一致"):
            normalize_reviews(
                [sample_review(image={"path": f"media/{'a' * 64}.png", "name": "x.png", "mime": "image/jpeg", "size": 1})]
            )


class ReviewStoreTests(unittest.TestCase):
    @staticmethod
    def write_png(path: Path, suffix: bytes = b"") -> Path:
        path.write_bytes(VALID_PNG + suffix)
        return path

    def test_first_use_creates_plain_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            self.assertEqual(store.load(), [])
            self.assertEqual(json.loads(store.data_path.read_text(encoding="utf-8")), [])

    def test_save_is_atomic_and_keeps_previous_version_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            store.save([sample_review(title="旧标题")])
            store.save([sample_review(title="新标题")])

            current = json.loads(store.data_path.read_text(encoding="utf-8"))
            backups = sorted(store.backup_directory.glob("movie-reviews-*.json"))
            backup_documents = [json.loads(path.read_text(encoding="utf-8")) for path in backups]

            self.assertEqual(current[0]["title"], "新标题")
            self.assertTrue(any(document and document[0]["title"] == "旧标题" for document in backup_documents))
            self.assertFalse(list(Path(directory).glob("*.tmp")))

    def test_corrupt_primary_file_is_not_overwritten_on_load(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            store.data_path.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "有效的 JSON"):
                store.load()
            self.assertEqual(store.data_path.read_text(encoding="utf-8"), "{broken")

    def test_image_import_deduplicates_and_chunk_round_trip_matches_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.write_png(root / "original.png")
            store = ReviewStore(root / "data")
            store.directory.mkdir()

            first = store.import_image(source)
            second = store.import_image(source)
            self.assertEqual(first, second)
            self.assertTrue(first["path"].endswith(".png"))
            self.assertEqual(len(list(store.media_directory.iterdir())), 1)

            info = store.get_image_info(first["path"])
            offset = 0
            assembled = bytearray()
            while offset < info["size"]:
                chunk = store.read_image_chunk(first["path"], offset, 7)
                assembled.extend(base64.b64decode(chunk["data"]))
                offset = chunk["nextOffset"]
            self.assertEqual(bytes(assembled), source.read_bytes())
            with self.assertRaisesRegex(DataValidationError, "偏移量"):
                store.read_image_chunk(first["path"], -1, 7)
            with self.assertRaisesRegex(DataValidationError, "长度"):
                store.read_image_chunk(first["path"], 0, 1024 * 1024 + 1)

            disguised = self.write_png(root / "disguised.jpg")
            with self.assertRaisesRegex(DataValidationError, "扩展名与实际文件内容不一致"):
                store.import_image(disguised)

    def test_image_import_rejects_non_image_and_over_limit_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ReviewStore(root / "data")
            store.directory.mkdir()
            invalid = root / "not-image.jpg"
            invalid.write_bytes(b"not an image")
            with self.assertRaisesRegex(DataValidationError, "仅支持"):
                store.import_image(invalid)

            oversized = root / "oversized.png"
            with oversized.open("wb") as stream:
                stream.write(b"\x89PNG\r\n\x1a\n")
                stream.seek(MAX_IMAGE_BYTES)
                stream.write(b"x")
            with self.assertRaisesRegex(DataValidationError, "50 MB"):
                store.import_image(oversized)

    def test_image_size_limit_accepts_exact_boundary_and_rejects_next_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ReviewStore(root / "data")
            store.directory.mkdir()
            exact = root / "exact.png"
            exact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 8)
            over = root / "over.png"
            over.write_bytes(exact.read_bytes() + b"1")
            with mock.patch("app.MAX_IMAGE_BYTES", 16):
                self.assertEqual(store.import_image(exact)["size"], 16)
                with self.assertRaisesRegex(DataValidationError, "50 MB"):
                    store.import_image(over)

    def test_dropped_image_import_uses_native_path_and_precise_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            store = ReviewStore(data)
            source = self.write_png(root / "dragged.png")
            event = {"dataTransfer": {"files": [{"name": source.name, "pywebviewFullPath": str(source.resolve())}]}}

            dropped_path = _dropped_image_path(event)
            first = store.import_dropped_image(dropped_path)
            second = store.import_dropped_image(dropped_path)
            self.assertEqual(first, second)
            self.assertEqual(len(list(store.media_directory.iterdir())), 1)

            with self.assertRaisesRegex(DataValidationError, "只能上传一张"):
                _dropped_image_path({"dataTransfer": {"files": [{}, {}]}})
            with self.assertRaisesRegex(DataValidationError, "暂不支持"):
                _dropped_image_path({"dataTransfer": {"files": [{"name": "virtual.png"}]}})
            with self.assertRaisesRegex(DataValidationError, "不支持文件夹"):
                store.import_dropped_image(root)

            text_file = root / "notes.txt"
            text_file.write_text("not an image", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "仅支持 JPG"):
                store.import_dropped_image(text_file)

            empty = root / "empty.png"
            empty.touch()
            with self.assertRaisesRegex(DataValidationError, "剧照文件为空"):
                store.import_dropped_image(empty)

            disguised = self.write_png(root / "disguised.jpg")
            with self.assertRaisesRegex(DataValidationError, "扩展名与实际文件内容不一致"):
                store.import_dropped_image(disguised)

            with self.assertRaisesRegex(DataValidationError, "无法读取拖入"):
                store.import_dropped_image(root / "moved.png")

    def test_dropped_image_size_limit_accepts_exact_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            store = ReviewStore(data)
            exact = root / "exact.png"
            exact.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 8)
            over = root / "over.png"
            over.write_bytes(exact.read_bytes() + b"1")
            with mock.patch("app.MAX_IMAGE_BYTES", 16):
                self.assertEqual(store.import_dropped_image(exact)["size"], 16)
                with self.assertRaisesRegex(DataValidationError, "不能超过 50 MB"):
                    store.import_dropped_image(over)

    def test_image_reference_cannot_escape_media_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            with self.assertRaisesRegex(DataValidationError, "不安全"):
                store.resolve_image_path("media/../../secret.jpg")
            missing = f"media/{'f' * 64}.jpg"
            with self.assertRaisesRegex(DataValidationError, "缺失"):
                store.get_image_info(missing)

    def test_complete_backup_contains_only_referenced_media_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_store = ReviewStore(root / "source")
            source_store.directory.mkdir()
            referenced = source_store.import_image(self.write_png(root / "referenced.png"))
            source_store.import_image(self.write_png(root / "unreferenced.png", b"unused"))
            reviews = [sample_review(image=referenced)]
            source_store.save(reviews)

            backup = root / "backup.zip"
            source_store.export_backup(backup, reviews)
            with zipfile.ZipFile(backup) as archive:
                self.assertEqual(set(archive.namelist()), {"manifest.json", "movie-reviews.json", referenced["path"]})
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["format"], BACKUP_FORMAT)
                self.assertEqual(manifest["version"], BACKUP_FORMAT_VERSION)

            target_store = ReviewStore(root / "target")
            target_store.directory.mkdir()
            target_store.prepare()
            plan = target_store.prepare_zip_import(backup)
            restored = target_store.apply_import(plan)
            self.assertEqual(restored, normalize_reviews(reviews))
            self.assertEqual(target_store.resolve_image_path(referenced["path"]).read_bytes(), VALID_PNG)

    def test_json_with_sibling_media_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_store = ReviewStore(root / "source")
            source_store.directory.mkdir()
            image = source_store.import_image(self.write_png(root / "still.png"))
            source_json = source_store.directory / "portable.json"
            source_json.write_bytes(_json_bytes([sample_review(image=image)]))

            target_store = ReviewStore(root / "target")
            target_store.directory.mkdir()
            plan = target_store.prepare_json_import(source_json)
            restored = target_store.apply_import(plan)
            self.assertEqual(restored[0]["image"], image)
            self.assertTrue(target_store.resolve_image_path(image["path"]).exists())

    def test_backup_rejects_path_traversal_missing_media_and_size_bomb(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ReviewStore(root / "data")
            store.directory.mkdir()
            image = store.import_image(self.write_png(root / "still.png"))
            reviews = [sample_review(image=image)]
            manifest = {"format": BACKUP_FORMAT, "version": BACKUP_FORMAT_VERSION, "recordCount": 1}

            traversal = root / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as archive:
                archive.writestr("manifest.json", _json_bytes(manifest))
                archive.writestr("movie-reviews.json", _json_bytes(reviews))
                archive.writestr("../evil.txt", b"evil")
            with self.assertRaisesRegex(DataValidationError, "路径"):
                store.prepare_zip_import(traversal)

            missing = root / "missing.zip"
            store.save([sample_review(title="当前数据")])
            current_before = store.data_path.read_bytes()
            with zipfile.ZipFile(missing, "w") as archive:
                archive.writestr("manifest.json", _json_bytes(manifest))
                archive.writestr("movie-reviews.json", _json_bytes(reviews))
            with self.assertRaisesRegex(DataValidationError, "引用不一致"):
                store.prepare_zip_import(missing)
            self.assertEqual(store.data_path.read_bytes(), current_before)

            corrupt = root / "corrupt.zip"
            corrupt_bytes = VALID_PNG[:-1] + bytes([VALID_PNG[-1] ^ 0xFF])
            with zipfile.ZipFile(corrupt, "w") as archive:
                archive.writestr("manifest.json", _json_bytes(manifest))
                archive.writestr("movie-reviews.json", _json_bytes(reviews))
                archive.writestr(image["path"], corrupt_bytes)
            with self.assertRaisesRegex(DataValidationError, "内容与记录不一致"):
                store.prepare_zip_import(corrupt)
            self.assertEqual(store.data_path.read_bytes(), current_before)

            bomb = root / "bomb.zip"
            with zipfile.ZipFile(bomb, "w") as archive:
                archive.writestr("manifest.json", _json_bytes(manifest))
                archive.writestr("movie-reviews.json", _json_bytes([]))
            with mock.patch("app.MAX_BACKUP_UNCOMPRESSED_BYTES", 10):
                with self.assertRaisesRegex(DataValidationError, "5 GB"):
                    store.prepare_zip_import(bomb)


class SettingsStoreTests(unittest.TestCase):
    def test_round_trip_selected_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "config" / "settings.json")
            settings.save_data_directory(root / "data")
            self.assertEqual(settings.load_data_directory(), (root / "data").resolve())


class DesktopApiTests(unittest.TestCase):
    def test_internal_object_graph_is_private_for_safe_pywebview_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(SettingsStore(Path(directory) / "settings.json"))
            self.assertTrue(vars(api))
            self.assertTrue(all(name.startswith("_") for name in vars(api)))

    def test_bridge_exposes_only_the_intended_methods(self):
        class FakeExposeWindow:
            functions = ()

            def expose(self, *functions):
                self.functions = functions

        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(SettingsStore(Path(directory) / "settings.json"))
            window = FakeExposeWindow()
            expose_desktop_api(window, api)
            self.assertEqual(
                [function.__name__ for function in window.functions],
                [
                    "get_state",
                    "choose_data_directory",
                    "save_reviews",
                    "select_review_image",
                    "get_review_image_info",
                    "get_review_image_chunk",
                    "select_import_file",
                    "apply_import",
                    "export_backup",
                    "open_data_directory",
                    "exit_app",
                ],
            )

    def test_main_passes_drop_binding_arguments_as_one_iterable(self):
        class FakeWindow:
            def expose(self, *_functions):
                pass

        fake_window = FakeWindow()
        start = mock.Mock()
        fake_webview = SimpleNamespace(create_window=mock.Mock(return_value=fake_window), start=start)
        with mock.patch.dict("sys.modules", {"webview": fake_webview}):
            main()

        callback, callback_args = start.call_args.args
        self.assertEqual(callback.__name__, "bind_review_image_drop")
        self.assertIsInstance(callback_args, tuple)
        self.assertEqual(len(callback_args), 2)
        self.assertIs(callback_args[0], fake_window)
        self.assertEqual(start.call_args.kwargs["gui"], "edgechromium")

    def test_choose_data_directory_prepares_and_remembers_selected_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "config" / "settings.json")
            selected = root / "selected"
            selected.mkdir()

            class FakeWindow:
                def create_file_dialog(self, *_args, **_kwargs):
                    return (str(selected),)

            api = DesktopApi(settings)
            api._window = FakeWindow()
            result = api.choose_data_directory()

            self.assertTrue(result["ok"])
            self.assertEqual(result["dataDirectory"], str(selected.resolve()))
            self.assertEqual(settings.load_data_directory(), selected.resolve())
            self.assertTrue((selected / "movie-reviews.json").exists())

    def test_select_review_image_uses_native_result_and_installs_media(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            source = root / "still.png"
            source.write_bytes(VALID_PNG)
            settings = SettingsStore(root / "settings.json")
            settings.save_data_directory(data)

            class FakeWindow:
                def create_file_dialog(self, *_args, **_kwargs):
                    return (str(source),)

            api = DesktopApi(settings)
            api._window = FakeWindow()
            result = api.select_review_image()
            self.assertTrue(result["ok"])
            self.assertEqual(result["image"]["mime"], "image/png")
            self.assertTrue((data / result["image"]["path"]).exists())

    def test_zip_import_preview_token_applies_only_after_confirmation_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_data = root / "source"
            source_data.mkdir()
            source_store = ReviewStore(source_data)
            source_image_path = root / "still.png"
            source_image_path.write_bytes(VALID_PNG)
            image = source_store.import_image(source_image_path)
            reviews = [sample_review(title="待导入", image=image)]
            backup = root / "backup.zip"
            source_store.export_backup(backup, reviews)

            target_data = root / "target"
            target_data.mkdir()
            settings = SettingsStore(root / "settings.json")
            settings.save_data_directory(target_data)

            class FakeWindow:
                def create_file_dialog(self, *_args, **_kwargs):
                    return (str(backup),)

            api = DesktopApi(settings)
            api._window = FakeWindow()
            preview = api.select_import_file()
            self.assertTrue(preview["ok"])
            self.assertFalse((target_data / "movie-reviews.json").exists())

            applied = api.apply_import(preview["importToken"])
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["reviews"][0]["title"], "待导入")
            self.assertTrue((target_data / "movie-reviews.json").exists())
            self.assertTrue((target_data / image["path"]).exists())


class FrontendImageDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parents[1] / "index.html").read_text(encoding="utf-8")

    def test_drop_zone_keeps_click_fallback_and_accessible_status(self):
        self.assertIn('aria-label="主剧照上传区域"', self.html)
        self.assertIn("点击选择，或将一张 JPG、PNG、WebP 拖到这里", self.html)
        self.assertIn('id="selectImageButton"', self.html)
        self.assertIn('aria-live="polite"', self.html)

    def test_drag_events_prevent_navigation_and_manage_visual_state(self):
        for event_name in ("dragenter", "dragover", "dragleave", "drop"):
            self.assertIn(f"picker.addEventListener('{event_name}'", self.html)
        self.assertIn("event.preventDefault()", self.html)
        self.assertIn("is-drag-over", self.html)
        self.assertIn("释放以上传剧照", self.html)
        self.assertIn("正在读取剧照…", self.html)

    def test_native_result_is_token_guarded_and_submit_is_blocked_while_busy(self):
        self.assertIn("requestId !== activeNativeImageDropId", self.html)
        self.assertIn("if (imageImportBusy)", self.html)
        self.assertIn("saveReviewButton", self.html)
        self.assertIn("window.handleNativeImageDropResult", self.html)


if __name__ == "__main__":
    unittest.main()
