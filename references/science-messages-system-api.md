# Ilmiy faoliyat, Xabarlar, E-hujjatlar, Profil & Public Statistika APIs

## 1. `E-hujjatlar` & `Xodimlar`
- `Hujjatni imzolash`: `/document/sign-documents` (`#items-grid` PJAX GridView). Viewing a document differs from signing — electronic signature (`E-IMZO`) is a legally binding action requiring explicit user action/confirmation.
- `Yuklamani shakllantirish`: `/employee/teacher-load-formation` (HEMIS personal load table). **Note:** External Excel load files (`<Teacher>.xlsx`, etc.) and live HEMIS load records are independent sources — compare rather than assuming equality.

## 2. `Ilmiy faoliyat`

### Publications & IP (`#admin-grid` PJAX, Filter `EPublicationAuthorMeta`: `_education_year`, `search`)
| Submenu | List Route | Create New (`+ Kiritish`) | Claim Existing (`+ Tanlash`) | Export |
|---|---|---|---|---|
| `Uslubiy nashrlar` | `/science/publication-methodical` *(col typo: `Uslubiy nash nomi`)* | `/science/publication-methodical-edit` | `/science/publication-methodical-list` | `?export=1` |
| `Ilmiy nashrlar` | `/science/publication-scientifical` | `/science/publication-scientifical-edit` | `/science/publication-scientifical-list` | `?export=1` |
| `Intelektual mulklar` | `/science/publication-property` | `/science/publication-property-edit` | `/science/publication-property-list` | `?export=1` |

- Co-authors use `+ Tanlash` (`*-list`) via `So'rov` instead of duplicating works.
- **Verification note:** Entering a publication in HEMIS `/science/*` does **not** prove independent indexing in external databases (Scopus, etc.); `Tekshirish` reflects internal science-department verification.

### `Ilmiy faollik` (`/science/scientific-activity`)
- Split `#items-grid` PJAX: left filterable/sortable GridView (`#_education_year_search`, `#_scientific_platform_search`: `'10'` Boshqa, `'11'` Scopus, `'12'` Research Gate, `'13'` Google scholar); right `#w1` PJAX form requiring all 6 `EScientificPlatformProfile` fields (`_education_year`, `_scientific_platform`, `profile_link` max 512, integer `h_index`, `publication_work_count`, `citation_count`).

---

## 3. `Xabarlar` (`/message/*`)

- **List & Folders (`/message/my-messages`):** `?folder={inbox,outbox,draft,trash}`, filter `AdminMessageItem[search]`. Inline `setInterval` reloads `#my-messages-grid` every `3000ms`.
- **Counting rule:** Header notification bell often echoes `/message/my-messages` items — **never add header notifications + inbox rows together**!
- **Message Detail & Side-Effects:**
  - Read via `/message/my-messages?id=<message_id>&folder=inbox` -> verify **sender, actual recipient, full send datetime, title, and body**.
  - Opening a message can mark it read!
  - Even inside an authenticated portal, a broadcast/forwarded message can list a different recipient — verify target recipient before assuming personal obligation.
  - Interpret relative words (`"ertaga"`, `"bugun"`) inside message text relative to the **message's send date**, not today's date!
- **DANGER — State-Changing GET Traps:**
  - Bare `GET /message/compose` (no `?id=`) creates an empty draft (`302 -> ?id=<new_id>`). Never poll bare `/message/compose`.
  - `GET /message/my-messages?id=<item_id>&delete=1&folder=<folder>` deletes on GET.
- **Composer (`POST /message/compose?id=<id>`, `#message_form`):**
  - `AdminMessage[_recipients]` (Selectize -> `GET ?id=<id>&contacts=json&query=<term>`) + modals `+ Xodim` (`&type=admin`) / `+ Talaba` (`&type=student`), `title` (`max=256`), `message` (TinyMCE `/dashboard/file-upload?type=content-image&fileparam=file`), `action=send|draft`, 3s debounced autosave `POST ?id=<id>&autosave=1`.

---

## 4. `Hisobotlar` (Polls & Surveys) & Feedback

*(Note: For `O'qituvchi`, `Hisobotlar` has zero Excel/admin reports — those belong to Admin/Dean/Kafedra roles.)*
- `Mening fikrim` (`/poll/mine`): `setAnswer` triggers state-changing `GET /poll/mine?set=1&p=<p>&o=<o>`.
- `Global so'rovnoma` (`/teacher/survey`): polls `GET /teacher/survey-check` (`30 min` `localStorage` cache).
- `Tizimni baholang` (`/dashboard/feedback`): `GET`/`POST` via raw XHR without `X-CSRF-Token`.

---

## 5. `Tizim`, Profil & Auth Entrypoints

- Parent `/system/index` returns **`404 Not Found`** for `O'qituvchi`.
- `Fayl menejer` (`/file-resource/manager`): elFinder `2.1066` (`/files/connector`).
- **`Mening profilim` (`/dashboard/profile`):**
  - Reliable session liveness target (no-redirect `GET`).
  - Editable: `Admin[language]` (9 locales vs navbar `/oz/`, `/ru/`, `/en/`), `Admin[email]`, `Admin[telephone]`, `Admin[image]` (`yiiUpload`, 5 MB max), password (`/^(?=.*\d)(?=.*[A-Za-z]).{12,}$/`).
  - Read panels: `Xodim`, `Lavozim` (`?reference=<id>` PDF), `Til sertifikatlari` (`?certificate=1` `Sync UzBMB`). No role switcher, 2FA, or login history UI on teacher role.
- **Public Auth & Widgets:** `/dashboard/login` (`/ajax/captcha?refresh=1`, `/auth/eimzo` -> `eduimzo.uz`, OneID `/dashboard/auth?authclient=oneid`), TicketEdu widget (`ticketedu.uz/widget.js`). Note CSP `frame-ancestors` intersection shrinks framing to `'self'`.

---

## 6. `Statistika` & Unauthenticated Public University REST APIs

- `/dashboard/open-data` embeds `<iframe src="https://hemis.vercel.app/?api=https://student.namdtu.uz/rest/">`.
- **Direct Public JSON Endpoints (no token/cookies needed):**
  1. `GET https://student.namdtu.uz/rest/v1/public/stat-structure` (`groups`, `auditoriums`, `specialities`, `departments`)
  2. `GET https://student.namdtu.uz/rest/v1/public/stat-student` (`education_type`, `age`, `payment`, `region`, `citizenship`, `accommodation`, `education_form`, `level`)
  3. `GET https://student.namdtu.uz/rest/v1/public/stat-employee` (`position`, `gender`, `citizenship`, `academic_degree`, `academic_rank`, `direction`, `academic`, `age`, `employment_form`)

