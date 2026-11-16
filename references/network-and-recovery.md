# Network, Geo-IP, Tethering & Failure Recovery

> Evidence labels used in this repo: **Observed** (seen live) · **Verified-by-read-back** (re-read + hash checked) ·
> **User-context** (reported, not re-tested) · **Historical** (past snapshot) · **Unknown** (never assume).

## 1. How HEMIS blocks you (Observed)

| Symptom | Layer | Meaning |
|---|---|---|
| DNS → `195.158.3.34`, **TCP 80/443 timeout**, other sites fine, mobile internet works | IP/route filter | Your ISP path/IP is blocked or filtered. Not a server outage. |
| `HTTP 451 — Kirish cheklangan` | nginx Geo-IP | Non-Uzbekistan IP (cloud/VPS). Some foreign probes even reach TCP 443 but get 451. |
| `CERTIFICATE_VERIFY_FAILED` on Linux/macOS only | TLS chain | Server omits `Thawte TLS RSA CA G1`; the client embeds it. |
| Works in Chrome, Python times out | Proxy config | Chrome honours the Windows PAC; `urllib`/`requests` do **not**. |

The exact server rule (e.g. fail2ban) cannot be proven without server logs — never claim it. A block on one IP/route does **not**
mean the server is down for everyone.

## 2. Reach HEMIS from a blocked network

1. **Preferred (programmatic):** `HemisOneIDClient` auto-fails over to another local interface (tethering `10.*` first) and
   recovers to the OS default route if a bound interface disappears. Force one with `--source-ip` / `HEMIS_SOURCE_IP`.
2. **Browser (Windows, zero-admin):** `python scripts/hemis_split_proxy.py --enable-pac` → PAC `http://127.0.0.1:8899/proxy.pac`
   routes only HEMIS hosts through the unblocked interface; everything else stays `DIRECT`. Undo with `--disable-pac`.
3. **Python via the local proxy (when you cannot bind a source IP):**
   ```python
   import urllib.request
   opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": "http://127.0.0.1:8899"}))
   # requests: s = requests.Session(); s.trust_env = False; s.proxies = {"https": "http://127.0.0.1:8899"}
   ```
   Use this opener **only** for HEMIS; unrelated services get a direct session. Never expose the proxy on a LAN/public interface.
4. USB tethering stabilises the phone↔PC link but does not strengthen the mobile radio signal. Changing DNS is not a fix when
   DNS already resolves and TCP times out.

## 3. Diagnosing correctly

- **PAC opens ≠ CONNECT works.** If the tethering interface vanished the proxy answers `502` even though the PAC URL loads.
- Interface IPs/metrics (Wi-Fi, PAN `10.x.x.x`, gateway, ifIndex) are **Historical** — re-discover on every reconnect.
- Classify first, then act: `python scripts/hemis_cli.py probe` → `ok` / `geo_blocked` / `network_or_tls_error` / `server_error`.
- Network/TLS errors are **never** "session expired"; auth/permission errors are **never** retried by hammering login.

## 4. Recovery policy for long jobs (uploads, audits)

1. On connection loss: **wait 30 s, retry at most 3 times**, then stop and report.
2. After reconnect: **search first** (resource card list, elFinder folder) and resume from the last checkpoint — never repeat an
   upload blindly. `HemisOneIDClient` itself does **not** auto-resend non-idempotent POSTs (uploads/forms) after an ambiguous
   timeout; GET/HEAD and OneID login POSTs are retried.
3. Slow tethering: connect-timeout and write-timeout are different. A large multipart POST can fail with
   `The write operation timed out` while profile GETs work. `elfinder_upload_bytes` raises its timeout to
   `upload_timeout` (default 180 s); keep ordinary GET timeouts short.
4. Report partial progress precisely: done · verified · unverified · remaining.
5. A shared cookie session can change role in another browser window → `/file-resource/*` suddenly `403` while
   `/dashboard/profile` is still `200`. Re-read the profile role and the visible role links; **never** guess `switch-role` IDs
   (`/dashboard/switch-role` is blocked by the client's unsafe-GET guard).
