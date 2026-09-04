#!/usr/bin/env python3
"""Plan, generate, collect, and audit e-commerce product image sets."""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "1.0.0"
DEFAULT_BASE_URL = "https://token.qixuai.com/v1"
DEFAULT_MODEL = "Qx-Image"
MAX_REFERENCE_IMAGES = 3
MAX_PROMPT_CHARS = 5000
MAX_RESPONSE_BYTES = 5_000_000
MAX_IMAGE_BYTES = 30_000_000
TERMINAL_STATES = {"succeeded", "success", "completed", "error", "failed", "cancelled", "canceled", "result_missing"}
SUCCESS_STATES = {"succeeded", "success", "completed"}
TYPE_ORDER = ("white_bg", "hero", "lifestyle", "feature", "detail", "size_reference")
TYPE_LABELS = {
    "white_bg": "白底主图",
    "hero": "氛围首图",
    "lifestyle": "场景使用图",
    "feature": "核心卖点图",
    "detail": "材质细节图",
    "size_reference": "尺寸参照图",
}
TYPE_INSTRUCTIONS = {
    "white_bg": (
        "Create a clean marketplace catalog image on a true pure white #FFFFFF background. "
        "Show the complete product centered with generous margins, a natural contact shadow, "
        "front three-quarter view, even studio lighting, and no props."
    ),
    "hero": (
        "Create a premium e-commerce hero image with a restrained brand-appropriate set, clear visual hierarchy, "
        "commercial studio lighting, realistic shadows, and intentional negative space for a headline."
    ),
    "lifestyle": (
        "Place the exact product in a realistic use scene appropriate for its category and target customer. "
        "Show a plausible scale and interaction, natural lighting, uncluttered composition, and clear product focus."
    ),
    "feature": (
        "Create a benefit-led product image that visually demonstrates the supplied selling points without inventing claims. "
        "Use a clean editorial composition with restrained callout areas and clear negative space."
    ),
    "detail": (
        "Create a macro detail product photograph emphasizing the real material, finish, seams, controls, texture, "
        "or craftsmanship visible in the references. Keep the detail physically consistent with the full product."
    ),
    "size_reference": (
        "Create a scale-reference product image using familiar neutral objects or a natural human interaction. "
        "Do not print measurements unless exact verified dimensions were supplied. Keep perspective physically plausible."
    ),
}


class StudioError(RuntimeError):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)


class SafeImageRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            raise urllib.error.HTTPError(req.full_url, code, "Unsafe image redirect refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    temporary.replace(path)


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StudioError("Cannot read JSON file %s: %s" % (path, exc)) from exc
    if not isinstance(value, dict):
        raise StudioError("Expected a JSON object in %s" % path)
    return value


def plan_fingerprint(plan: Dict[str, Any]) -> str:
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" and not (parsed.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}):
        raise StudioError("Base URL must use HTTPS, except for localhost development")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise StudioError("Base URL must not contain credentials, query parameters, or fragments")
    path = parsed.path.rstrip("/") or "/v1"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def validate_reference_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise StudioError("Reference images for direct API use must be public HTTPS URLs without credentials")
    return value.strip()


def validate_size(value: str) -> str:
    value = value.strip().lower()
    if value in {"1:1", "16:9", "9:16", "3:2", "2:3", "4:3", "3:4"}:
        return value
    match = re.fullmatch(r"(\d{2,5})x(\d{2,5})", value)
    if not match:
        raise StudioError("Size must be a supported ratio or custom WIDTHxHEIGHT")
    width, height = int(match.group(1)), int(match.group(2))
    if not 256 <= width <= 8192 or not 256 <= height <= 8192:
        raise StudioError("Custom image dimensions must be between 256 and 8192 pixels")
    return value


def bounded_text(value: Optional[str], label: str, maximum: int) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    if len(cleaned) > maximum:
        raise StudioError("%s must be at most %d characters" % (label, maximum))
    return cleaned


def parse_types(value: str) -> List[str]:
    requested = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in requested if item not in TYPE_ORDER]
    if unknown:
        raise StudioError("Unknown image types: %s" % ", ".join(unknown))
    if not requested:
        raise StudioError("At least one image type is required")
    if len(set(requested)) != len(requested):
        raise StudioError("Image types must not be repeated")
    return requested


