#!/usr/bin/env python3
"""Plan, generate, collect, and audit e-commerce product image sets."""

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import qixuai_auth


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")


VERSION = "1.9.0"
DEFAULT_BASE_URL = "https://token.qixuai.com/v1"
DEFAULT_MODEL = "Qx-Image"
RECHARGE_URL = "https://token.qixuai.com/console/recharge?intent=buy"
MAX_REFERENCE_IMAGES = 16
MAX_JOBS = 16
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
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
        "using the best-supported view from the supplied references, even studio lighting, and no props."
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
REFERENCE_ROLE_ALIASES = {
    "front": "front",
    "正面": "front",
    "back": "back",
    "背面": "back",
    "left": "left",
    "左侧": "left",
    "right": "right",
    "右侧": "right",
    "side": "side",
    "侧面": "side",
    "top": "top",
    "顶部": "top",
    "bottom": "bottom",
    "底部": "bottom",
    "detail": "detail",
    "细节": "detail",
    "packaging": "packaging",
    "包装": "packaging",
    "label": "label",
    "标签": "label",
    "scale": "scale",
    "尺寸参照": "scale",
}
REFERENCE_ROLE_ORDER = tuple(dict.fromkeys(REFERENCE_ROLE_ALIASES.values()))
FRONT_FACING_TYPES = {"white_bg", "hero", "lifestyle", "feature", "size_reference"}
APPAREL_KEYWORDS = (
    "服装", "服饰", "女装", "男装", "童装", "内衣", "衣", "裤", "裙", "上装", "下装", "外套", "t恤", "衬衫",
    "garment", "apparel", "clothing", "dress", "shirt", "jacket", "coat", "trousers",
)


class StudioError(RuntimeError):
    pass


class InsufficientCreditsError(StudioError):
    def __init__(
        self,
        balance: Any,
        required: Any,
        recharge_url: str = RECHARGE_URL,
    ) -> None:
        self.balance = decimal_credits(balance, "balance")
        self.required = decimal_credits(required, "required")
        self.shortfall = max(Decimal("0"), self.required - self.balance)
        self.recharge_url = trusted_recharge_url(recharge_url)
        super().__init__(
            "积分余额不足：当前 %s 积分，本次请求需要 %s 积分，还差 %s 积分。"
            "任务已暂停，请充值后回复“继续”：%s"
            % (
                format_credits(self.balance),
                format_credits(self.required),
                format_credits(self.shortfall),
                self.recharge_url,
            )
        )


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


def normalize_reference_role(value: str) -> str:
    role = REFERENCE_ROLE_ALIASES.get(value.strip().lower()) or REFERENCE_ROLE_ALIASES.get(value.strip())
    if not role:
        raise StudioError(
            "Reference role must be one of: %s" % ", ".join(REFERENCE_ROLE_ORDER)
        )
    return role


def parse_reference_input(value: Any, location_key: str) -> Tuple[str, str]:
    if isinstance(value, dict):
        role_value = value.get("role")
        location = value.get(location_key)
        if not isinstance(role_value, str) or not isinstance(location, str):
            raise StudioError("Reference objects require role and %s string fields" % location_key)
        return normalize_reference_role(role_value), location.strip()
    role_value, separator, location = str(value).partition("=")
    if not separator or not location.strip():
        raise StudioError(
            "Every reference image must declare its view as ROLE=VALUE, for example front=product-front.jpg"
        )
    return normalize_reference_role(role_value), location.strip()


def required_reference_roles(category: str, image_types: Sequence[str], requested: Sequence[str]) -> List[str]:
    required = {normalize_reference_role(str(value)) for value in requested}
    if FRONT_FACING_TYPES.intersection(image_types):
        required.add("front")
    if "detail" in image_types:
        required.add("detail")
    lowered_category = category.lower()
    if FRONT_FACING_TYPES.intersection(image_types) and any(keyword in lowered_category for keyword in APPAREL_KEYWORDS):
        required.add("back")
    return [role for role in REFERENCE_ROLE_ORDER if role in required]


def detect_reference_mime(path: Path, header: bytes) -> str:
    suffix = path.suffix.lower()
    expected = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix)
    if not expected:
        raise StudioError("Reference file must be PNG, JPG, JPEG, WEBP, or GIF: %s" % path)
    detected = None
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif header.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif header.startswith((b"GIF87a", b"GIF89a")):
        detected = "image/gif"
    elif len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        detected = "image/webp"
    if detected != expected:
        raise StudioError("Reference file content does not match its extension: %s" % path)
    return expected


def describe_reference_file(value: str, base_directory: Optional[Path] = None) -> Dict[str, Any]:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute() and base_directory is not None:
        candidate = base_directory / candidate
    path = candidate.resolve()
    try:
        size = path.stat().st_size
        if not path.is_file():
            raise StudioError("Reference file is not a regular file: %s" % path)
        if size <= 0:
            raise StudioError("Reference file is empty: %s" % path)
        if size > MAX_UPLOAD_BYTES:
            raise StudioError("Reference file exceeds the 10 MB upload limit: %s" % path)
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            header = handle.read(16)
            digest.update(header)
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise StudioError("Cannot read reference file %s: %s" % (path, exc)) from exc
    return {
        "path": str(path),
        "size_bytes": size,
        "sha256": digest.hexdigest(),
        "mime_type": detect_reference_mime(path, header),
    }


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


