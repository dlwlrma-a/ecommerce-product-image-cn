import argparse
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("ecommerce_image_studio.py")
SPEC = importlib.util.spec_from_file_location("ecommerce_image_studio", MODULE_PATH)
assert SPEC and SPEC.loader
studio = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(studio)


def plan_args(**overrides):
    values = {
        "product_name": "便携咖啡杯",
        "category": "饮具",
        "description": "磨砂黑杯身，不锈钢内胆，黑色旋盖",
        "selling_point": ["保温锁温", "单手开合"],
        "dimensions": "350 mL",
        "reference_url": ["https://assets.example.com/cup.jpg"],
        "reference_file": None,
        "platform": "京东",
        "tone": "clean and premium",
        "language": "简体中文",
        "audience": None,
        "scene": None,
        "copy": None,
        "types": "white_bg,hero,feature",
        "size": "1:1",
        "quality": "medium",
        "text_mode": "render",
        "points_per_image": 10,
        "base_url": studio.DEFAULT_BASE_URL,
        "output": "plan.json",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class PlanTests(unittest.TestCase):
    def test_plan_contains_product_lock_and_cost(self):
        plan = studio.create_plan(plan_args())
        self.assertEqual(3, len(plan["jobs"]))
        self.assertEqual(30, plan["estimated_points"])
        prompt = plan["jobs"][0]["request"]["prompt"]
        self.assertIn("Preserve the product exactly", prompt)
        self.assertIn("pure white #FFFFFF", prompt)

    def test_reserve_text_mode_avoids_rendered_copy(self):
        plan = studio.create_plan(plan_args(types="hero", text_mode="reserve"))
        prompt = plan["jobs"][0]["request"]["prompt"]
        self.assertIn("negative space", prompt)
        self.assertIn("Do not render any text", prompt)

    def test_render_text_mode_uses_only_verified_copy(self):
        plan = studio.create_plan(plan_args(types="feature", text_mode="render"))
        prompt = plan["jobs"][0]["request"]["prompt"]
        self.assertIn("保温锁温 / 单手开合", prompt)
        self.assertIn("Do not add any other", prompt)

    def test_render_mode_assigns_copy_by_image_type(self):
        plan = studio.create_plan(plan_args(types="white_bg,hero,lifestyle,feature,detail,size_reference"))
        copy_by_type = {job["type"]: job["copy"] for job in plan["jobs"]}
        self.assertEqual([], copy_by_type["white_bg"])
        self.assertEqual(["便携咖啡杯", "保温锁温", "单手开合"], copy_by_type["hero"])
        self.assertEqual(["便携咖啡杯", "保温锁温"], copy_by_type["lifestyle"])
        self.assertEqual(["保温锁温", "单手开合"], copy_by_type["feature"])
        self.assertEqual(["便携咖啡杯", "350 mL"], copy_by_type["size_reference"])

    def test_requested_copy_language_is_in_prompt(self):
        plan = studio.create_plan(plan_args(types="hero", language="English"))
        self.assertEqual("English", plan["language"])
        self.assertIn("visible copy in English", plan["jobs"][0]["request"]["prompt"])

    def test_language_and_image_types_must_be_chosen(self):
        args = studio.build_parser().parse_args(["plan", "--output", "plan.json"])
        with self.assertRaises(studio.StudioError):
            studio.create_plan(plan_args(types="hero", language=args.language, text_mode=args.text_mode))
        with self.assertRaises(studio.StudioError):
            studio.create_plan(plan_args(types=None, language="简体中文", text_mode=args.text_mode))

    def test_platform_and_image_size_must_be_chosen(self):
        with self.assertRaises(studio.StudioError):
            studio.create_plan(plan_args(platform=None))
        with self.assertRaises(studio.StudioError):
            studio.create_plan(plan_args(size=None))

    def test_approved_copy_can_override_automatic_copy(self):
        plan = studio.create_plan(
            plan_args(types="hero", copy=["hero=轻盈随行", "hero=全天安心锁温"])
        )
        self.assertEqual(["轻盈随行", "全天安心锁温"], plan["jobs"][0]["copy"])
        prompt = plan["jobs"][0]["request"]["prompt"]
        self.assertIn("轻盈随行 / 全天安心锁温", prompt)

    def test_approved_copy_must_match_selected_rendered_types(self):
        with self.assertRaises(studio.StudioError):
            studio.create_plan(plan_args(types="feature", copy=["hero=轻盈随行"]))
        with self.assertRaises(studio.StudioError):
            studio.create_plan(plan_args(types="hero", text_mode="reserve", copy=["hero=轻盈随行"]))

    def test_audience_and_scene_are_added_to_relevant_prompt(self):
        plan = studio.create_plan(
            plan_args(types="lifestyle", audience="城市通勤人群", scene="早晨地铁通勤")
        )
        prompt = plan["jobs"][0]["request"]["prompt"]
        self.assertIn("Target audience: 城市通勤人群", prompt)
        self.assertIn("Requested use scene: 早晨地铁通勤", prompt)

    def test_rejects_more_than_three_references(self):
        references = ["https://example.com/%d.jpg" % index for index in range(4)]
        with self.assertRaises(studio.StudioError):
            studio.create_plan(plan_args(reference_url=references))

    def test_rejects_insecure_reference(self):
        with self.assertRaises(studio.StudioError):
            studio.validate_reference_url("http://example.com/product.jpg")

    def test_plan_accepts_local_reference_file(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "product.jpg"
            image_path.write_bytes(b"\xff\xd8\xff" + b"test-image")
            plan = studio.create_plan(plan_args(reference_url=[], reference_file=[str(image_path)]))
        self.assertEqual(1, len(plan["reference_files"]))
        self.assertEqual("image/jpeg", plan["reference_files"][0]["mime_type"])
        self.assertEqual([], plan["jobs"][0]["request"]["image"])
        self.assertIn("Preserve the product exactly", plan["jobs"][0]["request"]["prompt"])

    def test_rejects_reference_file_with_mismatched_content(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "product.png"
            image_path.write_bytes(b"\xff\xd8\xff" + b"not-a-png")
            with self.assertRaises(studio.StudioError):
                studio.create_plan(plan_args(reference_url=[], reference_file=[str(image_path)]))

    def test_combined_local_and_remote_references_are_limited(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "product.jpg"
            image_path.write_bytes(b"\xff\xd8\xff" + b"test-image")
            with self.assertRaises(studio.StudioError):
                studio.create_plan(
                    plan_args(
                        reference_url=["https://example.com/%d.jpg" % index for index in range(3)],
                        reference_file=[str(image_path)],
                    )
                )

    def test_size_accepts_documented_ratios_and_custom_dimensions(self):
        self.assertEqual("3:4", studio.validate_size("3:4"))
        self.assertEqual("1200x1600", studio.validate_size("1200x1600"))
        with self.assertRaises(studio.StudioError):
            studio.validate_size("100x100")

    def test_product_brief_can_supply_reusable_values(self):
        with tempfile.TemporaryDirectory() as directory:
            brief_path = Path(directory) / "brief.json"
            studio.write_json_atomic(
                brief_path,
                {
                    "product_name": "测试商品",
                    "category": "家居",
                    "description": "白色方形产品",
                    "selling_points": ["容易清洁"],
                    "reference_urls": ["https://example.com/product.jpg"],
                    "platform": "Amazon US",
                    "types": "white_bg,lifestyle",
                    "size": "3:4",
                    "quality": "high",
                    "language": "English",
                    "text_mode": "reserve",
                },
            )
            args = plan_args(
                brief=str(brief_path), product_name=None, category=None, description=None,
                selling_point=None, dimensions=None, reference_url=None, platform=None,
                tone=None, language=None, types=None, size=None, quality=None, text_mode=None,
            )
            plan = studio.create_plan(args)
        self.assertEqual("测试商品", plan["product"]["name"])
        self.assertEqual(2, len(plan["jobs"]))
        self.assertEqual("3:4", plan["jobs"][0]["request"]["size"])
        self.assertEqual("high", plan["jobs"][0]["request"]["quality"])
        self.assertEqual("English", plan["language"])
        self.assertEqual("reserve", plan["text_mode"])


class ResponseTests(unittest.TestCase):
    def test_task_id_prefers_top_level_task_id(self):
        response = {"id": "generic", "task_id": "task-123", "data": {"id": "nested"}}
        self.assertEqual("task-123", studio.extract_task_id(response))

    def test_output_urls_ignore_echoed_request(self):
        response = {
            "request": {"image": [{"url": "https://example.com/input.jpg"}]},
            "result": {"images": [{"url": "https://cdn.example.com/output.png"}]},
        }
        self.assertEqual(["https://cdn.example.com/output.png"], studio.extract_output_urls(response))

    def test_task_state_is_normalized(self):
        self.assertEqual("succeeded", studio.extract_task_state({"data": {"status": "SUCCEEDED"}}))

    def test_upload_url_accepts_documented_response(self):
        self.assertEqual(
            "https://cdn.example.com/reference.jpg",
            studio.extract_upload_url({"url": "https://cdn.example.com/reference.jpg"}),
        )

    def test_file_upload_uses_multipart_file_field(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "product.jpg"
            image_path.write_bytes(b"\xff\xd8\xff" + b"test-image")
            descriptor = studio.describe_reference_file(str(image_path))
            captured = {}

            class FakeOpener:
                def open(self, request, timeout):
                    captured["request"] = request
                    captured["timeout"] = timeout
                    return io.BytesIO(b'{"url":"https://cdn.example.com/reference.jpg"}')

            original_build_opener = studio.urllib.request.build_opener
            try:
                studio.urllib.request.build_opener = lambda *handlers: FakeOpener()
                response = studio.request_file_upload(
                    "https://token.qixuai.com/v1/images/uploads", "test-key", descriptor, 30
                )
            finally:
                studio.urllib.request.build_opener = original_build_opener
        request = captured["request"]
        self.assertEqual("https://token.qixuai.com/v1/images/uploads", request.full_url)
        self.assertIn("multipart/form-data; boundary=", request.get_header("Content-type"))
        self.assertIn(b'name="file"', request.data)
        self.assertIn(b'filename="reference.jpg"', request.data)
        self.assertEqual("https://cdn.example.com/reference.jpg", response["url"])


class WorkflowTests(unittest.TestCase):
    def test_generate_uploads_local_reference_once_and_uses_returned_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "product.jpg"
            image_path.write_bytes(b"\xff\xd8\xff" + b"test-image")
            plan = studio.create_plan(
                plan_args(types="white_bg", reference_url=[], reference_file=[str(image_path)])
            )
            plan_path = root / "plan.json"
            output_dir = root / "output"
            studio.write_json_atomic(plan_path, plan)
            upload_calls = []
            generation_payloads = []
            original_upload = studio.request_file_upload
            original_request = studio.request_json
            original_key = os.environ.get("QIXUAI_API_KEY")
            try:
                os.environ["QIXUAI_API_KEY"] = "test-key"

                def fake_upload(url, api_key, descriptor, timeout):
                    upload_calls.append(descriptor["path"])
                    return {"url": "https://cdn.example.com/uploaded.jpg"}

                def fake_request(method, url, api_key, payload, timeout):
                    generation_payloads.append(payload)
                    return {"task_id": "task-1"}

                studio.request_file_upload = fake_upload
                studio.request_json = fake_request
                args = argparse.Namespace(
                    plan=str(plan_path), output_dir=str(output_dir), max_points=10,
                    execute=True, wait=False, poll_interval=5, wait_timeout=900,
                    api_key_env="QIXUAI_API_KEY", confirm_live_run=True, timeout=30,
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    studio.command_generate(args)
                    studio.command_generate(args)
            finally:
                studio.request_file_upload = original_upload
                studio.request_json = original_request
                if original_key is None:
                    os.environ.pop("QIXUAI_API_KEY", None)
                else:
                    os.environ["QIXUAI_API_KEY"] = original_key
            manifest = studio.load_json(output_dir / "generation_manifest.json")
            self.assertEqual(1, len(upload_calls))
            self.assertEqual(1, len(generation_payloads))
            self.assertEqual(["https://cdn.example.com/uploaded.jpg"], generation_payloads[0]["image"])
            self.assertEqual("uploaded", manifest["reference_uploads"][0]["status"])

    def test_upload_rejects_file_changed_after_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "product.jpg"
            image_path.write_bytes(b"\xff\xd8\xff" + b"first")
            descriptor = studio.describe_reference_file(str(image_path))
            image_path.write_bytes(b"\xff\xd8\xff" + b"changed")
            with self.assertRaises(studio.StudioError):
                studio.request_file_upload(
                    "https://token.qixuai.com/v1/images/uploads", "test-key", descriptor, 30
                )

    def test_generate_dry_run_does_not_create_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            studio.write_json_atomic(plan_path, studio.create_plan(plan_args()))
            output_dir = root / "output"
            args = argparse.Namespace(
                plan=str(plan_path),
                output_dir=str(output_dir),
                max_points=None,
                execute=False,
                wait=False,
                poll_interval=5,
                wait_timeout=900,
                api_key_env="QIXUAI_API_KEY",
                confirm_live_run=False,
                timeout=30,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                studio.command_generate(args)
            self.assertFalse(output_dir.exists())

    def test_generate_requires_explicit_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            studio.write_json_atomic(plan_path, studio.create_plan(plan_args()))
            args = argparse.Namespace(
                plan=str(plan_path), output_dir=str(root / "output"), max_points=20,
                execute=True, wait=False, poll_interval=5, wait_timeout=900,
                api_key_env="QIXUAI_API_KEY", confirm_live_run=True, timeout=30,
            )
            with self.assertRaises(studio.StudioError):
                studio.command_generate(args)

    def test_existing_task_ids_are_not_resubmitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = studio.create_plan(plan_args(types="white_bg,hero"))
            plan_path = root / "plan.json"
            output_dir = root / "output"
            manifest_path = output_dir / "generation_manifest.json"
            studio.write_json_atomic(plan_path, plan)
            output_dir.mkdir()
            manifest = studio.initial_manifest(plan, plan_path.resolve())
            manifest["jobs"][0]["task_id"] = "existing-task"
            manifest["jobs"][0]["status"] = "submitted"
            studio.write_json_atomic(manifest_path, manifest)
            calls = []
            original_request = studio.request_json
            original_key = os.environ.get("QIXUAI_API_KEY")
            try:
                os.environ["QIXUAI_API_KEY"] = "test-key"

                def fake_request(method, url, api_key, payload, timeout):
                    calls.append(payload)
                    return {"task_id": "new-task"}

                studio.request_json = fake_request
                args = argparse.Namespace(
                    plan=str(plan_path), output_dir=str(output_dir), max_points=20,
                    execute=True, wait=False, poll_interval=5, wait_timeout=900,
                    api_key_env="QIXUAI_API_KEY", confirm_live_run=True, timeout=30,
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    studio.command_generate(args)
            finally:
                studio.request_json = original_request
                if original_key is None:
                    os.environ.pop("QIXUAI_API_KEY", None)
                else:
                    os.environ["QIXUAI_API_KEY"] = original_key
            updated = studio.load_json(manifest_path)
            self.assertEqual(1, len(calls))
            self.assertEqual("existing-task", updated["jobs"][0]["task_id"])
            self.assertEqual("new-task", updated["jobs"][1]["task_id"])

    def test_collect_downloads_successful_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "generation_manifest.json"
            manifest = {
                "schema_version": 1,
                "base_url": studio.DEFAULT_BASE_URL,
                "jobs": [{
                    "job_id": "img-01", "type": "white_bg", "status": "submitted",
                    "task_id": "task-1", "output_urls": [], "files": [],
                }],
            }
            studio.write_json_atomic(manifest_path, manifest)
            original_request = studio.request_json
            original_download = studio.download_image
            try:
                studio.request_json = lambda *args, **kwargs: {
                    "status": "succeeded",
                    "result": {"url": "https://cdn.example.com/result.png"},
                }

                def fake_download(url, destination, timeout):
                    path = destination.with_suffix(".png")
                    path.write_bytes(b"fake-image")
                    return path

                studio.download_image = fake_download
                result = studio.collect_manifest(manifest_path, root, "test-key", 30, 5, 30, False)
            finally:
                studio.request_json = original_request
                studio.download_image = original_download
            updated = studio.load_json(manifest_path)
            self.assertEqual(0, result["unfinished"])
            self.assertEqual("succeeded", updated["jobs"][0]["status"])
            self.assertTrue(Path(updated["jobs"][0]["files"][0]).exists())

    def test_manifest_preserves_reference_urls_for_result_filtering(self):
        plan = studio.create_plan(plan_args())
        manifest = studio.initial_manifest(plan, Path("plan.json"))
        self.assertEqual(plan["reference_images"], manifest["reference_images"])

    def test_plan_fingerprint_is_stable(self):
        plan = studio.create_plan(plan_args())
        self.assertEqual(studio.plan_fingerprint(plan), studio.plan_fingerprint(json.loads(json.dumps(plan))))

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is optional")
    def test_white_background_audit_measures_corners(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "white.png"
            Image.new("RGB", (800, 800), "white").save(path)
            result = studio.inspect_with_pillow(path, True)
        self.assertEqual(800, result["width"])
        self.assertEqual(255.0, result["corner_white_score"])

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is optional")
    def test_audit_command_writes_report(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "01-white_bg.png"
            Image.new("RGB", (800, 800), "white").save(image_path)
            plan = studio.create_plan(plan_args(types="white_bg"))
            plan_path = root / "plan.json"
            manifest_path = root / "manifest.json"
            report_path = root / "audit.json"
            studio.write_json_atomic(plan_path, plan)
            studio.write_json_atomic(
                manifest_path,
                {
                    "jobs": [{
                        "job_id": "img-01", "type": "white_bg", "status": "succeeded",
                        "files": [str(image_path)],
                    }]
                },
            )
            args = argparse.Namespace(
                plan=str(plan_path), manifest=str(manifest_path), output=str(report_path),
                min_dimension=768, white_threshold=247.0,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                studio.command_audit(args)
            report = studio.load_json(report_path)
        self.assertTrue(report["automatic_checks_passed"])
        self.assertEqual("简体中文", report["language"])
        self.assertEqual([], report["files"][0]["expected_copy"])
        self.assertEqual(5, len(report["manual_review"]))


if __name__ == "__main__":
    unittest.main()
