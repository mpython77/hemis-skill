"""Automated pure-Python OneID -> HEMIS (hemis.namdtu.uz) authentication client.

Reproduces the full 6-step browser SSO flow using Python's standard library
(`urllib.request`, `http.cookiejar`, `ssl.create_default_context()`):
1. GET /dashboard/auth?authclient=oneid -> initial HEMIS session cookie + 302 to sso.egov.uz
2. GET sso.egov.uz Authorization.do -> 307/302 to id.egov.uz with dynamic UUID token_id
3. POST https://id.egov.uz/api/identity/auth/login -> OneID JWT token
4. POST https://id.egov.uz/api/sso/v1/generate -> OneID callback code + state
5. GET callbackUrl?code=...&state=... -> establishes authenticated HEMIS session cookies
6. GET /dashboard/profile (NoRedirect) -> verifies live session and extracts CSRF token
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes
from email.message import Message
import getpass
import gzip
import hashlib
import hmac
from html.parser import HTMLParser
import http.client
import http.cookiejar
import io
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
import uuid
import zlib

DEFAULT_BASE_URL = "https://hemis.namdtu.uz"
ONEID_LOGIN_API = "https://id.egov.uz/api/identity/auth/login"
ONEID_SSO_GENERATE_API = "https://id.egov.uz/api/sso/v1/generate"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
REDIRECT_CODES = (301, 302, 303, 307, 308)
MAX_ELFINDER_UPLOAD_BYTES = 5 * 1024 * 1024  # 5242880 bytes (5 MiB elFinder limit)
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Public Thawte TLS RSA CA G1 intermediate CA (issued by DigiCert Global Root G2).
# Embedded for Linux/macOS/Docker environments where OpenSSL does not auto-chase AIA.
THAWTE_TLS_RSA_CA_G1_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIEizCCA3OgAwIBAgIQCQ7oxd5b+mLSri/3CXxIVzANBgkqhkiG9w0BAQsFADBh\n"
    "MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3\n"
    "d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH\n"
    "MjAeFw0xNzExMDIxMjI0MjVaFw0yNzExMDIxMjI0MjVaMF4xCzAJBgNVBAYTAlVT\n"
    "MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5j\n"
    "b20xHTAbBgNVBAMTFFRoYXd0ZSBUTFMgUlNBIENBIEcxMIIBIjANBgkqhkiG9w0B\n"
    "AQEFAAOCAQ8AMIIBCgKCAQEAxjngmPhVetC0b/ozbYJdzOBUA1sMog47030cAP+P\n"
    "23ANUN8grXECL8NhDEF4F1R9tL0wY0mczHaR0a7lYanlxtwWo1s2uGnnyDs6mOCs\n"
    "66ew2w3YETr6Tb14xgjpu1gGFtAeewaikO9Fud8hxGJTSwn8xeNkfKVWpD2L4vFN\n"
    "36FNgxeilK6aE4ykgGAzNlokTp6hNOLAYpDySdLAPKzuJSQ7JCEZ6O+SDKywIdXL\n"
    "oMTnpxuBKGSG88NWTo3CHCOGmQECia2yqdPDjgLqnEiYNjwQL8uMqj8rOvlMgviB\n"
    "cHA7xty+7/uYLN6ZS7Vq1/F/lVhVOf5ej6jZdmB85szFbQIDAQABo4IBQDCCATww\n"
    "HQYDVR0OBBYEFKWM/jLM6w8s1BnGCLgAJIhdw8W3MB8GA1UdIwQYMBaAFE4iVCAY\n"
    "lebjbuYP+vq5Eu0GF485MA4GA1UdDwEB/wQEAwIBhjAdBgNVHSUEFjAUBggrBgEF\n"
    "BQcDAQYIKwYBBQUHAwIwEgYDVR0TAQH/BAgwBgEB/wIBADA0BggrBgEFBQcBAQQo\n"
    "MCYwJAYIKwYBBQUHMAGGGGh0dHA6Ly9vY3NwLmRpZ2ljZXJ0LmNvbTBCBgNVHR8E\n"
    "OzA5MDegNaAzhjFodHRwOi8vY3JsMy5kaWdpY2VydC5jb20vRGlnaUNlcnRHbG9i\n"
    "YWxSb290RzIuY3JsMD0GA1UdIAQ2MDQwMgYEVR0gADAqMCgGCCsGAQUFBwIBFhxo\n"
    "dHRwczovL3d3dy5kaWdpY2VydC5jb20vQ1BTMA0GCSqGSIb3DQEBCwUAA4IBAQC6\n"
    "km0KA4sTb2VYpEBm/uL2HL/pZX9B7L/hbJ4NcoBe7V56oCnt7aeIo8sMjCRWTCWZ\n"
    "D1dY0+2KZOC1dKj8d1VXXAtnjytDDuPPf6/iow0mYQTO/GAg/MLyL6CDm3FzDB8V\n"
    "tsH/aeMgP6pgD1XQqz+haDnfnJTKBuxhcpnx3Adbleue/QnPf1hHYa8L+Rv8Pi5U\n"
    "h4V9FwHOfphdMXOxi14OqmsiTbc5cOs9/uukH+YVsuFdWTna6IVw1qh+tEtyH16R\n"
    "vmi7pkqyZYULOPMIE7avrljVVBZuikwARtY8tCVV6Pp9l3VeagBqb2ffgqNJt3C0\n"
    "TYNYQI+BXG1R1cABlold\n"
    "-----END CERTIFICATE-----\n"
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent urllib from auto-following HTTP redirects so 3xx hops are explicit."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


class _SourceBoundHTTPHandler(urllib.request.HTTPHandler):
    """HTTPHandler that binds outbound sockets to a local IPv4 source address when set."""

    def __init__(self, source_ip: str | None = None) -> None:
        super().__init__()
        self.source_ip = source_ip

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        if self.source_ip:
            return self.do_open(
                http.client.HTTPConnection,
                req,
                source_address=(self.source_ip, 0),
            )
        return super().http_open(req)


class _SourceBoundHTTPSHandler(urllib.request.HTTPSHandler):
    """HTTPSHandler that binds outbound sockets to a local IPv4 source address when set."""

    def __init__(
        self,
        context: ssl.SSLContext | None = None,
        source_ip: str | None = None,
    ) -> None:
        super().__init__(context=context)
        self.source_ip = source_ip

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        if self.source_ip:
            return self.do_open(
                http.client.HTTPSConnection,
                req,
                context=self._context,
                source_address=(self.source_ip, 0),
            )
        return super().https_open(req)


def prioritize_local_ipv4s(candidates: list[str]) -> list[str]:
    """Order local IPv4s so tethering interfaces (10.*, secondary 192.168.x.*) precede Wi-Fi/vEthernet."""

    def sort_key(ip: str) -> tuple[int, str]:
        if ip.startswith("10."):
            return (0, ip)
        if ip.startswith("192.168.") and not ip.startswith("192.168.0."):
            return (1, ip)
        if ip.startswith("172."):
            return (3, ip)
        return (2, ip)

    return sorted(candidates, key=sort_key)


def discover_local_ipv4s() -> list[str]:
    """Return prioritized non-loopback, non-link-local local IPv4 addresses available on this host."""
    try:
        _, _, ips = socket.gethostbyname_ex(socket.gethostname())
    except OSError:
        return []
    seen: set[str] = set()
    candidates: list[str] = []
    for ip in ips:
        if not ip or ip in seen:
            continue
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        seen.add(ip)
        candidates.append(ip)
    return prioritize_local_ipv4s(candidates)


def get_credentials_file_path(custom_path: str | None = None) -> str:
    """Return path to the user-scoped encrypted credential vault (~/.hemis/credentials.json)."""
    raw = (custom_path or os.environ.get("HEMIS_CREDENTIALS_FILE") or "").strip()
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    return os.path.join(os.path.expanduser("~"), ".hemis", "credentials.json")


def mask_identifier(value: str) -> str:
    """Return a masked login hint (e.g. 'ab***yz') so raw identifiers are never printed."""
    clean = value.strip()
    if not clean:
        return ""
    if len(clean) <= 4:
        return clean[0] + "***"
    return f"{clean[:2]}***{clean[-2:]}"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_encrypt(plaintext: bytes) -> bytes:
    """Encrypt bytes using Windows user-scoped DPAPI (CryptProtectData)."""
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    buf = ctypes.create_string_buffer(plaintext, len(plaintext))
    in_blob = _DataBlob(len(plaintext), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "hemis-skill-vault",
        None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    ):
        raise OSError("Windows DPAPI CryptProtectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_decrypt(ciphertext: bytes) -> bytes:
    """Decrypt bytes using Windows user-scoped DPAPI (CryptUnprotectData)."""
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    buf = ctypes.create_string_buffer(ciphertext, len(ciphertext))
    in_blob = _DataBlob(len(ciphertext), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    ):
        raise OSError("Windows DPAPI CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _posix_machine_key(salt: bytes) -> bytes:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    seed = f"hemis-skill-v1|{user}|{socket.gethostname()}|{os.path.expanduser('~')}".encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", seed, salt, 60000, dklen=32)


def _seal_secret_bytes(plaintext: bytes) -> tuple[str, str]:
    """Seal secret bytes with Windows DPAPI when available, or POSIX 0600 PBKDF2-HMAC stream."""
    if os.name == "nt" and hasattr(ctypes, "windll"):
        try:
            enc = _dpapi_encrypt(plaintext)
            return "windows-dpapi", base64.b64encode(enc).decode("ascii")
        except OSError:
            pass
    salt = os.urandom(16)
    key = _posix_machine_key(salt)
    stream = b"".join(
        hmac.new(key, salt + i.to_bytes(4, "big"), hashlib.sha256).digest()
        for i in range((len(plaintext) // 32) + 1)
    )[: len(plaintext)]
    xored = bytes(a ^ b for a, b in zip(plaintext, stream))
    mac = hmac.new(key, salt + xored, hashlib.sha256).digest()
    return "posix-0600-sealed", base64.b64encode(salt + mac + xored).decode("ascii")


def _unseal_secret_bytes(scheme: str, encoded: str) -> bytes:
    raw = base64.b64decode(encoded.encode("ascii"))
    if scheme == "windows-dpapi":
        return _dpapi_decrypt(raw)
    if scheme == "posix-0600-sealed":
        if len(raw) < 48:
            raise ValueError("Corrupted sealed credential blob.")
        salt, mac, xored = raw[:16], raw[16:48], raw[48:]
        key = _posix_machine_key(salt)
        expected_mac = hmac.new(key, salt + xored, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            raise ValueError("Credential vault integrity check failed.")
        stream = b"".join(
            hmac.new(key, salt + i.to_bytes(4, "big"), hashlib.sha256).digest()
            for i in range((len(xored) // 32) + 1)
        )[: len(xored)]
        return bytes(a ^ b for a, b in zip(xored, stream))
    raise ValueError(f"Unsupported credential vault scheme: {scheme!r}")


def save_credentials(
    login: str,
    password: str,
    base_url: str | None = None,
    file_path: str | None = None,
) -> dict[str, Any]:
    """Encrypt and store credentials in ~/.hemis/credentials.json (outside any git repo)."""
    clean_login = (login or "").strip()
    if not clean_login or not password:
        raise ValueError("Both login and password are required to save credentials.")
    path = get_credentials_file_path(file_path)
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        if os.name != "nt":
            os.chmod(parent, 0o700)
    except OSError:
        pass

    secret_obj: dict[str, str] = {"login": clean_login, "password": password}
    if base_url:
        secret_obj["base_url"] = base_url.strip().rstrip("/")
    plaintext = json.dumps(secret_obj, ensure_ascii=False).encode("utf-8")
    scheme, ciphertext_b64 = _seal_secret_bytes(plaintext)
    envelope = {
        "version": 1,
        "scheme": scheme,
        "login_masked": mask_identifier(clean_login),
        "base_url": secret_obj.get("base_url"),
        "ciphertext_b64": ciphertext_b64,
    }
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(envelope, fh, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return {
        "saved": True,
        "path": path,
        "scheme": scheme,
        "login_masked": envelope["login_masked"],
        "base_url": envelope["base_url"],
    }


def load_credentials(file_path: str | None = None) -> dict[str, str] | None:
    """Load and decrypt credentials from ~/.hemis/credentials.json into memory."""
    path = get_credentials_file_path(file_path)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        envelope = json.load(fh)
    if not isinstance(envelope, dict) or not envelope.get("ciphertext_b64"):
        return None
    scheme = str(envelope.get("scheme") or "")
    decrypted = _unseal_secret_bytes(scheme, str(envelope["ciphertext_b64"]))
    data = json.loads(decrypted.decode("utf-8"))
    if not isinstance(data, dict) or not data.get("login") or not data.get("password"):
        return None
    return {str(k): str(v) for k, v in data.items() if v is not None}


def clear_credentials(file_path: str | None = None) -> dict[str, Any]:
    """Remove stored credentials from ~/.hemis/credentials.json."""
    path = get_credentials_file_path(file_path)
    existed = os.path.isfile(path)
    if existed:
        os.remove(path)
    return {"cleared": True, "existed": existed, "path": path}


def resolve_credentials(
    login: str | None = None,
    password: str | None = None,
    file_path: str | None = None,
) -> tuple[str, str, str] | None:
    """Resolve (login, password, source) from explicit args -> env vars -> encrypted vault."""
    if login and password:
        return login, password, "argument"
    env_login = os.environ.get("HEMIS_ONEID_LOGIN", "").strip()
    env_pass = os.environ.get("HEMIS_ONEID_PASSWORD", "")
    if env_login and env_pass:
        return env_login, env_pass, "env"
    try:
        vault = load_credentials(file_path)
    except Exception:  # noqa: BLE001
        vault = None
    if vault and vault.get("login") and vault.get("password"):
        return vault["login"], vault["password"], "vault"
    return None


def get_credentials_status(file_path: str | None = None) -> dict[str, Any]:
    """Return non-secret credential availability report for agents and doctor checks."""
    path = get_credentials_file_path(file_path)
    env_login = os.environ.get("HEMIS_ONEID_LOGIN", "").strip()
    env_pass = os.environ.get("HEMIS_ONEID_PASSWORD", "")
    if env_login and env_pass:
        return {
            "configured": True,
            "source": "env",
            "login_masked": mask_identifier(env_login),
            "vault_path": path,
            "vault_exists": os.path.isfile(path),
        }
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                env_meta = json.load(fh)
            creds = load_credentials(file_path)
            if creds:
                return {
                    "configured": True,
                    "source": "vault",
                    "scheme": env_meta.get("scheme"),
                    "login_masked": mask_identifier(creds["login"]),
                    "base_url": creds.get("base_url"),
                    "vault_path": path,
                }
        except Exception as exc:  # noqa: BLE001
            return {
                "configured": False,
                "source": None,
                "vault_path": path,
                "error": f"Vault unreadable: {exc}",
            }
    return {
        "configured": False,
        "source": None,
        "vault_path": path,
        "hint": "Ask the user once for login/password and save via: python scripts/hemis_cli.py auth-save",
    }


class _PageMetaParser(HTMLParser):
    """Lightweight HTML parser for title, meta CSRF tags, hidden CSRF inputs, and visible text."""

    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self._skip_depth = 0
        self._title_parts: list[str] = []
        self._visible_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.hidden_inputs: dict[str, str] = {}
        self._capture_user_panel = False
        self._user_panel_depth = 0
        self.user_header_texts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = attr_map.get("name", "").strip()
            content = attr_map.get("content", "")
            if name:
                self.meta[name] = content
        elif tag == "input" and attr_map.get("type", "").lower() == "hidden":
            name = attr_map.get("name", "").strip()
            if name:
                self.hidden_inputs[name] = attr_map.get("value", "")

        if tag in ("li", "div") and "user-menu" in attr_map.get("class", ""):
            self._capture_user_panel = True
            self._user_panel_depth = 1
        elif self._capture_user_panel:
            self._user_panel_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

        if self._capture_user_panel:
            self._user_panel_depth -= 1
            if self._user_panel_depth <= 0:
                self._capture_user_panel = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._skip_depth == 0:
            cleaned = " ".join(data.split())
            if cleaned:
                self._visible_parts.append(cleaned)
        if self._capture_user_panel:
            text = " ".join(data.split())
            if text:
                self.user_header_texts.append(text)

    @property
    def title(self) -> str:
        return " ".join("".join(self._title_parts).split())

    @property
    def visible_text(self) -> str:
        return " ".join(self._visible_parts)


def _parse_page_meta(html: str) -> _PageMetaParser:
    parser = _PageMetaParser()
    parser.feed(html)
    parser.close()
    return parser


class _GridViewParser(HTMLParser):
    """Extract Yii2 GridView summary counts, table headers, rows, links, and toggle checkboxes."""

    def __init__(self) -> None:
        super().__init__()
        self._in_summary = False
        self._summary_depth = 0
        self._summary_parts: list[str] = []
        self._in_thead = False
        self._in_tbody = False
        self._in_th = False
        self._in_td = False
        self._cell_parts: list[str] = []
        self._cell_links: list[str] = []
        self.headers: list[str] = []
        self.rows: list[dict[str, Any]] = []
        self._current_row: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        raw_keys = {k.lower() for k, _ in attrs}
        cls = attr_map.get("class", "")

        if tag == "div" and "summary" in cls.split() and not self._in_summary:
            self._in_summary = True
            self._summary_depth = 1
        elif self._in_summary and tag == "div":
            self._summary_depth += 1

        if tag == "thead":
            self._in_thead = True
        elif tag == "tbody":
            self._in_tbody = True
        elif tag == "tr" and self._in_tbody:
            self._current_row = {
                "data_key": attr_map.get("data-key") or None,
                "active_id": None,
                "active_checked": None,
                "cells": [],
                "links": [],
            }
        elif tag == "th" and self._in_thead:
            self._in_th = True
            self._cell_parts = []
        elif tag == "td" and self._current_row is not None:
            self._in_td = True
            self._cell_parts = []
            self._cell_links = []
        elif tag == "a" and self._in_td:
            href = attr_map.get("href", "").strip()
            if href:
                self._cell_links.append(href)
        elif tag == "input" and self._current_row is not None:
            if attr_map.get("type", "").lower() == "checkbox" and attr_map.get("data-id"):
                self._current_row["active_id"] = attr_map.get("data-id")
                self._current_row["active_checked"] = "checked" in raw_keys

    def handle_endtag(self, tag: str) -> None:
        if self._in_summary and tag == "div":
            self._summary_depth -= 1
            if self._summary_depth <= 0:
                self._in_summary = False
        if tag == "thead":
            self._in_thead = False
        elif tag == "tbody":
            self._in_tbody = False
        elif tag == "th" and self._in_th:
            self._in_th = False
            self.headers.append(" ".join("".join(self._cell_parts).split()))
        elif tag == "td" and self._in_td and self._current_row is not None:
            self._in_td = False
            self._current_row["cells"].append(" ".join("".join(self._cell_parts).split()))
            self._current_row["links"].extend(self._cell_links)
        elif tag == "tr" and self._current_row is not None:
            if self._current_row["cells"] or self._current_row["data_key"] is not None:
                self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._in_summary:
            self._summary_parts.append(data)
        if self._in_th or self._in_td:
            self._cell_parts.append(data)

    @property
    def summary_text(self) -> str:
        return " ".join("".join(self._summary_parts).split())


def parse_gridview(html: str) -> dict[str, Any]:
    """Parse a Yii2 GridView HTML page into summary counts, column headers, and row records."""
    parser = _GridViewParser()
    parser.feed(html)
    parser.close()
    summary_text = parser.summary_text
    begin = end = total = None
    m_uz = re.search(r"Jami\s+(\d+)\s+ta\b.*?(\d+)\s*-\s*(\d+)", summary_text, re.IGNORECASE)
    m_en = re.search(r"(\d+)\s*-\s*(\d+)\s+of\s+(\d+)", summary_text, re.IGNORECASE)
    if m_uz:
        total, begin, end = int(m_uz.group(1)), int(m_uz.group(2)), int(m_uz.group(3))
    elif m_en:
        begin, end, total = int(m_en.group(1)), int(m_en.group(2)), int(m_en.group(3))
    return {
        "summary": {
            "text": summary_text or None,
            "begin": begin,
            "end": end,
            "total": total,
        },
        "headers": parser.headers,
        "rows": parser.rows,
    }


def parse_timetable_events(html: str) -> list[dict[str, Any]]:
    """Extract FullCalendar events JSON list from /teacher/time-table HTML."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r'(?:"events"|events)\s*:\s*(\[)', html):
        start_idx = match.start(1)
        try:
            parsed, _ = decoder.raw_decode(html[start_idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            events: list[dict[str, Any]] = []
            for item in parsed:
                if isinstance(item, dict):
                    entry = dict(item)
                    raw_title = str(entry.get("title", ""))
                    clean_text = " | ".join(
                        part.strip()
                        for part in re.sub(r"<[^>]+>", "\n", raw_title).splitlines()
                        if part.strip()
                    )
                    entry["title_text"] = clean_text
                    events.append(entry)
            return events
    return []


def parse_resource_item_files(html: str) -> list[dict[str, Any]]:
    """Extract attached file metadata from /file-resource/edit?id=...&item=... HTML."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r'(?:"files"|files)\s*:\s*(\[)', html):
        start_idx = match.start(1)
        try:
            parsed, _ = decoder.raw_decode(html[start_idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list) and all(isinstance(x, dict) for x in parsed):
            return [dict(x) for x in parsed]

    # Fallback: hidden inputs ESubjectFileResourceItem[files][idx][field]
    meta = _parse_page_meta(html)
    grouped: dict[int, dict[str, Any]] = {}
    pat = re.compile(r"^ESubjectFileResourceItem\[files\]\[(\d+)\]\[([^\]]+)\]$")
    for name, value in meta.hidden_inputs.items():
        m = pat.match(name)
        if m:
            idx = int(m.group(1))
            key = m.group(2)
            grouped.setdefault(idx, {})[key] = int(value) if key in ("size", "order") and value.isdigit() else value
    return [grouped[k] for k in sorted(grouped)]


def auto_parse_hemis_html(html: str, path_hint: str = "") -> dict[str, Any]:
    """Auto-detect and parse FullCalendar timetable, resource files, or Yii2 GridView HTML."""
    lower_path = path_hint.lower()
    if "time-table" in lower_path or ('"events":[' in html.replace(" ", "") and "fullcalendar" in html.lower()):
        events = parse_timetable_events(html)
        if events or "time-table" in lower_path:
            return {"type": "timetable", "count": len(events), "events": events}
    if ("item=" in lower_path and "file-resource/edit" in lower_path) or "esubjectfileresourceitem-files" in html:
        files = parse_resource_item_files(html)
        if files or ("item=" in lower_path and "file-resource/edit" in lower_path):
            return {"type": "resource_files", "count": len(files), "files": files}
    grid = parse_gridview(html)
    if grid["rows"] or grid["headers"] or grid["summary"]["text"]:
        return {"type": "gridview", **grid}
    meta = _parse_page_meta(html)
    return {"type": "page_meta", "title": meta.title, "visible_text": meta.visible_text[:4000]}


def _strip_headers(headers: dict[str, str], names_to_remove: set[str]) -> dict[str, str]:
    """Return a copy of headers with any matching names removed case-insensitively."""
    lowered = {n.lower() for n in names_to_remove}
    return {k: v for k, v in headers.items() if k.lower() not in lowered}


def _decompress_body(raw_bytes: bytes, content_encoding: str | None) -> bytes:
    """Decompress gzip or deflate HTTP payloads when Content-Encoding is set."""
    if not raw_bytes or not content_encoding:
        return raw_bytes
    enc = content_encoding.lower()
    if "gzip" in enc:
        try:
            return gzip.decompress(raw_bytes)
        except OSError:
            return raw_bytes
    if "deflate" in enc:
        try:
            return zlib.decompress(raw_bytes)
        except zlib.error:
            try:
                return zlib.decompress(raw_bytes, -zlib.MAX_WBITS)
            except zlib.error:
                return raw_bytes
    return raw_bytes


def _is_unsafe_hemis_get(url: str, base_host: str) -> str | None:
    """Return a reason string if url matches a known state-mutating HEMIS GET route."""
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc and parsed.netloc.lower() != base_host:
        return None
    path = parsed.path.rstrip("/") or "/"
    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    if path == "/message/compose" and not (qs.get("id") and qs["id"][0]):
        return "bare /message/compose creates a blank message draft"
    if path == "/dashboard/toggle":
        return "/dashboard/toggle mutates active/face flags"
    if path.endswith("/switch-role") or path == "/switch-role":
        return "switch-role changes the active role of the shared cookie session"
    if path == "/dashboard/logout":
        return "/dashboard/logout terminates the active session"
    if path == "/poll/mine" and "1" in qs.get("set", []):
        return "/poll/mine?set=1 casts a poll vote"
    if "1" in qs.get("delete", []):
        return "delete=1 deletes a record"
    if "1" in qs.get("cancel", []):
        return "cancel=1 cancels/removes an item"
    if path == "/dashboard/profile" and "1" in qs.get("certificate", []):
        return "/dashboard/profile?certificate=1 triggers UzBMB sync"
    if path in ("/oz", "/ru", "/en") or path.startswith(("/oz/", "/ru/", "/en/")):
        return "locale prefix route mutates session language"
    if path == "/student/reference" and "1" in qs.get("get", []):
        return "/student/reference?get=1 creates an official certificate"
    if path in ("/student-data/rent-sync", "/student-data/upload-plagiarism"):
        return f"{path} performs a state-mutating student action"
    return None


class HemisOneIDClient:
    """HTTP session client for authenticating into HEMIS via OneID."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 25,
        user_agent: str = DEFAULT_USER_AGENT,
        step_delay: float = 0.35,
        max_retries: int = 2,
        retry_backoff: float = 1.5,
        source_ip: str | None = None,
        auto_interface_failover: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.base_host = urllib.parse.urlparse(self.base_url).netloc.lower()
        self.timeout = timeout
        self.user_agent = user_agent
        self.step_delay = step_delay
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.source_ip = source_ip
        self.auto_interface_failover = auto_interface_failover
        self.ssl_context = ssl.create_default_context()
        try:
            self.ssl_context.load_verify_locations(cadata=THAWTE_TLS_RSA_CA_G1_PEM)
        except ssl.SSLError:
            pass
        self.cookie_jar = http.cookiejar.CookieJar()
        self._rebuild_opener()
        self.csrf_param: str | None = None
        self.csrf_token: str | None = None
        self._elfinder_csrf: str | None = None
        self._student_bearer_token: str | None = None
        self.upload_timeout = 180
        self._login: str | None = None
        self._password: str | None = None

    parse_gridview = staticmethod(parse_gridview)
    parse_timetable_events = staticmethod(parse_timetable_events)
    parse_resource_item_files = staticmethod(parse_resource_item_files)
    auto_parse_hemis_html = staticmethod(auto_parse_hemis_html)

    def _rebuild_opener(self) -> None:
        """Rebuild the urllib opener with active CookieJar and optional source_ip binding."""
        self.opener = urllib.request.build_opener(
            _SourceBoundHTTPHandler(source_ip=self.source_ip),
            _SourceBoundHTTPSHandler(context=self.ssl_context, source_ip=self.source_ip),
            urllib.request.HTTPCookieProcessor(self.cookie_jar),
            NoRedirect(),
        )

    def _try_interface_failover(self, target_url: str) -> bool:
        """Probe alternate local IPv4 interfaces (or OS default route if bound IP went stale)."""
        parsed = urllib.parse.urlparse(target_url)
        host = parsed.hostname
        if not host:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        probe_timeout = min(float(self.timeout), 3.0)

        for candidate_ip in discover_local_ipv4s():
            if candidate_ip == self.source_ip:
                continue
            try:
                with socket.create_connection(
                    (host, port), timeout=probe_timeout, source_address=(candidate_ip, 0)
                ):
                    self.source_ip = candidate_ip
                    self._rebuild_opener()
                    return True
            except OSError:
                continue

        # If an explicit/cached source_ip disconnected (e.g. Bluetooth PAN turned off),
        # fall back to the OS default route when reachable.
        if self.source_ip is not None:
            try:
                with socket.create_connection((host, port), timeout=probe_timeout):
                    self.source_ip = None
                    self._rebuild_opener()
                    return True
            except OSError:
                pass
        return False

    def _pause(self) -> None:
        if self.step_delay > 0:
            time.sleep(self.step_delay)

    def _resolve_url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return urllib.parse.urljoin(f"{self.base_url}/", path_or_url.lstrip("/"))

    @staticmethod
    def _get_header(headers: dict[str, str], name: str) -> str | None:
        target = name.lower()
        for key, value in headers.items():
            if key.lower() == target:
                return value
        return None

    def _raw_open_bytes(
        self,
        url: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        retry_unsafe: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        req_headers = {"User-Agent": self.user_agent}
        if headers:
            req_headers.update(headers)
        # Never blindly re-send non-idempotent requests (uploads, form posts): an ambiguous
        # timeout may already have created the record. Callers must read back instead.
        can_retry = retry_unsafe or method.upper() in ("GET", "HEAD", "OPTIONS")

        failed_over = False
        attempt = 0
        while True:
            req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
            try:
                with self.opener.open(req, timeout=self.timeout) as resp:
                    resp_headers = dict(resp.headers.items())
                    raw_bytes = _decompress_body(
                        resp.read(), self._get_header(resp_headers, "Content-Encoding")
                    )
                    if resp.status in (502, 503, 504) and can_retry and attempt < self.max_retries:
                        time.sleep(self.retry_backoff * (2 ** attempt))
                        attempt += 1
                        continue
                    return resp.status, resp_headers, raw_bytes
            except urllib.error.HTTPError as exc:
                resp_headers = dict(exc.headers.items())
                raw_bytes = _decompress_body(
                    exc.read(), self._get_header(resp_headers, "Content-Encoding")
                )
                if exc.code in (502, 503, 504) and can_retry and attempt < self.max_retries:
                    time.sleep(self.retry_backoff * (2 ** attempt))
                    attempt += 1
                    continue
                return exc.code, resp_headers, raw_bytes
            except (
                urllib.error.URLError,
                OSError,
                http.client.HTTPException,
            ):
                if (
                    self.auto_interface_failover
                    and can_retry
                    and not failed_over
                    and self._try_interface_failover(url)
                ):
                    failed_over = True
                    continue
                if can_retry and attempt < self.max_retries:
                    time.sleep(self.retry_backoff * (2 ** attempt))
                    attempt += 1
                    continue
                raise

    def _raw_open(
        self,
        url: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        retry_unsafe: bool = False,
    ) -> tuple[int, dict[str, str], str]:
        status, resp_headers, raw_bytes = self._raw_open_bytes(
            url, method=method, data=data, headers=headers, retry_unsafe=retry_unsafe
        )
        return status, resp_headers, raw_bytes.decode("utf-8", errors="replace")

    def request_bytes(
        self,
        path_or_url: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
        max_redirects: int = 5,
        allow_unsafe: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        """Send an HTTP request returning raw (decompressed) bytes with safe redirect hygiene."""
        current_url = self._resolve_url(path_or_url)
        current_method = method.upper()
        current_data = data
        current_headers = dict(headers) if headers else {}
        hops = 0

        while True:
            if current_method == "GET" and not allow_unsafe:
                unsafe_reason = _is_unsafe_hemis_get(current_url, self.base_host)
                if unsafe_reason:
                    raise ValueError(
                        f"Blocked state-mutating GET request ({unsafe_reason}). "
                        "Pass allow_unsafe=True if intentional."
                    )

            status, resp_headers, body = self._raw_open_bytes(
                current_url,
                method=current_method,
                data=current_data,
                headers=current_headers,
            )
            if not follow_redirects or status not in REDIRECT_CODES:
                return status, resp_headers, body

            location = self._get_header(resp_headers, "Location")
            if not location:
                return status, resp_headers, body

            hops += 1
            if hops > max_redirects:
                raise RuntimeError(f"Too many redirects (> {max_redirects}) at {current_url}")

            next_url = urllib.parse.urljoin(current_url, location)
            prev_host = urllib.parse.urlparse(current_url).netloc.lower()
            next_host = urllib.parse.urlparse(next_url).netloc.lower()

            if status in (301, 302, 303) and current_method != "HEAD":
                current_method = "GET"
                current_data = None
                current_headers = _strip_headers(
                    current_headers, {"Content-Type", "Content-Length"}
                )

            if next_host != prev_host:
                current_headers = _strip_headers(
                    current_headers,
                    {"Authorization", "X-CSRF-Token", "X-elFinder-CSRF", "Origin", "Referer"},
                )
            elif any(k.lower() == "referer" for k in current_headers):
                current_headers = _strip_headers(current_headers, {"Referer"})
                current_headers["Referer"] = current_url

            current_url = next_url

    def request(
        self,
        path_or_url: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
        max_redirects: int = 5,
        allow_unsafe: bool = False,
    ) -> tuple[int, dict[str, str], str]:
        """Send an HTTP request returning decoded UTF-8 text."""
        status, resp_headers, raw_bytes = self.request_bytes(
            path_or_url,
            method=method,
            data=data,
            headers=headers,
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
            allow_unsafe=allow_unsafe,
        )
        return status, resp_headers, raw_bytes.decode("utf-8", errors="replace")

    def download_resource_bytes(
        self, path_or_url: str, follow_redirects: bool = True
    ) -> tuple[bytes, dict[str, str]]:
        """Download a binary resource and reject HTML error/login pages."""
        status, resp_headers, raw_bytes = self.request_bytes(
            path_or_url, method="GET", follow_redirects=follow_redirects
        )
        if status != 200:
            raise RuntimeError(f"Download failed for {path_or_url}: HTTP {status}")
        content_type = (self._get_header(resp_headers, "Content-Type") or "").lower()
        if "text/html" in content_type:
            raise RuntimeError(
                f"Download failed for {path_or_url}: expected binary payload, got Content-Type '{content_type}'"
            )
        return raw_bytes, resp_headers

    def _oneid_json_post(
        self,
        url: str,
        payload: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "X-Requested-With": "XMLHttpRequest",
            "X-Origin": "web-client",
            "Accept-Language": "oz",
            "Origin": "https://id.egov.uz",
            "Referer": "https://id.egov.uz/",
        }
        if extra_headers:
            headers.update(extra_headers)

        body_bytes = json.dumps(payload).encode("utf-8")
        status, _, resp_text = self._raw_open(
            url, method="POST", data=body_bytes, headers=headers, retry_unsafe=True
        )
        try:
            parsed = json.loads(resp_text) if resp_text else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"OneID endpoint {url} returned non-JSON response (HTTP {status})"
            ) from exc

        if status < 200 or status >= 300:
            oneid_code = parsed.get("code")
            err_msg = parsed.get("message") or parsed.get("error") or f"HTTP {status}"
            if oneid_code == 72:
                raise RuntimeError(
                    f"OneID request to {url} failed ({status}, code=72): "
                    f"SSO scope/TIN/session error ({err_msg})"
                )
            raise RuntimeError(
                f"OneID request to {url} failed ({status}, code={oneid_code}): {err_msg}"
            )
        return status, parsed

    def get_cookie_names(self) -> list[str]:
        """Return unique cookie names stored for the HEMIS portal domain."""
        base_hostname = self.base_host.split(":", 1)[0]
        names: set[str] = set()
        for cookie in self.cookie_jar:
            cookie_domain = cookie.domain.lstrip(".").lower().split(":", 1)[0]
            if (
                not cookie_domain
                or cookie_domain == base_hostname
                or base_hostname.endswith("." + cookie_domain)
            ):
                names.add(cookie.name)
        return sorted(names)

    def get_csrf_token(self, html: str | None = None) -> str:
        """Extract or refresh the Yii2 CSRF token from meta tags or hidden form inputs."""
        if html is not None:
            parsed = _parse_page_meta(html)
            csrf_param = parsed.meta.get("csrf-param") or "_csrf-backend"
            token = (
                parsed.meta.get("csrf-token")
                or parsed.hidden_inputs.get(csrf_param)
                or parsed.hidden_inputs.get("_csrf-backend")
                or parsed.hidden_inputs.get("_csrf-frontend")
            )
            if token:
                self.csrf_param = csrf_param
                self.csrf_token = token
                return token
            raise RuntimeError("CSRF token meta tag or hidden input not found in provided HTML.")

        if self.csrf_token:
            return self.csrf_token

        status, _, profile_html = self.request("/dashboard/profile", follow_redirects=False)
        if status != 200:
            raise RuntimeError(
                f"Cannot refresh CSRF token; /dashboard/profile returned HTTP {status}"
            )
        return self.get_csrf_token(profile_html)

    def post_form(
        self,
        path_or_url: str,
        data_dict: dict[str, Any],
        referer_path: str = "/dashboard/profile",
        headers: dict[str, str] | None = None,
        follow_redirects: bool = False,
    ) -> tuple[int, dict[str, str], str]:
        """Submit a Yii2 URL-encoded POST form with matching CSRF parameter and header."""
        csrf_token = self.get_csrf_token()
        csrf_param = self.csrf_param or "_csrf-backend"
        payload = dict(data_dict)
        payload.setdefault(csrf_param, csrf_token)

        req_headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-CSRF-Token": csrf_token,
            "Origin": self.base_url,
            "Referer": self._resolve_url(referer_path),
        }
        if headers:
            req_headers.update(headers)

        encoded = urllib.parse.urlencode(payload, doseq=True).encode("utf-8")
        return self.request(
            path_or_url,
            method="POST",
            data=encoded,
            headers=req_headers,
            follow_redirects=follow_redirects,
        )

    @staticmethod
    def normalize_resource_path(path: str) -> str:
        """Percent-encode a stored static file path exactly once (avoids %20 -> %2520 false 404s)."""
        return urllib.parse.quote(urllib.parse.unquote(path), safe="/")

    @staticmethod
    def validate_hemis_filename(filename: str, size_bytes: int | None = None) -> str:
        """Validate filename and size against HEMIS elFinder rules (reject '+' and >5 MiB)."""
        if not filename or not filename.strip():
            raise ValueError("Filename must not be empty.")
        if "/" in filename or "\\" in filename or filename in (".", ".."):
            raise ValueError(f"Filename must be a single path segment without slashes: {filename!r}")
        if "\r" in filename or "\n" in filename or '"' in filename:
            raise ValueError(f"Filename contains unsafe control or quote characters: {filename!r}")
        if "+" in filename:
            raise ValueError(
                "Filename must not contain '+' (triggers HEMIS download bug); use '_' or '-' instead."
            )
        if size_bytes is not None and size_bytes > MAX_ELFINDER_UPLOAD_BYTES:
            raise ValueError(
                f"File size {size_bytes} exceeds HEMIS 5 MiB limit ({MAX_ELFINDER_UPLOAD_BYTES} bytes)."
            )
        return filename

    @staticmethod
    def build_multipart_form_data(
        fields: dict[str, Any],
        files: list[tuple[str, str, bytes, str]] | None = None,
    ) -> tuple[bytes, str]:
        """Build an RFC 7578 multipart/form-data payload and matching Content-Type header."""
        boundary = f"----HemisBoundary{uuid.uuid4().hex}"
        chunks: list[bytes] = []

        for key, value in fields.items():
            safe_key = str(key).replace('"', "").replace("\r", "").replace("\n", "")
            chunks.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{safe_key}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )

        for field_name, filename, file_bytes, content_type in files or []:
            clean_name = HemisOneIDClient.validate_hemis_filename(filename, len(file_bytes))
            safe_field = str(field_name).replace('"', "").replace("\r", "").replace("\n", "")
            safe_ctype = str(content_type or "application/octet-stream").replace("\r", "").replace("\n", "")
            chunks.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{safe_field}"; filename="{clean_name}"\r\n'
                    f"Content-Type: {safe_ctype}\r\n\r\n"
                ).encode("utf-8")
            )
            chunks.append(file_bytes)
            chunks.append(b"\r\n")

        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    def clear_resource_filters(self) -> tuple[int, dict[str, str], str]:
        """Reset all 6 sticky Yii2 session filters on /file-resource/index."""
        query = urllib.parse.urlencode(
            [
                ("ESubjectResource[_curriculum]", ""),
                ("ESubjectResource[_curriculum_subject]", ""),
                ("ESubjectResource[_training_type]", ""),
                ("ESubjectResource[_employee]", ""),
                ("ESubjectResource[_department]", ""),
                ("ESubjectResource[language]", ""),
            ]
        )
        return self.request(f"/file-resource/index?{query}", follow_redirects=False)

    def query_depdrop_subjects(
        self,
        curriculum_id: str | int,
        referer_path: str = "/file-resource/edit",
    ) -> list[dict[str, Any]]:
        """Query DepDrop /file-resource/edit?subject=1 with depdrop_all_params[_curriculum]."""
        if not self.csrf_token:
            st, _, ref_html = self.request(referer_path, follow_redirects=False)
            if st != 200:
                raise RuntimeError(f"Cannot fetch CSRF from {referer_path}: HTTP {st}")
            self.get_csrf_token(ref_html)

        cid_str = str(curriculum_id)
        status, _, body = self.post_form(
            "/file-resource/edit?subject=1",
            {
                "depdrop_parents[0]": cid_str,
                "depdrop_all_params[_curriculum]": cid_str,
            },
            referer_path=referer_path,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
            follow_redirects=False,
        )
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"DepDrop query returned non-JSON response (HTTP {status}); check depdrop_all_params."
            ) from exc
        if status != 200 or not isinstance(parsed, dict) or not isinstance(parsed.get("output"), list):
            raise RuntimeError(f"Unexpected DepDrop response (HTTP {status}): {parsed}")
        return parsed["output"]

    def elfinder_open(
        self,
        target: str = "",
        init: bool = True,
        element_id: str = "esubjectfileresourceitem-files",
    ) -> dict[str, Any]:
        """Open elFinder /files/connector and capture any secondary X-elFinder-CSRF token."""
        params: list[tuple[str, str]] = [
            ("id", element_id),
            ("multiple", "1"),
            ("cmd", "open"),
        ]
        if init:
            params.append(("init", "1"))
        if target:
            params.append(("target", target))
        query = urllib.parse.urlencode(params)
        status, _, body = self.request(
            f"/files/connector?{query}",
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
            follow_redirects=False,
        )
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"elFinder /files/connector returned non-JSON response (HTTP {status})"
            ) from exc
        if status >= 400 or not isinstance(parsed, dict):
            raise RuntimeError(f"elFinder open failed (HTTP {status}): {parsed}")
        ef_csrf = parsed.get("csrf")
        if isinstance(ef_csrf, str) and ef_csrf:
            self._elfinder_csrf = ef_csrf
        return parsed

    def _elfinder_write(
        self,
        fields: dict[str, Any],
        element_id: str = "esubjectfileresourceitem-files",
    ) -> dict[str, Any]:
        """POST a non-upload elFinder command with Dual-CSRF and strict error reporting."""
        if not self._elfinder_csrf:
            self.elfinder_open(init=True, element_id=element_id)
        yii_csrf = self.get_csrf_token()
        csrf_param = self.csrf_param or "_csrf-backend"
        payload = dict(fields)
        payload[csrf_param] = yii_csrf
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": yii_csrf,
            "Origin": self.base_url,
            "Referer": self._resolve_url("/file-resource/edit"),
        }
        if self._elfinder_csrf:
            payload["csrf"] = self._elfinder_csrf
            headers["X-elFinder-CSRF"] = self._elfinder_csrf
        query = urllib.parse.urlencode([("id", element_id), ("multiple", "1")])
        status, _, text = self.request(
            f"/files/connector?{query}",
            method="POST",
            data=urllib.parse.urlencode(payload, doseq=True).encode("utf-8"),
            headers=headers,
            follow_redirects=False,
        )
        if status == 403:
            raise RuntimeError(
                "elFinder returned 403: stale connector CSRF (csrfReload) or the shared session's "
                "role changed in another browser window. Re-read /dashboard/profile for the current "
                "role; never guess role IDs or bypass the permission wall."
            )
        try:
            parsed = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"elFinder returned non-JSON response (HTTP {status})") from exc
        if status >= 400 or not isinstance(parsed, dict) or parsed.get("error"):
            raise RuntimeError(f"elFinder command {fields.get('cmd')} failed (HTTP {status}): {parsed}")
        return parsed

    def elfinder_mkdir(self, parent_hash: str, name: str) -> dict[str, Any]:
        """Create a folder in the personal elFinder storage (read back with elfinder_open)."""
        if not parent_hash:
            raise ValueError("parent_hash is required.")
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise ValueError(f"Invalid folder name: {name!r}")
        return self._elfinder_write({"cmd": "mkdir", "target": parent_hash, "name": name})

    def elfinder_copy(self, src_hash: str, dst_hash: str, target_hashes: list[str]) -> dict[str, Any]:
        """Copy (never move) files between elFinder folders; verify bytes before deleting anything."""
        if not (src_hash and dst_hash and target_hashes):
            raise ValueError("src_hash, dst_hash and target_hashes are required.")
        return self._elfinder_write(
            {
                "cmd": "paste",
                "src": src_hash,
                "dst": dst_hash,
                "targets[]": list(target_hashes),
                "cut": "0",
            }
        )

    def elfinder_upload_bytes(
        self,
        target_hash: str,
        filename: str,
        file_bytes: bytes,
        content_type: str = "application/pdf",
        elfinder_csrf: str | None = None,
        element_id: str = "esubjectfileresourceitem-files",
    ) -> dict[str, Any]:
        """Upload raw bytes to elFinder via Dual-CSRF (X-CSRF-Token + X-elFinder-CSRF)."""
        clean_name = self.validate_hemis_filename(filename, len(file_bytes))
        if not target_hash:
            raise ValueError("target_hash is required for elFinder upload.")

        ef_csrf = elfinder_csrf or self._elfinder_csrf
        if not ef_csrf:
            open_payload = self.elfinder_open(target=target_hash, init=True, element_id=element_id)
            raw_csrf = open_payload.get("csrf")
            if isinstance(raw_csrf, str) and raw_csrf:
                ef_csrf = raw_csrf
                self._elfinder_csrf = raw_csrf

        yii_csrf = self.get_csrf_token()
        csrf_param = self.csrf_param or "_csrf-backend"

        fields: dict[str, Any] = {
            "cmd": "upload",
            "target": target_hash,
            csrf_param: yii_csrf,
        }
        if ef_csrf:
            fields["csrf"] = ef_csrf

        body_bytes, content_type_hdr = self.build_multipart_form_data(
            fields,
            [("upload[]", clean_name, file_bytes, content_type)],
        )

        req_headers = {
            "Content-Type": content_type_hdr,
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": yii_csrf,
            "Origin": self.base_url,
            "Referer": self._resolve_url("/file-resource/edit"),
        }
        if ef_csrf:
            req_headers["X-elFinder-CSRF"] = ef_csrf

        query = urllib.parse.urlencode([("id", element_id), ("multiple", "1")])
        prev_timeout = self.timeout
        self.timeout = max(self.timeout, self.upload_timeout)  # slow tethering: long write phase
        try:
            status, _, resp_text = self.request(
                f"/files/connector?{query}",
                method="POST",
                data=body_bytes,
                headers=req_headers,
                follow_redirects=False,
            )
        finally:
            self.timeout = prev_timeout
        if status == 403:
            raise RuntimeError(
                "elFinder upload returned 403: stale connector CSRF (csrfReload) or the session's "
                "role changed elsewhere. Re-check the profile role; do not guess role IDs."
            )
        try:
            parsed = json.loads(resp_text) if resp_text else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"elFinder upload returned non-JSON response (HTTP {status})"
            ) from exc

        if status >= 400 or not isinstance(parsed, dict) or (parsed.get("error") and not parsed.get("added")):
            raise RuntimeError(f"elFinder upload failed (HTTP {status}): {parsed}")
        return parsed

    def verify_session(self) -> dict[str, Any]:
        """Verify live HEMIS session via GET /dashboard/profile with NoRedirect."""
        status, headers, html = self.request("/dashboard/profile", follow_redirects=False)
        location = self._get_header(headers, "Location")

        if status in REDIRECT_CODES:
            return {
                "authenticated": False,
                "status": status,
                "redirect_location": location,
                "profile_title": None,
                "cookie_names": self.get_cookie_names(),
                "has_csrf_token": False,
                "csrf_param": None,
                "csrf_token_length": 0,
                "source_ip": self.source_ip,
                "user_summary": None,
            }

        parsed = _parse_page_meta(html)
        is_login_html = (
            "authclient=oneid" in html
            or "FormAdminLogin" in html
            or parsed.title.lower().startswith("login")
        )
        if status != 200 or is_login_html:
            result = {
                "authenticated": False,
                "status": status,
                "redirect_location": location,
                "profile_title": parsed.title or None,
                "cookie_names": self.get_cookie_names(),
                "has_csrf_token": False,
                "csrf_param": None,
                "csrf_token_length": 0,
                "source_ip": self.source_ip,
                "user_summary": None,
            }
            if status == 451:
                result["geo_blocked"] = True
            return result

        csrf_param = parsed.meta.get("csrf-param") or "_csrf-backend"
        csrf_token = (
            parsed.meta.get("csrf-token")
            or parsed.hidden_inputs.get(csrf_param)
            or parsed.hidden_inputs.get("_csrf-backend")
        )
        if csrf_token:
            self.csrf_param = csrf_param
            self.csrf_token = csrf_token

        visible = parsed.visible_text
        uid_match = re.search(r"\bUID\s*[:·]*\s*(\d+)", visible)
        version_match = re.search(r"Dastur versiyasi\s*[:·]*\s*([0-9]+(?:\.[0-9]+)+)", visible)
        core_match = re.search(r"\bYadro\s*[:·]*\s*([0-9]+(?:\.[0-9]+)+)", visible)
        user_header = " ".join(parsed.user_header_texts[:2]) if parsed.user_header_texts else None
        user_summary = {
            "header_label": user_header,
            "uid": uid_match.group(1) if uid_match else None,
            "version": version_match.group(1) if version_match else None,
            "core_version": core_match.group(1) if core_match else None,
        }

        return {
            "authenticated": True,
            "status": status,
            "profile_title": parsed.title,
            "cookie_names": self.get_cookie_names(),
            "has_csrf_token": bool(csrf_token),
            "csrf_param": csrf_param if csrf_token else None,
            "csrf_token_length": len(csrf_token) if csrf_token else 0,
            "source_ip": self.source_ip,
            "user_summary": user_summary,
        }

    def ensure_session(
        self, login: str | None = None, password: str | None = None
    ) -> dict[str, Any]:
        """Return active session verification, re-authenticating automatically if expired."""
        try:
            verification = self.verify_session()
        except (
            urllib.error.URLError,
            ssl.SSLError,
            TimeoutError,
            OSError,
            http.client.HTTPException,
        ) as exc:
            raise RuntimeError(
                f"Network/TLS error while checking HEMIS session (not treating as expired): {exc}"
            ) from exc

        if verification["authenticated"]:
            return {"reauthenticated": False, "verification": verification}

        if verification["status"] >= 500:
            raise RuntimeError(
                f"HEMIS gateway/server error (HTTP {verification['status']}), not treating as expired."
            )

        use_login = login or self._login
        use_password = password or self._password
        if not use_login or not use_password:
            raise RuntimeError(
                "HEMIS session expired and no credentials are available for re-authentication."
            )

        auth_result = self.login_with_oneid(use_login, use_password)
        return {
            "reauthenticated": True,
            "auth": auth_result,
            "verification": auth_result["steps"]["step6_profile_verify"],
        }

    def keepalive_ping(self) -> dict[str, Any]:
        """Call /keepalive/ping?t=<epoch_ms> to confirm active session status."""
        epoch_ms = int(time.time() * 1000)
        status, _, body = self.request(
            f"/keepalive/ping?t={epoch_ms}",
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
            follow_redirects=False,
        )
        parsed = None
        if body:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = None
        return {
            "status": status,
            "ok": bool(status == 200 and isinstance(parsed, dict) and parsed.get("ok")),
            "response": parsed,
        }

    def _resolve_student_base_url(self, student_base_url: str | None = None) -> str:
        """Resolve student.<uni>.uz base URL from explicit arg, HEMIS_STUDENT_URL, or base_url."""
        raw = (student_base_url or os.environ.get("HEMIS_STUDENT_URL") or "").strip().rstrip("/")
        if raw:
            return raw
        parsed = urllib.parse.urlparse(self.base_url)
        host = parsed.netloc.lower()
        if host.startswith("hemis."):
            host = "student." + host[len("hemis.") :]
        return f"{parsed.scheme or 'https'}://{host}"

    def fetch_public_stats(
        self,
        stat_type: str = "structure",
        student_base_url: str | None = None,
    ) -> dict[str, Any]:
        """Fetch public university statistics from GET /rest/v1/public/stat-{structure|student|employee}."""
        clean_type = stat_type.strip().lower()
        if clean_type not in ("structure", "student", "employee"):
            raise ValueError(
                f"Invalid stat_type {stat_type!r}; expected 'structure', 'student', or 'employee'."
            )
        s_base = self._resolve_student_base_url(student_base_url)
        url = f"{s_base}/rest/v1/public/stat-{clean_type}"
        status, _, text = self.request(
            url,
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        try:
            parsed = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Public stats returned non-JSON response (HTTP {status})") from exc
        if status >= 400 or not isinstance(parsed, dict):
            raise RuntimeError(f"Public stats request failed (HTTP {status}): {parsed}")
        return parsed

    def student_rest_login(
        self,
        login: str,
        password: str,
        student_base_url: str | None = None,
    ) -> dict[str, Any]:
        """Authenticate against Student REST API (/rest/v1/auth/login); keep Bearer token in memory only."""
        if not login or not password:
            raise ValueError("Both login and password are required for Student REST API.")
        s_base = self._resolve_student_base_url(student_base_url)
        url = f"{s_base}/rest/v1/auth/login"
        body = json.dumps({"login": login, "password": password}).encode("utf-8")
        status, _, text = self.request(
            url,
            method="POST",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            follow_redirects=False,
        )
        try:
            parsed = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Student REST login returned non-JSON (HTTP {status})") from exc
        data_obj = parsed.get("data") if isinstance(parsed, dict) else None
        token = (
            (data_obj.get("token") if isinstance(data_obj, dict) else None)
            or (parsed.get("token") if isinstance(parsed, dict) else None)
        )
        if status >= 400 or not isinstance(token, str) or not token:
            err = parsed.get("error") if isinstance(parsed, dict) else None
            raise RuntimeError(f"Student REST login failed (HTTP {status}): {err or 'no token'}")
        self._student_bearer_token = token
        return {
            "authenticated": True,
            "has_token": True,
            "student_base_url": s_base,
            "status": status,
        }

    def student_rest_get(
        self,
        endpoint: str,
        student_base_url: str | None = None,
    ) -> dict[str, Any]:
        """Call a Student REST API GET endpoint (/rest/v1/...) with the in-memory Bearer token."""
        if not self._student_bearer_token:
            raise RuntimeError("Student REST API requires student_rest_login() first.")
        s_base = self._resolve_student_base_url(student_base_url)
        clean_ep = endpoint.strip()
        if clean_ep.startswith(("http://", "https://")):
            url = clean_ep
        else:
            if not clean_ep.startswith("/"):
                clean_ep = "/" + clean_ep
            if not clean_ep.startswith("/rest/v1/"):
                clean_ep = "/rest/v1" + clean_ep
            url = f"{s_base}{clean_ep}"
        status, _, text = self.request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._student_bearer_token}",
            },
            follow_redirects=False,
        )
        try:
            parsed = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Student REST GET returned non-JSON (HTTP {status})") from exc
        if status >= 400 or not isinstance(parsed, dict):
            raise RuntimeError(f"Student REST GET failed (HTTP {status})")
        return parsed

    def login_with_oneid(self, login: str | None = None, password: str | None = None) -> dict[str, Any]:
        """Execute the complete 6-step OneID -> HEMIS login sequence."""
        if not login or not password:
            resolved = resolve_credentials(login, password)
            if resolved:
                login, password, _ = resolved
        if not login or not password:
            raise ValueError("Both login and password are required.")

        # Store in memory for automatic ensure_session() recovery and clear stale cookies/state
        self._login = login
        self._password = password
        self.cookie_jar.clear()
        self.csrf_param = None
        self.csrf_token = None
        self._elfinder_csrf = None

        steps: dict[str, Any] = {}

        # Step 1: Start OneID OAuth on HEMIS (sets initial backend_* cookie + state)
        step1_status, step1_headers, _ = self.request(
            "/dashboard/auth?authclient=oneid", follow_redirects=False
        )
        if step1_status == 451:
            raise RuntimeError(
                "Step 1 failed: HEMIS returned HTTP 451 (Kirish cheklangan / Geo-IP block: "
                "hemis.namdtu.uz only accepts Uzbekistan IP addresses)."
            )
        sso_location = self._get_header(step1_headers, "Location")
        if step1_status not in REDIRECT_CODES or not sso_location:
            raise RuntimeError(
                f"Step 1 failed: expected redirect from /dashboard/auth?authclient=oneid, got {step1_status}"
            )
        sso_location = urllib.parse.urljoin(f"{self.base_url}/", sso_location)
        steps["step1_hemis_auth_init"] = {
            "status": step1_status,
            "redirect_host": urllib.parse.urlparse(sso_location).netloc,
            "initial_cookies": self.get_cookie_names(),
        }

        # Step 2: Follow sso.egov.uz redirect(s) to id.egov.uz
        hop_url = sso_location
        step2_statuses: list[int] = []
        id_location: str | None = None
        if urllib.parse.urlparse(hop_url).netloc == "id.egov.uz":
            id_location = hop_url
            steps["step2_sso_authorize"] = {"status": step1_status, "skipped": True}
        else:
            for _ in range(5):
                self._pause()
                step2_status, step2_headers, _ = self.request(
                    hop_url, follow_redirects=False
                )
                step2_statuses.append(step2_status)
                next_loc = self._get_header(step2_headers, "Location")
                if step2_status not in REDIRECT_CODES or not next_loc:
                    raise RuntimeError(
                        f"Step 2 failed: expected redirect from sso.egov.uz, got {step2_status}"
                    )
                next_loc = urllib.parse.urljoin(hop_url, next_loc)
                if urllib.parse.urlparse(next_loc).netloc == "id.egov.uz":
                    id_location = next_loc
                    break
                hop_url = next_loc

            if not id_location:
                raise RuntimeError("Step 2 failed: never reached id.egov.uz redirect.")
            steps["step2_sso_authorize"] = {
                "status": step2_statuses[-1],
                "statuses": step2_statuses,
                "redirect_host": urllib.parse.urlparse(id_location).netloc,
            }

        qs = urllib.parse.parse_qs(urllib.parse.urlparse(id_location).query)
        client_id = (qs.get("client_id") or [""])[0]
        token_id = (qs.get("token_id") or [""])[0]
        auth_method = (qs.get("method") or ["IDPW"])[0]
        if not client_id or not token_id:
            raise RuntimeError("Step 2 failed: missing client_id or token_id in OneID redirect URL.")

        steps["step2_oneid_params"] = {
            "client_id": client_id,
            "method": auth_method,
            "token_id_is_uuid": bool(UUID_RE.match(token_id)),
            "token_id_length": len(token_id),
        }

        # Step 3: Authenticate credentials against OneID identity API
        self._pause()
        step3_status, login_data = self._oneid_json_post(
            ONEID_LOGIN_API,
            {"login": login, "password": password},
            extra_headers={"X-Authorization-Method": auth_method},
        )
        if login_data.get("code") == 2 and not (
            login_data.get("token") or (login_data.get("data") or {}).get("token")
        ):
            raise RuntimeError(
                "Step 3 failed: OneID requires SMS/2FA confirmation (code=2)."
            )

        jwt_token = login_data.get("token") or (login_data.get("data") or {}).get("token")
        if not jwt_token:
            detail_msg = login_data.get("message") or login_data.get("error") or ""
            suffix = f": {detail_msg}" if detail_msg else ""
            raise RuntimeError(
                f"Step 3 failed: OneID did not return a JWT token (code={login_data.get('code')}){suffix}."
            )
        steps["step3_oneid_login"] = {
            "status": step3_status,
            "response_keys": sorted(login_data.keys()),
            "has_token": True,
        }

        # Step 4: Generate SSO authorization code + callback URL for HEMIS
        self._pause()
        step4_status, sso_data = self._oneid_json_post(
            ONEID_SSO_GENERATE_API,
            {"uuid": token_id, "scope": client_id},
            extra_headers={"Authorization": f"Bearer {jwt_token}"},
        )
        callback_base = sso_data.get("callbackUrl")
        auth_code = sso_data.get("code")
        oauth_state = sso_data.get("state")
        if not callback_base or not auth_code or not oauth_state:
            raise RuntimeError("Step 4 failed: sso/v1/generate missing callbackUrl, code, or state.")
        steps["step4_sso_generate"] = {
            "status": step4_status,
            "response_keys": sorted(sso_data.keys()),
            "callback_url": callback_base,
        }

        # Step 5: Deliver OneID callback to HEMIS using the same CookieJar
        self._pause()
        parsed_cb = urllib.parse.urlsplit(callback_base)
        cb_query = urllib.parse.parse_qsl(parsed_cb.query, keep_blank_values=True)
        cb_query.extend([("code", str(auth_code)), ("state", str(oauth_state))])
        callback_url = urllib.parse.urlunsplit(
            (
                parsed_cb.scheme,
                parsed_cb.netloc,
                parsed_cb.path,
                urllib.parse.urlencode(cb_query),
                parsed_cb.fragment,
            )
        )
        step5_status, _, _ = self.request(callback_url, follow_redirects=True)
        if step5_status == 451:
            raise RuntimeError(
                "Step 5 failed: HEMIS returned HTTP 451 (Kirish cheklangan / Geo-IP block: "
                "hemis.namdtu.uz only accepts Uzbekistan IP addresses)."
            )
        if step5_status < 200 or step5_status >= 400:
            raise RuntimeError(f"Step 5 failed: HEMIS callback returned HTTP {step5_status}")
        steps["step5_hemis_callback"] = {
            "status": step5_status,
            "cookie_names": self.get_cookie_names(),
        }

        # Step 6: Verify authenticated session on /dashboard/profile (NoRedirect)
        self._pause()
        verification = self.verify_session()
        if not verification["authenticated"]:
            raise RuntimeError(
                f"Step 6 verification failed: session is not active (status={verification['status']})"
            )
        steps["step6_profile_verify"] = verification

        return {
            "authenticated": True,
            "base_url": self.base_url,
            "source_ip": self.source_ip,
            "steps": steps,
        }


