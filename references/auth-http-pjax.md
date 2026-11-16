# Auth, Cookies, Session Liveness, CSRF, PJAX & Frontend Stack

## 1. TLS & HTTP Client Setup (Windows / Python)

- `requests` frequently fails against `https://hemis.namdtu.uz/` with `SSL: CERTIFICATE_VERIFY_FAILED` because certifi lacks Windows truststore chain handling.
- **Standard working pattern (keeps `Set-Cookie` updates synced inside one cookie jar):**
  ```python
  import http.cookiejar, ssl, urllib.request

  ctx = ssl.create_default_context()  # Uses Windows OS truststore cleanly
  cj = http.cookiejar.CookieJar()

  class NoRedirect(urllib.request.HTTPRedirectHandler):
      def redirect_request(self, req, fp, code, msg, headers, newurl):
          return None  # Catch 302 -> /dashboard/login or 302 -> ?id=<new_id> explicitly

  opener = urllib.request.build_opener(
      urllib.request.HTTPSHandler(context=ctx),
      urllib.request.HTTPCookieProcessor(cj),
      NoRedirect(),
  )
  ```
- **Never disable SSL verification** (`verify=False` / `CERT_NONE` is prohibited). Classify TLS, DNS, or timeout failures as **connection issues** — never misreport them as expired cookies!

## 2. Cookies & Portal Separation

| Portal | Cookies | Attributes / Notes |
|---|---|---|
| Teacher (`hemis.namdtu.uz`) | `backend_8` (or `backend_<num>`), `_csrf-backend`, optional `_identity-backend`, `sm_menu` (`'0'` expanded, `'1'` collapsed) | PHP session (`1800s` sliding TTL observed); Yii2 HMAC-SHA256 CSRF seed |
| Student (`student.namdtu.uz`) | `frontend`, `_csrf-frontend` | Distinct Yii2 app & session; incompatible with teacher cookies |
| Student REST (`student.namdtu.uz/rest/v1`) | **None (uses Bearer JWT)** | Cookies do not authenticate `/rest/v1/*` (`401`) |
| Third-party CDN (`.cloudflare.com`) | `__cf_*`, `_gcl_au`, `CF_VERIFIED_DEVICE_*`, `OptanonConsent` | Originates from `cdnjs.cloudflare.com` Font Awesome CSS — **IGNORE & NEVER SEND** to NamDTU |

- **Cookie freshness & handling:** Exported cookies may be stale while a live browser session is active. Never kill an active browser or force-unlock profile databases to dump cookies. Remind users to rotate/revoke shared sessions after sensitive tasks.

## 3. Reliable Session Liveness Verification

Never trust HTTP status `200` alone (login pages return `200`, wrong endpoints return `404`).

```text
GET https://hemis.namdtu.uz/dashboard/profile  (with NoRedirect)
```

| Response | Accurate Interpretation |
|---|---|
| `302 Location: .../dashboard/login` | Session unauthenticated / expired |
| `200` + profile identity markers (account name / ID) | Session **live & verified** for that exact person |
| `200` showing login form / no account markers | Unauthenticated (not live) |
| `404` | Wrong URL or missing route — does not prove session state |
| Timeout / DNS / TLS error | Connection failure — session status unknown |

- **Keepalive (`/keepalive/ping?t=<epoch_ms>`):** Teacher polls every `300000 ms` (5 min); student observed polling ~every 4 min. Returns `200 {"ok":true,...}` when live, `401 {"ok":false}` when expired. **Ping cannot resurrect a dead session** or override server absolute timeout / host sleep.

## 4. Dual CSRF Architecture (Yii2 + elFinder)

1. **Yii2 Form / AJAX CSRF:**
   - `<head>` contains `<meta name="csrf-param" content="_csrf-backend">` (or `_csrf-frontend`) and `<meta name="csrf-token" content="<88-char URL-safe b64 BREACH mask>">`.
   - ActiveForm renders `<input type="hidden" name="_csrf-backend" value="<88-char>">`.
   - `yii.js` injects `X-CSRF-Token` automatically into same-origin `$.ajax`, `$.post`, `$.pjax`, `DepDrop`, and `yiiActiveForm` calls.
   - **Automation rule:** Always fetch the target HTML first (`GET`), extract `<meta name="csrf-token">` or hidden `_csrf-backend` from *that* response matching your `_csrf-*` cookie, and send `_csrf-backend` in form body + `X-CSRF-Token` + `Origin` + `Referer`.
   - **Frontend CSRF quirks:** `scripts.js` `sortTable()` reads wrong field `input[name="_csrf"]` (works only via `$.ajaxPrefilter` header); `FeedbackWidget` (`/dashboard/feedback`) sends raw `XMLHttpRequest` without `X-CSRF-Token`.
2. **elFinder Connector Secondary CSRF:**
   - Initial `/files/connector?cmd=open&init=1` response can return a dedicated connector `csrf` token.
   - Write operations (`cmd=upload`, `mkdir`, `rm`, etc.) may require header **`X-elFinder-CSRF: <token>`** (plus `_csrf-backend`); sending only the Yii token can return **`403` with `csrfReload:true`**.

## 5. UI Framework, PJAX, `yiiGridView` Filters & Modals

