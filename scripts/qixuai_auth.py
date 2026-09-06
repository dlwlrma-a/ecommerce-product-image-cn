#!/usr/bin/env python3
"""Authorize QixuAI Skills without exposing an API key in chat or argv."""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import secrets
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Set


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")


CLIENT_ID = "qixuai-skill-cli"
DEVICE_CODE_ENDPOINT = "https://token.qixuai.com/oauth/device/code"
TOKEN_ENDPOINT = "https://token.qixuai.com/oauth/token"
VERIFICATION_URI = "https://token.qixuai.com/device"
API_BASE_URL = "https://token.qixuai.com/v1"
DEFAULT_SCOPES = ("models:read", "chat:write", "images:write", "files:write", "billing:read")
MAX_RESPONSE_BYTES = 1024 * 1024
PENDING_VERSION = 1


class AuthError(RuntimeError):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _scope_set(value: Any) -> Set[str]:
    if isinstance(value, str):
        values = value.replace(",", " ").split()
    elif isinstance(value, Iterable):
        values = [str(item) for item in value]
    else:
        values = []
    return {item.strip() for item in values if item and item.strip()}


def is_qixuai_url(base_url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(base_url)
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() == "token.qixuai.com"


def config_dir() -> Path:
    override = os.environ.get("QIXUAI_CONFIG_DIR", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".qixuai"


def credential_path() -> Path:
    return config_dir() / "credentials.json"


def pending_path() -> Path:
    return config_dir() / "pending-device.json"


def _windows_protect(raw: bytes, decrypt: bool = False) -> bytes:
    source_buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    if decrypt:
        ok = function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target))
    else:
        ok = function(ctypes.byref(source), "QixuAI Skill credential", None, None, None, 0, ctypes.byref(target))
    if not ok:
        raise AuthError("Windows DPAPI 无法处理本地凭据")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def protect_token(token: str) -> str:
    raw = token.encode("utf-8")
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_windows_protect(raw)).decode("ascii")
    return "plain:" + base64.b64encode(raw).decode("ascii")


def unprotect_token(value: str) -> str:
    if value.startswith("dpapi:"):
        if os.name != "nt":
            raise AuthError("该凭据由 Windows 当前用户加密，无法在此系统读取")
        raw = _windows_protect(base64.b64decode(value[6:], validate=True), decrypt=True)
    elif value.startswith("plain:"):
        raw = base64.b64decode(value[6:], validate=True)
    else:
        raise AuthError("本地凭据格式不受支持，请重新授权")
    token = raw.decode("utf-8").strip()
    if not token:
        raise AuthError("本地凭据为空，请重新授权")
    return token


def load_credential() -> Optional[Dict[str, Any]]:
    path = credential_path()
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError("无法读取本地 QixuAI 凭据，请重新授权") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise AuthError("本地 QixuAI 凭据版本不受支持，请重新授权")
    return value


