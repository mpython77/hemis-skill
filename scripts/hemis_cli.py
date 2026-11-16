#!/usr/bin/env python3
"""Unified agent-friendly CLI for HEMIS (stdlib only, zero-secret).

Every command prints ONE JSON document to stdout and uses exit codes:
  0 = success, 1 = operational failure (auth/network/geo), 2 = usage error.

Credentials are read ONLY from the environment (never from argv, never written
to disk, never printed):
  HEMIS_ONEID_LOGIN, HEMIS_ONEID_PASSWORD   (optional) HEMIS_SOURCE_IP

Commands:
  doctor              Health check (Python, self-tests, local IPv4s, vault & update check).
  auth-save           Encrypt & save credentials to ~/.hemis/credentials.json (DPAPI/0600).
  auth-status         Check whether credentials are configured in env or encrypted vault.
  auth-clear          Remove stored credentials from ~/.hemis/credentials.json.
  check-update        Check if a newer hemis-skill version exists on GitHub.
  update              Pull latest skill & scripts from GitHub and sync installed skill dir.
  probe               Credential-free reachability / Geo-IP / TLS diagnosis.
  public-stats        Credential-free public university statistics (/rest/v1/public/stat-*).
  login               Full OneID -> HEMIS login; prints sanitized session report.
  get PATH [PATH...]  Login once, then safe GET one or more paths (with optional --parse).
  student-get EP...   Student REST API (/rest/v1/...) GET after student login.
  bundle              Build Web Skill ZIP (Claude/Gemini/ChatGPT) & ChatGPT Free bundle.
  self-test           Run all deterministic offline self-tests.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Callable

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import hemis_oneid_auth as auth  # noqa: E402

DEFAULT_VERSION = "1.0.1"
GITHUB_REPO = os.environ.get("HEMIS_SKILL_REPO", "mpython77/hemis-skill")
GITHUB_REPO_URL = f"https://github.com/{GITHUB_REPO}"
GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"
SKILL_SYNC_FILES = (
    "VERSION",
    "SKILL.md",
    "AGENTS.md",
    "docs/ARCHITECTURE.md",
    "docs/WEB_PLATFORMS.md",
    "references/auth-http-pjax.md",
    "references/curriculum-tasks-exams.md",
    "references/lessons-attendance-grading.md",
    "references/network-and-recovery.md",
    "references/reasoning-pitfalls-and-evidence.md",
    "references/resources-elfinder.md",
    "references/science-messages-system-api.md",
    "references/student-portal-and-rest-api.md",
    "scripts/build_web_bundles.py",
    "scripts/hemis_cli.py",
    "scripts/hemis_mcp_server.py",
    "scripts/hemis_oneid_auth.py",
    "scripts/hemis_split_proxy.py",
)


def get_local_version(root_dir: str = SKILL_ROOT) -> str:
    version_path = os.path.join(root_dir, "VERSION")
    try:
        with open(version_path, encoding="utf-8") as fh:
            val = fh.read().strip()
            if val:
                return val
    except OSError:
        pass
    return DEFAULT_VERSION


def _parse_semver(version_str: str) -> tuple[int, ...]:
    cleaned = version_str.strip().lstrip("vV").split("-", 1)[0]
    parts: list[int] = []
    for piece in cleaned.split("."):
        parts.append(int(piece) if piece.isdigit() else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _default_fetch_text(url: str, timeout: float = 3.5) -> str:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "hemis-skill-updater/1.0", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


def check_for_updates(
    timeout: float = 3.5,
    fetch_fn: Callable[[str, float], str] | None = None,
    local_version: str | None = None,
) -> dict[str, Any]:
    """Compare local VERSION against GitHub main/VERSION without failing offline runs."""
    loc = (local_version or get_local_version()).strip()
    fetcher = fetch_fn or _default_fetch_text
    version_url = f"{GITHUB_RAW_BASE}/VERSION"
    try:
        remote_raw = fetcher(version_url, timeout).strip()
        remote_ver = remote_raw.splitlines()[0].strip() if remote_raw else ""
    except Exception as exc:  # noqa: BLE001
        return {
            "checked": False,
            "repository": GITHUB_REPO_URL,
            "local_version": loc,
            "remote_version": None,
            "update_available": False,
            "status": "offline_or_unreachable",
            "error": str(exc),
        }

    if not remote_ver:
        return {
            "checked": False,
            "repository": GITHUB_REPO_URL,
            "local_version": loc,
            "remote_version": None,
            "update_available": False,
            "status": "empty_remote_version",
        }

    newer = _parse_semver(remote_ver) > _parse_semver(loc)
    result: dict[str, Any] = {
        "checked": True,
        "repository": GITHUB_REPO_URL,
        "local_version": loc,
        "remote_version": remote_ver,
        "update_available": newer,
        "status": "update_available" if newer else "up_to_date",
    }
    if newer:
        result["hint"] = (
            f"New hemis-skill version {remote_ver} is available (installed: {loc}). "
            "Run: python scripts/hemis_cli.py update"
        )
    return result


def _sync_installed_skill_dirs(source_root: str) -> list[str]:
    """If global skill directories exist (~/.gemini/config/skills/hemis or ~/.claude/skills/hemis), sync them."""
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".gemini", "config", "skills", "hemis"),
        os.path.join(home, ".claude", "skills", "hemis"),
    ]
    synced: list[str] = []
    src_norm = os.path.normcase(os.path.abspath(source_root))
    for dest in candidates:
        dest_norm = os.path.normcase(os.path.abspath(dest))
        if dest_norm == src_norm or not os.path.isdir(dest):
            continue
        for rel in SKILL_SYNC_FILES:
            src_file = os.path.join(source_root, *rel.split("/"))
            dst_file = os.path.join(dest, *rel.split("/"))
            if os.path.isfile(src_file):
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                shutil.copy2(src_file, dst_file)
        synced.append(dest)
    return synced


def update_skill(
    root_dir: str = SKILL_ROOT,
    fetch_fn: Callable[[str, float], str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Update hemis-skill via git pull (if in a git clone) or direct GitHub raw download."""
    before_version = get_local_version(root_dir)
    fetcher = fetch_fn or _default_fetch_text
    git_dir = os.path.join(root_dir, ".git")

    if os.path.isdir(git_dir) and fetch_fn is None:
        proc = subprocess.run(
            ["git", "-C", root_dir, "pull", "--ff-only", "origin", "main"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode == 0:
            synced = _sync_installed_skill_dirs(root_dir)
            after_version = get_local_version(root_dir)
            return {
                "ok": True,
                "method": "git_pull",
                "before_version": before_version,
                "after_version": after_version,
                "updated": before_version != after_version or "Already up to date" not in proc.stdout,
                "git_output": proc.stdout.strip(),
                "synced_skill_dirs": synced,
            }

    # Direct HTTPS file sync (works inside ~/.gemini/config/skills/hemis without .git)
    updated_files: list[str] = []
    for rel in SKILL_SYNC_FILES:
        url = f"{GITHUB_RAW_BASE}/{rel}"
        content = fetcher(url, timeout)
        target_path = os.path.join(root_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        updated_files.append(rel)

    synced = _sync_installed_skill_dirs(root_dir)
    after_version = get_local_version(root_dir)
    return {
        "ok": True,
        "method": "github_raw_sync",
        "before_version": before_version,
        "after_version": after_version,
        "files_synced": len(updated_files),
        "synced_skill_dirs": synced,
    }


def _emit(payload: dict[str, Any], code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def _creds() -> tuple[str, str] | None:
    resolved = auth.resolve_credentials()
    if resolved:
        return resolved[0], resolved[1]
    return None


def _new_client(args: argparse.Namespace) -> auth.HemisOneIDClient:
    base_url = args.base_url
    if base_url == auth.DEFAULT_BASE_URL and not os.environ.get("HEMIS_BASE_URL"):
        try:
            vault = auth.load_credentials()
            if vault and vault.get("base_url"):
                base_url = vault["base_url"]
        except Exception:  # noqa: BLE001
            pass
    return auth.HemisOneIDClient(
        base_url=base_url,
        source_ip=args.source_ip or os.environ.get("HEMIS_SOURCE_IP") or None,
    )


def cmd_self_test(_: argparse.Namespace) -> int:
    results: dict[str, Any] = {"auth": auth.run_self_test()}
    ok = bool(results["auth"].get("self_test_passed"))
    for key, mod_name in (
        ("split_proxy", "hemis_split_proxy"),
        ("web_bundles", "build_web_bundles"),
        ("mcp_server", "hemis_mcp_server"),
    ):
        try:
            mod = __import__(mod_name)
            res = mod.run_self_test()
            results[key] = res
            ok = ok and bool(res.get("self_test_passed"))
        except Exception as exc:  # noqa: BLE001
            results[key] = {"self_test_passed": False, "error": repr(exc)}
            ok = False
    return _emit({"ok": ok, **results}, 0 if ok else 1)


def cmd_doctor(args: argparse.Namespace) -> int:
    cred_status = auth.get_credentials_status()
    report: dict[str, Any] = {
        "version": get_local_version(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "openssl": ssl.OPENSSL_VERSION,
        "local_ipv4_candidates": auth.discover_local_ipv4s(),
        "credentials_in_env": os.environ.get("HEMIS_ONEID_LOGIN", "") != ""
        and os.environ.get("HEMIS_ONEID_PASSWORD", "") != "",
        "credentials": cred_status,
    }
    try:
        report["auth_self_test"] = auth.run_self_test()["checks_passed"]
        import build_web_bundles as wb
        import hemis_mcp_server as mcp
        import hemis_split_proxy as proxy

        report["proxy_self_test"] = proxy.run_self_test()["checks_passed"]
        report["bundle_self_test"] = wb.run_self_test()["checks_passed"]
        report["mcp_self_test"] = mcp.run_self_test()["checks_passed"]
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["error"] = repr(exc)

    skip_update = getattr(args, "offline", False) or os.environ.get("HEMIS_NO_UPDATE_CHECK") == "1"
    if not skip_update:
        report["update_check"] = check_for_updates(timeout=3.0)
    else:
        report["update_check"] = {"checked": False, "status": "skipped_offline"}
    return _emit(report, 0 if report["ok"] else 1)


def cmd_bundle(args: argparse.Namespace) -> int:
    import build_web_bundles as wb

    try:
        res = wb.build_all(SKILL_ROOT, args.output_dir, args.server_url)
    except Exception as exc:  # noqa: BLE001
        return _emit({"ok": False, "stage": "bundle", "error": str(exc)}, 1)
    return _emit(res, 0)


def cmd_auth_status(_: argparse.Namespace) -> int:
    status = auth.get_credentials_status()
    return _emit({"ok": True, **status}, 0)


def cmd_auth_save(args: argparse.Namespace) -> int:
    login = os.environ.get("HEMIS_ONEID_LOGIN", "").strip()
    password = os.environ.get("HEMIS_ONEID_PASSWORD", "")
    if getattr(args, "stdin", False):
        try:
            raw_stdin = (sys.stdin.read() or "{}").lstrip("\ufeff")
            payload = json.loads(raw_stdin or "{}")
            if isinstance(payload, dict):
                login = str(payload.get("login") or login).strip()
                password = str(payload.get("password") or password)
        except json.JSONDecodeError as exc:
            return _emit({"ok": False, "error": f"Invalid JSON on stdin: {exc}"}, 2)
    elif (not login or not password) and sys.stdin.isatty():
        import getpass

        if not login:
            login = input("OneID login: ").strip()
        if not password:
            password = getpass.getpass("OneID password: ")
    if not login or not password:
        return _emit(
            {
                "ok": False,
                "error": "Provide HEMIS_ONEID_LOGIN and HEMIS_ONEID_PASSWORD in the environment "
                "(or pass JSON {\"login\":\"...\",\"password\":\"...\"} via --stdin).",
            },
            2,
        )
    try:
        saved = auth.save_credentials(login, password, base_url=args.base_url)
    except Exception as exc:  # noqa: BLE001
        return _emit({"ok": False, "stage": "auth-save", "error": str(exc)}, 1)
    return _emit({"ok": True, **saved}, 0)


def cmd_auth_clear(_: argparse.Namespace) -> int:
    try:
        res = auth.clear_credentials()
    except Exception as exc:  # noqa: BLE001
        return _emit({"ok": False, "stage": "auth-clear", "error": str(exc)}, 1)
    return _emit({"ok": True, **res}, 0)


def cmd_check_update(_: argparse.Namespace) -> int:
    res = check_for_updates(timeout=5.0)
    return _emit({"ok": res.get("checked", False), **res}, 0 if res.get("checked") else 1)


def cmd_update(_: argparse.Namespace) -> int:
    try:
        res = update_skill()
    except Exception as exc:  # noqa: BLE001
        return _emit({"ok": False, "stage": "update", "error": str(exc)}, 1)
    return _emit(res, 0)


def cmd_probe(args: argparse.Namespace) -> int:
    """Credential-free: classify why HEMIS is (not) reachable from this machine."""
    client = _new_client(args)
    out: dict[str, Any] = {"base_url": args.base_url, "source_ip": client.source_ip}
    try:
        status, headers, body = client.request("/dashboard/login", follow_redirects=False)
    except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
        out.update(
            reachable=False,
            diagnosis="network_or_tls_error",
            error=repr(exc),
            hint="Not proof of an expired session. If DNS resolves (195.158.3.34) but TCP 80/443 times out while "
            "other sites work, your ISP route/IP is likely filtered: try --source-ip on another interface "
            "(tethering) or scripts/hemis_split_proxy.py. See references/network-and-recovery.md.",
        )
        return _emit(out, 1)
    out.update(status=status, source_ip=client.source_ip)
    if status == 451:
        out.update(
            reachable=False,
            diagnosis="geo_blocked",
            hint="HEMIS only accepts Uzbekistan IPs. Use a UZ network, a UZ proxy, or "
            "tethering via a UZ mobile operator (see scripts/hemis_split_proxy.py).",
        )
        return _emit(out, 1)
    out.update(reachable=status < 500, diagnosis="ok" if status < 500 else "server_error")
    return _emit(out, 0 if out["reachable"] else 1)


def _login_or_exit(args: argparse.Namespace) -> tuple[auth.HemisOneIDClient | None, int | None]:
    creds = _creds()
    if not creds:
        return None, _emit(
            {
                "ok": False,
                "error": "No credentials found in env or ~/.hemis/credentials.json. "
                "Ask the user once and run: python scripts/hemis_cli.py auth-save",
            },
            2,
        )
    client = _new_client(args)
    try:
        client.login_with_oneid(*creds)
    except (RuntimeError, ValueError, urllib.error.URLError, OSError, ssl.SSLError) as exc:
        return None, _emit({"ok": False, "stage": "login", "error": str(exc)}, 1)
    return client, None


def cmd_login(args: argparse.Namespace) -> int:
    client, rc = _login_or_exit(args)
    if client is None:
        return rc or 1
    verification = client.verify_session()
    ping = client.keepalive_ping()
    return _emit(
        {
            "ok": bool(verification["authenticated"] and ping["ok"]),
            "verification": verification,
            "keepalive_ok": ping["ok"],
        }
    )


def _parse_body(body: str, mode: str, path_hint: str) -> Any:
    if mode == "none":
        return None
    if mode == "timetable":
        events = auth.parse_timetable_events(body)
        return {"type": "timetable", "count": len(events), "events": events}
    if mode == "grid":
        return {"type": "gridview", **auth.parse_gridview(body)}
    if mode == "files":
        files = auth.parse_resource_item_files(body)
        return {"type": "resource_files", "count": len(files), "files": files}
    return auth.auto_parse_hemis_html(body, path_hint=path_hint)


def cmd_get(args: argparse.Namespace) -> int:
    paths: list[str] = list(args.paths)
    client, rc = _login_or_exit(args)
    if client is None:
        return rc or 1

    results: list[dict[str, Any]] = []
    all_ok = True
    for idx, path in enumerate(paths):
        if idx > 0:
            client._pause()
        try:
            status, headers, body = client.request(path, follow_redirects=args.follow)
        except ValueError as exc:  # unsafe GET guard
            return _emit({"ok": False, "stage": "guard", "path": path, "error": str(exc)}, 1)
        except (RuntimeError, urllib.error.URLError, OSError, ssl.SSLError) as exc:
            return _emit({"ok": False, "stage": "request", "path": path, "error": str(exc)}, 1)

        ok = 200 <= status < 400
        all_ok = all_ok and ok
        if args.raw and len(paths) == 1:
            sys.stdout.write(body)
            return 0 if ok else 1

        item: dict[str, Any] = {
            "path": path,
            "ok": ok,
            "status": status,
            "location": client._get_header(headers, "Location"),
            "content_type": client._get_header(headers, "Content-Type"),
            "length": len(body),
        }
        if args.parse != "none":
            item["parsed"] = _parse_body(body, args.parse, path)
            if args.include_body:
                item["body"] = body[: args.max_chars]
                item["truncated"] = len(body) > args.max_chars
        else:
            item["body"] = body[: args.max_chars]
            item["truncated"] = len(body) > args.max_chars
        results.append(item)

    if len(results) == 1:
        return _emit(results[0], 0 if all_ok else 1)
    return _emit({"ok": all_ok, "count": len(results), "results": results}, 0 if all_ok else 1)


def cmd_public_stats(args: argparse.Namespace) -> int:
    client = _new_client(args)
    types = ("structure", "student", "employee") if args.type == "all" else (args.type,)
    data: dict[str, Any] = {}
    try:
        for idx, st_type in enumerate(types):
            if idx > 0:
                client._pause()
            data[st_type] = client.fetch_public_stats(st_type, student_base_url=args.student_url)
    except (RuntimeError, ValueError, urllib.error.URLError, OSError, ssl.SSLError) as exc:
        return _emit({"ok": False, "stage": "public-stats", "error": str(exc)}, 1)
    return _emit(
        {
            "ok": True,
            "student_base_url": client._resolve_student_base_url(args.student_url),
            "stats": data[types[0]] if len(types) == 1 else data,
        },
        0,
    )


def cmd_student_get(args: argparse.Namespace) -> int:
    login = os.environ.get("HEMIS_STUDENT_LOGIN") or ""
    password = os.environ.get("HEMIS_STUDENT_PASSWORD") or ""
    if not (login and password):
        fallback = _creds()
        if fallback:
            login, password = fallback
    if not login or not password:
        return _emit(
            {
                "ok": False,
                "error": "Set HEMIS_STUDENT_LOGIN and HEMIS_STUDENT_PASSWORD "
                "(or save credentials via auth-save).",
            },
            2,
        )
    client = _new_client(args)
    try:
        auth_info = client.student_rest_login(login, password, student_base_url=args.student_url)
        results: dict[str, Any] = {}
        for idx, ep in enumerate(args.endpoints):
            if idx > 0:
                client._pause()
            results[ep] = client.student_rest_get(ep, student_base_url=args.student_url)
    except (RuntimeError, ValueError, urllib.error.URLError, OSError, ssl.SSLError) as exc:
        return _emit({"ok": False, "stage": "student-get", "error": str(exc)}, 1)
    return _emit(
        {
            "ok": True,
            "auth": auth_info,
            "results": results[args.endpoints[0]] if len(args.endpoints) == 1 else results,
        },
        0,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("HEMIS_BASE_URL", auth.DEFAULT_BASE_URL),
                        help="HEMIS portal base URL (env HEMIS_BASE_URL; default: %(default)s)")
    parser.add_argument("--student-url", default=os.environ.get("HEMIS_STUDENT_URL") or None,
                        help="Optional student.<uni>.uz base URL (auto-derived from --base-url when omitted)")
    parser.add_argument("--source-ip", default=None, help="Bind outbound IPv4 (or HEMIS_SOURCE_IP)")
    sub = parser.add_subparsers(dest="command", required=True)
    doc_p = sub.add_parser("doctor")
    doc_p.add_argument("--offline", action="store_true", help="Skip GitHub version update check")
    doc_p.set_defaults(func=cmd_doctor)

    asave_p = sub.add_parser("auth-save")
    asave_p.add_argument("--stdin", action="store_true", help="Read JSON {\"login\":...,\"password\":...} from stdin")
    asave_p.set_defaults(func=cmd_auth_save)
    sub.add_parser("auth-status").set_defaults(func=cmd_auth_status)
    sub.add_parser("auth-clear").set_defaults(func=cmd_auth_clear)

    sub.add_parser("check-update").set_defaults(func=cmd_check_update)
    sub.add_parser("update").set_defaults(func=cmd_update)
    sub.add_parser("probe").set_defaults(func=cmd_probe)
    sub.add_parser("login").set_defaults(func=cmd_login)
    sub.add_parser("self-test").set_defaults(func=cmd_self_test)

    pub_p = sub.add_parser("public-stats")
    pub_p.add_argument(
        "--type",
        choices=("structure", "student", "employee", "all"),
        default="structure",
        help="Public statistic endpoint type (default: structure)",
    )
    pub_p.set_defaults(func=cmd_public_stats)

    get_p = sub.add_parser("get")
    get_p.add_argument(
        "paths",
        nargs="+",
        help="One or more paths/URLs under HEMIS base URL (fetched sequentially in one login)",
    )
    get_p.add_argument("--follow", action="store_true", help="Follow redirects")
    get_p.add_argument("--raw", action="store_true", help="Print raw body instead of JSON (single path only)")
    get_p.add_argument(
        "--parse",
        choices=("none", "auto", "timetable", "grid", "files"),
        default="none",
        help="Built-in HTML/JSON parser mode (default: none)",
    )
    get_p.add_argument(
        "--include-body",
        action="store_true",
        help="Keep raw HTML body alongside parsed output when --parse is used",
    )
    get_p.add_argument("--max-chars", type=int, default=200000, help="Max body chars (default: 200000)")
    get_p.set_defaults(func=cmd_get)

    stu_p = sub.add_parser("student-get")
    stu_p.add_argument(
        "endpoints",
        nargs="+",
        help="One or more Student REST API endpoints (e.g. account/me education/schedule)",
    )
    stu_p.set_defaults(func=cmd_student_get)

    bnd_p = sub.add_parser("bundle")
    bnd_p.add_argument(
        "--output-dir",
        default=os.path.join(SKILL_ROOT, "dist"),
        help="Output directory for web skill packages (default: ./dist)",
    )
    bnd_p.add_argument(
        "--server-url",
        default="https://your-hemis-tunnel.example.com",
        help="Public HTTPS URL written into hemis_openapi.json",
    )
    bnd_p.set_defaults(func=cmd_bundle)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
