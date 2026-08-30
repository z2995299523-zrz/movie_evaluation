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
        result = normalize_reviews([sample_review(categories=["科幻", "悬疑", "科幻"])])
        self.assertEqual(result[0]["title"], "一部电影")
        self.assertEqual(result[0]["tags"], ["剧情", "经典"])
        self.assertEqual(result[0]["rating"], 5)
        self.assertEqual(result[0]["categories"], ["科幻", "悬疑"])
        self.assertNotIn("rewatches", result[0])

    def test_normalizes_repeat_viewing_notes_and_keeps_legacy_records_compatible(self):
        result = normalize_reviews(
            [
                sample_review(
                    rewatches=[
                        {
                            "watchedAt": "2026-08-20T19:30",
                            "feeling": "第二次看更喜欢配乐。",
                            "rating": 4,
                            "createdAt": 1_700_000_100_000,
                        }
                    ]
                )
            ]
        )
        self.assertEqual(
            result[0]["rewatches"],
            [{"watchedAt": "2026-08-20T19:30", "feeling": "第二次看更喜欢配乐。", "createdAt": 1_700_000_100_000, "rating": 4}],
        )

    def test_rejects_invalid_repeat_viewing_note(self):
        with self.assertRaisesRegex(DataValidationError, "观影时间"):
            normalize_reviews([sample_review(rewatches=[{"watchedAt": "2026-02-30T19:30", "feeling": "", "createdAt": 1}])])
        with self.assertRaisesRegex(DataValidationError, "观看记录不是对象"):
            normalize_reviews([sample_review(rewatches=["not a record"])])
        with self.assertRaisesRegex(DataValidationError, "评分"):
            normalize_reviews([sample_review(rewatches=[{"watchedAt": "2026-08-20T19:30", "feeling": "", "rating": 6}])])

    def test_normalizes_private_movie_relationship_fields(self):
        context = {"location": "电影院", "companions": "朋友", "mood": "期待", "lifeStage": "毕业季", "impact": "开始关注摄影", "favoriteScene": "结尾", "favoriteQuote": "一句台词", "recommendTo": "家人", "watchAgain": True}
        result = normalize_reviews([sample_review(personalContext=context)])
        self.assertEqual(result[0]["personalContext"], context)
        with self.assertRaisesRegex(DataValidationError, "再次重看意愿"):
            normalize_reviews([sample_review(personalContext={"watchAgain": "yes"})])

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

    def test_rewatch_adjustment_and_deletion_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory))
            saved = store.save(
                [
                    sample_review(
                        rewatches=[
                            {"watchedAt": "2026-08-20T19:30", "feeling": "第一次重看", "createdAt": 100},
                            {"watchedAt": "2026-08-21T20:00", "feeling": "第二次重看", "createdAt": 200},
                        ]
                    )
                ]
            )

            adjusted_entries = [dict(entry) for entry in saved[0]["rewatches"]]
            adjusted_entries[1]["watchedAt"] = "2026-08-22T21:15"
            adjusted_entries[1]["feeling"] = "调整后的感受"
            adjusted = store.save([{**saved[0], "rewatches": adjusted_entries}])
            self.assertEqual(adjusted[0]["rewatches"][1]["watchedAt"], "2026-08-22T21:15")
            self.assertEqual(adjusted[0]["rewatches"][1]["feeling"], "调整后的感受")
            self.assertEqual(adjusted[0]["rewatches"][1]["createdAt"], 200)

            remaining = store.save([{**adjusted[0], "rewatches": adjusted[0]["rewatches"][:1]}])
            self.assertEqual(len(remaining[0]["rewatches"]), 1)
            self.assertEqual(remaining[0]["rewatches"][0]["feeling"], "第一次重看")

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
            reviews = [
                sample_review(
                    image=referenced,
                    rewatches=[
                        {
                            "watchedAt": "2026-08-20T19:30",
                            "feeling": "重看后留意到了更多细节。",
                            "createdAt": 1_700_000_100_000,
                        }
                    ],
                )
            ]
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
                    "export_annual_report",
                    "export_graph_image",
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

    def test_generated_report_and_graph_exports_write_valid_files(self):
        class FakeWindow:
            def __init__(self, paths):
                self.paths = iter(paths)
            def create_file_dialog(self, *_args, **_kwargs):
                return [str(next(self.paths))]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report.html"
            graph = root / "graph.png"
            api = DesktopApi(SettingsStore(root / "settings.json"))
            api._window = FakeWindow([report, graph])
            fake_webview = SimpleNamespace(FileDialog=SimpleNamespace(SAVE="save"))
            with mock.patch.dict("sys.modules", {"webview": fake_webview}):
                report_result = api.export_annual_report("年度观影报告-2026.html", "<!doctype html><meta charset='utf-8'><h1>报告</h1>")
                graph_result = api.export_graph_image("data:image/png;base64," + base64.b64encode(VALID_PNG).decode("ascii"))
            self.assertTrue(report_result["ok"])
            self.assertIn("报告", report.read_text(encoding="utf-8"))
            self.assertTrue(graph_result["ok"])
            self.assertEqual(graph.read_bytes(), VALID_PNG)

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

    def test_homepage_cards_have_image_visual_and_text_fallback(self):
        self.assertIn("card-visual", self.html)
        self.assertIn("card-image-overlay", self.html)
        self.assertIn("card-rating-chip", self.html)
        self.assertIn("card-fallback-mark", self.html)
        self.assertIn("movie-card ${r.image ? 'has-image' : 'no-image'}", self.html)

    def test_homepage_card_images_are_lazy_loaded_and_cached(self):
        self.assertIn("const cardImageCache = new Map()", self.html)
        self.assertIn("const MAX_CARD_IMAGE_CACHE = 24", self.html)
        self.assertIn("IntersectionObserver", self.html)
        self.assertIn("rootMargin: '280px 0px'", self.html)
        self.assertIn("while (cardImageInFlight < 2", self.html)
        self.assertIn("URL.revokeObjectURL(url)", self.html)

    def test_curation_wall_uses_current_filtered_reviews_and_rating_weights(self):
        self.assertIn('id="galleryButton"', self.html)
        self.assertIn("renderGallery(getVisibleReviews())", self.html)
        self.assertIn("gallery-card.rating-5", self.html)
        self.assertIn("gallery-card.rating-4", self.html)
        self.assertIn("gallery-card.rating-3", self.html)
        self.assertIn("gallery-card.rating-2", self.html)
        self.assertIn("rating-low", self.html)

    def test_curation_wall_focus_and_accessibility_behaviors_are_present(self):
        self.assertIn("gallery-wall.has-focus", self.html)
        self.assertIn("transform: translateY(-2px) scale(1.035)", self.html)
        self.assertIn("setGalleryFocus", self.html)
        self.assertIn("openGalleryDetail", self.html)
        self.assertIn("prefers-reduced-motion", self.html)
        self.assertIn("getElementById('galleryOverlay').classList.contains('active')", self.html)

    def test_curation_wall_has_all_rating_tiers_and_empty_filter_fallback(self):
        self.assertIn("function getGalleryRatingClass", self.html)
        self.assertIn("if (rating >= 5)", self.html)
        self.assertIn("if (rating === 4)", self.html)
        self.assertIn("if (rating === 3)", self.html)
        self.assertIn("if (rating === 2)", self.html)
        self.assertIn("当前筛选没有影评可展示", self.html)
        self.assertIn("clearGalleryFilters", self.html)

    def test_curation_wall_reuses_image_cache_and_handles_keyboard_close(self):
        self.assertIn("galleryImageNodes", self.html)
        self.assertIn("hydrateGalleryImages", self.html)
        self.assertIn("root: stage", self.html)
        self.assertIn("openGalleryDetail(card.dataset.galleryReviewId)", self.html)
        self.assertIn("if (document.getElementById('galleryOverlay').classList.contains('active')) closeGallery();", self.html)

    def test_curation_wall_is_dense_and_bounds_image_memory(self):
        self.assertIn("repeat(auto-fill, minmax(132px, 1fr))", self.html)
        self.assertIn("aspect-ratio: 2 / 3", self.html)
        self.assertIn("content-visibility: auto", self.html)
        self.assertIn("while (cardImageCache.size > MAX_CARD_IMAGE_CACHE)", self.html)
        self.assertIn("resetCachedImageNodes(oldestPath)", self.html)
        self.assertIn("root: stage, rootMargin: '220px 0px'", self.html)
        self.assertIn("requestAnimationFrame(hydrateCardImages)", self.html)
        self.assertNotIn("transform: scale(1.4)", self.html)
        self.assertIn("function renderRelationGraph", self.html)
        self.assertIn("knowledge-graph-canvas", self.html)
        self.assertIn("knowledge-graph-panel", self.html)
        self.assertIn("setGalleryMode('relation')", self.html)
        self.assertIn("function getRelationLabels", self.html)
        self.assertIn("function selectKnowledgeGraphNode", self.html)
        self.assertIn("relations.forEach(label=>edges.push", self.html)
        self.assertIn("shared:getRelationLabels", self.html)
        self.assertIn("通过共同关系关联了哪些电影", self.html)
        self.assertIn("showConnectedMovieName", self.html)
        self.assertIn("selectedNode.kind==='relation'", self.html)
        self.assertIn("c.onwheel", self.html)

    def test_relation_graph_switches_one_dimension_at_a_time(self):
        self.assertIn('id="relationDimension"', self.html)
        self.assertIn('value="category"', self.html)
        self.assertIn('value="director"', self.html)
        self.assertIn('value="year"', self.html)
        self.assertIn('value="rating"', self.html)
        self.assertIn('value="rewatch"', self.html)
        self.assertIn('value="tag"', self.html)
        self.assertIn('value="cast" disabled', self.html)
        self.assertIn("function setRelationDimension", self.html)
        self.assertIn("getWatchMoments(review)", self.html)
        self.assertIn("${getRelationDimensionName()}知识图谱", self.html)

    def test_category_picker_supports_recommended_and_custom_values(self):
        self.assertIn("RECOMMENDED_CATEGORIES", self.html)
        self.assertIn('id="categoryInput"', self.html)
        self.assertIn('id="categoryOptions"', self.html)
        self.assertIn("function addCategoryFromInput", self.html)
        self.assertIn("selectedCategories.length >= 8", self.html)
        self.assertIn("title, director, date, rating, tags, categories, comment", self.html)

    def test_cinematic_archive_theme_is_consistent(self):
        self.assertIn("--bg: #0d0b0c", self.html)
        self.assertIn("--accent: #a44752", self.html)
        self.assertIn("--gold: #d6b06a", self.html)
        self.assertIn("--graph-movie: #6fa7a1", self.html)
        self.assertIn("node.kind==='relation'?'#d6b06a'", self.html)
        self.assertIn("hover?'#f0c674':'#6fa7a1'", self.html)
        for old_color in ("#6366f1", "#818cf8", "#a78bfa", "#67e8f9", "#fbbf24"):
            self.assertNotIn(old_color, self.html.lower())

    def test_repeat_viewing_ui_records_time_feeling_and_derived_count(self):
        self.assertIn('id="rewatchOverlay"', self.html)
        self.assertIn('id="rewatchWatchedAt"', self.html)
        self.assertIn('id="rewatchFeeling"', self.html)
        self.assertIn('data-action="rewatch"', self.html)
        self.assertIn("function getWatchCount", self.html)
        self.assertIn("function handleRewatchSubmit", self.html)
        self.assertIn("currentEntries.push({ watchedAt, feeling, rating, createdAt: Date.now() })", self.html)
        self.assertIn("const watchTotal = reviews.reduce", self.html)
        self.assertIn("/ reviewCount).toFixed(1)", self.html)
        self.assertIn("＋ 记录重看", self.html)
        self.assertIn("记录第 ${currentWatchCount + 1} 次观看", self.html)
        self.assertIn("保存重看记录", self.html)
        self.assertIn("历次观看记录", self.html)
        self.assertIn("rewatchEditIndex", self.html)
        self.assertIn("调整第 ${watchedNumber} 次观看", self.html)
        self.assertIn("function deleteRewatch", self.html)
        self.assertIn("currentEntries.filter((_, index) => index !== rewatchIndex)", self.html)
        self.assertIn("第 ${watchedNumber} 次观看记录已删除", self.html)

    def test_edit_modal_shows_every_watch_record_and_date(self):
        self.assertIn('id="editWatchHistorySection"', self.html)
        self.assertIn('id="editWatchHistory"', self.html)
        self.assertIn("function renderEditWatchHistory", self.html)
        self.assertIn("appendEntry(1, review.date, '', review.rating, true)", self.html)
        self.assertIn("getRewatches(review).forEach", self.html)
        self.assertIn("formatWatchedAt(watchedAt)", self.html)
        self.assertIn("data-edit-initial-watch-date", self.html)
        self.assertIn("独立评分与当次感受", Path("README.md").read_text(encoding="utf-8"))

    def test_rewatch_ratings_render_an_evolution_curve(self):
        self.assertIn('id="rewatchStarSelector"', self.html)
        self.assertIn('id="rewatchRating"', self.html)
        self.assertIn("function renderRatingEvolution", self.html)
        self.assertIn("polyline.setAttribute('stroke','#d6b06a')", self.html)
        self.assertIn("越来越喜欢", self.html)
        self.assertIn("逐渐降温", self.html)
        self.assertIn("评分稳定", self.html)

    def test_today_memories_include_rewatches_and_explain_fallbacks(self):
        self.assertIn('id="memorySpotlight"', self.html)
        self.assertIn("function getViewingEvents", self.html)
        self.assertIn("function getTodayMemoryData", self.html)
        self.assertIn("yearsAgo>0", self.html)
        self.assertIn("getRewatches(review).map", self.html)
        self.assertIn("item.rating>=4&&item.daysAgo>=365", self.html)
        self.assertIn("今日重看建议", self.html)
        self.assertIn("记录重看", self.html)

    def test_taste_profile_is_local_deterministic_and_explained(self):
        self.assertIn('onclick="openTasteProfile()"', self.html)
        self.assertIn('id="tasteOverlay"', self.html)
        self.assertIn("function buildTasteProfile", self.html)
        self.assertIn("function getLatestReviewRating", self.html)
        self.assertIn("data.rewatched/reviews.length*100", self.html)
        self.assertIn("item.rated>=2", self.html)
        self.assertIn("legacyCategoryCount", self.html)
        self.assertIn("规则固定且可解释", self.html)
        self.assertIn("closeTasteProfile();openDetail", self.html)

    def test_archive_extensions_cover_context_reports_health_merge_and_graph_export(self):
        self.assertIn('id="contextLocation"', self.html)
        self.assertIn("function renderPersonalContext", self.html)
        self.assertIn("function buildAnnualReport", self.html)
        self.assertIn("export_annual_report", self.html)
        self.assertIn("function buildArchiveHealth", self.html)
        self.assertIn("重看早于初看", self.html)
        self.assertIn("function mergeCategory", self.html)
        self.assertIn("保存前会自动备份", self.html)
        self.assertIn("function exportKnowledgeGraph", self.html)
        self.assertIn("export_graph_image", self.html)

    def test_detail_dialog_navigates_previous_and_next_in_visible_order(self):
        self.assertIn('id="detailPrevious"', self.html)
        self.assertIn('id="detailNext"', self.html)
        self.assertIn("function getDetailSequence", self.html)
        self.assertIn("function updateDetailNavigation", self.html)
        self.assertIn("function navigateDetail", self.html)
        self.assertIn("visible.some(item=>item.id===detailReviewId)", self.html)
        self.assertIn("event.key==='ArrowLeft'||event.key==='ArrowRight'", self.html)
        self.assertIn("已经是第一条", self.html)
        self.assertIn("已经是最后一条", self.html)


if __name__ == "__main__":
    unittest.main()
