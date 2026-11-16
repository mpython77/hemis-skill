#!/usr/bin/env python3
"""Zero-dependency Remote + Stdio MCP & OpenAPI HTTP bridge for HEMIS.

Enables BOTH:
  1. Local AI clients (Claude Desktop, Cursor, Claude Code, Gemini CLI, Codex, Antigravity)
     via standard JSON-RPC 2.0 `stdio` transport (`--stdio`), AND
  2. Web AI clients (Claude Web `Custom connectors`, ChatGPT Web `Developer Mode`)
     via Streamable HTTP (`POST /mcp`) + built-in OAuth 2.1 (RFC 9728 / RFC 8414 /
     RFC 7591 Dynamic Client Registration + PKCE S256) and REST API (`/api/*`).

When executed on the user's Uzbekistan PC (directly or behind `cloudflared tunnel`),
outbound HEMIS traffic exits from the user's local Uzbekistan IP (avoiding HTTP 451
Geo-IP blocks) while OneID credentials stay inside the OS-encrypted local vault
(`~/.hemis/credentials.json` — Windows DPAPI / POSIX 0600) and never enter LLM chats.

Zero external dependencies (Python >= 3.10 standard library only).
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable
from email.utils import formatdate
import hashlib
import hmac
import html
import http.server
import json
import os
import secrets
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
from typing import Any

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import hemis_oneid_auth as auth  # noqa: E402

MCP_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")
SERVER_NAME = "hemis-mcp"
SERVER_VERSION = "1.0.0"
DEFAULT_MAX_OUTPUT_CHARS = 35_000

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "hemis_public_stats",
        "description": "Fetch credential-free public HEMIS university statistics (structure, student, or employee).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "stat_type": {
                    "type": "string",
                    "enum": ["structure", "student", "employee"],
                    "default": "structure",
                    "description": "Public statistics dataset type.",
                },
                "student_base_url": {
                    "type": "string",
                    "description": "Optional student.<uni>.uz base URL override.",
                },
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "hemis_probe",
        "description": "Diagnose reachability to HEMIS from the Uzbekistan host (HTTP status, Geo-IP 451, TLS, source IP).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_url": {
                    "type": "string",
                    "description": "Optional HEMIS base URL override.",
                }
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "hemis_session_status",
        "description": "Ensure an active OneID -> HEMIS session using the host's encrypted local vault (never leaks secrets) and return verified identity summary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_url": {
                    "type": "string",
                    "description": "Optional HEMIS base URL override.",
                }
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "hemis_fetch_page",
        "description": "Safely GET a read-only HEMIS page (unsafe mutating GETs blocked) and parse FullCalendar timetables, Yii2 GridView tables, or elFinder item files into clean JSON.",
        "inputSchema": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path under HEMIS base URL, e.g. /teacher/time-table or /file-resource/index",
                },
                "parse": {
                    "type": "string",
                    "enum": ["auto", "timetable", "grid", "files", "none"],
                    "default": "auto",
                    "description": "Built-in parser mode (default: auto).",
                },
                "max_chars": {
                    "type": "integer",
                    "default": DEFAULT_MAX_OUTPUT_CHARS,
                    "description": "Max raw body characters if parse='none' (default 35000).",
                },
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "hemis_audit_subjects",
        "description": "Audit allowed curriculum subjects via DepDrop (/file-resource/edit?subject=1) or fetch a parsed subject resource catalog (/file-resource/subject?cs=...).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "curriculum_id": {
                    "type": "string",
                    "description": "Curriculum ID to query editable subjects via DepDrop.",
                },
                "curriculum_subject_id": {
                    "type": "string",
                    "description": "Curriculum-subject (cs) ID to audit /file-resource/subject?cs=<id>.",
                },
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "hemis_student_api",
        "description": "Call Student REST API (/rest/v1/...) GET endpoint using local student credentials (fallback to local vault).",
        "inputSchema": {
            "type": "object",
            "required": ["endpoint"],
            "properties": {
                "endpoint": {
                    "type": "string",
                    "description": "Endpoint path such as account/me, education/schedule, or education/semesters",
                },
                "student_base_url": {
                    "type": "string",
                    "description": "Optional student.<uni>.uz base URL override.",
                },
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
]


def _pkce_s256(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _is_trusted_redirect_uri(uri: str, registered_uris: list[str] | None = None) -> bool:
    """Validate OAuth redirect URIs against explicit registration or HTTPS/loopback rules."""
    if not uri or len(uri) > 1024 or "\r" in uri or "\n" in uri:
        return False
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme not in ("https", "http"):
        return False
    if parsed.scheme == "http" and (parsed.hostname or "").lower() not in ("127.0.0.1", "localhost"):
        return False
    if registered_uris is not None:
        return uri in registered_uris
    return True


class OAuthStateStore:
    """Thread-safe OAuth 2.1 + PKCE (S256) + Dynamic Client Registration (RFC 7591) store."""

    def __init__(
        self,
        passcode: str | None = None,
        static_bearer_token: str | None = None,
        token_ttl: int = 3600,
    ) -> None:
        self._lock = threading.Lock()
        self.passcode = (passcode or os.environ.get("HEMIS_MCP_PASSCODE") or "").strip() or secrets.token_urlsafe(12)
        self.static_bearer_token = (
            static_bearer_token if static_bearer_token is not None else os.environ.get("HEMIS_MCP_TOKEN", "")
        ).strip() or None
        self.token_ttl = token_ttl
        self.clients: dict[str, dict[str, Any]] = {}
        self.auth_codes: dict[str, dict[str, Any]] = {}
        self.access_tokens: dict[str, dict[str, Any]] = {}

    def _prune_locked(self, now: float) -> None:
        self.auth_codes = {k: v for k, v in self.auth_codes.items() if v["expires_at"] > now}
        self.access_tokens = {k: v for k, v in self.access_tokens.items() if v["expires_at"] > now}

    def register_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        redirect_uris = payload.get("redirect_uris")
        if not isinstance(redirect_uris, list) or not redirect_uris or not all(
            isinstance(u, str) and _is_trusted_redirect_uri(u) for u in redirect_uris
        ):
            raise ValueError("Dynamic client registration requires valid HTTPS/loopback redirect_uris.")
        client_id = f"hemis_{secrets.token_urlsafe(16)}"
        client_name = str(payload.get("client_name") or "MCP Web Client")[:120]
        record = {
            "client_id": client_id,
            "client_name": client_name,
            "redirect_uris": list(redirect_uris),
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "created_at": int(time.time()),
        }
        with self._lock:
            self.clients[client_id] = record
        return record

    def validate_authorize_request(self, client_id: str, redirect_uri: str, code_challenge: str, method: str) -> None:
        if not client_id or not code_challenge:
            raise ValueError("Missing client_id or PKCE code_challenge.")
        if method != "S256":
            raise ValueError("Only PKCE code_challenge_method=S256 is supported.")
        with self._lock:
            client = self.clients.get(client_id)
        if client is not None:
            if not _is_trusted_redirect_uri(redirect_uri, client["redirect_uris"]):
                raise ValueError("redirect_uri does not match registered client.")
        else:
            # Support Client ID Metadata Documents (CIMD URLs) when redirect_uri is HTTPS/loopback
            parsed_cid = urllib.parse.urlparse(client_id)
            if parsed_cid.scheme != "https" or not _is_trusted_redirect_uri(redirect_uri):
                raise ValueError("Unknown client_id or untrusted redirect_uri.")

    def create_auth_code(
        self,
        passcode: str,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        resource: str = "",
    ) -> str:
        if not hmac.compare_digest(passcode.strip().encode("utf-8"), self.passcode.encode("utf-8")):
            raise PermissionError("Invalid approval passcode.")
        self.validate_authorize_request(client_id, redirect_uri, code_challenge, code_challenge_method)
        code = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            self.auth_codes[code] = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": code_challenge,
                "resource": resource,
                "expires_at": now + 180,
            }
        return code

    def exchange_code(
        self,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            entry = self.auth_codes.pop(code, None)
        if not entry:
            raise PermissionError("Authorization code is invalid or expired.")
        if not hmac.compare_digest(entry["client_id"].encode("utf-8"), client_id.encode("utf-8")):
            raise PermissionError("client_id mismatch.")
        if not hmac.compare_digest(entry["redirect_uri"].encode("utf-8"), redirect_uri.encode("utf-8")):
            raise PermissionError("redirect_uri mismatch.")
        if not code_verifier or len(code_verifier) < 43:
            raise PermissionError("Invalid PKCE code_verifier.")
        computed = _pkce_s256(code_verifier)
        if not hmac.compare_digest(computed.encode("ascii"), entry["code_challenge"].encode("ascii")):
            raise PermissionError("PKCE S256 verification failed.")

        token = f"hmcp_{secrets.token_urlsafe(32)}"
        with self._lock:
            self.access_tokens[token] = {
                "client_id": client_id,
                "scope": "hemis:read",
                "expires_at": now + self.token_ttl,
            }
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": self.token_ttl,
            "scope": "hemis:read",
        }

    def verify_bearer(self, auth_header: str) -> bool:
        if not auth_header or not auth_header.lower().startswith("bearer "):
            return False
        presented = auth_header[7:].strip()
        if not presented:
            return False
        if self.static_bearer_token and hmac.compare_digest(
            presented.encode("utf-8"), self.static_bearer_token.encode("utf-8")
        ):
            return True
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            for stored_token, meta in list(self.access_tokens.items()):
                if meta["expires_at"] > now and hmac.compare_digest(
                    presented.encode("utf-8"), stored_token.encode("utf-8")
                ):
                    return True
        return False


class HemisToolDispatcher:
    """Thread-safe execution engine backing both MCP tools and REST endpoints."""

    def __init__(
        self,
        base_url: str = auth.DEFAULT_BASE_URL,
        source_ip: str | None = None,
        client_factory: Callable[[str, str | None], auth.HemisOneIDClient] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.source_ip = source_ip
        self._lock = threading.Lock()
        self._client_factory = client_factory or (
            lambda b_url, s_ip: auth.HemisOneIDClient(base_url=b_url, source_ip=s_ip)
        )
        self._clients: dict[str, auth.HemisOneIDClient] = {}

    def _get_client(self, base_url: str | None = None) -> auth.HemisOneIDClient:
        resolved_base = (base_url or "").strip().rstrip("/")
        if not resolved_base:
            if self.base_url == auth.DEFAULT_BASE_URL and not os.environ.get("HEMIS_BASE_URL"):
                try:
                    vault = auth.load_credentials()
                    if vault and vault.get("base_url"):
                        resolved_base = vault["base_url"]
                except Exception:  # noqa: BLE001
                    pass
            resolved_base = resolved_base or self.base_url
        if resolved_base not in self._clients:
            self._clients[resolved_base] = self._client_factory(resolved_base, self.source_ip)
        return self._clients[resolved_base]

    def _ensure_oneid_locked(self, client: auth.HemisOneIDClient) -> dict[str, Any]:
        creds = auth.resolve_credentials()
        if not creds:
            raise RuntimeError(
                "No HEMIS credentials in environment or local encrypted vault (~/.hemis/credentials.json). "
                "Run `python scripts/hemis_cli.py auth-save` on the host machine first."
            )
        login, password, _ = creds
        res = client.ensure_session(login, password)
        return res["verification"]

    def run_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        args = arguments or {}
        if name == "hemis_public_stats":
            stat_type = str(args.get("stat_type") or "structure")
            student_base = args.get("student_base_url")
            with self._lock:
                client = self._get_client()
                return {
                    "ok": True,
                    "stat_type": stat_type,
                    "student_base_url": client._resolve_student_base_url(student_base),
                    "data": client.fetch_public_stats(stat_type, student_base_url=student_base),
                }

        if name == "hemis_probe":
            with self._lock:
                client = self._get_client(args.get("base_url"))
                try:
                    status, _, _ = client.request("/dashboard/login", follow_redirects=False)
                except (urllib.error.URLError, OSError) as exc:
                    return {
                        "ok": False,
                        "reachable": False,
                        "diagnosis": "network_or_tls_error",
                        "error": repr(exc),
                        "source_ip": client.source_ip,
                    }
                return {
                    "ok": status < 500 and status != 451,
                    "reachable": status < 500 and status != 451,
                    "status": status,
                    "diagnosis": "geo_blocked" if status == 451 else ("ok" if status < 500 else "server_error"),
                    "base_url": client.base_url,
                    "source_ip": client.source_ip,
                }

        if name == "hemis_session_status":
            with self._lock:
                client = self._get_client(args.get("base_url"))
                verification = self._ensure_oneid_locked(client)
                ping = client.keepalive_ping()
                return {
                    "ok": bool(verification.get("authenticated")),
                    "base_url": client.base_url,
                    "verification": verification,
                    "keepalive_ok": ping.get("ok"),
                }

        if name == "hemis_fetch_page":
            path = str(args.get("path") or "").strip()
            if not path:
                raise ValueError("Missing required parameter: path")
            parse_mode = str(args.get("parse") or "auto")
            max_chars = max(1000, min(int(args.get("max_chars") or DEFAULT_MAX_OUTPUT_CHARS), 100_000))
            with self._lock:
                client = self._get_client()
                self._ensure_oneid_locked(client)
                status, headers, body = client.request(path, follow_redirects=False)
            out: dict[str, Any] = {
                "ok": 200 <= status < 400,
                "path": path,
                "status": status,
                "content_type": client._get_header(headers, "Content-Type"),
                "length": len(body),
            }
            if parse_mode == "timetable":
                events = auth.parse_timetable_events(body)
                out["parsed"] = {"type": "timetable", "count": len(events), "events": events}
            elif parse_mode == "grid":
                out["parsed"] = {"type": "gridview", **auth.parse_gridview(body)}
            elif parse_mode == "files":
                files = auth.parse_resource_item_files(body)
                out["parsed"] = {"type": "resource_files", "count": len(files), "files": files}
            elif parse_mode == "auto":
                out["parsed"] = auth.auto_parse_hemis_html(body, path_hint=path)
            else:
                out["body"] = body[:max_chars]
                out["truncated"] = len(body) > max_chars
            return out

        if name == "hemis_audit_subjects":
            cid = args.get("curriculum_id")
            cs_id = args.get("curriculum_subject_id")
            if not cid and not cs_id:
                raise ValueError("Provide either curriculum_id (DepDrop) or curriculum_subject_id (/file-resource/subject).")
            with self._lock:
                client = self._get_client()
                self._ensure_oneid_locked(client)
                result: dict[str, Any] = {"ok": True}
                if cid:
                    result["depdrop_subjects"] = client.query_depdrop_subjects(str(cid))
                if cs_id:
                    st, _, body = client.request(
                        f"/file-resource/subject?cs={urllib.parse.quote(str(cs_id))}",
                        follow_redirects=False,
                    )
                    result["subject_resources_status"] = st
                    result["subject_resources"] = auth.parse_gridview(body)
                return result

        if name == "hemis_student_api":
            endpoint = str(args.get("endpoint") or "").strip()
            if not endpoint:
                raise ValueError("Missing required parameter: endpoint")
            student_base = args.get("student_base_url")
            s_login = os.environ.get("HEMIS_STUDENT_LOGIN") or ""
            s_pass = os.environ.get("HEMIS_STUDENT_PASSWORD") or ""
            if not (s_login and s_pass):
                resolved = auth.resolve_credentials()
                if not resolved:
                    raise RuntimeError(
                        "No Student/OneID credentials found in env or encrypted local vault."
                    )
                s_login, s_pass, _ = resolved
            with self._lock:
                client = self._get_client()
                if not client._student_bearer_token:
                    client.student_rest_login(s_login, s_pass, student_base_url=student_base)
                return {
                    "ok": True,
                    "endpoint": endpoint,
                    "data": client.student_rest_get(endpoint, student_base_url=student_base),
                }

        raise KeyError(f"Unknown MCP tool: {name!r}")


class MCPProtocolHandler:
    """Pure JSON-RPC 2.0 MCP message processor shared by stdio and Streamable HTTP."""

    def __init__(self, dispatcher: HemisToolDispatcher) -> None:
        self.dispatcher = dispatcher

    def handle_message(self, msg: Any) -> dict[str, Any] | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return {
                "jsonrpc": "2.0",
                "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "Invalid JSON-RPC 2.0 Request"},
            }
        req_id = msg.get("id")
        method = str(msg["method"])
        params = msg.get("params") or {}
        is_notification = "id" not in msg

        if method == "notifications/initialized" or method.startswith("notifications/"):
            return None

        if method == "initialize":
            req_ver = str(params.get("protocolVersion") or MCP_PROTOCOL_VERSIONS[0])
            proto = req_ver if req_ver in MCP_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSIONS[0]
            result = {
                "protocolVersion": proto,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": MCP_TOOLS}
        elif method == "tools/call":
            t_name = str(params.get("name") or "")
            t_args = params.get("arguments") or {}
            try:
                tool_out = self.dispatcher.run_tool(t_name, t_args)
                result = {
                    "content": [{"type": "text", "text": json.dumps(tool_out, ensure_ascii=False, indent=2)}],
                    "isError": False,
                }
            except Exception as exc:  # noqa: BLE001
                err_payload = {"ok": False, "tool": t_name, "error": str(exc)}
                result = {
                    "content": [{"type": "text", "text": json.dumps(err_payload, ensure_ascii=False, indent=2)}],
                    "isError": True,
                }
        else:
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}


def run_stdio_server(protocol: MCPProtocolHandler) -> None:
    """Serve MCP over newline-delimited JSON-RPC 2.0 on stdin/stdout."""
    for raw_line in sys.stdin:
        line = raw_line.lstrip("\ufeff").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        if isinstance(msg, list):
            replies = [r for item in msg if (r := protocol.handle_message(item)) is not None]
            if replies:
                sys.stdout.write(json.dumps(replies, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        else:
            reply = protocol.handle_message(msg)
            if reply is not None:
                sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
                sys.stdout.flush()


class HemisMCPHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        protocol: MCPProtocolHandler,
        oauth: OAuthStateStore,
        require_auth: bool = True,
        public_url: str | None = None,
    ) -> None:
        self.protocol = protocol
        self.oauth = oauth
        self.require_auth = require_auth
        self.public_url = public_url.rstrip("/") if public_url else None
        super().__init__(server_address, _HemisMCPHTTPHandler)


class _HemisMCPHTTPHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: HemisMCPHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
        return  # Keep stdout/stderr clean of raw headers or query params

    def _external_origin(self) -> str:
        if self.server.public_url:
            return self.server.public_url
        fwd_proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        scheme = fwd_proto if fwd_proto in ("http", "https") else "http"
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",")[0].strip()
        if not host:
            addr, port = self.server.server_address[:2]
            host = f"{addr}:{port}"
        return f"{scheme}://{host}"

    def _send_bytes(
        self,
        status: int,
        content_type: str,
        body: bytes,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Date", formatdate(usegmt=True))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, status: int, payload: Any, extra_headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", raw, extra_headers)

    def _read_body_bytes(self, limit: int = 1024 * 1024) -> bytes:
        raw_len = self.headers.get("Content-Length") or "0"
        length = int(raw_len) if raw_len.isdigit() else 0
        if length <= 0:
            return b""
        if length > limit:
            raise ValueError("Payload too large")
        return self.rfile.read(length)

    def _check_auth_or_challenge(self) -> bool:
        if not self.server.require_auth:
            return True
        auth_hdr = self.headers.get("Authorization") or ""
        if self.server.oauth.verify_bearer(auth_hdr):
            return True
        origin = self._external_origin()
        self._send_json(
            401,
            {"error": "unauthorized", "message": "Bearer token required"},
            {
                "WWW-Authenticate": (
                    f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource", '
                    'scope="hemis:read"'
                )
            },
        )
        return False

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_bytes(
            204,
            "text/plain",
            b"",
            {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, Mcp-Session-Id",
            },
        )

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        origin = self._external_origin()

        if path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": SERVER_NAME,
                    "version": SERVER_VERSION,
                    "oauth_enabled": self.server.require_auth,
                },
            )
            return

        if path.startswith("/.well-known/oauth-protected-resource"):
            self._send_json(
                200,
                {
                    "resource": f"{origin}/mcp",
                    "authorization_servers": [origin],
                    "bearer_methods_supported": ["header"],
                    "scopes_supported": ["hemis:read"],
                },
            )
            return

        if path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
            self._send_json(
                200,
                {
                    "issuer": origin,
                    "authorization_endpoint": f"{origin}/oauth/authorize",
                    "token_endpoint": f"{origin}/oauth/token",
                    "registration_endpoint": f"{origin}/oauth/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "scopes_supported": ["hemis:read"],
                },
            )
            return

        if path == "/oauth/authorize":
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            client_id = (qs.get("client_id") or [""])[0]
            redirect_uri = (qs.get("redirect_uri") or [""])[0]
            state = (qs.get("state") or [""])[0]
            code_challenge = (qs.get("code_challenge") or [""])[0]
            method = (qs.get("code_challenge_method") or ["S256"])[0]
            resource = (qs.get("resource") or [""])[0]
            try:
                self.server.oauth.validate_authorize_request(client_id, redirect_uri, code_challenge, method)
            except ValueError as exc:
                self._send_bytes(400, "text/plain; charset=utf-8", str(exc).encode("utf-8"))
                return

            esc = html.escape
            page = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>Authorize HEMIS Remote MCP</title>"
                "<style>body{font-family:system-ui,sans-serif;max-width:460px;margin:48px auto;padding:24px;"
                "border:1px solid #ddd;border-radius:12px}input{width:100%;padding:10px;margin:8px 0 16px;"
                "box-sizing:border-box}button{padding:10px 18px;background:#111;color:#fff;border:0;"
                "border-radius:8px;cursor:pointer}</style></head><body>"
                "<h2>HEMIS Local-UZ MCP Authorization</h2>"
                f"<p>Client <code>{esc(client_id)}</code> requests read-only access to your local HEMIS bridge.</p>"
                "<form method='POST' action='/oauth/authorize'>"
                f"<input type='hidden' name='client_id' value='{esc(client_id)}'>"
                f"<input type='hidden' name='redirect_uri' value='{esc(redirect_uri)}'>"
                f"<input type='hidden' name='state' value='{esc(state)}'>"
                f"<input type='hidden' name='code_challenge' value='{esc(code_challenge)}'>"
                f"<input type='hidden' name='code_challenge_method' value='{esc(method)}'>"
                f"<input type='hidden' name='resource' value='{esc(resource)}'>"
                "<label>One-time terminal passcode:</label>"
                "<input type='password' name='passcode' required autofocus>"
                "<button type='submit'>Approve Access</button></form></body></html>"
            ).encode("utf-8")
            self._send_bytes(200, "text/html; charset=utf-8", page)
            return

        if path == "/api/public-stats":
            if not self._check_auth_or_challenge():
                return
            qs = urllib.parse.parse_qs(parsed.query)
            st_type = (qs.get("type") or ["structure"])[0]
            try:
                res = self.server.protocol.dispatcher.run_tool("hemis_public_stats", {"stat_type": st_type})
                self._send_json(200, res)
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/session-status":
            if not self._check_auth_or_challenge():
                return
            try:
                res = self.server.protocol.dispatcher.run_tool("hemis_session_status", {})
                self._send_json(200, res)
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": str(exc)})
            return

        if path == "/mcp":
            if not self._check_auth_or_challenge():
                return
            self._send_json(
                405,
                {"error": "method_not_allowed", "message": "Use POST /mcp for Streamable HTTP MCP requests."},
                {"Allow": "POST, OPTIONS"},
            )
            return

        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            body_bytes = self._read_body_bytes()
        except ValueError as exc:
            self._send_json(413, {"error": str(exc)})
            return

        if path == "/oauth/register":
            try:
                payload = json.loads(body_bytes.decode("utf-8") or "{}")
                reg = self.server.oauth.register_client(payload)
                self._send_json(201, reg)
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"error": "invalid_client_metadata", "error_description": str(exc)})
            return

        if path == "/oauth/authorize":
            form = urllib.parse.parse_qs(body_bytes.decode("utf-8"), keep_blank_values=True)
            client_id = (form.get("client_id") or [""])[0]
            redirect_uri = (form.get("redirect_uri") or [""])[0]
            state = (form.get("state") or [""])[0]
            code_challenge = (form.get("code_challenge") or [""])[0]
            method = (form.get("code_challenge_method") or ["S256"])[0]
            resource = (form.get("resource") or [""])[0]
            passcode = (form.get("passcode") or [""])[0]
            try:
                code = self.server.oauth.create_auth_code(
                    passcode=passcode,
                    client_id=client_id,
                    redirect_uri=redirect_uri,
                    code_challenge=code_challenge,
                    code_challenge_method=method,
                    resource=resource,
                )
            except PermissionError as exc:
                self._send_bytes(403, "text/plain; charset=utf-8", str(exc).encode("utf-8"))
                return
            except ValueError as exc:
                self._send_bytes(400, "text/plain; charset=utf-8", str(exc).encode("utf-8"))
                return

            sep = "&" if "?" in redirect_uri else "?"
            params = [("code", code)]
            if state:
                params.append(("state", state))
            loc = f"{redirect_uri}{sep}{urllib.parse.urlencode(params)}"
            self._send_bytes(302, "text/plain; charset=utf-8", b"Redirecting", {"Location": loc})
            return

        if path == "/oauth/token":
            ctype = (self.headers.get("Content-Type") or "").lower()
            raw_text = body_bytes.decode("utf-8", errors="replace")
            if "application/json" in ctype:
                try:
                    raw_obj = json.loads(raw_text or "{}")
                except json.JSONDecodeError:
                    self._send_json(400, {"error": "invalid_request"})
                    return
                if not isinstance(raw_obj, dict):
                    self._send_json(400, {"error": "invalid_request"})
                    return
                form_flat = {str(k): str(v) for k, v in raw_obj.items()}
            else:
                parsed_qs = urllib.parse.parse_qs(raw_text, keep_blank_values=True)
                form_flat = {k: vals[0] for k, vals in parsed_qs.items() if vals}

            if form_flat.get("grant_type") != "authorization_code":
                self._send_json(400, {"error": "unsupported_grant_type"})
                return
            try:
                token_resp = self.server.oauth.exchange_code(
                    code=form_flat.get("code", ""),
                    client_id=form_flat.get("client_id", ""),
                    redirect_uri=form_flat.get("redirect_uri", ""),
                    code_verifier=form_flat.get("code_verifier", ""),
                )
                self._send_json(200, token_resp)
            except PermissionError as exc:
                self._send_json(400, {"error": "invalid_grant", "error_description": str(exc)})
            return

        if path == "/mcp":
            if not self._check_auth_or_challenge():
                return
            try:
                msg = json.loads(body_bytes.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json(
                    400,
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                )
                return

            if isinstance(msg, list):
                replies = [r for item in msg if (r := self.server.protocol.handle_message(item)) is not None]
                if not replies:
                    self._send_bytes(202, "application/json; charset=utf-8", b"")
                    return
                self._send_json(200, replies)
                return

            reply = self.server.protocol.handle_message(msg)
            if reply is None:
                self._send_bytes(202, "application/json; charset=utf-8", b"")
                return
            hdrs: dict[str, str] = {}
            if msg.get("method") == "initialize":
                hdrs["Mcp-Session-Id"] = secrets.token_hex(12)
            self._send_json(200, reply, hdrs)
            return

        if path in ("/api/fetch", "/api/student-fetch"):
            if not self._check_auth_or_challenge():
                return
            try:
                req_json = json.loads(body_bytes.decode("utf-8") or "{}")
                tool_name = "hemis_fetch_page" if path == "/api/fetch" else "hemis_student_api"
                res = self.server.protocol.dispatcher.run_tool(tool_name, req_json)
                self._send_json(200, res)
            except Exception as exc:  # noqa: BLE001
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        self._send_json(404, {"error": "not_found"})


def run_self_test() -> dict[str, Any]:
    """Deterministically verify OAuth 2.1 + PKCE + DCR + Streamable HTTP MCP + stdio MCP offline."""
    import io
    import urllib.request

    mock_csrf = "M" * 88
    mock_uuid = "11111111-2222-4333-8444-555555555555"

    def make_mock_client(base_url: str, source_ip: str | None) -> auth.HemisOneIDClient:
        c = auth.HemisOneIDClient(
            base_url=base_url,
            source_ip=source_ip,
            step_delay=0.0,
            max_retries=0,
            auto_interface_failover=False,
        )

        def fake_open(req: urllib.request.Request, timeout: int = 25):  # noqa: ARG001
            p = urllib.parse.urlparse(req.full_url)

            def resp_or_err(st: int, hdrs: list[tuple[str, str]], body: bytes):
                r = auth._MockHTTPResponse(st, hdrs, body)
                c.cookie_jar.extract_cookies(r, req)
                if st >= 300:
                    raise urllib.error.HTTPError(req.full_url, st, f"HTTP {st}", r.headers, io.BytesIO(body))
                return r

            if p.path == "/dashboard/auth" and "authclient=oneid" in p.query:
                return resp_or_err(
                    302,
                    [("Location", "https://id.egov.uz?client_id=hemis&token_id=" + mock_uuid + "&method=IDPW")],
                    b"",
                )
            if req.full_url == auth.ONEID_LOGIN_API:
                return resp_or_err(200, [("Content-Type", "application/json")], b'{"token":"jwt.mock","code":0}')
            if req.full_url == auth.ONEID_SSO_GENERATE_API:
                return resp_or_err(
                    200,
                    [("Content-Type", "application/json")],
                    json.dumps({"callbackUrl": f"{base_url}/dashboard/auth", "code": "c1", "state": "s1"}).encode(),
                )
            if p.path == "/dashboard/auth" and "code=" in p.query:
                return resp_or_err(200, [("Set-Cookie", "backend_8=sid; Path=/")], b"ok")
            if p.path == "/dashboard/profile":
                html_doc = (
                    f"<html><head><title>Profil</title><meta name='csrf-token' content='{mock_csrf}'></head>"
                    "<body><footer>Dastur versiyasi <span>1.9.88</span> / UID <span>: 100</span></footer></body></html>"
                ).encode()
                return resp_or_err(200, [("Content-Type", "text/html")], html_doc)
            if p.path.startswith("/keepalive/ping"):
                return resp_or_err(200, [("Content-Type", "application/json")], b'{"ok":true}')
            if p.path == "/teacher/time-table":
                tt = '<script>jQuery("#w0").fullCalendar({"events":[{"id":1,"title":"1. AI<br>Ma\'ruza","start":"2026-11-16T08:30:00"}]});</script>'.encode()
                return resp_or_err(200, [("Content-Type", "text/html")], tt)
            if p.path == "/rest/v1/public/stat-structure":
                return resp_or_err(200, [("Content-Type", "application/json")], b'{"success":true,"data":{"faculties":5}}')
            return resp_or_err(404, [("Content-Type", "text/plain")], b"not found")

        c.opener.open = fake_open  # type: ignore[method-assign]
        return c

    orig_resolve = auth.resolve_credentials
    auth.resolve_credentials = lambda *a, **kw: ("demo_u", "demo_p", "mock")  # type: ignore[assignment]
    try:
        dispatcher = HemisToolDispatcher(client_factory=make_mock_client)
        protocol = MCPProtocolHandler(dispatcher)
        oauth = OAuthStateStore(passcode="approve-9999", static_bearer_token="static-dev-tok")

        # 1. JSON-RPC initialize / tools/list / tools/call + unsafe GET guard
        init_res = protocol.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert init_res and init_res["result"]["serverInfo"]["name"] == SERVER_NAME
        list_res = protocol.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert list_res and len(list_res["result"]["tools"]) == 6
        tt_call = protocol.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "hemis_fetch_page", "arguments": {"path": "/teacher/time-table"}},
            }
        )
        assert tt_call and tt_call["result"]["isError"] is False
        tt_json = json.loads(tt_call["result"]["content"][0]["text"])
        assert tt_json["parsed"]["events"][0]["title_text"] == "1. AI | Ma'ruza"

        unsafe_call = protocol.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "hemis_fetch_page", "arguments": {"path": "/dashboard/logout"}},
            }
        )
        assert unsafe_call and unsafe_call["result"]["isError"] is True

        # 2. Live ephemeral HTTP server: unauthenticated 401 challenge, .well-known metadata, DCR + PKCE S256
        with HemisMCPHTTPServer(("127.0.0.1", 0), protocol, oauth, require_auth=True) as srv:
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{port}"

            class _NoRedir(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None

            opener = urllib.request.build_opener(_NoRedir())
            try:
                opener.open(urllib.request.Request(f"{base}/mcp", data=b"{}", method="POST"))
                raise AssertionError("Expected 401 on unauthenticated POST /mcp")
            except urllib.error.HTTPError as err:
                assert err.code == 401
                www = err.headers.get("WWW-Authenticate", "")
                assert "/.well-known/oauth-protected-resource" in www

            prm = json.loads(opener.open(f"{base}/.well-known/oauth-protected-resource").read().decode())
            assert prm["resource"] == f"{base}/mcp"
            asm = json.loads(opener.open(f"{base}/.well-known/oauth-authorization-server").read().decode())
            assert "S256" in asm["code_challenge_methods_supported"]

            # Dynamic client registration (RFC 7591)
            reg_req = urllib.request.Request(
                asm["registration_endpoint"],
                data=json.dumps({"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            reg_resp = json.loads(opener.open(reg_req).read().decode())
            cid = reg_resp["client_id"]

            # PKCE S256 authorize POST -> 302 Location with code
            verifier = "V" * 64
            challenge = _pkce_s256(verifier)
            auth_form = urllib.parse.urlencode(
                {
                    "client_id": cid,
                    "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                    "state": "xyz",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "passcode": "approve-9999",
                }
            ).encode()
            try:
                opener.open(urllib.request.Request(asm["authorization_endpoint"], data=auth_form, method="POST"))
                raise AssertionError("Expected 302 from /oauth/authorize")
            except urllib.error.HTTPError as redir:
                assert redir.code == 302
                loc_q = urllib.parse.parse_qs(urllib.parse.urlparse(redir.headers["Location"]).query)
                code = loc_q["code"][0]

            # Token exchange -> Bearer access token
            tok_req = urllib.request.Request(
                asm["token_endpoint"],
                data=urllib.parse.urlencode(
                    {
                        "grant_type": "authorization_code",
                        "code": code,
                        "client_id": cid,
                        "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                        "code_verifier": verifier,
                    }
                ).encode(),
                method="POST",
            )
            tok_resp = json.loads(opener.open(tok_req).read().decode())
            access_token = tok_resp["access_token"]

            # Call POST /mcp with Bearer token -> public stats tool
            mcp_req = urllib.request.Request(
                f"{base}/mcp",
                data=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 99,
                        "method": "tools/call",
                        "params": {"name": "hemis_public_stats", "arguments": {"stat_type": "structure"}},
                    }
                ).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {access_token}",
                },
                method="POST",
            )
            mcp_out = json.loads(opener.open(mcp_req).read().decode())
            stat_json = json.loads(mcp_out["result"]["content"][0]["text"])
            assert stat_json["data"]["data"]["faculties"] == 5

            # X-Forwarded-Proto header yields HTTPS discovery URLs behind Cloudflare Tunnel
            fwd_req = urllib.request.Request(
                f"{base}/.well-known/oauth-protected-resource",
                headers={"X-Forwarded-Proto": "https", "Host": "hemis-mcp.example.com"},
            )
            fwd_prm = json.loads(opener.open(fwd_req).read().decode())
            assert fwd_prm["resource"] == "https://hemis-mcp.example.com/mcp"

            srv.shutdown()
    finally:
        auth.resolve_credentials = orig_resolve

    return {
        "self_test_passed": True,
        "checks_passed": 5,
        "details": [
            "mcp_initialize_tools_list_and_call",
            "unsafe_get_guard_enforced_inside_mcp",
            "oauth_401_challenge_and_well_known_discovery",
            "rfc7591_dcr_pkce_s256_token_exchange_and_bearer_call",
            "reverse_proxy_x_forwarded_proto_https_urls",
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--stdio", action="store_true", help="Serve MCP JSON-RPC 2.0 over stdin/stdout (local agents)")
    p.add_argument("--host", default="127.0.0.1", help="HTTP bind address (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765, help="HTTP port for Streamable HTTP MCP (default: 8765)")
    p.add_argument("--public-url", default=os.environ.get("HEMIS_MCP_PUBLIC_URL") or None,
                   help="Optional public HTTPS base URL (e.g. https://hemis.yourdomain.com)")
    p.add_argument("--base-url", default=os.environ.get("HEMIS_BASE_URL", auth.DEFAULT_BASE_URL),
                   help="HEMIS portal base URL")
    p.add_argument("--source-ip", default=os.environ.get("HEMIS_SOURCE_IP") or None,
                   help="Optional outbound IPv4 source address")
    p.add_argument("--no-auth", action="store_true",
                   help="Disable OAuth/Bearer auth (permitted ONLY when --host is loopback)")
    p.add_argument("--self-test", action="store_true", help="Run deterministic offline MCP + OAuth self-tests")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    if args.self_test:
        print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
        return 0

    dispatcher = HemisToolDispatcher(base_url=args.base_url, source_ip=args.source_ip)
    protocol = MCPProtocolHandler(dispatcher)

    if args.stdio:
        run_stdio_server(protocol)
        return 0

    if args.no_auth and args.host not in ("127.0.0.1", "localhost", "::1"):
        print("--no-auth is permitted only on loopback interfaces.", file=sys.stderr)
        return 2

    oauth = OAuthStateStore()
    with HemisMCPHTTPServer(
        (args.host, args.port),
        protocol,
        oauth,
        require_auth=not args.no_auth,
        public_url=args.public_url,
    ) as srv:
        msg = {
            "service": SERVER_NAME,
            "version": SERVER_VERSION,
            "mcp_endpoint": f"http://{args.host}:{args.port}/mcp",
            "oauth_required": not args.no_auth,
            "oauth_passcode": oauth.passcode if not args.no_auth else None,
            "vault_configured": auth.get_credentials_status().get("configured", False),
            "hint": "Expose locally via `cloudflared tunnel --url http://127.0.0.1:" + str(args.port) + "` for Claude/ChatGPT Web.",
        }
        print(json.dumps(msg, ensure_ascii=False, indent=2), flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
