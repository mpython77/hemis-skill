# Security Policy

## Credential Handling & Encrypted Local Vault
- Credentials may be provided via `HEMIS_ONEID_LOGIN` / `HEMIS_ONEID_PASSWORD` environment variables or stored once in the user-scoped encrypted vault at `~/.hemis/credentials.json` (outside any project/git repository) via `python scripts/hemis_cli.py auth-save`.
- On Windows, `~/.hemis/credentials.json` is encrypted using **Windows DPAPI (`CryptProtectData`)** bound to the current OS user account; on POSIX systems it is sealed with PBKDF2-HMAC-SHA256 and `0600` owner-only file permissions.
- Passwords, cookies, JWTs, CSRF tokens, PINFL/passport numbers, and raw credentials must **never** be passed on the command line, printed to stdout/logs, echoed in chat, or committed to Git. `.gitignore` blocks `.env*`, `.hemis/`, `*credentials*.json`, cookie dumps, and `exports/`.
- TLS verification is never disabled (`CERT_REQUIRED`).
- To wipe stored credentials at any time, run `python scripts/hemis_cli.py auth-clear`.

## Reporting a vulnerability
Open a private security advisory on GitHub (Security → Report a vulnerability). Do not post secrets in public issues.

## Responsible use
Use only with your own account and permissions. Respect HEMIS rate limits and RBAC; do not bypass the Geo-IP restriction by
unauthorized means. The client's read-only guard exists to prevent accidental data mutation.
