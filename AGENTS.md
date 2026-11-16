# AGENTS.md — instructions for any AI agent working in this repository

You are looking at **hemis-skill**: a skill and toolkit for operating HEMIS on behalf of a user.

## Bootstrap (do this first, in order)

1. Read [`SKILL.md`](SKILL.md) fully. It is the canonical playbook; `references/*.md` are loaded on demand by topic.
2. Run `python scripts/hemis_cli.py doctor` (Python ≥ 3.10, **no dependencies**).
   - It must print `"ok": true`.
   - **Auto-update check:** Inspect `update_check.update_available` in the `doctor` JSON output. If `"update_available": true`, run `python scripts/hemis_cli.py update` immediately and re-read [`SKILL.md`](SKILL.md) so you always operate with the latest rules and fixes from `https://github.com/mpython77/hemis-skill`.
3. **Check `credentials.configured` in `doctor` output:**
   - If `"configured": true` (`source: "vault"` or `"env"`), credentials are **already saved** in `~/.hemis/credentials.json` — **do NOT ask the user for login/password again**; proceed directly to step 4/5!
   - If `"configured": false`, ask the user **once** for their OneID login and password, save them immediately into the OS-encrypted local vault (`~/.hemis/credentials.json`, encrypted with Windows DPAPI / POSIX `0600`) by piping JSON to `python scripts/hemis_cli.py auth-save --stdin` (or setting `HEMIS_ONEID_LOGIN` / `HEMIS_ONEID_PASSWORD` and running `python scripts/hemis_cli.py auth-save`), and never echo the password in chat or commit any file.
4. Run `python scripts/hemis_cli.py probe`. Interpret `diagnosis`:
   - `ok` → proceed. `geo_blocked` → HEMIS rejects this IP (HTTP 451); tell the user, do not retry or bypass.
   - `network_or_tls_error` → report as a connection problem, **never** as "session expired".
5. `python scripts/hemis_cli.py login` to prove authentication, then use `get` or the Python API.

## Rules (non-negotiable)

- **Zero-Secret:** never write passwords, cookies (`backend_*`, `_csrf-*`, `frontend`), JWTs or PINFL/passport numbers to files, logs, commits or chat.
- **Never** disable TLS verification. The client already handles the server's incomplete certificate chain.
- **Read-only first.** Do not call routes listed in `references/auth-http-pjax.md §6`; the client blocks them unless `allow_unsafe=True`. Confirm with the user before any write (uploads, grades, deletes, vedomost locking).
- Label claims **Observed / Verified-by-read-back / User-context / Historical / Unknown**; never present historical counts as live.
- Do not resend an upload after a timeout before searching the server (the client already refuses to auto-resend POSTs).
- Pause ≥0.25 s between sequential requests; no parallel scraping.
- Verify identity (`login`/`verify_session` → `user_summary`) before acting; teacher and student sessions are separate accounts.
- Respect RBAC: visible ≠ editable.

## Where things live

| Task | Look at |
|---|---|
| Auth / CSRF / PJAX / danger URLs | `references/auth-http-pjax.md` |
| Fan resurslari, elFinder upload (≤5 MiB, no `+` in names) | `references/resources-elfinder.md` |
| Curriculum, tasks, exams, vedomost | `references/curriculum-tasks-exams.md` |
| Schedule, attendance, grading | `references/lessons-attendance-grading.md` |
| Science, messages, system, public API | `references/science-messages-system-api.md` |
| Student portal + REST | `references/student-portal-and-rest-api.md` |
| Geo-IP / tethering / recovery / timeouts | `references/network-and-recovery.md` |
| Evidence levels, audit counting, wrong-conclusion table | `references/reasoning-pitfalls-and-evidence.md` |
| Web Claude / Web Gemini / ChatGPT setup & Remote MCP | `docs/WEB_PLATFORMS.md` |
| Code design | `docs/ARCHITECTURE.md` |

## Verify your changes

```bash
python scripts/hemis_cli.py self-test     # or: python -m unittest discover -s tests -v
```
All checks must pass before committing. Offline tests never contact HEMIS.

## Contributing knowledge

Add verified findings to the matching `references/*.md` (cite how verified, date, and what was *not* verified).
Never include real names, IDs, or credentials — use placeholders.