def product_lock(product: Dict[str, Any], has_references: bool) -> str:
    parts = [
        "The product is %s in category %s." % (product["name"], product["category"]),
    ]
    if product.get("description"):
        parts.append("Verified appearance and construction: %s." % product["description"])
    if has_references:
        parts.append(
            "The reference images show the same SKU. Preserve the product exactly: silhouette, proportions, color, "
            "material, surface finish, components, label placement, logo, packaging, and all visible text. "
            "Do not redesign, simplify, add, remove, mirror, or hallucinate product details."
        )
    else:
        parts.append("Do not invent logos, labels, certifications, packaging text, accessories, or technical details.")
    return " ".join(parts)


def build_prompt(
    image_type: str,
    product: Dict[str, Any],
    platform: str,
    tone: str,
    text_mode: str,
    has_references: bool,
) -> str:
    selling_points = product.get("selling_points") or []
    exact_dimensions = product.get("dimensions") or ""
    prompt = [
        "Professional e-commerce product photography for %s." % platform,
        product_lock(product, has_references),
        TYPE_INSTRUCTIONS[image_type],
        "Visual direction: %s." % tone,
    ]
    if selling_points:
        prompt.append("Only verified selling points: %s." % "; ".join(selling_points))
    if exact_dimensions:
        prompt.append("Verified product dimensions: %s." % exact_dimensions)
    if text_mode == "render" and selling_points and image_type in {"hero", "feature", "size_reference"}:
        short_copy = [point[:18] for point in selling_points[:3]]
        prompt.append(
            "Render only this exact Simplified Chinese copy, spelled exactly and legibly: %s. "
            "Do not add any other words, numbers, badges, or claims." % " / ".join(short_copy)
        )
    elif text_mode == "reserve" and image_type in {"hero", "feature", "size_reference"}:
        prompt.append("Leave clean negative space for later copy layout. Do not render any text, letters, numbers, or badges.")
    else:
        prompt.append("No text, letters, numbers, badges, watermarks, borders, or marketplace UI.")
    prompt.append(
        "Photorealistic, commercially usable composition, accurate perspective, realistic materials, clean edges, "
        "natural shadows, no duplicate product, no floating parts, no visual artifacts."
    )
    result = " ".join(prompt)
    if len(result) > MAX_PROMPT_CHARS:
        raise StudioError("Generated prompt exceeds the Qx-Image 5000-character limit")
    return result


