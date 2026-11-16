---
name: hemis
description: "Complete operational playbook and toolkit for Uzbekistan HEMIS university portals (Teacher Web, Student Web, Student/Public REST API). Use for session health, OneID login, schedules, attendance, grading/vedomost, Fan resurslari/elFinder audits and uploads, Student REST API, or offline file analysis and script generation in web chats."
license: MIT
compatibility: "Universal Agent Skill (Antigravity, Claude Code/Web, Gemini CLI/Web, ChatGPT Skills/Projects, Codex). Live HEMIS calls require Python >=3.10 stdlib (no pip) and Uzbekistan IP (or Local-UZ Remote MCP Bridge)."
metadata:
  author: Dostonbek Mo'ydinov
  version: "1.0.0"
  repository: https://github.com/mpython77/hemis-skill
---

# HEMIS Operational Skill

> **Canonical Verification Chain:** `Who/Account -> Which Portal -> Curriculum -> Semester -> Subject (vs curriculum_subject) -> Training Type -> Record ID -> Actual File / Result`.
> Never declare completeness, live session, or empty status from surface similarity, cached files, single-filter checks, or raw HTTP `200` alone.

## 1. Three Separate Interfaces & Account Separation

| Interface | Base URL | Auth Mechanism | Scope |
|---|---|---|---|
| **Teacher Web** (`mening HEMISim`) | `https://hemis.namdtu.uz` | `backend_8` (or `backend_<n>`) + `_csrf-backend` (+ optional `_identity-backend`) | Teacher cabinet (`O'qituvchi` role) |
| **Student Web** | `https://student.namdtu.uz` | `frontend` + `_csrf-frontend` (`FormStudentLogin`) | Student HTML cabinet (separate account) |
| **Student REST API** | `https://student.namdtu.uz/rest/v1` | `Authorization: Bearer <token>` via `POST /auth/login` (`data.token`) | Student JSON API (`/v1` already in base; public stats at `/rest/v1/public/stat-*`) |

- **Never cross-use auth:** Student REST Bearer tokens do not work on `hemis.namdtu.uz`; web cookies do not replace REST Bearer tokens (`401`); Teacher sessions and Student sessions belong to distinct accounts — always confirm identity first (`GET /dashboard/profile` on web without auto-redirect, or `GET /rest/v1/account/me` on API).
- **Encrypted Local Credential Vault (`~/.hemis/credentials.json`) & Zero-Leak Policy:**
  - When a user provides their OneID login and password once, immediately save them into the OS-encrypted user vault (`~/.hemis/credentials.json` — encrypted via **Windows DPAPI `CryptProtectData`** on Windows or `0600` PBKDF2-HMAC sealed file on Linux/macOS) using `python scripts/hemis_cli.py auth-save`.
  - In every new chat, check `python scripts/hemis_cli.py doctor` (`credentials.configured`). When `"configured": true`, `hemis_cli.py` and `HemisOneIDClient` automatically load and decrypt `~/.hemis/credentials.json` in memory — **never ask the user to re-enter credentials that are already saved in the vault**.
  - Never write raw passwords, cookies (`backend_*`, `frontend`, `_csrf-*`), Bearer tokens, CAPTCHA values, or PINFL/passport numbers into workspace files, git commits, or chat output.
- **Windows TLS Rule:** Python `requests` often raises `SSL: CERTIFICATE_VERIFY_FAILED` on HEMIS hosts. Always use `urllib.request` + `ssl.create_default_context()` (OS truststore), never disable TLS verification (`verify=False`), and handle redirects manually (`NoRedirect`) so `302 -> /dashboard/login` is caught cleanly.

## 2. Hard Safety & Side-Effect Guardrails

1. **Reliable session liveness check:** `GET /dashboard/profile` with redirects disabled -> `302 -> /dashboard/login` means unauthenticated; `200` + profile identity markers confirms active; `200` without identity markers (login HTML), `404`, or network/TLS timeout does **not** confirm session state accurately — report network/TLS errors separately.
2. **Ping is not resurrection:** `/keepalive/ping` extends active idle time (`1800s` on teacher backend; ~4 min polling on student) but cannot revive an expired session or bypass absolute server expiry/sleep.
3. **Never GET state-mutating endpoints casually:**
   - Teacher: `/message/compose` *(bare, no `?id=` -> creates blank DB draft)*, `/dashboard/toggle?...`, `/poll/mine?set=1&...`, `/message/my-messages?...&delete=1`, `/dashboard/logout`, `/dashboard/profile?certificate=1`, `/oz|ru|en/...`, `/file-resource/edit?id=<res>&item=<item>&cancel=1`, `/file-resource/edit?id=<res>&delete=1`, `/system/cache` *(POST)*.
   - Student: `/student/reference?get=1` *(generates new certificate)*, `/student-data/rent-sync` *(sync action)*, `/student-data/upload-plagiarism` *(consumes quota)*, `/dashboard/logout`.
   - Never force-close an active browser or pry open locked browser profile DBs to harvest cookies.
