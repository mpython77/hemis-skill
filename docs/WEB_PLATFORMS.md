# Using `hemis-skill` on Web Gemini, Web Claude & Web ChatGPT

This repository follows the open **Agent Skills (`SKILL.md`)** standard while providing built-in generators and a zero-dependency **Local-UZ Remote MCP Bridge** for web platforms.

## 1. Two Reality Checks Before Using Web AI with HEMIS

1. **Cloud sandboxes cannot connect to HEMIS directly:**
   - **Gemini Web (`gemini.google.com`)** and **ChatGPT Code Interpreter** run Python with **outbound internet disabled**.
   - **Claude Web (`claude.ai`)** runs containers on non-Uzbekistan cloud IPs behind an egress proxy: outbound requests to `hemis.*.uz` / `id.egov.uz` fail at the sandbox proxy with **`403 CONNECT (policy denial)`**, and even with open egress, HEMIS rejects non-Uzbekistan IPs with **HTTP 451 (`Geo-IP block`)**. Do not ask the user for OneID credentials or retry `login` inside the cloud sandbox — switch straight to Mode A or Mode B.
2. **Two clean ways web AI works with `hemis-skill`:**
   - **Mode A — Skill Playbook / Offline Audit & Script Mode (works on ALL 3 platforms immediately):** Upload HTML/Excel/PDF exports from HEMIS; Web AI applies every verified rule (elFinder 5 MiB/`+` check, GridView pagination traps, schedule timezone `UTC+05`, DepDrop field names), parses tables via `hemis_oneid_auth.py`, builds Word/Excel deliverables, or writes exact `hemis_cli.py` commands.
   - **Mode B — Live HEMIS Access via Local-UZ Remote MCP Bridge (Claude Web Free/Pro/Max & ChatGPT Web Plus/Pro/Business) or Chrome Extensions:** Run `scripts/hemis_mcp_server.py` on your Uzbekistan computer behind an HTTPS tunnel (`cloudflared tunnel`). Credentials stay locked in your local encrypted vault (`~/.hemis/credentials.json` — Windows DPAPI / POSIX `0600`) and outbound requests to HEMIS exit from your Uzbekistan IP (`HTTP 200`, no `451`).

---

## 2. Quick Setup by Web Platform

First, build the validated web packages locally (or download `hemis-web-skill.zip` from GitHub Releases/Actions):

```bash
python scripts/hemis_cli.py bundle
```

Outputs in `./dist/`:
- `dist/hemis-web-skill.zip` (single-folder root `hemis/SKILL.md`, `references/`, `scripts/`)
- `dist/chatgpt-project/HEMIS_PLAYBOOK_BUNDLE.md` + `dist/chatgpt-project/INSTRUCTIONS.txt`
- `dist/hemis_openapi.json` (OpenAPI `3.1.0` schema for REST clients)

### A) Claude Web (`claude.ai` — Free, Pro, Max, Team, Enterprise)
1. **Skill ZIP (Mode A):** Enable **Code execution & file creation** in `Settings > Capabilities`, then go to `Customize > Skills > Add / Upload skill` and upload **`dist/hemis-web-skill.zip`**.
2. **Live HEMIS Connector (Mode B — works even on Free tier, 1 connector):**
   - On your Uzbekistan PC, save credentials once (`python scripts/hemis_cli.py auth-save`).
   - Start `python scripts/hemis_mcp_server.py --port 8765` and expose via `cloudflared tunnel --url http://127.0.0.1:8765`.
   - In `claude.ai` -> `Settings > Connectors > Add custom connector`, paste `https://<your-tunnel>/mcp` and approve OAuth with the one-time terminal passcode.
3. **In browser directly:** Use **Claude in Chrome** alongside the uploaded `hemis` skill on an open HEMIS tab.

### B) Gemini Web (`gemini.google.com`)
*Note: Google is retiring legacy Gems in favor of open **`SKILL.md` Skills** (personal accounts transition Nov 17 – Dec 17, 2026).*
1. Go to `gemini.google.com` -> `Menu > Customize > Skills > Create > Upload`.
2. Upload **`dist/hemis-web-skill.zip`** (or `SKILL.md` directly; under 100 files / 100 MB limit).
3. Invoke inline in any chat via **`/hemis`** or **`@hemis`**.
4. *(Live browser alternative:)* Pair with **Gemini in Chrome** (`/`) on an open HEMIS tab, or use **Antigravity / Gemini CLI** locally for full OneID automation. *(Consumer `gemini.google.com` does not support custom Remote MCP servers; Gemini Enterprise supports StreamableHTTP MCP.)*

### C) ChatGPT Web (`chatgpt.com`)
*Note: OpenAI froze personal Custom GPT creation and retires legacy Custom GPTs on Dec 11, 2026 in favor of Projects, Developer Mode MCP Apps, and Skills (Business/Enterprise/Edu).*
- **Free / Plus / Pro — ChatGPT Projects (Mode A):**
  1. Create a new **Project** -> paste `dist/chatgpt-project/INSTRUCTIONS.txt` (`<= 8000` chars) into **Instructions**.
  2. Upload **`dist/chatgpt-project/HEMIS_PLAYBOOK_BUNDLE.md`**, **`scripts/hemis_oneid_auth.py`**, and **`scripts/hemis_cli.py`** (only `3` files — fits Free tier's `5`-file limit!).
- **Plus / Pro / Business / Enterprise — Live Remote MCP (Mode B):**
  1. Enable **Developer mode** (`Settings > Security` or `Apps > Advanced settings`).
  2. Run `python scripts/hemis_mcp_server.py --port 8765` + `cloudflared tunnel` on your UZ PC.
  3. Add custom MCP URL `https://<your-tunnel>/mcp` and complete the PKCE OAuth prompt with your terminal passcode.
- **Business / Enterprise / Edu — Skills (`Plugins > Skills`):** Upload **`dist/hemis-web-skill.zip`** or `SKILL.md` directly.

---

## 3. MCP Tools Exposed by `hemis_mcp_server.py` (Read-Only Safe)

All MCP tools use local encrypted vault auth, enforce the `unsafe-GET` guard, and sanitize responses:
- `hemis_public_stats` — credential-free `/rest/v1/public/stat-*` stats (`structure`, `student`, `employee`)
- `hemis_probe` — reachability, HTTP `451` Geo-IP, TLS, local source IP diagnosis
- `hemis_session_status` — verify/refresh OneID session via local vault (`ensure_session()`)
- `hemis_fetch_page` — safe GET + auto-parse (`timetable`, `grid`, `files`, `auto`)
- `hemis_audit_subjects` — `DepDrop` subjects + parsed `/file-resource/subject?cs=<id>`
- `hemis_student_api` — Student REST API GET (`/rest/v1/...`)
