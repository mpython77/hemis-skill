# Changelog

## 1.0.0 — 2026-11-15
- Pure-stdlib OneID → HEMIS client (`hemis_oneid_auth.py`): 6-step login, session verify/keepalive/auto re-login.
- Embedded Thawte TLS RSA CA G1 for strict TLS on Windows/Linux/macOS.
- HTTP 451 Geo-IP detection; NIC failover with tethering priority and stale-IP recovery.
- Safe request layer: unsafe-GET guard (teacher, student, switch-role), cross-origin header hygiene, gzip/deflate;
  non-idempotent POSTs are never auto-retried.
- Helpers: Yii2 CSRF form posts, sticky-filter clearing, DepDrop, elFinder Dual-CSRF upload/mkdir/copy (5 MiB, no `+`), path normalization.
- Built-in stdlib parsers (`parse_timetable_events`, `parse_gridview`, `parse_resource_item_files`, `auto_parse_hemis_html`) and Student REST / Public Stats helpers (`fetch_public_stats`, `student_rest_login`, `student_rest_get`).
- Agent CLI (`hemis_cli.py`): `doctor`, `auth-save`, `auth-status`, `auth-clear`, `check-update`, `update`, `probe`, `public-stats`, `login`, multi-path `get` with `--parse`, `student-get`, `bundle`, `self-test`; OS-encrypted `~/.hemis/credentials.json` vault (Windows DPAPI / POSIX `0600`) and `HEMIS_BASE_URL` / `HEMIS_STUDENT_URL` support.
- Web Skill compatibility & packager (`build_web_bundles.py`): strict AgentSkills `SKILL.md` frontmatter (`metadata:` nesting), deterministic `dist/hemis-web-skill.zip` (`hemis/SKILL.md`), merged ChatGPT Free 5-file `HEMIS_PLAYBOOK_BUNDLE.md` + `<=8000`-char `INSTRUCTIONS.txt`, and ChatGPT-compliant `hemis_openapi.json`.
- Zero-dependency MCP server (`hemis_mcp_server.py`): supports both `stdio` and Streamable HTTP (`/mcp`) with built-in OAuth 2.1 + PKCE `S256` + RFC 9728 / RFC 8414 `.well-known` discovery + RFC 7591 DCR (`/oauth/register`) + REST endpoints (`/api/*`) and reverse-proxy HTTPS header support (`cloudflared tunnel`) for Web Claude & ChatGPT Developer Mode behind Uzbekistan IPs.
- Windows zero-admin PAC + CONNECT split-routing proxy (`hemis_split_proxy.py`) with multi-university `hemis.*.uz` / `student.*.uz` PAC matching, `--hosts`, and target-host allowlist guard (`403 Forbidden Target Host`).
- Skill playbook + references (auth, elFinder, network & recovery, reasoning pitfalls, web platforms guide), AGENTS.md, CI matrix, install scripts.
- 37 deterministic offline checks (auth 23 + proxy 6 + web bundles 3 + MCP/OAuth 5).