4. **Confirm before irreversible/write actions:** Locking vedomost (`POST /teacher/check-overall-rating`), deleting resources/files/messages, submitting grades/attendance, posting announcements/applications/signatures, or overwriting existing attachments. Pause `0.25s–0.6s` between sequential requests; avoid spamming parallel scrapes.
5. **Respect RBAC (`View != Edit`):** Seeing another teacher's record or curriculum in a filter does not grant edit/upload access. Never bypass empty `DepDrop` responses or `403` guards.

## 3. Reference Files Map

| Topic / Workflow | Reference File |
|---|---|
| **Auth, Cookies, Session Liveness, Dual CSRF, PJAX, GridView, Danger URLs** | [auth-http-pjax.md](references/auth-http-pjax.md) |
| **Fan resurslari (`elFinder`), Audits, Recovery, Buggy `+` Filenames & Verification** | [resources-elfinder.md](references/resources-elfinder.md) |
| **Fanlar bazasi (`subject-topics`/`tasks`/`calendar-plan`), Imtihonlar, Nazoratlar & Vedomost** | [curriculum-tasks-exams.md](references/curriculum-tasks-exams.md) |
| **Schedules (`UTC+05`), Parallel Lessons, Topic Matching, Attendance/NB, Grades & Retakes** | [lessons-attendance-grading.md](references/lessons-attendance-grading.md) |
| **Ilmiy faoliyat, E-hujjatlar, Xabarlar, Hisobotlar, Profil & Public Stats API** | [science-messages-system-api.md](references/science-messages-system-api.md) |
| **Student Web (`student.namdtu.uz`) & Authenticated Student REST API (`/rest/v1`)** | [student-portal-and-rest-api.md](references/student-portal-and-rest-api.md) |
| **Geo-IP `451`, tethering, PAC vs Python, failure recovery, upload timeouts, role-drift `403`** | [network-and-recovery.md](references/network-and-recovery.md) |
| **Evidence levels, wrong-conclusion table, audit counting, acceptance discipline** | [reasoning-pitfalls-and-evidence.md](references/reasoning-pitfalls-and-evidence.md) |

## 4. Critical Anti-Mistake Checklist

- Clear **all 6 filter params** (`_curriculum`, `_curriculum_subject`, `_training_type`, `_employee`, `_department`, `language`) before auditing `/file-resource/index`.
- Never trust `per-page=100` (server caps at `50`); paginate every page, deduplicate IDs, and check `Collected IDs == Server Summary`.
- Distinguish `subject` ID (`curriculum=10&subject=900`) from `curriculum_subject` / `cs` ID (`20001`); and distinguish item count (`Files=1`) from physical file count (`DOCX + ZIP = 2`).
- For `/file-resource/edit` POST: field name is `ESubjectFileResource[_curriculum_subject]` (DOM id `#_subject`), never `_subject`. Never put `+` in uploaded filenames.
- On download tests (`/file-resource/edit?id=<id>&download=<id>[&index=<idx>]`): `HTTP 200` + `text/html` means failure; inspect binary/ZIP members, individual `HEAD` status, and SHA-256 hashes.
- Use `UTC+05:00` (`Asia/Tashkent`) whenever interpreting `"bugun"`, `"ertaga"`, or UNIX `lesson_date` timestamps.
- For Student REST: `/rest/v1` already contains `/v1`; `/education/exam-table` requires `semester=<id>` (`400` otherwise); `/student/contract` returns `400` on grant students; `/education/semesters` can mark multiple rows `current=true`.

## 5. Bundled Automation (`scripts/`, stdlib-only, Python 3.10+)

> **Agents: start here (mandatory pre-flight & update check).**
> Before starting any HEMIS task, run `python scripts/hemis_cli.py doctor`. If the JSON output shows `"update_check": {"update_available": true}`, run `python scripts/hemis_cli.py update` immediately to pull the latest skill rules and scripts from `https://github.com/mpython77/hemis-skill` (works both inside a git clone and inside standalone `~/.gemini/config/skills/hemis` or `~/.claude/skills/hemis`). No `pip install` is required.

