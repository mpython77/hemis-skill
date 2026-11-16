#!/usr/bin/env python3
"""Build & validate cross-platform web skill packages from the single SKILL.md source of truth.

Outputs under --output-dir (default: ./dist):
  1. hemis-web-skill.zip                    -> Claude Web (Customize > Skills), Gemini Web (Skills), ChatGPT Skills
  2. chatgpt-project/HEMIS_PLAYBOOK_BUNDLE.md -> Merged references for ChatGPT Free Projects (5-file cap)
  3. chatgpt-project/INSTRUCTIONS.txt       -> <=8000-char system instructions naming bundled files explicitly
  4. hemis_openapi.json                     -> OpenAPI 3.1.0 schema for REST / GPT Actions bridge

Zero external dependencies (Python >= 3.10 standard library only).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from typing import Any
import zipfile

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTPUT_DIR = os.path.join(ROOT_DIR, "dist")

ALLOWED_FRONTMATTER_KEYS = frozenset(
    {"name", "description", "license", "compatibility", "allowed-tools", "metadata"}
)
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 1024
MAX_COMPATIBILITY_CHARS = 500
MAX_BODY_CHARS = 500_000
MAX_ZIP_FILES = 100
MAX_ZIP_BYTES = 20 * 1024 * 1024  # 20 MiB (fits both Claude Web ~20 MB and Gemini Web 100 MB)
MAX_CHATGPT_INSTRUCTIONS_CHARS = 8000

BUNDLE_SCRIPT_FILES = (
    "hemis_oneid_auth.py",
    "hemis_cli.py",
    "hemis_split_proxy.py",
    "hemis_mcp_server.py",
    "build_web_bundles.py",
)

CHATGPT_INSTRUCTIONS_TEMPLATE = """You are the HEMIS Operational Assistant (Uzbekistan university Teacher Web, Student Web, Student REST API v{version}).

ALWAYS inspect your attached knowledge files BEFORE answering:
1. Read `HEMIS_PLAYBOOK_BUNDLE.md` whenever asked about routes, OneID login, CSRF, DepDrop, elFinder uploads (max 5 MiB, NEVER '+' in filename), schedules (UTC+05 Asia/Tashkent), GridView pagination (server caps per-page at 50), Student REST (/rest/v1), danger GET traps, or verification levels.
2. When analyzing uploaded HEMIS HTML files (/teacher/time-table, /file-resource/index, /file-resource/edit), use the exact parsers inside `hemis_oneid_auth.py` (`parse_timetable_events`, `parse_gridview`, `parse_resource_item_files`, `auto_parse_hemis_html`).