def create_plan(args: argparse.Namespace) -> Dict[str, Any]:
    brief = load_json(Path(args.brief)) if getattr(args, "brief", None) else {}
    raw_references = args.reference_url if args.reference_url is not None else brief.get("reference_urls", [])
    if not isinstance(raw_references, list):
        raise StudioError("reference_urls in the product brief must be an array")
    references = [validate_reference_url(str(value)) for value in raw_references]
    if len(references) > MAX_REFERENCE_IMAGES:
        raise StudioError("Qx-Image accepts at most %d reference images" % MAX_REFERENCE_IMAGES)
    raw_selling_points = args.selling_point if args.selling_point is not None else brief.get("selling_points", [])
    if not isinstance(raw_selling_points, list):
        raise StudioError("selling_points in the product brief must be an array")
    product = {
        "name": bounded_text(args.product_name or brief.get("product_name"), "Product name", 120),
        "category": bounded_text(args.category or brief.get("category"), "Category", 120),
        "description": bounded_text(args.description or brief.get("description"), "Description", 1200),
        "selling_points": [bounded_text(str(value), "Selling point", 160) for value in raw_selling_points],
        "dimensions": bounded_text(args.dimensions or brief.get("dimensions"), "Dimensions", 160),
    }
    if not product["name"] or not product["category"]:
        raise StudioError("Product name and category are required")
    if not (references or product["description"]):
        raise StudioError("Provide at least one reference URL or a verified product description")
    image_types = parse_types(args.types or str(brief.get("types") or ",".join(TYPE_ORDER)))
    if not 1 <= args.points_per_image <= 10000:
        raise StudioError("Points per image estimate must be between 1 and 10000")
    size = validate_size(args.size or str(brief.get("size") or "1:1"))
    platform = bounded_text(args.platform or brief.get("platform"), "Platform", 80) or "通用电商平台"
    tone = bounded_text(args.tone or brief.get("tone"), "Visual tone", 240) or "clean, modern, credible, conversion-focused"
    quality = args.quality or str(brief.get("quality") or "medium")
    if quality not in {"low", "medium", "high"}:
        raise StudioError("Quality must be low, medium, or high")
    jobs = []
    for position, image_type in enumerate(image_types, 1):
        jobs.append(
            {
                "job_id": "img-%02d" % position,
                "index": position,
                "type": image_type,
                "label": TYPE_LABELS[image_type],
                "copy": product["selling_points"][:3] if image_type in {"hero", "feature", "size_reference"} else [],
                "request": {
                    "model": DEFAULT_MODEL,
                    "prompt": build_prompt(image_type, product, platform, tone, args.text_mode, bool(references)),
                    "size": size,
                    "quality": quality,
                    "image": references,
                },
            }
        )
    return {
        "schema_version": 1,
        "generator_version": VERSION,
        "created_at": now_iso(),
        "provider": "算点边界",
        "base_url": validate_base_url(args.base_url),
        "model": DEFAULT_MODEL,
        "platform": platform,
        "text_mode": args.text_mode,
        "product": product,
        "reference_images": references,
        "points_per_image_estimate": args.points_per_image,
        "estimated_points": args.points_per_image * len(jobs),
        "jobs": jobs,
        "manual_review_required": True,
    }


def command_plan(args: argparse.Namespace) -> None:
    plan = create_plan(args)
    output = Path(args.output).resolve()
    write_json_atomic(output, plan)
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(output),
                "images": len(plan["jobs"]),
                "estimated_points": plan["estimated_points"],
                "reference_images": len(plan["reference_images"]),
            },
            ensure_ascii=False,
        )
    )


def api_endpoint(base_url: str, path: str) -> str:
    return validate_base_url(base_url).rstrip("/") + "/" + path.lstrip("/")


def read_limited(response: Any, maximum: int) -> bytes:
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise StudioError("Remote response exceeded the allowed size")
    return data