def parse_types(value: Any) -> List[str]:
    if isinstance(value, str):
        requested = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list):
        requested = [str(item).strip() for item in value if str(item).strip()]
    else:
        raise StudioError("Image types must be a comma-separated string or an array")
    unknown = [item for item in requested if item not in TYPE_ORDER]
    if unknown:
        raise StudioError("Unknown image types: %s" % ", ".join(unknown))
    if not requested:
        raise StudioError("At least one image type is required")
    if len(requested) > MAX_JOBS:
        raise StudioError("A plan can contain at most %d image jobs" % MAX_JOBS)
    return requested


def parse_copy_list(value: Any, label: str = "Visible copy") -> List[str]:
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list):
        raise StudioError("%s must be a string or an array" % label)
    result = [bounded_text(str(item), label, 120) for item in values]
    if any(not item for item in result):
        raise StudioError("%s must not be empty" % label)
    if len(result) > 4:
        raise StudioError("Each image can contain at most four approved copy phrases")
    return result


def parse_copy_overrides(cli_values: Optional[List[str]], brief_value: Any) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    if cli_values is not None:
        for item in cli_values:
            image_type, separator, copy = item.partition("=")
            image_type = image_type.strip()
            if not separator or image_type not in TYPE_ORDER:
                raise StudioError("Copy must use TYPE=TEXT with a supported image type")
            result.setdefault(image_type, []).append(bounded_text(copy, "Visible copy", 120))
    else:
        if brief_value is None:
            return result
        if not isinstance(brief_value, dict):
            raise StudioError("copy_by_type in the product brief must be an object")
        for image_type, values in brief_value.items():
            if image_type not in TYPE_ORDER:
                raise StudioError("copy_by_type contains an unsupported image type: %s" % image_type)
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list):
                raise StudioError("copy_by_type values must be strings or arrays")
            result[image_type] = [bounded_text(str(value), "Visible copy", 120) for value in values]
    for image_type, values in result.items():
        if any(not value for value in values):
            raise StudioError("Visible copy must not be empty")
        if len(values) > 4:
            raise StudioError("Each image can contain at most four approved copy phrases")
        if image_type == "white_bg" and values:
            raise StudioError("white_bg does not support overlay copy; use hero for a text-led main visual")
    return result


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
    language: str,
    visible_copy: List[str],
    audience: str,
    scene: str,
    reference_roles: Sequence[str],
    has_references: bool,
    shot_direction: str = "",
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
    if audience:
        prompt.append("Target audience: %s. Keep casting, styling, props, and visual hierarchy appropriate for them." % audience)
    if scene and image_type in {"hero", "lifestyle"}:
        prompt.append("Requested use scene: %s. Keep it realistic for the product and audience." % scene)
    if shot_direction:
        prompt.append("Shot-specific art direction: %s." % shot_direction)
    if reference_roles:
        ordered_roles = ", ".join("image %d=%s" % (index, role) for index, role in enumerate(reference_roles, 1))
        prompt.append(
            "Reference order and evidence roles: %s. Use each reference only as evidence for its labeled view. "
            "Compose the product only from these evidenced views and keep unseen faces out of frame. "
            "Never invent an unseen product face, construction, closure, print, seam, label, logo, or accessory." % ordered_roles
        )
    if text_mode == "render" and visible_copy:
        prompt.append(
            "Render clear, correctly spelled visible copy in %s. Use each of these verified phrases once: %s. "
            "Keep phrases exactly as supplied when they already use the target language; otherwise translate faithfully "
            "without changing meaning. Build a professional headline and callout hierarchy. Do not add any other words, "
            "numbers, badges, or claims." % (language, " / ".join(visible_copy))
        )
    elif text_mode == "reserve" and image_type != "white_bg":
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


def visible_copy_for_type(image_type: str, product: Dict[str, Any]) -> List[str]:
    name = str(product.get("name") or "").strip()
    selling_points = [str(value).strip() for value in (product.get("selling_points") or []) if str(value).strip()]
    dimensions = str(product.get("dimensions") or "").strip()
    if image_type == "white_bg":
        return []
    if image_type == "hero":
        return ([name] if name else []) + selling_points[:2]
    if image_type == "lifestyle":
        return ([name] if name else []) + selling_points[:1]
    if image_type == "feature":
        return selling_points[:3] or ([name] if name else [])
    if image_type == "detail":
        return ([name] if name else []) + selling_points[:1]
    if image_type == "size_reference":
        return ([name] if name else []) + ([dimensions] if dimensions else [])
    return []


