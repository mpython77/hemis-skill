# Architecture

```
            ┌────────────────────────── AI agent (any) ──────────────────────────┐
            │ CLI / IDE Agents (Antigravity, Claude Code, Codex, Gemini CLI)     │
            │ Web AI (Claude.ai, Gemini Web, ChatGPT Projects/MCP) via dist/     │
            └───────┬──────────────────────────┬───────────────────────┬─────────┘
                    │ CLI JSON (stdout)        │ import                │ Remote/Stdio MCP + OAuth 2.1
           scripts/hemis_cli.py       scripts/hemis_oneid_auth.py   scripts/hemis_mcp_server.py
       (doctor/auth-save/update/get)    (stdlib OneID + parsers)   (UZ PC + cloudflared tunnel)
                    │                          │                               │
                    └──────────────────────────┴───────────────┬───────────────┘
                                                               ▼
         scripts/hemis_split_proxy.py            hemis.*.uz / student.*.uz (Geo-IP: UZ only)
         PAC + CONNECT bridge (Windows)          id.egov.uz / sso.egov.uz (global)
```

## Design principles

1. **Zero dependencies.** Python ≥3.10 stdlib only → `git clone` and run; works inside any sandbox.
2. **Zero-secret.** Credentials enter via env vars, live in process memory only; login is fresh per run; outputs are sanitized.
3. **Agent-first interface.** The CLI emits exactly one JSON document and uses stable exit codes (0 ok / 1 operational / 2 usage).
4. **Fail loudly and precisely.** Geo-block (451), TLS/network errors, OneID `code=2` (SMS) and `code=72` (scope) are distinct errors; network faults are never reported as expired sessions.
5. **Safe by default.** Known state-mutating GET routes raise `ValueError` unless `allow_unsafe=True`; cross-origin redirects strip auth/CSRF/Origin/Referer.
6. **Strict TLS everywhere.** `CERT_REQUIRED` + embedded Thawte intermediate (HEMIS serves an incomplete chain; Windows fetches AIA, OpenSSL does not).
7. **Network resilience.** If the default route is blocked, the client probes other local IPv4 interfaces (tethering `10.*` first) and recovers when a bound interface disappears.
8. **Deterministic tests.** `run_self_test()` in each script uses mocks/ephemeral local sockets only — no network, no credentials.

## Login flow (OneID → HEMIS)

1. `GET /dashboard/auth?authclient=oneid` → 302 to `sso.egov.uz`, sets initial `backend_*` cookie
2. Follow `sso.egov.uz` redirects → `id.egov.uz?client_id&token_id(UUID)&method`
3. `POST id.egov.uz/api/identity/auth/login` → OneID JWT
4. `POST id.egov.uz/api/sso/v1/generate` → `callbackUrl`, `code`, `state`
5. `GET callbackUrl?code&state` (same cookie jar) → authenticated HEMIS cookies
6. `GET /dashboard/profile` (no redirect) → verify identity, extract Yii2 CSRF token

## Extending

- New HEMIS workflow → add a method to `HemisOneIDClient` using `request()` / `post_form()`; add an offline mock case to `run_self_test()`; document the verified route in `references/`.
- Keep modules single-file and stdlib-only so agents can copy a script in isolation.