| Need | Command (auto-loads `~/.hemis/credentials.json` vault or `HEMIS_ONEID_*` env vars) |
|---|---|
| Health + vault status + GitHub update check | `python scripts/hemis_cli.py doctor` |
| Save credentials once (OS-encrypted DPAPI/0600 vault) | `python scripts/hemis_cli.py auth-save` (or `--stdin` JSON) |
| Check / clear saved credentials | `python scripts/hemis_cli.py auth-status` / `python scripts/hemis_cli.py auth-clear` |
| Check or apply latest GitHub updates | `python scripts/hemis_cli.py check-update` / `python scripts/hemis_cli.py update` |
| Why can't I reach HEMIS? (Geo-IP `451`, TLS, DNS) | `python scripts/hemis_cli.py probe` |
| Public university stats (no login needed) | `python scripts/hemis_cli.py public-stats --type all` |
| Prove a login works | `python scripts/hemis_cli.py login` |
| Fetch & parse one or more pages in 1 login | `python scripts/hemis_cli.py get /dashboard/profile /teacher/time-table --parse auto` |
| Student REST API (`/rest/v1/...`) | `python scripts/hemis_cli.py student-get account/me education/schedule` |
| Build Web Skill ZIP & ChatGPT bundle | `python scripts/hemis_cli.py bundle` |
| Start Remote MCP / OpenAPI Bridge (Local UZ PC) | `python scripts/hemis_mcp_server.py --port 8765` (or `--stdio`) |
| Full programmatic control | `from hemis_oneid_auth import HemisOneIDClient` (see `docs/ARCHITECTURE.md`) |
| Browser access from a blocked network | `python scripts/hemis_split_proxy.py --enable-pac` (Windows, zero-admin PAC split-routing) |

## 6. Environment Adaptation (Local Agents vs Web Sandboxes & Remote MCP)

1. **Local Terminal / Coding Agent (Antigravity, Claude Code, Codex, Gemini CLI):** Follow §5 CLI/Python workflows directly (`doctor` -> `probe` -> vault -> `login`/`get`).
2. **Web Chat with Attached Remote MCP Tools (`claude.ai` Custom Connector or `chatgpt.com` Developer Mode):** Call the `hemis_*` MCP tools directly. Credentials stay inside the user's Uzbekistan host vault — never ask the user to paste OneID passwords into web chat when MCP is connected.
3. **Web Sandbox Without Internet / Without Terminal (`gemini.google.com`, `chatgpt.com` Projects, `claude.ai` offline):** Do **not** fabricate live HEMIS results or pretend a network call succeeded (`HEMIS` also blocks foreign cloud IPs with `HTTP 451`). Clearly switch to **Offline Audit & Script Mode**: analyze user-uploaded HTML/Excel/PDF/ZIP files using the `references/*.md` rules and `parse_gridview` / `parse_timetable_events` / `parse_resource_item_files` in `scripts/hemis_oneid_auth.py`, generate clean Word/Excel/PDF deliverables, or output ready-to-run local `hemis_cli.py` commands.
4. **Browser Extension Mode (Claude in Chrome, Gemini in Chrome, ChatGPT Atlas):** Operate inside the user's live Uzbekistan-IP browser tab while obeying all §2 safety guardrails (never open bare `/message/compose`, `?delete=1`, `?cancel=1`, or `/dashboard/switch-role`).

**Hard facts discovered in production:**
- `hemis.namdtu.uz` / `student.namdtu.uz` return **HTTP 451 (Kirish cheklangan)** to non-Uzbekistan IPs. Cloud agents (GitHub Actions, Railway, etc.) **cannot** log in; run from a UZ network. OneID (`id.egov.uz`) itself works from anywhere.
- The server omits the `Thawte TLS RSA CA G1` intermediate; the client embeds it, keeping `CERT_REQUIRED` on Windows, Linux and macOS.
- Login is **fresh per run** (no cookie persistence) to honor the Zero-Secret Policy; reuse one `HemisOneIDClient` instance inside a task and call `ensure_session()` for auto re-login.
- Non-idempotent POSTs (uploads/forms) are **never auto-retried**; after an ambiguous timeout search/read back before resending.
- Label claims with evidence levels (see `references/reasoning-pitfalls-and-evidence.md`) — live vs historical.
- Never pass credentials as CLI arguments; never echo them; never commit `.env` files.