def create_plan(args: argparse.Namespace) -> Dict[str, Any]:
    brief_path = Path(args.brief).resolve() if getattr(args, "brief", None) else None
    brief = load_json(brief_path) if brief_path else {}
    raw_references = args.reference_url if args.reference_url is not None else brief.get("reference_urls", [])
    if not isinstance(raw_references, list):
        raise StudioError("reference_urls in the product brief must be an array")
    references = []
    reference_views = []
    for value in raw_references:
        role, url_value = parse_reference_input(value, "url")
        url = validate_reference_url(url_value)
        references.append(url)
        reference_views.append({"kind": "url", "role": role, "url": url})
    raw_reference_files = (
        getattr(args, "reference_file", None)
        if getattr(args, "reference_file", None) is not None
        else brief.get("reference_files", [])
    )
    if not isinstance(raw_reference_files, list):
        raise StudioError("reference_files in the product brief must be an array")
    file_base = brief_path.parent if brief_path and getattr(args, "reference_file", None) is None else None
    reference_files = []
    for value in raw_reference_files:
        role, path_value = parse_reference_input(value, "path")
        descriptor = describe_reference_file(path_value, file_base)
        descriptor["role"] = role
        reference_files.append(descriptor)
        reference_views.append({"kind": "file", "role": role, "path": descriptor["path"]})
    if len(references) + len(reference_files) > MAX_REFERENCE_IMAGES:
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
    if not reference_views:
        raise StudioError("At least one role-labeled product reference image is required for fidelity")
    raw_shots = brief.get("shots") if getattr(args, "types", None) is None else None
    if raw_shots is not None:
        if not isinstance(raw_shots, list) or not raw_shots:
            raise StudioError("shots in the product brief must be a non-empty array")
        if len(raw_shots) > MAX_JOBS:
            raise StudioError("A plan can contain at most %d image jobs" % MAX_JOBS)
        shot_specs = []
        for position, raw_shot in enumerate(raw_shots, 1):
            if not isinstance(raw_shot, dict):
                raise StudioError("Each shots item must be an object")
            image_type = str(raw_shot.get("type") or "").strip()
            parse_types([image_type])
            shot_specs.append(dict(raw_shot))
        image_types = [str(shot["type"]).strip() for shot in shot_specs]
    else:
        raw_types = getattr(args, "types", None) or brief.get("types")
        if not raw_types:
            raise StudioError("Choose the image types or provide shots before creating a plan")
        image_types = parse_types(raw_types)
        shot_specs = [{"type": image_type} for image_type in image_types]
    raw_required_views = getattr(args, "required_view", None)
    if raw_required_views is None:
        raw_required_views = brief.get("required_views", [])
    if not isinstance(raw_required_views, list):
        raise StudioError("required_views in the product brief must be an array")
    shot_required_roles = []
    for shot in shot_specs:
        values = shot.get("reference_roles", [])
        if not isinstance(values, list):
            raise StudioError("shots.reference_roles must be an array")
        shot_required_roles.extend(str(value) for value in values)
    required_roles = required_reference_roles(
        product["category"], image_types, list(raw_required_views) + shot_required_roles
    )
    supplied_roles = {view["role"] for view in reference_views}
    missing_roles = [role for role in required_roles if role not in supplied_roles]
    if not 1 <= args.points_per_image <= 10000:
        raise StudioError("Points per image estimate must be between 1 and 10000")
    raw_size = args.size or brief.get("size")
    if not raw_size and any(not shot.get("size") for shot in shot_specs):
        raise StudioError("Choose a default image size or set size on every shot")
    size = validate_size(str(raw_size)) if raw_size else None
    platform = bounded_text(args.platform or brief.get("platform"), "Platform", 80)
    if not platform:
        raise StudioError("Choose the target platform and use before creating a plan")
    tone = bounded_text(args.tone or brief.get("tone"), "Visual tone", 240) or "clean, modern, credible, conversion-focused"
    language = bounded_text(getattr(args, "language", None) or brief.get("language"), "Language", 80)
    if not language:
        raise StudioError("Choose the visible copy language before creating a plan")
    audience = bounded_text(getattr(args, "audience", None) or brief.get("audience"), "Audience", 240)
    scene = bounded_text(getattr(args, "scene", None) or brief.get("scene"), "Scene", 300)
    copy_overrides = parse_copy_overrides(getattr(args, "copy", None), brief.get("copy_by_type"))
    text_mode = getattr(args, "text_mode", None) or str(brief.get("text_mode") or "render")
    if text_mode not in {"reserve", "render", "none"}:
        raise StudioError("Text mode must be reserve, render, or none")
    unused_copy_types = sorted(set(copy_overrides) - set(image_types))
    if unused_copy_types:
        raise StudioError("Approved copy was provided for unselected image types: %s" % ", ".join(unused_copy_types))
    if copy_overrides and text_mode != "render":
        raise StudioError("Approved visible copy requires text mode render")
    quality = args.quality or str(brief.get("quality") or "medium")
    if quality not in {"low", "medium", "high"}:
        raise StudioError("Quality must be low, medium, or high")
    jobs = []
    deferred_shots = []
    used_job_ids = set()
    for position, shot in enumerate(shot_specs, 1):
        image_type = str(shot["type"]).strip()
        shot_id = str(shot.get("id") or "img-%02d" % position).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", shot_id):
            raise StudioError("Shot id must contain 1-48 letters, digits, underscores, or hyphens")
        if shot_id in used_job_ids:
            raise StudioError("Shot ids must be unique: %s" % shot_id)
        used_job_ids.add(shot_id)
        shot_text_mode = str(shot.get("text_mode") or text_mode)
        if shot_text_mode not in {"reserve", "render", "none"}:
            raise StudioError("Shot text_mode must be reserve, render, or none")
        if "copy" in shot:
            visible_copy = parse_copy_list(shot.get("copy"), "Shot visible copy")
        elif shot_text_mode == "render":
            visible_copy = copy_overrides.get(image_type, visible_copy_for_type(image_type, product))
        else:
            visible_copy = []
        if visible_copy and shot_text_mode != "render":
            raise StudioError("Shot visible copy requires text_mode render")
        if image_type == "white_bg" and visible_copy:
            raise StudioError("white_bg does not support overlay copy; use hero for a text-led main visual")
        shot_size = validate_size(str(shot.get("size") or size))
        shot_quality = str(shot.get("quality") or quality)
        if shot_quality not in {"low", "medium", "high"}:
            raise StudioError("Shot quality must be low, medium, or high")
        shot_tone = bounded_text(str(shot.get("tone") or tone), "Shot visual tone", 240)
        shot_audience = bounded_text(str(shot.get("audience") or audience), "Shot audience", 240)
        shot_scene = bounded_text(str(shot.get("scene") or scene), "Shot scene", 300)
        shot_direction = bounded_text(str(shot.get("direction") or ""), "Shot direction", 500)
        requested_roles = [normalize_reference_role(str(value)) for value in shot.get("reference_roles", [])]
        shot_missing_roles = [role for role in requested_roles if role not in supplied_roles]
        if shot_missing_roles:
            deferred_shots.append(
                {
                    "job_id": shot_id,
                    "index": position,
                    "type": image_type,
                    "label": TYPE_LABELS[image_type],
                    "copy": visible_copy,
                    "direction": shot_direction,
                    "requested_roles": requested_roles,
                    "missing_roles": shot_missing_roles,
                    "reason": "该镜头明确需要尚未提供的商品视角，补图后可生成",
                }
            )
            continue
        jobs.append(
            {
                "job_id": shot_id,
                "index": position,
                "type": image_type,
                "label": TYPE_LABELS[image_type],
                "copy": visible_copy,
                "shot": {
                    "direction": shot_direction,
                    "scene": shot_scene,
                    "audience": shot_audience,
                    "reference_roles": requested_roles,
                },
                "fidelity": {
                    "mode": "covered" if not missing_roles else "reference_bounded",
                    "evidence_roles": [role for role in REFERENCE_ROLE_ORDER if role in supplied_roles],
                    "requested_roles": requested_roles,
                    "missing_roles": [],
                },
                "request": {
                    "model": DEFAULT_MODEL,
                    "prompt": build_prompt(
                        image_type,
                        product,
                        platform,
                        shot_tone,
                        shot_text_mode,
                        language,
                        visible_copy,
                        shot_audience,
                        shot_scene,
                        [view["role"] for view in reference_views],
                        bool(references or reference_files),
                        shot_direction,
                    ),
                    "size": shot_size,
                    "quality": shot_quality,
                    "image": references,
                },
            }
        )
    return {
        "schema_version": 5,
        "generator_version": VERSION,
        "created_at": now_iso(),
        "provider": "算点边界",
        "base_url": validate_base_url(args.base_url),
        "model": DEFAULT_MODEL,
        "platform": platform,
        "audience": audience,
        "scene": scene,
        "text_mode": text_mode,
        "language": language,
        "product": product,
        "reference_images": references,
        "reference_files": reference_files,
        "reference_views": reference_views,
        "reference_coverage": {
            "supplied_roles": [role for role in REFERENCE_ROLE_ORDER if role in supplied_roles],
            "recommended_roles": required_roles,
            "missing_recommended_roles": missing_roles,
            "status": "complete" if not missing_roles else "limited",
            "generation_policy": "Only executable jobs constrained to supplied evidence may be generated",
        },
        "points_per_image_estimate": args.points_per_image,
        "estimated_points": args.points_per_image * len(jobs),
        "jobs": jobs,
        "deferred_shots": deferred_shots,
        "reference_suggestions": [
            "补充 %s 视角可提高一致性并解锁更多构图" % role for role in missing_roles
        ],
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
                "deferred_images": len(plan.get("deferred_shots") or []),
                "estimated_points": plan["estimated_points"],
                "reference_images": len(plan["reference_images"]) + len(plan.get("reference_files") or []),
                "reference_roles": plan["reference_coverage"]["supplied_roles"],
                "reference_coverage": plan["reference_coverage"]["status"],
                "suggested_roles": plan["reference_coverage"]["missing_recommended_roles"],
            },
            ensure_ascii=False,
        )
    )