def save_credential(token: str, scope: Any) -> Path:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass
    payload = {
        "version": 1,
        "client_id": CLIENT_ID,
        "token_protected": protect_token(token),
        "scope": sorted(_scope_set(scope)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    handle, temporary = tempfile.mkstemp(prefix="credentials-", suffix=".tmp", dir=str(directory))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, credential_path())
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return credential_path()


def _parse_utc(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuthError("本地%s格式无效，请重新发起授权" % label) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def save_pending_device(response: Dict[str, Any], scopes: Sequence[str]) -> Path:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass
    expires_in = int(response["expires_in"])
    payload = {
        "version": PENDING_VERSION,
        "client_id": CLIENT_ID,
        "device_code_protected": protect_token(str(response["device_code"])),
        "user_code": str(response["user_code"]),
        "verification_uri": str(response.get("verification_uri") or VERIFICATION_URI),
        "verification_uri_complete": str(
            response.get("verification_uri_complete") or response.get("verification_uri") or VERIFICATION_URI
        ),
        "scope": sorted(_scope_set(scopes)),
        "interval": max(1, int(response.get("interval", 5))),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat(),
    }
    handle, temporary = tempfile.mkstemp(prefix="pending-device-", suffix=".tmp", dir=str(directory))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, pending_path())
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return pending_path()


def clear_pending_device() -> None:
    path = pending_path()
    if path.exists():
        path.unlink()


def load_pending_device() -> Optional[Dict[str, Any]]:
    path = pending_path()
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError("无法读取待完成的 QixuAI 授权，请重新运行 login") from exc
    if not isinstance(value, dict) or value.get("version") != PENDING_VERSION:
        raise AuthError("待完成的 QixuAI 授权格式无效，请重新运行 login")
    if value.get("client_id") != CLIENT_ID:
        raise AuthError("待完成的 QixuAI 授权属于其他客户端")
    expires_at = _parse_utc(value.get("expires_at"), "设备授权过期时间")
    if expires_at <= datetime.now(timezone.utc):
        clear_pending_device()
        raise AuthError("设备授权已过期，请重新运行 login")
    protected = value.get("device_code_protected")
    if not isinstance(protected, str):
        raise AuthError("待完成的 QixuAI 授权缺少设备码")
    value["device_code"] = unprotect_token(protected)
    return value


def resolve_api_key(
    base_url: str,
    environment_name: str,
    required_scopes: Sequence[str] = (),
) -> Optional[str]:
    environment_value = os.environ.get(environment_name, "").strip()
    if environment_value:
        return environment_value
    if not is_qixuai_url(base_url):
        return None
    credential = load_credential()
    if not credential:
        return None
    granted = _scope_set(credential.get("scope"))
    missing = set(required_scopes) - granted
    if missing:
        raise AuthError("QixuAI 授权缺少权限 %s，请重新运行授权器" % ", ".join(sorted(missing)))
    protected = credential.get("token_protected")
    if not isinstance(protected, str):
        raise AuthError("本地 QixuAI 凭据不完整，请重新授权")
    return unprotect_token(protected)


def require_api_key(base_url: str, environment_name: str, required_scopes: Sequence[str]) -> str:
    token = resolve_api_key(base_url, environment_name, required_scopes)
    if token:
        return token
    if is_qixuai_url(base_url):
        raise AuthError("尚未授权 QixuAI。请运行: python scripts/qixuai_auth.py login")
    raise AuthError("API Key 环境变量缺失或为空: %s" % environment_name)


def _read_json(response) -> Dict[str, Any]:  # noqa: ANN001
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AuthError("授权服务响应过大")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthError("授权服务返回了无效 JSON") from exc
    if not isinstance(value, dict):
        raise AuthError("授权服务响应必须是 JSON 对象")
    return value


def _post_form(url: str, fields: Dict[str, str], timeout: int) -> tuple[int, Dict[str, Any]]:
    body = urllib.parse.urlencode(fields).encode("ascii")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, _read_json(response)
    except urllib.error.HTTPError as exc:
        return exc.code, _read_json(exc)
    except (urllib.error.URLError, OSError) as exc:
        raise AuthError("无法连接 QixuAI 授权服务: %s" % getattr(exc, "reason", exc)) from exc


def validate_token(token: str, timeout: int) -> None:
    request = urllib.request.Request(
        API_BASE_URL + "/models",
        headers={"Accept": "application/json", "Authorization": "Bearer " + token},
    )
    try:
        with urllib.request.build_opener(NoRedirectHandler()).open(request, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                raise AuthError("QixuAI 凭据验证失败，HTTP %d" % response.status)
            _read_json(response)
    except urllib.error.HTTPError as exc:
        raise AuthError("QixuAI 凭据验证失败，HTTP %d" % exc.code) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise AuthError("QixuAI 凭据已保存，但在线验证失败: %s" % getattr(exc, "reason", exc)) from exc


def start_device_authorization(args: argparse.Namespace) -> int:
    scopes = sorted(_scope_set(args.scope or DEFAULT_SCOPES))
    status, response = _post_form(
        DEVICE_CODE_ENDPOINT,
        {"client_id": CLIENT_ID, "scope": " ".join(scopes)},
        args.timeout,
    )
    if status != 200:
        raise AuthError("无法创建设备授权，HTTP %d: %s" % (status, response.get("error", "unknown_error")))
    required = {"device_code", "user_code", "verification_uri", "expires_in"}
    if not required.issubset(response):
        raise AuthError("设备授权响应缺少必要字段")
    verification_uri = str(response.get("verification_uri") or VERIFICATION_URI)
    complete_uri = str(response.get("verification_uri_complete") or verification_uri)
    user_code = str(response["user_code"])
    save_pending_device(response, scopes)
    print("请在浏览器中登录 QixuAI 并确认授权：")
    print(complete_uri)
    print("确认页面显示的验证码与此一致：%s" % user_code)
    print("网页允许后运行：python scripts/qixuai_auth.py login --complete")
    if not args.no_browser:
        webbrowser.open(complete_uri, new=2)
    return 0


def complete_device_authorization(args: argparse.Namespace) -> int:
    pending = load_pending_device()
    if not pending:
        raise AuthError("没有待完成的设备授权，请先运行: python scripts/qixuai_auth.py login")
    wait_seconds = int(args.wait_seconds)
    if not 0 <= wait_seconds <= 50:
        raise AuthError("--wait-seconds 必须在 0 到 50 之间")
    remote_deadline = _parse_utc(pending.get("expires_at"), "设备授权过期时间")
    local_deadline = time.monotonic() + wait_seconds
    interval = max(1, int(pending.get("interval", 5)))
    fields = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": str(pending["device_code"]),
        "client_id": CLIENT_ID,
    }
    while datetime.now(timezone.utc) < remote_deadline:
        token_status, token_response = _post_form(TOKEN_ENDPOINT, fields, args.timeout)
        if token_status == 200 and token_response.get("access_token"):
            if str(token_response.get("token_type", "Bearer")).lower() != "bearer":
                raise AuthError("授权服务返回了不支持的 token_type")
            token = str(token_response["access_token"]).strip()
            granted_scopes = _scope_set(token_response.get("scope") or pending.get("scope"))
            save_credential(token, granted_scopes)
            clear_pending_device()
            if "models:read" in granted_scopes:
                validate_token(token, args.timeout)
                print("QixuAI 授权成功，凭据已安全保存并通过连接验证。")
            else:
                print("QixuAI 授权成功，凭据已安全保存。当前权限不包含 models:read，已跳过模型列表验证。")
            return 0
        error_code = str(token_response.get("error", "unknown_error"))
        if error_code == "authorization_pending":
            pass
        elif error_code == "slow_down":
            interval += 5
        elif error_code == "access_denied":
            clear_pending_device()
            raise AuthError("用户拒绝了 QixuAI 授权")
        elif error_code == "expired_token":
            clear_pending_device()
            raise AuthError("设备授权已过期，请重新运行 login")
        else:
            raise AuthError("QixuAI 授权失败: %s" % error_code)
        remaining = local_deadline - time.monotonic()
        if remaining <= 0:
            print("网页授权尚未完成。完成后再次运行：python scripts/qixuai_auth.py login --complete")
            return 2
        time.sleep(min(interval, remaining))
    clear_pending_device()
    raise AuthError("设备授权已过期，请重新运行 login")


def command_login(args: argparse.Namespace) -> int:
    if args.complete:
        return complete_device_authorization(args)
    return start_device_authorization(args)


def command_status(args: argparse.Namespace) -> int:
    credential = load_credential()
    if not credential:
        pending = load_pending_device()
        if pending:
            print("QixuAI 设备授权等待网页确认。")
            print("授权页面：%s" % pending.get("verification_uri_complete"))
            print("网页允许后运行：python scripts/qixuai_auth.py login --complete")
            return 2
        print("QixuAI 尚未授权。先运行: python scripts/qixuai_auth.py login")
        return 1
    token = unprotect_token(str(credential.get("token_protected", "")))
    if args.check:
        if "models:read" not in _scope_set(credential.get("scope")):
            raise AuthError("当前授权不包含 models:read，无法通过 /v1/models 在线验证")
        validate_token(token, args.timeout)
    print("QixuAI 已授权%s。" % ("并通过在线验证" if args.check else ""))
    print("权限：%s" % " ".join(sorted(_scope_set(credential.get("scope")))))
    print("创建时间：%s" % credential.get("created_at", "未知"))
    return 0


def command_logout(args: argparse.Namespace) -> int:
    path = credential_path()
    if path.exists():
        path.unlink()
    clear_pending_device()
    print("本机 QixuAI 凭据已删除；如需远程撤销，请前往 https://token.qixuai.com/console/keys 。")
    return 0


def command_run(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise AuthError("run 后必须提供要执行的命令")
    token = require_api_key(API_BASE_URL, args.env, args.scope)
    child_environment = os.environ.copy()
    child_environment[args.env] = token
    return subprocess.run(command, env=child_environment, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=20)
    subparsers = parser.add_subparsers(dest="command_name", required=True)
    login = subparsers.add_parser("login", help="启动或完成浏览器设备授权")
    login.add_argument("--scope", action="append", help="请求的权限；可重复或用空格分隔")
    login.add_argument("--no-browser", action="store_true")
    login.add_argument("--complete", action="store_true", help="网页允许后兑换并保存设备令牌")
    login.add_argument("--wait-seconds", type=int, default=45, help="完成阶段最多等待秒数，最大 50")
    login.set_defaults(function=command_login)
    status_parser = subparsers.add_parser("status", help="查看本地授权状态")
    status_parser.add_argument("--check", action="store_true", help="调用 /v1/models 在线验证")
    status_parser.set_defaults(function=command_status)
    logout = subparsers.add_parser("logout", help="删除本地凭据")
    logout.set_defaults(function=command_logout)
    run = subparsers.add_parser("run", help="仅向子进程注入密钥，不输出密钥")
    run.add_argument("--env", default="QIXUAI_API_KEY")
    run.add_argument("--scope", action="append", default=[])
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(function=command_run)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.function(args))
    except (AuthError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