- **Stack (`hemis.namdtu.uz`, v`1.9.90` / `Yadro 2.0.55`):** Bootstrap `v3.4.1` (patched for `CVE-2024-6485`), AdminLTE `v2.4.18`, jQuery `v3.7.1`, Select2, Krajee `DepDrop` (`v1.4.9`), iCheck, checkBo, Toastr, `theiaStickySidebar`, `floatThead`, `jquery.formatter`, `js.cookie`.
- **Teacher Portal REST Absence:** Teacher portal (`hemis.namdtu.uz`) has **no REST API** (`/rest/`, `/rest/v1`, `/api/` return `404` HTML); it is pure Yii2 MVC + PJAX + AJAX fragments + `elFinder 2.1069`.
- **Header Controls & Dashboard (`/`, `/dashboard/index`):**
  - Sidebar has 14 `treeview` categories / 31 submenus; header `Tezkor menyu` (`select[name="menu-search"]`) exposes 37 quick routes.
  - Header controls: locale links, `#survey-badge-w0` (`GET /teacher/survey-check`), `<a href="/system/cache" data-method="post" data-confirm="...">` (POST via `yii.js`), `FeedbackWidget` (`/dashboard/feedback`), and lazy `TicketEdu` launcher (`ticketedu.uz/widget.js`).
  - Dashboard renders `Mashg'ulotlar` (`Davomat jurnali`, `Mening dars jadvalim`, `Darslar ro'yxati`) and `Nazoratlar` (`Oraliq`, `Yakuniy`, `Boshqa`).
- **Anti-DevTools UI deterrent (`scripts.js`):** When `window.allowDevTools === false` (teachers), right-click outside inputs + `F12` / `Ctrl+Shift+I/J/C` / `Ctrl+U` are intercepted client-side.
- **Script hotkeys (`scripts.js`):** `Ctrl/Cmd+B` converts focused input Latin->Cyrillic; `Ctrl/Cmd+L` Cyrillic->Latin.
- **PJAX & GridView Summaries:** PJAX (`#admin-grid`, `#items-grid`, `#p0`) sets `maxCacheLength = 0`, `push: false`, `timeout: false`, `scrollTo: false`. Address bar URLs do not update on PJAX clicks; `data-pjax="0"` bypasses PJAX. Live GridView summaries render inside `<span class="summary">1-50 / jami 1 306 ta</span>` (with space-separated thousands).
- **`yiiGridView` GET Filter Nuance:** Filter toolbars render `<form id="w0" method="post">`, **but `#w0` is not submitted when filtering!** `yiiGridView('applyFilter')` serializes `#data-grid-filters input, #data-grid-filters select` into a hidden `<form method="get">` -> sends PJAX **`GET`** query parameters.
- **Modals (`ajax-modal-popup.js`):** `.showModalButton` (`value="..."`) loads fragment HTML via jQuery `.load()` (`X-Requested-With: XMLHttpRequest`) into `#modal` (`modal-big` = `1024px`, `modal-xxl` = `1400px`). Fetching modal routes like `/teacher/subject-info?curriculum=...&subject=...&semester=...` **without** `X-Requested-With: XMLHttpRequest` returns the full parent page instead of the modal fragment!
- **Boolean Switches (`toggle_table`):** Single switch `toggleAttributes(el)` triggers `GET /dashboard/toggle?attribute=active&model=<table>&id=<id>`; header `toggleAllRows(el)` posts `POST /dashboard/toggle?...&batch=1`.
- **Verified Teacher RBAC & Error Boundaries:** `403 Forbidden` on `/employee/employee`, `/curriculum/education-year`, `/curriculum/curriculum`, `/student/student`; `404` on `/system/index`, `/teacher/check-grade?id=<other_teacher_id>`, `/rest/*`, `/api/*`; `500` on bare `/teacher/check-overall-rating` (without `?id=`).

## 6. State-Mutating URL Blacklist (Never Trigger Without Intent)

- **Teacher Portal (`hemis.namdtu.uz`):**
  - `GET /message/compose` *(bare, without `?id=`)* -> creates blank draft (`302 ?id=<new_id>`)
  - `GET /message/my-messages?id=<item_id>&delete=1&folder=...` -> deletes message
  - `GET /message/my-messages?id=<msg_id>&folder=inbox` -> can mark message as read
  - `GET /dashboard/toggle?...` -> toggles `active` / `check_face`
  - `GET /teacher/subject-task-list?...&id=<task_id>&attribute=<attr>` -> `changeAttribute()` mutates task attribute on GET!
  - `GET /poll/mine?set=1&p=...&o=...` -> casts poll vote
  - `GET /file-resource/edit?id=<res>&item=<item>&cancel=1` -> removes item
  - `GET /file-resource/edit?id=<res>&delete=1` -> deletes resource
  - `GET /dashboard/profile?certificate=1` -> triggers UzBMB sync
  - `GET /dashboard/logout` -> terminates session
  - `GET /oz/...`, `/ru/...`, `/en/...` -> mutates session locale
  - `POST /system/cache` -> flushes cache (`data-method="post"`)
- **Student Portal (`student.namdtu.uz`):**
  - `GET /student/reference?get=1` -> creates new official reference document
  - `/student-data/rent-sync` -> triggers external rent sync
  - `/student-data/upload-plagiarism` -> consumes plagiarism-check quota
  - `GET /dashboard/logout` -> terminates student session