def api_endpoint(base_url: str, path: str) -> str:
    return validate_base_url(base_url).rstrip("/") + "/" + path.lstrip("/")


def decimal_credits(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise StudioError("Billing response has an invalid %s" % field)
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise StudioError("Billing response has an invalid %s" % field) from exc
    if not amount.is_finite() or amount < 0:
        raise StudioError("Billing response has an invalid %s" % field)
    return amount


def format_credits(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def trusted_recharge_url(value: Any) -> str:
    candidate = str(value or RECHARGE_URL).strip()
    try:
        parsed = urllib.parse.urlsplit(candidate)
    except ValueError:
        return RECHARGE_URL
    if parsed.scheme == "https" and (parsed.hostname or "").lower() == "token.qixuai.com":
        return candidate
    return RECHARGE_URL


def insufficient_credits_from_response(raw: bytes) -> Optional[InsufficientCreditsError]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    codes = [
        str(value) for value in nested_values(payload, {"code", "error"})
        if isinstance(value, str)
    ]
    if "insufficient_credits" not in codes:
        return None

    def first_value(keys: Iterable[str]) -> Any:
        return next(nested_values(payload, keys), None)

    try:
        return InsufficientCreditsError(
            first_value({"balance", "current_balance", "available_credits"}),
            first_value({"required", "required_credits"}),
            first_value({"recharge_url"}) or RECHARGE_URL,
        )
    except StudioError:
        return None


def read_limited(response: Any, maximum: int) -> bytes:
    data = response.read(maximum + 1)
    if len(data) > maximum:
        raise StudioError("Remote response exceeded the allowed size")
    return data


def request_json(
    method: str,
    url: str,
    api_key: str,
    payload: Optional[Dict[str, Any]],
    timeout: int,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    headers = {"Authorization": "Bearer " + api_key, "Accept": "application/json"}
    for name, value in (extra_headers or {}).items():
        if not re.fullmatch(r"[A-Za-z0-9-]+", name) or "\r" in value or "\n" in value:
            raise StudioError("Invalid HTTP header")
        headers[name] = value
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
        raw_detail = exc.read(MAX_RESPONSE_BYTES + 1)
        if exc.code == 402:
            insufficient = insufficient_credits_from_response(raw_detail[:MAX_RESPONSE_BYTES])
            if insufficient:
                raise insufficient from exc
        detail = raw_detail[:1200].decode("utf-8", errors="replace")
        raise StudioError("API request failed with HTTP %d: %s" % (exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise StudioError("API request failed: %s" % exc.reason) from exc
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StudioError("API returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise StudioError("API returned a non-object JSON response")
    return value


def verify_reference_descriptor(descriptor: Dict[str, Any]) -> Dict[str, Any]:
    current = describe_reference_file(str(descriptor.get("path") or ""))
    for field in ("size_bytes", "sha256", "mime_type"):
        if current[field] != descriptor.get(field):
            raise StudioError("Reference file changed after the plan was created: %s" % current["path"])
    return current


def request_file_upload(url: str, api_key: str, descriptor: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    current = verify_reference_descriptor(descriptor)
    path = Path(current["path"])
    try:
        file_data = path.read_bytes()
    except OSError as exc:
        raise StudioError("Cannot read reference file %s: %s" % (path, exc)) from exc
    if len(file_data) != descriptor.get("size_bytes") or hashlib.sha256(file_data).hexdigest() != descriptor.get("sha256"):
        raise StudioError("Reference file changed while it was being prepared for upload: %s" % path)
    boundary = "----ecommerce-image-studio-" + secrets.token_hex(16)
    safe_name = "reference" + path.suffix.lower()
    prefix = (
        "--%s\r\n"
        "Content-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
        "Content-Type: %s\r\n\r\n" % (boundary, safe_name, current["mime_type"])
    ).encode("ascii")
    body = prefix + file_data + ("\r\n--%s--\r\n" % boundary).encode("ascii")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": "Bearer " + api_key,
            "Accept": "application/json",
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "User-Agent": "ecommerce-product-image-cn/%s" % VERSION,
        },
        method="POST",
    )
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            data = read_limited(response, MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        detail = exc.read(1200).decode("utf-8", errors="replace")
        raise StudioError("Image upload failed with HTTP %d: %s" % (exc.code, detail[:1200])) from exc
    except urllib.error.URLError as exc:
        raise StudioError("Image upload failed: %s" % exc.reason) from exc
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StudioError("Image upload returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise StudioError("Image upload returned a non-object JSON response")
    return value


def extract_upload_url(response: Dict[str, Any]) -> Optional[str]:
    candidates = [response.get("url")]
    data = response.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("url"), data.get("image_url")])
    for value in candidates:
        if isinstance(value, str):
            try:
                return validate_reference_url(value)
            except StudioError:
                continue
    return None


def extract_upload_expiry_seconds(response: Dict[str, Any]) -> int:
    candidates = [response.get("expires_in")]
    data = response.get("data")
    if isinstance(data, dict):
        candidates.append(data.get("expires_in"))
    for value in candidates:
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return seconds
    return 24 * 60 * 60


def upload_url_expired(row: Dict[str, Any]) -> bool:
    expiry_value = row.get("expires_at")
    if not expiry_value and row.get("uploaded_at"):
        try:
            uploaded_at = datetime.fromisoformat(str(row["uploaded_at"]))
            expiry_value = (uploaded_at + timedelta(hours=24)).isoformat()
        except ValueError:
            return True
    if not expiry_value:
        return True
    try:
        expiry = datetime.fromisoformat(str(expiry_value))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= datetime.now(timezone.utc)


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


def require_api_key(
    environment_name: str,
    base_url: str = DEFAULT_BASE_URL,
    required_scopes: Sequence[str] = ("images:write", "files:write"),
) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", environment_name):
        raise StudioError("Invalid API key environment variable name")
    try:
        return qixuai_auth.require_api_key(
            base_url, environment_name, required_scopes
        )
    except qixuai_auth.AuthError as exc:
        raise StudioError(str(exc)) from exc


def initial_manifest(plan: Dict[str, Any], plan_path: Path) -> Dict[str, Any]:
    rows = []
    for job in plan["jobs"]:
        rows.append(
            {
                "job_id": job["job_id"],
                "type": job["type"],
                "label": job["label"],
                "status": "not_submitted",
                "idempotency_key": secrets.token_urlsafe(24),
                "task_id": None,
                "submitted_at": None,
                "updated_at": None,
                "output_urls": [],
                "files": [],
            }
        )
    return {
        "schema_version": 5,
        "generator_version": VERSION,
        "plan": str(plan_path),
        "plan_sha256": plan_fingerprint(plan),
        "base_url": plan["base_url"],
        "model": plan["model"],
        "reference_images": plan.get("reference_images") or [],
        "reference_uploads": [
            {
                **descriptor,
                "status": "not_uploaded",
                "url": None,
                "uploaded_at": None,
                "expires_in": None,
                "expires_at": None,
                "updated_at": None,
            }
            for descriptor in (plan.get("reference_files") or [])
        ],
        "created_at": now_iso(),
        "billing": {
            "status": "not_checked",
            "balance": None,
            "required": None,
            "shortfall": None,
            "unit": "credits",
            "recharge_url": RECHARGE_URL,
            "checked_at": None,
        },
        "jobs": rows,
    }


def load_or_create_manifest(path: Path, plan: Dict[str, Any], plan_path: Path) -> Dict[str, Any]:
    if path.exists():
        manifest = load_json(path)
        if manifest.get("plan_sha256") != plan_fingerprint(plan):
            raise StudioError("Existing manifest belongs to a different plan")
        changed = False
        for row in manifest.get("jobs") or []:
            if not row.get("idempotency_key"):
                row["idempotency_key"] = secrets.token_urlsafe(24)
                changed = True
        if changed:
            manifest["schema_version"] = 5
            write_json_atomic(path, manifest)
        return manifest
    return initial_manifest(plan, plan_path)


def upload_local_references(
    manifest: Dict[str, Any], manifest_path: Path, plan: Dict[str, Any], api_key: str, timeout: int
) -> List[str]:
    direct_urls = [validate_reference_url(str(value)) for value in (plan.get("reference_images") or [])]
    uploads = manifest.get("reference_uploads")
    if uploads is None:
        uploads = [
            {
                **descriptor,
                "status": "not_uploaded",
                "url": None,
                "uploaded_at": None,
                "expires_in": None,
                "expires_at": None,
                "updated_at": None,
            }
            for descriptor in (plan.get("reference_files") or [])
        ]
        manifest["reference_uploads"] = uploads
        write_json_atomic(manifest_path, manifest)
    if not isinstance(uploads, list) or len(uploads) != len(plan.get("reference_files") or []):
        raise StudioError("Manifest reference uploads do not match the generation plan")
    upload_endpoint = api_endpoint(str(plan.get("base_url") or DEFAULT_BASE_URL), "images/uploads")
    uploaded_urls = []
    for row in uploads:
        status = row.get("status")
        if status == "uploaded":
            if upload_url_expired(row):
                row["status"] = "not_uploaded"
                row["url"] = None
                row["updated_at"] = now_iso()
                status = "not_uploaded"
                write_json_atomic(manifest_path, manifest)
            else:
                url = extract_upload_url({"url": row.get("url")})
                if not url:
                    raise StudioError("Manifest contains an invalid uploaded reference URL")
                uploaded_urls.append(url)
                continue
        if status in {"uploading", "upload_unknown"}:
            raise StudioError(
                "A previous reference upload has an unknown outcome. Check uploaded files before creating a new plan "
                "or replacing it with a known --reference-url: %s" % row.get("path")
            )
        if status != "not_uploaded":
            raise StudioError("Manifest contains an unsupported reference upload status: %s" % status)
        verify_reference_descriptor(row)
        row["status"] = "uploading"
        row["updated_at"] = now_iso()
        write_json_atomic(manifest_path, manifest)
        try:
            response = request_file_upload(upload_endpoint, api_key, row, timeout)
        except StudioError as exc:
            row["status"] = "upload_unknown"
            row["updated_at"] = now_iso()
            row["error"] = str(exc)
            write_json_atomic(manifest_path, manifest)
            raise StudioError(
                "Reference upload outcome is unknown. Manifest was saved; check uploaded files before retrying."
            ) from exc
        url = extract_upload_url(response)
        if not url:
            row["status"] = "upload_unknown"
            row["updated_at"] = now_iso()
            row["upload_response"] = response
            write_json_atomic(manifest_path, manifest)
            raise StudioError("Reference upload returned no HTTPS URL; response was saved for inspection")
        row["status"] = "uploaded"
        row["url"] = url
        uploaded_at = datetime.now(timezone.utc).replace(microsecond=0)
        expires_in = extract_upload_expiry_seconds(response)
        row["uploaded_at"] = uploaded_at.isoformat()
        row["expires_in"] = expires_in
        row["expires_at"] = (uploaded_at + timedelta(seconds=expires_in)).isoformat()
        row["updated_at"] = row["uploaded_at"]
        row.pop("error", None)
        manifest["reference_images"] = direct_urls + uploaded_urls + [url]
        write_json_atomic(manifest_path, manifest)
        uploaded_urls.append(url)
    combined = direct_urls + uploaded_urls
    if len(combined) > MAX_REFERENCE_IMAGES:
        raise StudioError("Qx-Image accepts at most %d reference images" % MAX_REFERENCE_IMAGES)
    manifest["reference_images"] = combined
    write_json_atomic(manifest_path, manifest)
    return combined


def command_generate(args: argparse.Namespace) -> None:
    plan_path = Path(args.plan).resolve()
    plan = load_json(plan_path)
    jobs = plan.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise StudioError("Generation plan contains no jobs")
    output_dir = Path(args.output_dir).resolve()
    manifest_path = output_dir / "generation_manifest.json"
    estimate = int(plan.get("estimated_points") or len(jobs) * int(plan.get("points_per_image_estimate") or 10))
    preview = {
        "mode": "dry-run" if not args.execute else "execute",
        "endpoint": api_endpoint(str(plan.get("base_url") or DEFAULT_BASE_URL), "images/generations?async=true"),
        "model": plan.get("model"),
        "images": len(jobs),
        "deferred_images": len(plan.get("deferred_shots") or []),
        "reference_roles": (plan.get("reference_coverage") or {}).get("supplied_roles", []),
        "fidelity_gate": (plan.get("reference_coverage") or {}).get("status", "unknown"),
        "local_reference_uploads": len(plan.get("reference_files") or []),
        "remote_reference_urls": len(plan.get("reference_images") or []),
        "estimated_points": estimate,
        "recharge_url": RECHARGE_URL,
        "charges_credits": bool(args.execute),
        "manifest": str(manifest_path),
    }
    if not args.execute:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return
    if preview["fidelity_gate"] not in {"complete", "limited"}:
        raise StudioError("Live generation requires a role-labeled, resource-aware plan; recreate the plan")
    if not args.confirm_live_run:
        raise StudioError("Live generation requires --confirm-live-run after the user confirms the image plan")
    base_url = str(plan.get("base_url") or DEFAULT_BASE_URL)
    required_scopes = ["images:write"]
    if plan.get("reference_files"):
        required_scopes.append("files:write")
    api_key = require_api_key(args.api_key_env, base_url, required_scopes)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_or_create_manifest(manifest_path, plan, plan_path)
    manifest_rows = {row["job_id"]: row for row in manifest["jobs"]}
    endpoint = api_endpoint(base_url, "images/generations?async=true")
    pending_rows = [
        row for row in manifest["jobs"]
        if not row.get("task_id") and row.get("status") in {"not_submitted", "submit_unknown"}
    ]
    if not pending_rows:
        print(json.dumps({"status": "ok", "manifest": str(manifest_path), "submitted": 0, "jobs": len(jobs)}, ensure_ascii=False))
        return
    reference_urls = upload_local_references(manifest, manifest_path, plan, api_key, args.timeout)
    submitted = 0
    for job in jobs:
        row = manifest_rows.get(job["job_id"])
        if row is None:
            raise StudioError("Manifest is missing job %s" % job["job_id"])
        if row.get("task_id") or row.get("status") not in {"not_submitted", "submit_unknown"}:
            continue
        payload = dict(job["request"])
        payload["image"] = reference_urls
        idempotency_key = str(row.get("idempotency_key") or "").strip()
        if not idempotency_key:
            idempotency_key = secrets.token_urlsafe(24)
            row["idempotency_key"] = idempotency_key
            write_json_atomic(manifest_path, manifest)
        try:
            response = request_json(
                "POST", endpoint, api_key, payload, args.timeout,
                {"Idempotency-Key": idempotency_key},
            )
        except InsufficientCreditsError as exc:
            manifest["billing"] = {
                "status": "waiting_for_recharge",
                "balance": format_credits(exc.balance),
                "required": format_credits(exc.required),
                "shortfall": format_credits(exc.shortfall),
                "unit": "credits",
                "recharge_url": exc.recharge_url,
                "checked_at": now_iso(),
                "source": "generation_http_402",
            }
            write_json_atomic(manifest_path, manifest)
            raise
        except StudioError:
            row["status"] = "submit_unknown"
            row["updated_at"] = now_iso()
            write_json_atomic(manifest_path, manifest)
            raise StudioError(
                "Submission outcome is unknown. The idempotency key was saved; retrying this same plan will not double-charge."
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
    manifest = load_json(manifest_path)
    api_key = require_api_key(args.api_key_env, str(manifest.get("base_url") or DEFAULT_BASE_URL))
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
    expected_by_job = {
        job["job_id"]: expected_ratio(str(job["request"]["size"])) for job in plan.get("jobs") or []
    }
    copy_by_job = {job["job_id"]: job.get("copy") or [] for job in plan.get("jobs") or []}
    rows = []
    for job in manifest.get("jobs") or []:
        for value in job.get("files") or []:
            path = Path(value).resolve()
            inspected = inspect_with_pillow(path, job.get("type") == "white_bg")
            inspected["job_id"] = job.get("job_id")
            inspected["type"] = job.get("type")
            inspected["expected_copy"] = copy_by_job.get(job.get("job_id"), [])
            if "width" in inspected and "height" in inspected:
                actual = inspected["width"] / inspected["height"]
                expected = expected_by_job.get(job.get("job_id"))
                inspected["aspect_ok"] = expected is None or abs(actual - expected) <= 0.025
                inspected["minimum_size_ok"] = min(inspected["width"], inspected["height"]) >= args.min_dimension
                if job.get("type") == "white_bg":
                    inspected["white_corners_ok"] = inspected.get("corner_white_score", 0) >= args.white_threshold
            rows.append(inspected)
    report = {
        "schema_version": 1,
        "audited_at": now_iso(),
        "language": plan.get("language") or "简体中文",
        "reference_coverage": plan.get("reference_coverage") or {},
        "files": rows,
        "automatic_checks_passed": bool(rows) and all(
            not row.get("error")
            and row.get("aspect_ok", False)
            and row.get("minimum_size_ok", False)
            and row.get("white_corners_ok", True)
            for row in rows
        ),
        "manual_review": [
            "将每张成图与对应角色参考图并排核对：轮廓、正背面结构、比例、颜色、材质和图案一致",
            "Logo、标签、包装文字、接缝、纽扣、拉链、接口、配件及其位置与参考图一致",
            "未生成不存在的配件、功能、认证、价格、功效或促销承诺",
            "逐字核对 expected_copy：目标语言正确、无错字漏字、无额外文案，尺寸与单位来自已核实数据",
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
    credential_error = None
    try:
        credential_ready = bool(
            qixuai_auth.resolve_api_key(
                DEFAULT_BASE_URL,
                args.api_key_env,
                ("images:write", "files:write"),
            )
        ) if key_name_ok else False
    except qixuai_auth.AuthError as exc:
        credential_ready = False
        credential_error = str(exc)
    report = {
        "python": sys.version.split()[0],
        "pillow": pillow,
        "api_key_env": args.api_key_env,
        "api_key_configured": credential_ready,
        "credential_source": (
            "environment" if key_name_ok and bool(os.environ.get(args.api_key_env, "").strip())
            else "qixuai_device" if credential_ready else None
        ),
        "credential_error": credential_error,
        "required_device_scopes": ["images:write", "files:write"],
        "recharge_url": RECHARGE_URL,
        "ready_for_plan": True,
        "ready_for_generate": credential_ready,
        "ready_for_audit": pillow,
        "max_reference_images": MAX_REFERENCE_IMAGES,
        "reference_roles": list(REFERENCE_ROLE_ORDER),
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
    plan.add_argument("--reference-url", action="append", help="Role-labeled reference as ROLE=HTTPS_URL")
    plan.add_argument("--reference-file", action="append", help="Role-labeled reference as ROLE=PATH")
    plan.add_argument("--required-view", action="append", help="Additional product view that must be covered")
    plan.add_argument("--platform")
    plan.add_argument("--tone")
    plan.add_argument("--language", help="Required visible copy language, such as 简体中文 or English")
    plan.add_argument("--audience")
    plan.add_argument("--scene")
    plan.add_argument("--copy", action="append", help="Approved visible copy in TYPE=TEXT format; repeat as needed")
    plan.add_argument("--types")
    plan.add_argument("--size")
    plan.add_argument("--quality", choices=("low", "medium", "high"))
    plan.add_argument(
        "--text-mode", choices=("reserve", "render", "none"), help="Copy strategy; effective default: render"
    )
    plan.add_argument("--points-per-image", type=int, default=10)
    plan.add_argument("--base-url", default=DEFAULT_BASE_URL)
    plan.add_argument("--output", required=True)
    plan.set_defaults(func=command_plan)

    generate = subparsers.add_parser("generate", help="Preview or submit asynchronous Qx-Image jobs")
    generate.add_argument("--plan", required=True)
    generate.add_argument("--output-dir", required=True)
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
