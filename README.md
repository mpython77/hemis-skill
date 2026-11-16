# hemis-skill

**An agent-ready skill + zero-dependency toolkit for HEMIS** (`hemis.namdtu.uz`, `student.namdtu.uz`).
Any AI agent (Antigravity, Claude Code, Codex, Cursor, Gemini CLI, …) can clone this repo, read
[`AGENTS.md`](AGENTS.md) / [`SKILL.md`](SKILL.md), and immediately log in through OneID and work with HEMIS.

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

## What you get

| Piece | Path | Purpose |
|---|---|---|
| Skill playbook | [`SKILL.md`](SKILL.md) + [`references/`](references/) | Verified HEMIS knowledge: auth, CSRF, elFinder, schedules, grading, student REST, danger URLs |
| OneID client | [`scripts/hemis_oneid_auth.py`](scripts/hemis_oneid_auth.py) | Pure-stdlib 6-step OneID → HEMIS login, OS-encrypted credential vault (`~/.hemis/credentials.json`), session verify/keepalive, safe HTTP, NIC failover, Dual-CSRF elFinder, DepDrop, GridView/Timetable/Files parsers, Student REST & Public Stats |
| Agent CLI | [`scripts/hemis_cli.py`](scripts/hemis_cli.py) | `doctor`, `auth-save`, `auth-status`, `auth-clear`, `check-update`, `update`, `probe`, `public-stats`, `login`, `get` (multi-path + `--parse`), `student-get`, `bundle`, `self-test` |
| Remote + Stdio MCP | [`scripts/hemis_mcp_server.py`](scripts/hemis_mcp_server.py) | Stdlib Streamable HTTP + `stdio` MCP server with OAuth 2.1 + PKCE `S256` + RFC 7591 DCR for **Claude Web / ChatGPT Developer Mode / Claude Desktop** |
| Web Skill Packager | [`scripts/build_web_bundles.py`](scripts/build_web_bundles.py) | Builds & validates `hemis-web-skill.zip` (Claude/Gemini/ChatGPT Skills), `HEMIS_PLAYBOOK_BUNDLE.md` (ChatGPT Free 5-file cap), and `hemis_openapi.json` |
| Split-routing proxy | [`scripts/hemis_split_proxy.py`](scripts/hemis_split_proxy.py) | Windows zero-admin PAC + CONNECT bridge (`hemis.*.uz` / `student.*.uz` + target host allowlist guard) |
| Tests / CI | [`tests/`](tests/), [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | **37** deterministic offline checks (`23` auth + `6` proxy + `3` web bundles + `5` MCP/OAuth) on Linux/Windows/macOS × Python 3.10–3.13 |

## Quick start (30 seconds)

```bash
git clone https://github.com/mpython77/hemis-skill.git
cd hemis-skill
python scripts/hemis_cli.py doctor            # no pip install needed
export HEMIS_ONEID_LOGIN=...  HEMIS_ONEID_PASSWORD=...   # PowerShell: $env:HEMIS_ONEID_LOGIN="..."
python scripts/hemis_cli.py auth-save         # encrypts & saves to ~/.hemis/credentials.json (once!)
python scripts/hemis_cli.py probe             # reachability / Geo-IP diagnosis
python scripts/hemis_cli.py public-stats      # credential-free university structure stats
python scripts/hemis_cli.py login             # auto-loads encrypted vault in every future chat!
python scripts/hemis_cli.py get /dashboard/profile /teacher/time-table --parse auto
```

Python API:

```python
import sys; sys.path.insert(0, "scripts")
from hemis_oneid_auth import HemisOneIDClient

c = HemisOneIDClient()
c.login_with_oneid(login, password)      # credentials come from the user/env, never from files
c.ensure_session()                       # auto re-login when the session expires
status, headers, html = c.request("/dashboard/profile")
```

## Installing as a skill

| Agent / Web App | Command / Setup |
|---|---|
| Antigravity / Gemini CLI | `./install.sh antigravity` (or `.\install.ps1 -Target antigravity`) → `~/.gemini/config/skills/hemis` |
| Claude Code | `./install.sh claude` → `~/.claude/skills/hemis` |
| Claude Web / Gemini Web / ChatGPT | Run `python scripts/hemis_cli.py bundle` → upload `dist/hemis-web-skill.zip` or `dist/chatgpt-project/HEMIS_PLAYBOOK_BUNDLE.md`; optional live access via `hemis_mcp_server.py`. See [`docs/WEB_PLATFORMS.md`](docs/WEB_PLATFORMS.md). |
| Codex / generic CLI | Clone anywhere; point the agent at [`AGENTS.md`](AGENTS.md) |

## ⚠️ Important constraints

- **Geo-IP:** HEMIS answers **HTTP 451** to non-Uzbekistan IPs. GitHub Actions, Railway and other foreign clouds can run the
  offline tests but **cannot log in**. Run live commands from a UZ network (or use tethering + the split proxy).
- **Zero-Secret Policy:** credentials only via environment variables; nothing is persisted. See [`SECURITY.md`](SECURITY.md).
- **Read-only by default:** known state-mutating GET routes are blocked unless `allow_unsafe=True`.

## Universities

The toolkit targets any Yii2-based HEMIS portal. Defaults point to the university this was built and verified on
(`https://hemis.namdtu.uz`); other portals can be selected with `HEMIS_BASE_URL` / `--base-url`. Route knowledge in `references/`
was verified on that deployment — re-verify before relying on it elsewhere.

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Author

**Dostonbek Mo‘ydinov** — creator and maintainer.

## License

MIT — © 2026 Dostonbek Mo‘ydinov, see [`LICENSE`](LICENSE). Unofficial community project; not affiliated with NamDTU, HEMIS or OneID.