OPERATING MODE BY ENVIRONMENT:
- If Remote MCP (`hemis_*`) or Action tools are connected: call `hemis_session_status`, `hemis_fetch_page`, `hemis_public_stats`, `hemis_audit_subjects`, or `hemis_student_api` directly. NEVER ask the user to paste OneID passwords into this web chat when MCP is configured (credentials live in the user's encrypted local vault).
- If NO MCP/Action tools are connected (sandboxed web chat): OpenAI Code Interpreter has NO internet access, and HEMIS returns HTTP 451 to non-Uzbekistan IPs. NEVER pretend a network call succeeded. Instead: (a) analyze uploaded HTML/Excel/PDF/ZIP files accurately, (b) generate verified Excel/DOCX/PDF deliverables, or (c) output exact `python scripts/hemis_cli.py ...` commands from `HEMIS_PLAYBOOK_BUNDLE.md` for the user's local machine.

NON-NEGOTIABLE SAFETY RULES:
- Never instruct calling mutating GET routes (`/message/compose` without `?id=`, `/dashboard/toggle`, `/dashboard/switch-role`, `/dashboard/logout`, `?delete=1`, `?cancel=1`, `?certificate=1`, `/oz/`, `/ru/`, `/en/`, `/student/reference?get=1`, `/student-data/rent-sync`, `/student-data/upload-plagiarism`).
- Clear all 6 sticky filters before auditing `/file-resource/index`; distinguish `subject` ID from `curriculum_subject` (`cs`) ID; distinguish card item count (`Files=1`) from physical file count.
- Never print or persist passwords, cookies (`backend_*`, `_csrf-*`, `frontend`), Bearer JWTs, or PINFL/passport numbers.
- Separate Teacher (`hemis.<uni>.uz`), Student Web (`student.<uni>.uz`), and Student REST (`student.<uni>.uz/rest/v1`) accounts/auth. Default target university can be overridden cleanly via `--base-url` / `HEMIS_BASE_URL`."""


def _unquote_yaml_scalar(raw: str) -> tuple[str, bool]:
    val = raw.strip()
    if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
        inner = val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return inner, True
    if len(val) >= 2 and val[0] == "'" and val[-1] == "'":
        return val[1:-1].replace("''", "'"), True
    return val, False


def parse_skill_frontmatter(skill_md_text: str) -> tuple[dict[str, Any], str, dict[str, bool]]:
    """Parse SKILL.md YAML frontmatter using stdlib only and return (fields, body, quoted_flags)."""
    text = skill_md_text.lstrip("\ufeff")
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        raise ValueError("SKILL.md must begin with YAML frontmatter '---'.")
    lines = text.splitlines()
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        raise ValueError("SKILL.md YAML frontmatter is missing closing '---'.")

    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :]).lstrip("\r\n")
    fields: dict[str, Any] = {}
    quoted_flags: dict[str, bool] = {}
    current_map_key: str | None = None

    for raw_line in fm_lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent > 0:
            if current_map_key is None:
                raise ValueError(f"Unexpected indented YAML line without parent key: {raw_line!r}")
            sub = raw_line.strip()
            if ":" not in sub:
                raise ValueError(f"Malformed nested YAML entry: {raw_line!r}")
            sk, sv = sub.split(":", 1)
            fields[current_map_key][sk.strip()] = _unquote_yaml_scalar(sv)[0]
            continue

        if ":" not in raw_line:
            raise ValueError(f"Malformed YAML frontmatter line: {raw_line!r}")
        key_raw, val_raw = raw_line.split(":", 1)
        key = key_raw.strip()
        val_stripped = val_raw.strip()
        if not val_stripped:
            fields[key] = {}
            current_map_key = key
        else:
            current_map_key = None
            unquoted, was_quoted = _unquote_yaml_scalar(val_stripped)
            fields[key] = unquoted
            quoted_flags[key] = was_quoted

    return fields, body, quoted_flags


def validate_skill_md_text(skill_md_text: str) -> dict[str, Any]:
    """Validate SKILL.md against strict AgentSkills / Claude Web / Gemini Web / ChatGPT rules."""
    fields, body, quoted_flags = parse_skill_frontmatter(skill_md_text)

    unknown = sorted(set(fields.keys()) - ALLOWED_FRONTMATTER_KEYS)
    if unknown:
        raise ValueError(
            f"Unexpected key(s) in SKILL.md frontmatter (move under 'metadata:'): {unknown}"
        )

    name = fields.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("SKILL.md frontmatter requires non-empty 'name'.")
    if len(name) > MAX_NAME_CHARS or not SKILL_NAME_RE.match(name):
        raise ValueError(
            f"SKILL.md 'name' ({name!r}) must be <= {MAX_NAME_CHARS} chars of lowercase a-z, 0-9, hyphens."
        )

    desc = fields.get("description")
    if not isinstance(desc, str) or not desc.strip():
        raise ValueError("SKILL.md frontmatter requires non-empty 'description'.")
    if len(desc) > MAX_DESCRIPTION_CHARS:
        raise ValueError(
            f"SKILL.md 'description' length ({len(desc)}) exceeds {MAX_DESCRIPTION_CHARS} chars."
        )
    if ": " in desc and not quoted_flags.get("description", False):
        raise ValueError("SKILL.md 'description' contains ': ' and should be YAML-quoted.")

    compat = fields.get("compatibility")
    if compat is not None:
        if not isinstance(compat, str) or len(compat) > MAX_COMPATIBILITY_CHARS:
            raise ValueError(f"SKILL.md 'compatibility' must be a string <= {MAX_COMPATIBILITY_CHARS} chars.")

    if len(body) > MAX_BODY_CHARS:
        raise ValueError(f"SKILL.md body ({len(body)} chars) exceeds {MAX_BODY_CHARS} chars.")

    return {
        "valid": True,
        "name": name,
        "description_chars": len(desc),
        "compatibility_chars": len(compat) if isinstance(compat, str) else 0,
        "body_chars": len(body),
        "root_keys": sorted(fields.keys()),
    }


def _read_version(root_dir: str) -> str:
    vfile = os.path.join(root_dir, "VERSION")
    if os.path.isfile(vfile):
        with open(vfile, encoding="utf-8") as fh:
            v = fh.read().strip()
            if v:
                return v
    return "1.0.1"


def _add_file_deterministic(
    zf: zipfile.ZipFile,
    src_path: str,
    arc_path: str,
    executable: bool = False,
) -> int:
    with open(src_path, "rb") as fh:
        data = fh.read()
    info = zipfile.ZipInfo(filename=arc_path, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    mode = 0o755 if executable else 0o644
    info.external_attr = (0o100000 | mode) << 16
    zf.writestr(info, data)
    return len(data)


def validate_web_zip(zip_path: str, expected_name: str = "hemis") -> dict[str, Any]:
    """Validate zip archive structure for Claude Web, Gemini Web, and ChatGPT Skills."""
    if not os.path.isfile(zip_path):
        raise ValueError(f"Missing zip archive: {zip_path}")
    compressed_bytes = os.path.getsize(zip_path)
    if compressed_bytes > MAX_ZIP_BYTES:
        raise ValueError(f"Zip size {compressed_bytes} exceeds {MAX_ZIP_BYTES} byte limit.")

    names: list[str] = []
    uncompressed_bytes = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            arc = info.filename
            if arc.startswith("/") or ".." in arc.split("/"):
                raise ValueError(f"Unsafe path in zip: {arc!r}")
            if "__pycache__" in arc or os.path.basename(arc) in (".DS_Store", "Thumbs.db"):
                raise ValueError(f"Disallowed artifact in zip: {arc!r}")
            if not arc.startswith(f"{expected_name}/"):
                raise ValueError(
                    f"Archive entry {arc!r} must live inside top-level '{expected_name}/' folder."
                )
            names.append(arc)
            uncompressed_bytes += info.file_size

        skill_arc = f"{expected_name}/SKILL.md"
        if skill_arc not in names:
            raise ValueError(f"Archive missing required manifest: {skill_arc}")
        skill_text = zf.read(skill_arc).decode("utf-8")
        fm_check = validate_skill_md_text(skill_text)
        if fm_check["name"] != expected_name:
            raise ValueError(
                f"Zip folder '{expected_name}/' does not match SKILL.md name {fm_check['name']!r}"
            )

    if len(names) > MAX_ZIP_FILES:
        raise ValueError(f"Zip contains {len(names)} files (limit {MAX_ZIP_FILES}).")
    if uncompressed_bytes > MAX_ZIP_BYTES:
        raise ValueError(f"Uncompressed zip size {uncompressed_bytes} exceeds {MAX_ZIP_BYTES}.")

    return {
        "valid": True,
        "zip_path": zip_path,
        "file_count": len(names),
        "compressed_bytes": compressed_bytes,
        "uncompressed_bytes": uncompressed_bytes,
        "skill_name": expected_name,
    }


def build_web_skill_zip(root_dir: str, out_zip_path: str) -> dict[str, Any]:
    """Package SKILL.md, VERSION, docs, references, and scripts under top-level `<name>/`."""
    skill_md_path = os.path.join(root_dir, "SKILL.md")
    with open(skill_md_path, encoding="utf-8") as fh:
        skill_text = fh.read()
    fm = validate_skill_md_text(skill_text)
    skill_name = fm["name"]

    os.makedirs(os.path.dirname(os.path.abspath(out_zip_path)), exist_ok=True)
    with zipfile.ZipFile(out_zip_path, "w") as zf:
        _add_file_deterministic(zf, skill_md_path, f"{skill_name}/SKILL.md")
        version_path = os.path.join(root_dir, "VERSION")
        if os.path.isfile(version_path):
            _add_file_deterministic(zf, version_path, f"{skill_name}/VERSION")
        arch_path = os.path.join(root_dir, "docs", "ARCHITECTURE.md")
        if os.path.isfile(arch_path):
            _add_file_deterministic(zf, arch_path, f"{skill_name}/docs/ARCHITECTURE.md")

        refs_dir = os.path.join(root_dir, "references")
        if os.path.isdir(refs_dir):
            for fname in sorted(os.listdir(refs_dir)):
                if fname.endswith(".md"):
                    _add_file_deterministic(
                        zf,
                        os.path.join(refs_dir, fname),
                        f"{skill_name}/references/{fname}",
                    )

        scripts_dir = os.path.join(root_dir, "scripts")
        for sname in sorted(BUNDLE_SCRIPT_FILES):
            spath = os.path.join(scripts_dir, sname)
            if os.path.isfile(spath):
                _add_file_deterministic(
                    zf,
                    spath,
                    f"{skill_name}/scripts/{sname}",
                    executable=True,
                )

    return validate_web_zip(out_zip_path, expected_name=skill_name)


def build_chatgpt_project_bundle(root_dir: str, out_dir: str) -> dict[str, Any]:
    """Build merged HEMIS_PLAYBOOK_BUNDLE.md + <=8000-char INSTRUCTIONS.txt for ChatGPT Projects."""
    os.makedirs(out_dir, exist_ok=True)
    version = _read_version(root_dir)

    with open(os.path.join(root_dir, "SKILL.md"), encoding="utf-8") as fh:
        _, skill_body, _ = parse_skill_frontmatter(fh.read())

    sections: list[tuple[str, str]] = [("SKILL.md", skill_body.strip())]
    arch_path = os.path.join(root_dir, "docs", "ARCHITECTURE.md")
    if os.path.isfile(arch_path):
        with open(arch_path, encoding="utf-8") as fh:
            sections.append(("docs/ARCHITECTURE.md", fh.read().strip()))

    refs_dir = os.path.join(root_dir, "references")
    for fname in sorted(os.listdir(refs_dir)):
        if fname.endswith(".md"):
            with open(os.path.join(refs_dir, fname), encoding="utf-8") as fh:
                sections.append((f"references/{fname}", fh.read().strip()))

    toc = "\n".join(f"- `{title}`" for title, _ in sections)
    merged_parts = [
        f"# HEMIS Skill Complete Playbook Bundle (v{version})\n",
        "Auto-generated single-file bundle from `SKILL.md`, `docs/ARCHITECTURE.md`, and `references/*.md`.\n",
        "## Included Sections\n" + toc + "\n",
    ]
    for title, content in sections:
        merged_parts.append(f"\n---\n\n<!-- SECTION: {title} -->\n\n{content}\n")

    bundle_text = "\n".join(merged_parts)
    bundle_path = os.path.join(out_dir, "HEMIS_PLAYBOOK_BUNDLE.md")
    with open(bundle_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(bundle_text)

    instructions = CHATGPT_INSTRUCTIONS_TEMPLATE.format(version=version).strip() + "\n"
    if len(instructions) > MAX_CHATGPT_INSTRUCTIONS_CHARS:
        raise ValueError(
            f"INSTRUCTIONS.txt ({len(instructions)} chars) exceeds ChatGPT {MAX_CHATGPT_INSTRUCTIONS_CHARS} limit."
        )
    instr_path = os.path.join(out_dir, "INSTRUCTIONS.txt")
    with open(instr_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(instructions)

    return {
        "bundle_path": bundle_path,
        "bundle_chars": len(bundle_text),
        "sections_merged": len(sections),
        "instructions_path": instr_path,
        "instructions_chars": len(instructions),
    }


def build_openapi_schema(server_url: str = "https://your-hemis-tunnel.example.com") -> dict[str, Any]:
    """Generate a ChatGPT-Action / Swagger compliant OpenAPI 3.1.0 spec for the HTTP bridge."""
    schema: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": "HEMIS Local-UZ Bridge API",
            "version": "1.0.1",
            "description": "Read-only HEMIS bridge running inside Uzbekistan with DPAPI/0600 local vault.",
        },
        "servers": [{"url": server_url.rstrip("/")}],
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                }
            }
        },
        "security": [{"BearerAuth": []}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "getHealth",
                    "summary": "Check local bridge health and configured version",
                    "security": [],
                    "responses": {"200": {"description": "Bridge is reachable"}},
                }
            },
            "/api/public-stats": {
                "get": {
                    "operationId": "getPublicStats",
                    "summary": "Fetch credential-free public university statistics",
                    "parameters": [
                        {
                            "name": "type",
                            "in": "query",
                            "required": False,
                            "description": "Stat type: structure, student, or employee",
                            "schema": {
                                "type": "string",
                                "enum": ["structure", "student", "employee"],
                                "default": "structure",
                            },
                        }
                    ],
                    "responses": {"200": {"description": "Public statistics JSON"}},
                }
            },
            "/api/session-status": {
                "get": {
                    "operationId": "getSessionStatus",
                    "summary": "Verify or establish OneID session using local encrypted vault",
                    "responses": {"200": {"description": "Sanitized session verification report"}},
                }
            },
            "/api/fetch": {
                "post": {
                    "operationId": "fetchHemisPage",
                    "summary": "Safely GET and parse a HEMIS page using built-in HTML/JSON parsers",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["path"],
                                    "properties": {
                                        "path": {
                                            "type": "string",
                                            "description": "Relative HEMIS path such as /teacher/time-table",
                                        },
                                        "parse": {
                                            "type": "string",
                                            "enum": ["auto", "timetable", "grid", "files", "none"],
                                            "default": "auto",
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Parsed page output"}},
                }
            },
            "/api/student-fetch": {
                "post": {
                    "operationId": "fetchStudentRest",
                    "summary": "Query Student REST API (/rest/v1/...) endpoints",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["endpoint"],
                                    "properties": {
                                        "endpoint": {
                                            "type": "string",
                                            "description": "Student REST endpoint such as account/me",
                                        }
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Student REST JSON response"}},
                }
            },
        },
    }
    return schema


def validate_openapi_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Validate ChatGPT GPT Actions constraints (summary/desc <=300, param desc <=700)."""
    assert schema.get("openapi") == "3.1.0"
    paths = schema.get("paths") or {}
    assert paths, "OpenAPI schema must contain paths."
    op_count = 0
    for path_item in paths.values():
        for method_obj in path_item.values():
            op_count += 1
            for field in ("summary", "description"):
                val = method_obj.get(field)
                if isinstance(val, str) and len(val) > 300:
                    raise ValueError(f"OpenAPI {field} exceeds 300 chars: {len(val)}")
            for param in method_obj.get("parameters", []):
                pdesc = param.get("description", "")
                if len(pdesc) > 700:
                    raise ValueError(f"OpenAPI param description exceeds 700 chars: {len(pdesc)}")
    return {"valid": True, "operations": op_count}


def build_all(
    root_dir: str = ROOT_DIR,
    out_dir: str = DEFAULT_OUTPUT_DIR,
    server_url: str = "https://your-hemis-tunnel.example.com",
) -> dict[str, Any]:
    """Generate and validate all Web Skill bundles inside out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    zip_report = build_web_skill_zip(root_dir, os.path.join(out_dir, "hemis-web-skill.zip"))
    cg_report = build_chatgpt_project_bundle(root_dir, os.path.join(out_dir, "chatgpt-project"))
    openapi = build_openapi_schema(server_url=server_url)
    oa_valid = validate_openapi_schema(openapi)
    oa_path = os.path.join(out_dir, "hemis_openapi.json")
    with open(oa_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(openapi, fh, ensure_ascii=False, indent=2)
    return {
        "ok": True,
        "output_dir": out_dir,
        "web_skill_zip": zip_report,
        "chatgpt_project": cg_report,
        "openapi": {"path": oa_path, **oa_valid},
    }


def run_self_test() -> dict[str, Any]:
    """Deterministically verify frontmatter rules, web zip packing, ChatGPT bundle, and OpenAPI."""
    # 1. Real SKILL.md passes strict AgentSkills/Claude/Gemini frontmatter checks
    with open(os.path.join(ROOT_DIR, "SKILL.md"), encoding="utf-8") as fh:
        real_fm = validate_skill_md_text(fh.read())
    assert real_fm["valid"] and real_fm["name"] == "hemis"

    # 2. Invalid root keys (e.g. author at root) and invalid names are rejected
    bad_author_md = "---\nname: hemis\ndescription: \"ok\"\nauthor: X\n---\nbody\n"
    try:
        validate_skill_md_text(bad_author_md)
        raise AssertionError("Expected ValueError for root author key")
    except ValueError as exc:
        assert "Unexpected key" in str(exc)

    bad_name_md = "---\nname: Bad_Name\ndescription: \"ok\"\n---\nbody\n"
    try:
        validate_skill_md_text(bad_name_md)
        raise AssertionError("Expected ValueError for invalid name")
    except ValueError:
        pass

    # 3. Full bundle generation + validation in temp dir
    with tempfile.TemporaryDirectory() as tmpdir:
        report = build_all(ROOT_DIR, tmpdir)
        assert report["ok"] is True
        assert report["web_skill_zip"]["valid"] is True
        assert report["web_skill_zip"]["file_count"] <= MAX_ZIP_FILES
        assert report["chatgpt_project"]["instructions_chars"] <= MAX_CHATGPT_INSTRUCTIONS_CHARS
        assert report["chatgpt_project"]["sections_merged"] >= 9
        assert report["openapi"]["valid"] is True

        # Verify zero __pycache__ and exact hemis/ root in zip
        with zipfile.ZipFile(report["web_skill_zip"]["zip_path"], "r") as zf:
            arcs = zf.namelist()
            assert "hemis/SKILL.md" in arcs
            assert "hemis/scripts/hemis_mcp_server.py" in arcs
            assert "hemis/scripts/hemis_oneid_auth.py" in arcs
            assert all(a.startswith("hemis/") for a in arcs)

    return {
        "self_test_passed": True,
        "checks_passed": 3,
        "details": [
            "strict_skill_md_frontmatter_validation",
            "reject_unexpected_keys_and_bad_names",
            "web_zip_chatgpt_bundle_and_openapi_generation",
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory (default: ./dist)")
    p.add_argument(
        "--server-url",
        default="https://your-hemis-tunnel.example.com",
        help="Public HTTPS URL written into hemis_openapi.json",
    )
    p.add_argument("--self-test", action="store_true", help="Run offline self-test suite")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    if args.self_test:
        print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(build_all(ROOT_DIR, args.output_dir, args.server_url), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