def request_json(method: str, url: str, api_key: str, payload: Optional[Dict[str, Any]], timeout: int) -> Dict[str, Any]:
    headers = {"Authorization": "Bearer " + api_key, "Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            data = read_limited(response, MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        detail = exc.read(1200).decode("utf-8", errors="replace")
        raise StudioError("API request failed with HTTP %d: %s" % (exc.code, detail[:1200])) from exc
    except urllib.error.URLError as exc:
        raise StudioError("API request failed: %s" % exc.reason) from exc
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StudioError("API returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise StudioError("API returned a non-object JSON response")
    return value


def nested_values(value: Any, keys: Iterable[str]) -> Iterable[Any]:
    key_set = set(keys)
    if isinstance(value, dict):
        for key, item in value.items():
            if key in key_set:
                yield item
            yield from nested_values(item, key_set)
    elif isinstance(value, list):
        for item in value:
            yield from nested_values(item, key_set)


def extract_task_id(response: Dict[str, Any]) -> Optional[str]:
    preferred = [response.get("task_id"), response.get("taskId"), response.get("id")]
    data = response.get("data")
    if isinstance(data, dict):
        preferred.extend([data.get("task_id"), data.get("taskId"), data.get("id")])
    for value in preferred:
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    for value in nested_values(response, {"task_id", "taskId"}):
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return None


def extract_task_state(response: Dict[str, Any]) -> str:
    for value in nested_values(response, {"state", "status"}):
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return "unknown"


def extract_output_urls(response: Dict[str, Any]) -> List[str]:
    result = []

    def walk(value: Any, blocked: bool = False) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child_blocked = blocked or key.lower() in {"input", "request", "request_body", "parameters"}
                if not child_blocked and key in {"url", "image_url", "output_url"} and isinstance(item, str):
                    parsed = urllib.parse.urlsplit(item)
                    if parsed.scheme == "https" and parsed.hostname and item not in result:
                        result.append(item)
                if not child_blocked and key in {"output", "result"} and isinstance(item, str):
                    parsed = urllib.parse.urlsplit(item)
                    if parsed.scheme == "https" and parsed.hostname and item not in result:
                        result.append(item)
                walk(item, child_blocked)
        elif isinstance(value, list):
            for item in value:
                walk(item, blocked)

    walk(response)
    return result


def require_api_key(environment_name: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", environment_name):
        raise StudioError("Invalid API key environment variable name")
    value = os.environ.get(environment_name, "").strip()
    if not value:
        raise StudioError("API key environment variable is not set: %s" % environment_name)
    return value


def initial_manifest(plan: Dict[str, Any], plan_path: Path) -> Dict[str, Any]:
    rows = []
    for job in plan["jobs"]:
        rows.append(
            {
                "job_id": job["job_id"],
                "type": job["type"],
                "label": job["label"],
                "status": "not_submitted",
                "task_id": None,
                "submitted_at": None,
                "updated_at": None,
                "output_urls": [],
                "files": [],
            }
        )
    return {
        "schema_version": 1,
        "generator_version": VERSION,
        "plan": str(plan_path),
        "plan_sha256": plan_fingerprint(plan),
        "base_url": plan["base_url"],
        "model": plan["model"],
        "reference_images": plan.get("reference_images") or [],
        "created_at": now_iso(),
        "jobs": rows,
    }


def load_or_create_manifest(path: Path, plan: Dict[str, Any], plan_path: Path) -> Dict[str, Any]:
    if path.exists():
        manifest = load_json(path)
        if manifest.get("plan_sha256") != plan_fingerprint(plan):
            raise StudioError("Existing manifest belongs to a different plan")
        return manifest
    return initial_manifest(plan, plan_path)


def command_generate(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve()
    plan = load_json(plan_path)
    jobs = plan.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise StudioError("Generation plan contains no jobs")
    estimate = int(plan.get("estimated_points") or len(jobs) * int(plan.get("points_per_image_estimate") or 10))
    output_dir = Path(args.output_dir).resolve()
    manifest_path = output_dir / "generation_manifest.json"
    preview = {
        "mode": "dry-run" if not args.execute else "execute",
        "endpoint": api_endpoint(str(plan.get("base_url") or DEFAULT_BASE_URL), "images/generations?async=true"),
        "model": plan.get("model"),
        "images": len(jobs),
        "estimated_points": estimate,
        "manifest": str(manifest_path),
    }
    if not args.execute:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return
    if not args.confirm_live_run:
        raise StudioError("Live generation requires --confirm-live-run after reviewing the endpoint and estimated points")
    if args.max_points is None or args.max_points < estimate:
        raise StudioError("Set --max-points to at least the current estimate (%d)" % estimate)
    api_key = require_api_key(args.api_key_env)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_or_create_manifest(manifest_path, plan, plan_path)
    manifest_rows = {row["job_id"]: row for row in manifest["jobs"]}
    endpoint = api_endpoint(str(plan.get("base_url") or DEFAULT_BASE_URL), "images/generations?async=true")
    submitted = 0
    for job in jobs:
        row = manifest_rows.get(job["job_id"])
        if row is None:
            raise StudioError("Manifest is missing job %s" % job["job_id"])
        if row.get("task_id") or row.get("status") != "not_submitted":
            continue
        try:
            response = request_json("POST", endpoint, api_key, job["request"], args.timeout)
        except StudioError:
            row["status"] = "submit_unknown"
            row["updated_at"] = now_iso()
            write_json_atomic(manifest_path, manifest)
            raise StudioError(
                "Submission outcome is unknown. Manifest was saved; do not resubmit this job until usage is checked."
            )
        task_id = extract_task_id(response)
        row["submitted_at"] = now_iso()
        row["updated_at"] = row["submitted_at"]
        if not task_id:
            row["status"] = "submit_unknown"
            row["submit_response"] = response
            write_json_atomic(manifest_path, manifest)
            raise StudioError("Submission response had no task ID; saved for inspection and stopped to avoid duplicate charges")
        row["task_id"] = task_id
        row["status"] = "submitted"
        write_json_atomic(manifest_path, manifest)
        submitted += 1
    result = {"status": "ok", "manifest": str(manifest_path), "submitted": submitted, "jobs": len(jobs)}
    if args.wait:
        collected = collect_manifest(manifest_path, output_dir, api_key, args.timeout, args.poll_interval, args.wait_timeout, True)
        result["collection"] = collected
    print(json.dumps(result, ensure_ascii=False))


def content_extension(content_type: str, url: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    by_type = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
    if media_type in by_type:
        return by_type[media_type]
    suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else ".img"


def download_image(url: str, destination_without_suffix: Path, timeout: int) -> Path:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise StudioError("Result image URL must use HTTPS")
    request = urllib.request.Request(url, headers={"Accept": "image/*", "User-Agent": "ecommerce-product-image-cn/%s" % VERSION})
    opener = urllib.request.build_opener(SafeImageRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            data = read_limited(response, MAX_IMAGE_BYTES)
    except urllib.error.HTTPError as exc:
        raise StudioError("Image download failed with HTTP %d" % exc.code) from exc
    except urllib.error.URLError as exc:
        raise StudioError("Image download failed: %s" % exc.reason) from exc
    if not content_type.lower().startswith("image/"):
        raise StudioError("Result URL did not return an image content type")
    destination = destination_without_suffix.with_suffix(content_extension(content_type, url))
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(data)
    temporary.replace(destination)
    return destination


def collect_manifest(
    manifest_path: Path,
    output_dir: Path,
    api_key: str,
    timeout: int,
    poll_interval: int,
    wait_timeout: int,
    wait: bool,
) -> Dict[str, Any]:
    if not 2 <= poll_interval <= 60:
        raise StudioError("Poll interval must be between 2 and 60 seconds")
    if not 10 <= wait_timeout <= 7200:
        raise StudioError("Wait timeout must be between 10 and 7200 seconds")
    manifest = load_json(manifest_path)
    base_url = str(manifest.get("base_url") or DEFAULT_BASE_URL)
    deadline = time.monotonic() + wait_timeout
    while True:
        active = 0
        for position, row in enumerate(manifest.get("jobs") or [], 1):
            task_id = row.get("task_id")
            if not task_id or row.get("status") in TERMINAL_STATES:
                continue
            active += 1
            response = request_json(
                "GET", api_endpoint(base_url, "tasks/%s" % urllib.parse.quote(str(task_id), safe="")), api_key, None, timeout
            )
            state = extract_task_state(response)
            row["status"] = state
            row["updated_at"] = now_iso()
            if state in SUCCESS_STATES:
                reference_urls = set(str(value) for value in (manifest.get("reference_images") or []))
                urls = [url for url in extract_output_urls(response) if url not in reference_urls]
                row["output_urls"] = urls
                files = []
                for image_index, url in enumerate(urls, 1):
                    stem = "%02d-%s" % (position, row.get("type") or "image")
                    if len(urls) > 1:
                        stem += "-%02d" % image_index
                    destination = download_image(url, output_dir / stem, timeout)
                    files.append(str(destination))
                row["files"] = files
                if not files:
                    row["status"] = "result_missing"
                    row["result_response"] = response
            elif state in {"error", "failed"}:
                row["error_response"] = response
            write_json_atomic(manifest_path, manifest)
        unfinished = [row for row in manifest.get("jobs") or [] if row.get("task_id") and row.get("status") not in TERMINAL_STATES]
        if not wait or not unfinished:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)
    counts: Dict[str, int] = {}
    for row in manifest.get("jobs") or []:
        state = str(row.get("status") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return {"manifest": str(manifest_path), "states": counts, "unfinished": len(unfinished)}


def command_collect(args: argparse.Namespace) -> None:
    if not args.confirm_live_run:
        raise StudioError("Remote task collection requires --confirm-live-run")
    manifest_path = Path(args.manifest).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else manifest_path.parent
    api_key = require_api_key(args.api_key_env)
    result = collect_manifest(
        manifest_path, output_dir, api_key, args.timeout, args.poll_interval, args.wait_timeout, args.wait
    )
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False))


def expected_ratio(size: str) -> Optional[float]:
    delimiter = ":" if ":" in size else "x" if "x" in size else None
    if not delimiter:
        return None
    left, right = size.split(delimiter, 1)
    try:
        return float(left) / float(right)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def inspect_with_pillow(path: Path, white_background: bool) -> Dict[str, Any]:
    try:
        from PIL import Image, ImageStat  # type: ignore
    except ImportError as exc:
        raise StudioError("Image audit requires Pillow; run: python -m pip install Pillow") from exc
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            result = {"file": str(path), "format": image.format, "width": width, "height": height, "mode": image.mode}
            if white_background:
                rgb = image.convert("RGB")
                sample = max(4, min(width, height) // 40)
                boxes = [
                    (0, 0, sample, sample),
                    (width - sample, 0, width, sample),
                    (0, height - sample, sample, height),
                    (width - sample, height - sample, width, height),
                ]
                means = [ImageStat.Stat(rgb.crop(box)).mean for box in boxes]
                result["corner_white_score"] = round(sum(sum(channel) / 3.0 for channel in means) / len(means), 2)
            return result
    except (OSError, ValueError) as exc:
        return {"file": str(path), "error": str(exc)}


def command_audit(args: argparse.Namespace) -> None:
    plan = load_json(Path(args.plan))
    manifest = load_json(Path(args.manifest))
    expected_by_type = {job["type"]: expected_ratio(str(job["request"]["size"])) for job in plan.get("jobs") or []}
    rows = []
    for job in manifest.get("jobs") or []:
        for value in job.get("files") or []:
            path = Path(value).resolve()
            inspected = inspect_with_pillow(path, job.get("type") == "white_bg")
            inspected["job_id"] = job.get("job_id")
            inspected["type"] = job.get("type")
            if "width" in inspected and "height" in inspected:
                actual = inspected["width"] / inspected["height"]
                expected = expected_by_type.get(job.get("type"))
                inspected["aspect_ok"] = expected is None or abs(actual - expected) <= 0.025
                inspected["minimum_size_ok"] = min(inspected["width"], inspected["height"]) >= args.min_dimension
                if job.get("type") == "white_bg":
                    inspected["white_corners_ok"] = inspected.get("corner_white_score", 0) >= args.white_threshold
            rows.append(inspected)
    report = {
        "schema_version": 1,
        "audited_at": now_iso(),
        "files": rows,
        "automatic_checks_passed": bool(rows) and all(
            not row.get("error")
            and row.get("aspect_ok", False)
            and row.get("minimum_size_ok", False)
            and row.get("white_corners_ok", True)
            for row in rows
        ),
        "manual_review": [
            "商品轮廓、比例、颜色、材质、Logo、标签和包装与原图一致",
            "未生成不存在的配件、功能、认证、价格、功效或促销承诺",
            "中文文案无错字，尺寸与单位来自已核实数据",
            "场景中的使用方式、人物接触关系和产品尺度真实合理",
            "图片符合目标平台当前上架规则并拥有素材使用权",
        ],
    }
    output = Path(args.output).resolve()
    write_json_atomic(output, report)
    print(json.dumps({"status": "ok", "output": str(output), "files": len(rows), "passed": report["automatic_checks_passed"]}, ensure_ascii=False))


def command_doctor(args: argparse.Namespace) -> None:
    try:
        import PIL  # type: ignore  # noqa: F401
        pillow = True
    except ImportError:
        pillow = False
    key_name_ok = bool(re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", args.api_key_env))
    report = {
        "python": sys.version.split()[0],
        "pillow": pillow,
        "api_key_env": args.api_key_env,
        "api_key_configured": key_name_ok and bool(os.environ.get(args.api_key_env, "").strip()),
        "ready_for_plan": True,
        "ready_for_generate": key_name_ok and bool(os.environ.get(args.api_key_env, "").strip()),
        "ready_for_audit": pillow,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def add_remote_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-key-env", default="QIXUAI_API_KEY")
    parser.add_argument("--confirm-live-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=30)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check optional dependencies and API key presence")
    doctor.add_argument("--api-key-env", default="QIXUAI_API_KEY")
    doctor.set_defaults(func=command_doctor)

    plan = subparsers.add_parser("plan", help="Create a reviewable e-commerce image plan")
    plan.add_argument("--brief", help="Optional product brief JSON; CLI values override it")
    plan.add_argument("--product-name")
    plan.add_argument("--category")
    plan.add_argument("--description")
    plan.add_argument("--selling-point", action="append")
    plan.add_argument("--dimensions")
    plan.add_argument("--reference-url", action="append")
    plan.add_argument("--platform")
    plan.add_argument("--tone")
    plan.add_argument("--types")
    plan.add_argument("--size")
    plan.add_argument("--quality", choices=("low", "medium", "high"))
    plan.add_argument("--text-mode", choices=("reserve", "render", "none"), default="reserve")
    plan.add_argument("--points-per-image", type=int, default=10)
    plan.add_argument("--base-url", default=DEFAULT_BASE_URL)
    plan.add_argument("--output", required=True)
    plan.set_defaults(func=command_plan)

    generate = subparsers.add_parser("generate", help="Preview or submit asynchronous Qx-Image jobs")
    generate.add_argument("--plan", required=True)
    generate.add_argument("--output-dir", required=True)
    generate.add_argument("--max-points", type=int)
    generate.add_argument("--execute", action="store_true")
    generate.add_argument("--wait", action="store_true")
    generate.add_argument("--poll-interval", type=int, default=5)
    generate.add_argument("--wait-timeout", type=int, default=900)
    add_remote_options(generate)
    generate.set_defaults(func=command_generate)

    collect = subparsers.add_parser("collect", help="Poll submitted tasks and download completed images")
    collect.add_argument("--manifest", required=True)
    collect.add_argument("--output-dir")
    collect.add_argument("--wait", action="store_true")
    collect.add_argument("--poll-interval", type=int, default=5)
    collect.add_argument("--wait-timeout", type=int, default=900)
    add_remote_options(collect)
    collect.set_defaults(func=command_collect)

    audit = subparsers.add_parser("audit", help="Check image files and produce a manual fidelity checklist")
    audit.add_argument("--plan", required=True)
    audit.add_argument("--manifest", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--min-dimension", type=int, default=768)
    audit.add_argument("--white-threshold", type=float, default=247.0)
    audit.set_defaults(func=command_audit)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except StudioError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "error", "error": "Interrupted"}), file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