class _MockHTTPResponse:
    """Minimal file-like HTTP response object compatible with CookieJar.extract_cookies."""

    def __init__(self, status: int, headers_list: list[tuple[str, str]], body: bytes) -> None:
        self.status = status
        self._body = body
        self.headers = Message()
        for key, value in headers_list:
            self.headers[key] = value

    def info(self) -> Message:
        return self.headers

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _MockHTTPResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def run_self_test() -> dict[str, Any]:
    """Deterministically verify all 9 auth, retry, CSRF, safety, failover, and HTTP edge cases."""
    mock_csrf = "A" * 88
    mock_uuid_1 = "11111111-2222-4333-8444-555555555555"
    mock_uuid_2 = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    profile_html = (
        "<html><head>"
        "<title>Mening profilim | HEMIS OTM axborot tizimi</title>"
        '<meta name="csrf-param" content="_csrf-backend">'
        f'<meta name="csrf-token" content="{mock_csrf}">'
        "</head><body>"
        '<li class="dropdown user user-menu"><span>TEST U. S.</span><span>O\'qituvchi</span></li>'
        "<footer>Dastur versiyasi <span>1.9.88</span> / Yadro <span>2.0.55</span> / UID <span>: 100</span></footer>"
        "</body></html>"
    ).encode("utf-8")

    state_box: dict[str, Any] = {
        "login_runs": 0,
        "step1_timeouts_left": 0,
        "session_expired": False,
        "oneid_mode": "normal",
        "step1_cookie_headers": [],
        "posted_forms": [],
        "cross_origin_redirect_headers": [],
    }

    client = HemisOneIDClient(
        step_delay=0.0, max_retries=2, retry_backoff=0.01, auto_interface_failover=False
    )

    def fake_open(req: urllib.request.Request, timeout: int = 25):  # noqa: ARG001
        url = req.full_url
        parsed = urllib.parse.urlparse(url)
        client.cookie_jar.add_cookie_header(req)
        req_headers = {k.lower(): v for k, v in req.header_items()}

        def respond(status: int, hdrs: list[tuple[str, str]], body: bytes):
            resp = _MockHTTPResponse(status, hdrs, body)
            client.cookie_jar.extract_cookies(resp, req)
            if status >= 300:
                raise urllib.error.HTTPError(url, status, f"HTTP {status}", resp.headers, io.BytesIO(body))
            return resp

        if parsed.path == "/dashboard/auth" and "authclient=oneid" in parsed.query:
            if state_box["step1_timeouts_left"] > 0:
                state_box["step1_timeouts_left"] -= 1
                raise TimeoutError("Simulated transient Step 1 timeout")
            state_box["step1_cookie_headers"].append(req_headers.get("cookie", ""))
            state_box["login_runs"] += 1
            return respond(
                302,
                [
                    ("Location", "https://sso.egov.uz/sso/oauth/Authorization.do?client_id=hemis_namdtu_uz&state=st1"),
                    ("Set-Cookie", "backend_8=init_sid; Path=/; HttpOnly"),
                ],
                b"",
            )

        if parsed.netloc == "sso.egov.uz":
            uuid_val = mock_uuid_1 if state_box["login_runs"] == 1 else mock_uuid_2
            return respond(
                307,
                [("Location", f"https://id.egov.uz?client_id=hemis_namdtu_uz&token_id={uuid_val}&method=IDPW")],
                b"",
            )

        if url == ONEID_LOGIN_API:
            if state_box["oneid_mode"] == "code2":
                return respond(
                    200,
                    [("Content-Type", "application/json")],
                    json.dumps({"code": 2, "message": "SMS required"}).encode("utf-8"),
                )
            return respond(
                200,
                [("Content-Type", "application/json")],
                json.dumps({"role": "USER", "token": "jwt.mock.token", "permissions": [], "code": 0}).encode("utf-8"),
            )

        if url == ONEID_SSO_GENERATE_API:
            if state_box["oneid_mode"] == "code72":
                return respond(
                    400,
                    [("Content-Type", "application/json")],
                    json.dumps({"code": 72, "message": "Invalid scope"}).encode("utf-8"),
                )
            return respond(
                200,
                [("Content-Type", "application/json")],
                json.dumps({
                    "callbackUrl": "https://hemis.namdtu.uz/dashboard/auth",
                    "code": "one_code_xyz",
                    "state": "st1",
                    "name": "HEMIS OTM",
                    "logo": "logo.png",
                }).encode("utf-8"),
            )

        if parsed.path == "/dashboard/auth" and "code=" in parsed.query:
            state_box["session_expired"] = False
            return respond(
                200,
                [
                    ("Content-Type", "text/html; charset=utf-8"),
                    ("Set-Cookie", "backend_8=auth_sid; Path=/; HttpOnly"),
                    ("Set-Cookie", "_csrf-backend=csrf_cookie; Path=/; HttpOnly"),
                ],
                b"<html><body>Logged in</body></html>",
            )

        if parsed.path == "/dashboard/profile":
            if state_box["session_expired"]:
                return respond(302, [("Location", "https://hemis.namdtu.uz/dashboard/login")], b"")
            return respond(
                200,
                [("Content-Type", "text/html; charset=utf-8"), ("Content-Encoding", "gzip")],
                gzip.compress(profile_html),
            )

        if parsed.path == "/teacher/sample-post":
            state_box["posted_forms"].append({
                "headers": req_headers,
                "body": (req.data or b"").decode("utf-8"),
            })
            return respond(200, [("Content-Type", "application/json")], b'{"saved":true}')

        if parsed.path == "/files/sample.docx":
            return respond(
                200,
                [("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")],
                b"PK\x03\x04mock-docx",
            )

        if parsed.path == "/redirect-source":
            return respond(302, [("Location", "https://other.example.com/landing")], b"")

        if parsed.netloc == "other.example.com":
            state_box["cross_origin_redirect_headers"].append(req_headers)
            return respond(200, [("Content-Type", "text/plain")], b"ok")

        if parsed.path == "/file-resource/index":
            state_box["last_filter_query"] = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            return respond(200, [("Content-Type", "text/html")], b"<html><body>grid</body></html>")

        if parsed.path == "/file-resource/edit" and parsed.query == "subject=1":
            body_qs = urllib.parse.parse_qs((req.data or b"").decode("utf-8"), keep_blank_values=True)
            assert body_qs.get("depdrop_parents[0]") == ["10"]
            assert body_qs.get("depdrop_all_params[_curriculum]") == ["10"]
            return respond(
                200,
                [("Content-Type", "application/json")],
                json.dumps({"output": [{"id": "20001", "name": "Dasturlash"}], "selected": ""}).encode(
                    "utf-8"
                ),
            )

        if parsed.path == "/files/connector" and req.get_method() == "GET":
            return respond(
                200,
                [("Content-Type", "application/json")],
                json.dumps({"cwd": {"hash": "l1_Lw"}, "csrf": "ef_mock_csrf_token"}).encode("utf-8"),
            )

        if parsed.path == "/files/connector" and req.get_method() == "POST" and b"cmd=mkdir" in (req.data or b""):
            assert req_headers.get("x-elfinder-csrf") == "ef_mock_csrf_token"
            return respond(200, [("Content-Type", "application/json")], b'{"added":[{"hash":"l1_dir","mime":"directory"}]}')

        if parsed.path == "/files/connector" and req.get_method() == "POST" and b"cmd=paste" in (req.data or b""):
            assert b"cut=0" in (req.data or b"") and b"targets%5B%5D=h1&targets%5B%5D=h2" in (req.data or b"")
            return respond(200, [("Content-Type", "application/json")], b'{"added":[{"hash":"l1_copy"}]}')

        if parsed.path == "/teacher/post-count":
            state_box["post_count"] = state_box.get("post_count", 0) + 1
            raise TimeoutError("Simulated ambiguous upload timeout")

        if parsed.path == "/files/connector" and req.get_method() == "POST":
            assert req_headers.get("x-csrf-token") == mock_csrf
            assert req_headers.get("x-elfinder-csrf") == "ef_mock_csrf_token"
            assert b'filename="lab_01.pdf"' in (req.data or b"")
            return respond(
                200,
                [("Content-Type", "application/json")],
                json.dumps({"added": [{"hash": "l1_bGFiXzAxLnBkZg", "name": "lab_01.pdf"}]}).encode(
                    "utf-8"
                ),
            )

        if parsed.netloc == "student.namdtu.uz" and parsed.path == "/rest/v1/public/stat-structure":
            return respond(
                200,
                [("Content-Type", "application/json")],
                json.dumps({"success": True, "data": {"faculties": 8, "departments": 39}}).encode("utf-8"),
            )

        if parsed.netloc == "student.namdtu.uz" and parsed.path == "/rest/v1/auth/login" and req.get_method() == "POST":
            return respond(
                200,
                [("Content-Type", "application/json")],
                json.dumps({"success": True, "data": {"token": "stu.jwt.secret"}}).encode("utf-8"),
            )

        if parsed.netloc == "student.namdtu.uz" and parsed.path == "/rest/v1/account/me":
            assert req_headers.get("authorization") == "Bearer stu.jwt.secret"
            return respond(
                200,
                [("Content-Type", "application/json")],
                json.dumps({"success": True, "data": {"student_id_number": "30001"}}).encode("utf-8"),
            )

        return respond(404, [("Content-Type", "text/plain")], b"not found")

    client.opener.open = fake_open  # type: ignore[method-assign]

    # 1. Full 6-step login + UID/version/CSRF extraction (over gzipped HTML)
    auth1 = client.login_with_oneid("test_user", "test_pass")
    v1 = auth1["steps"]["step6_profile_verify"]
    assert auth1["authenticated"] is True
    assert v1["user_summary"]["uid"] == "100"
    assert v1["user_summary"]["version"] == "1.9.88"
    assert v1["user_summary"]["core_version"] == "2.0.55"
    assert v1["csrf_token_length"] == 88

    # 2. Consecutive login clears stale cookies before Step 1
    auth2 = client.login_with_oneid("test_user", "test_pass")
    assert auth2["authenticated"] is True
    assert state_box["step1_cookie_headers"] == ["", ""], state_box["step1_cookie_headers"]

    # 3. ensure_session() active -> expired recovery
    es_live = client.ensure_session()
    assert es_live["reauthenticated"] is False
    state_box["session_expired"] = True
    es_rel = client.ensure_session()
    assert es_rel["reauthenticated"] is True
    assert es_rel["verification"]["user_summary"]["uid"] == "100"

    # 4. Transient TimeoutError on Step 1 automatically recovered via retry
    state_box["step1_timeouts_left"] = 1
    auth_retry = client.login_with_oneid("test_user", "test_pass")
    assert auth_retry["authenticated"] is True

    # 5. OneID code=2 and code=72 errors
    state_box["oneid_mode"] = "code2"
    try:
        client.login_with_oneid("test_user", "test_pass")
        raise AssertionError("Expected RuntimeError for OneID code=2")
    except RuntimeError as exc:
        assert "code=2" in str(exc)

    state_box["oneid_mode"] = "code72"
    try:
        client.login_with_oneid("test_user", "test_pass")
        raise AssertionError("Expected RuntimeError for OneID code=72")
    except RuntimeError as exc:
        assert "code=72" in str(exc)
    state_box["oneid_mode"] = "normal"

    # 6. Unsafe GET guard blocks mutating URLs while allowing safe /message/compose?id=123
    for unsafe in (
        "/message/compose",
        "/dashboard/toggle?attribute=active&model=x&id=1",
        "/poll/mine?set=1&p=1&o=1",
        "/file-resource/edit?id=1&delete=1",
        "/file-resource/edit?id=1&item=2&cancel=1",
        "/dashboard/profile?certificate=1",
        "/dashboard/logout",
        "/oz/dashboard/index",
        "/student/reference?get=1",
        "/student-data/rent-sync",
        "/student-data/upload-plagiarism",
    ):
        try:
            client.request(unsafe)
            raise AssertionError(f"Expected ValueError for {unsafe}")
        except ValueError:
            pass
    assert _is_unsafe_hemis_get("https://hemis.namdtu.uz/message/compose?id=123", "hemis.namdtu.uz") is None

    # 7. post_form injects _csrf-backend + X-CSRF-Token + Origin + Referer
    client.login_with_oneid("test_user", "test_pass")
    post_status, _, _ = client.post_form("/teacher/sample-post", {"k": "v"}, referer_path="/teacher/list")
    assert post_status == 200
    posted = state_box["posted_forms"][-1]
    assert posted["headers"].get("x-csrf-token") == mock_csrf
    assert posted["headers"].get("origin") == "https://hemis.namdtu.uz"
    assert posted["headers"].get("referer") == "https://hemis.namdtu.uz/teacher/list"
    assert f"_csrf-backend={mock_csrf}" in posted["body"]

    # 8. download_resource_bytes, deflate/gzip decompression, and cross-origin redirect header stripping
    try:
        client.download_resource_bytes("/dashboard/profile")
        raise AssertionError("Expected RuntimeError for text/html download")
    except RuntimeError as exc:
        assert "text/html" in str(exc)

    raw_doc, _ = client.download_resource_bytes("/files/sample.docx")
    assert raw_doc == b"PK\x03\x04mock-docx"
    assert _decompress_body(zlib.compress(b"deflate-ok"), "deflate") == b"deflate-ok"

    client.request(
        "/redirect-source",
        headers={
            "Authorization": "Bearer secret",
            "X-CSRF-Token": "secret",
            "X-elFinder-CSRF": "secret",
            "Origin": "https://hemis.namdtu.uz",
            "Accept": "text/plain",
        },
        follow_redirects=True,
    )
    fwd_headers = state_box["cross_origin_redirect_headers"][-1]
    for stripped in ("authorization", "x-csrf-token", "x-elfinder-csrf", "origin"):
        assert stripped not in fwd_headers, f"Header {stripped} leaked cross-origin"
    assert fwd_headers.get("accept") == "text/plain"

    # 9. Interface failover & _SourceBoundHTTPSHandler source_address binding
    captured_do_open: list[dict[str, Any]] = []
    handler = _SourceBoundHTTPSHandler(source_ip="10.99.0.2")
    handler.do_open = lambda cls, req, **kw: captured_do_open.append(kw)  # type: ignore[method-assign,assignment]
    handler.https_open(urllib.request.Request("https://hemis.namdtu.uz/dashboard/profile"))
    assert captured_do_open[-1].get("source_address") == ("10.99.0.2", 0)

    default_handler = _SourceBoundHTTPSHandler(source_ip=None)
    default_handler.do_open = lambda cls, req, **kw: captured_do_open.append(kw)  # type: ignore[method-assign,assignment]
    default_handler.https_open(urllib.request.Request("https://hemis.namdtu.uz/dashboard/profile"))
    assert "source_address" not in captured_do_open[-1]

    orig_gethostbyname_ex = socket.gethostbyname_ex
    orig_create_connection = socket.create_connection

    class _DummySock:
        def __enter__(self) -> _DummySock:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    try:
        socket.gethostbyname_ex = lambda _name: (  # type: ignore[assignment]
            "host",
            [],
            ["127.0.0.1", "169.254.1.2", "192.168.1.10", "10.99.0.2", "10.99.0.2"],
        )
        assert discover_local_ipv4s() == ["10.99.0.2", "192.168.1.10"]

        http_captured: list[dict[str, Any]] = []
        http_handler = _SourceBoundHTTPHandler(source_ip="10.99.0.2")
        http_handler.do_open = lambda cls, req, **kw: http_captured.append(kw)  # type: ignore[method-assign,assignment]
        http_handler.http_open(urllib.request.Request("http://hemis.namdtu.uz/dashboard/profile"))
        assert http_captured[-1].get("source_address") == ("10.99.0.2", 0)

        def fake_create_conn(address, timeout=None, source_address=None):  # noqa: ARG001
            if source_address == ("192.168.1.10", 0):
                raise TimeoutError("Primary interface blocked")
            if source_address == ("10.99.0.2", 0):
                return _DummySock()
            raise OSError("Unexpected source_address")

        socket.create_connection = fake_create_conn  # type: ignore[assignment]

        failover_client = HemisOneIDClient(
            step_delay=0.0, max_retries=0, retry_backoff=0.01, auto_interface_failover=True
        )
        probed_open_sources: list[str | None] = []

        def failover_fake_open(req: urllib.request.Request, timeout: int = 25):  # noqa: ARG001
            probed_open_sources.append(failover_client.source_ip)
            if failover_client.source_ip is None:
                raise TimeoutError("Simulated blocked primary route")
            return _MockHTTPResponse(200, [("Content-Type", "text/plain")], b"failover-ok")

        orig_rebuild = failover_client._rebuild_opener

        def wrapped_rebuild() -> None:
            orig_rebuild()
            bound_https = [
                h for h in failover_client.opener.handlers if isinstance(h, _SourceBoundHTTPSHandler)
            ]
            bound_http = [
                h for h in failover_client.opener.handlers if isinstance(h, _SourceBoundHTTPHandler)
            ]
            assert len(bound_https) == 1 and bound_https[0].source_ip == "10.99.0.2"
            assert len(bound_http) == 1 and bound_http[0].source_ip == "10.99.0.2"
            failover_client.opener.open = failover_fake_open  # type: ignore[method-assign]

        failover_client._rebuild_opener = wrapped_rebuild  # type: ignore[method-assign]
        failover_client.opener.open = failover_fake_open  # type: ignore[method-assign]

        fo_status, _, fo_body = failover_client.request("/dashboard/profile")
        assert fo_status == 200
        assert fo_body == "failover-ok"
        assert failover_client.source_ip == "10.99.0.2"
        assert probed_open_sources == [None, "10.99.0.2"]
    finally:
        socket.gethostbyname_ex = orig_gethostbyname_ex
        socket.create_connection = orig_create_connection

    # 10. prioritize_local_ipv4s orders tethering 10.* and secondary 192.168.x.* first
    assert prioritize_local_ipv4s(
        ["172.16.0.1", "192.168.0.10", "192.168.43.2", "10.10.0.2"]
    ) == ["10.10.0.2", "192.168.43.2", "192.168.0.10", "172.16.0.1"]

    # 11. Embedded Thawte TLS RSA CA G1 loads cleanly into strict SSLContext
    tls_ctx = ssl.create_default_context()
    tls_ctx.load_verify_locations(cadata=THAWTE_TLS_RSA_CA_G1_PEM)
    assert tls_ctx.verify_mode == ssl.CERT_REQUIRED

    # 12. validate_hemis_filename enforces '+' ban and 5 MiB size limit
    assert HemisOneIDClient.validate_hemis_filename("lab_01.pdf", 1024) == "lab_01.pdf"
    for bad_name, bad_size in [
        ("lab+01.pdf", 100),
        ("../escape.pdf", 100),
        ("", 100),
        ("huge.pdf", MAX_ELFINDER_UPLOAD_BYTES + 1),
    ]:
        try:
            HemisOneIDClient.validate_hemis_filename(bad_name, bad_size)
            raise AssertionError(f"Expected ValueError for {(bad_name, bad_size)}")
        except ValueError:
            pass

    # 13. Multipart builder, sticky filter clear, DepDrop & Dual-CSRF elFinder upload helpers
    body_bytes, ctype_hdr = HemisOneIDClient.build_multipart_form_data(
        {"cmd": "upload"}, [("upload[]", "doc_1.pdf", b"%PDF-1.4", "application/pdf")]
    )
    assert ctype_hdr.startswith("multipart/form-data; boundary=")
    assert b'filename="doc_1.pdf"' in body_bytes

    flt_status, _, _ = client.clear_resource_filters()
    assert flt_status == 200
    assert state_box["last_filter_query"].get("ESubjectResource[_training_type]") == [""]
    assert len(state_box["last_filter_query"]) == 6

    dep_out = client.query_depdrop_subjects(10)
    assert dep_out == [{"id": "20001", "name": "Dasturlash"}]

    open_res = client.elfinder_open(init=True)
    assert open_res.get("csrf") == "ef_mock_csrf_token"
    up_res = client.elfinder_upload_bytes("l1_Lw", "lab_01.pdf", b"%PDF-1.4-mock")
    assert up_res["added"][0]["name"] == "lab_01.pdf"

    # 14. Strict cookie domain suffix matching & HTTP 451 Geo-IP detection
    dom_client = HemisOneIDClient(base_url="https://hemis.namdtu.uz", step_delay=0.0)
    for dom, cname in [
        ("hemis.namdtu.uz", "backend_8"),
        (".namdtu.uz", "shared_namdtu"),
        (".cloudflare.com", "__cf_bm"),
        ("evilhemis.namdtu.uz", "evil"),
    ]:
        c = http.cookiejar.Cookie(
            version=0,
            name=cname,
            value="v",
            port=None,
            port_specified=False,
            domain=dom,
            domain_specified=True,
            domain_initial_dot=dom.startswith("."),
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
        )
        dom_client.cookie_jar.set_cookie(c)
    assert dom_client.get_cookie_names() == ["backend_8", "shared_namdtu"]

    geo_client = HemisOneIDClient(step_delay=0.0, max_retries=0)
    geo_client.opener.open = lambda req, timeout=25: (_ for _ in ()).throw(  # type: ignore[method-assign]
        urllib.error.HTTPError(req.full_url, 451, "Unavailable For Legal Reasons", Message(), io.BytesIO(b"Kirish cheklangan"))
    )
    try:
        geo_client.login_with_oneid("u", "p")
        raise AssertionError("Expected RuntimeError for HTTP 451")
    except RuntimeError as exc:
        assert "451" in str(exc) and "Geo-IP" in str(exc)

    # 15. Stale bound source_ip recovers to OS default route against local ephemeral TCP server
    import socketserver
    import threading

    class _PingHandler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.recv(64)

    class _PingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with _PingServer(("127.0.0.1", 0), _PingHandler) as ping_srv:
        ping_port = ping_srv.server_address[1]
        threading.Thread(target=ping_srv.serve_forever, daemon=True).start()
        orig_discover = discover_local_ipv4s
        try:
            globals()["discover_local_ipv4s"] = lambda: []
            stale_client = HemisOneIDClient(
                base_url=f"http://127.0.0.1:{ping_port}",
                source_ip="192.0.2.1",  # RFC 5737 TEST-NET unreachable source IP
                step_delay=0.0,
                max_retries=0,
            )
            recovered = stale_client._try_interface_failover(f"http://127.0.0.1:{ping_port}/")
            assert recovered is True
            assert stale_client.source_ip is None
        finally:
            globals()["discover_local_ipv4s"] = orig_discover
            ping_srv.shutdown()

    # 16. Non-idempotent POST is never blindly re-sent after an ambiguous timeout; GET still retries
    state_box["post_count"] = 0
    try:
        client.request("/teacher/post-count", method="POST", data=b"x=1")
        raise AssertionError("Expected TimeoutError for ambiguous POST")
    except TimeoutError:
        pass
    assert state_box["post_count"] == 1, state_box["post_count"]

    # 17. Path normalization (single encoding) + switch-role guard
    assert HemisOneIDClient.normalize_resource_path("200/My%20File%27s.pdf") == "200/My%20File%27s.pdf"
    assert HemisOneIDClient.normalize_resource_path("200/My File's.pdf") == "200/My%20File%27s.pdf"
    assert _is_unsafe_hemis_get("https://hemis.namdtu.uz/dashboard/switch-role?id=9", "hemis.namdtu.uz")
    try:
        client.request("/dashboard/switch-role?id=9")
        raise AssertionError("Expected ValueError for switch-role")
    except ValueError:
        pass

    # 18. elFinder mkdir / copy (never cut) via Dual-CSRF
    mk = client.elfinder_mkdir("l1_Lw", "01 - Mavzu")
    assert mk["added"][0]["mime"] == "directory"
    cp = client.elfinder_copy("l1_Lw", "l1_dir", ["h1", "h2"])
    assert cp["added"][0]["hash"] == "l1_copy"
    try:
        client.elfinder_mkdir("l1_Lw", "a/b")
        raise AssertionError("Expected ValueError for slash in folder name")
    except ValueError:
        pass

    # 19. Built-in FullCalendar timetable parser
    sample_tt = (
        '<script>jQuery("#w0").fullCalendar({"events":['
        '{"id":1,"title":"1. Dasturlash<br><b>Ma\x27ruza</b><br>201-xona","start":"2026-11-16T08:30:00"}'
        "]});</script>"
    )
    tt_events = parse_timetable_events(sample_tt)
    assert len(tt_events) == 1
    assert tt_events[0]["title_text"] == "1. Dasturlash | Ma'ruza | 201-xona"
    assert auto_parse_hemis_html(sample_tt, "/teacher/time-table")["type"] == "timetable"

    # 20. Built-in Yii2 GridView parser (summary + headers + rows + active checkbox + links)
    sample_grid = (
        '<div class="grid-view"><div class="summary">Jami 25 ta yozuvdan 1-20 tasi ko\'rsatilmoqda.</div>'
        '<table><thead><tr><th>#</th><th>Nomi</th><th>Faol</th></tr></thead>'
        '<tbody><tr data-key="50001"><td>1</td><td><a href="/file-resource/edit?id=50001">Kirish</a></td>'
        '<td><input type="checkbox" data-id="50001" checked></td></tr></tbody></table></div>'
    )
    gv = parse_gridview(sample_grid)
    assert gv["summary"] == {
        "text": "Jami 25 ta yozuvdan 1-20 tasi ko'rsatilmoqda.",
        "begin": 1,
        "end": 20,
        "total": 25,
    }
    assert gv["headers"] == ["#", "Nomi", "Faol"]
    assert gv["rows"][0]["data_key"] == "50001"
    assert gv["rows"][0]["active_id"] == "50001"
    assert gv["rows"][0]["active_checked"] is True
    assert gv["rows"][0]["links"] == ["/file-resource/edit?id=50001"]

    # 21. Built-in resource item files parser (elFinderDialog config + hidden input fallback)
    sample_res_html = (
        '<script>elFinderDialog({"files":[{"name":"1-maruza.pdf","path":"200/1-maruza.pdf",'
        '"size":12345,"type":"application/pdf","order":0,"base_url":"https://hemis.namdtu.uz/static/uploads"}]});</script>'
    )
    rfiles = parse_resource_item_files(sample_res_html)
    assert len(rfiles) == 1 and rfiles[0]["name"] == "1-maruza.pdf" and rfiles[0]["size"] == 12345

    fallback_res_html = (
        '<form><input type="hidden" name="ESubjectFileResourceItem[files][0][name]" value="2-amaliy.pdf">'
        '<input type="hidden" name="ESubjectFileResourceItem[files][0][size]" value="54321"></form>'
    )
    rfiles_fb = parse_resource_item_files(fallback_res_html)
    assert rfiles_fb == [{"name": "2-amaliy.pdf", "size": 54321}]

    # 22. Student REST API (/rest/v1) & Public University Stats (/rest/v1/public/stat-*)
    assert client._resolve_student_base_url() == "https://student.namdtu.uz"
    pstats = client.fetch_public_stats("structure")
    assert pstats["data"]["faculties"] == 8
    stu_login = client.student_rest_login("30001", "pass")
    assert stu_login["authenticated"] is True and "token" not in stu_login
    stu_me = client.student_rest_get("account/me")
    assert stu_me["data"]["student_id_number"] == "30001"

    # 23. Encrypted local credential vault (Windows DPAPI / POSIX 0600 sealed)
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        vault_file = os.path.join(tmpdir, "credentials.json")
        saved_meta = save_credentials(
            "demo_teacher_login",
            "S3cret_Pass#2026",
            base_url="https://hemis.namdtu.uz",
            file_path=vault_file,
        )
        assert saved_meta["saved"] is True
        assert saved_meta["login_masked"] == "de***in"
        with open(vault_file, encoding="utf-8") as vf_fh:
            raw_disk_text = vf_fh.read()
        assert "demo_teacher_login" not in raw_disk_text
        assert "S3cret_Pass#2026" not in raw_disk_text

        loaded = load_credentials(vault_file)
        assert loaded is not None
        assert loaded["login"] == "demo_teacher_login"
        assert loaded["password"] == "S3cret_Pass#2026"
        assert loaded["base_url"] == "https://hemis.namdtu.uz"

        status_info = get_credentials_status(vault_file)
        assert status_info["configured"] is True
        assert "S3cret_Pass#2026" not in json.dumps(status_info)

        cleared_info = clear_credentials(vault_file)
        assert cleared_info["cleared"] is True and cleared_info["existed"] is True
        assert load_credentials(vault_file) is None

    return {
        "self_test_passed": True,
        "checks_passed": 23,
        "details": [
            "6_step_login_uid_version_csrf",
            "consecutive_login_cookie_reset",
            "ensure_session_active_and_expired_recovery",
            "transient_timeout_retry",
            "oneid_code2_and_code72_errors",
            "unsafe_get_guard",
            "post_form_csrf_headers_and_body",
            "download_resource_bytes_decompression_and_redirect_header_hygiene",
            "interface_failover_source_ip_binding",
            "prioritize_local_ipv4s_tethering_first",
            "embedded_thawte_ca_strict_tls",
            "filename_plus_and_5mib_guard",
            "multipart_filter_clear_depdrop_and_elfinder_dual_csrf",
            "cookie_domain_suffix_and_http451_geo_detection",
            "stale_source_ip_default_route_recovery",
            "post_not_blindly_retried",
            "path_normalization_and_switch_role_guard",
            "elfinder_mkdir_copy_dual_csrf",
            "timetable_fullcalendar_events_parser",
            "yii2_gridview_html_parser",
            "resource_item_files_parser",
            "student_rest_and_public_stats",
            "encrypted_local_credential_vault",
        ],
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Authenticate into hemis.namdtu.uz via OneID and verify session."
    )
    parser.add_argument(
        "--login",
        default=os.environ.get("HEMIS_ONEID_LOGIN", ""),
        help="OneID login (or set HEMIS_ONEID_LOGIN env var)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("HEMIS_ONEID_PASSWORD", ""),
        help="OneID password (or set HEMIS_ONEID_PASSWORD env var)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("HEMIS_BASE_URL", DEFAULT_BASE_URL),
        help=f"HEMIS base URL (env HEMIS_BASE_URL; default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--source-ip",
        default=os.environ.get("HEMIS_SOURCE_IP") or None,
        help="Optional local source IPv4 to bind outbound HTTPS connections (or HEMIS_SOURCE_IP)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run deterministic offline edge-case verification suite and exit",
    )
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    if args.self_test:
        result = run_self_test()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not args.login or not args.password:
        print(
            "Missing credentials. Provide --login/--password or set "
            "HEMIS_ONEID_LOGIN and HEMIS_ONEID_PASSWORD.",
            file=sys.stderr,
        )
        return 2

    client = HemisOneIDClient(base_url=args.base_url, source_ip=args.source_ip)
    auth_report = client.login_with_oneid(args.login, args.password)
    ping_report = client.keepalive_ping()
    report = {
        "auth": auth_report,
        "keepalive_ping": ping_report,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if auth_report.get("authenticated") and ping_report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
