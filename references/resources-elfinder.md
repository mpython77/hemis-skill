# Fan resurslari (`elFinder`), Audits, Recovery & Verification

## 1. Routes & Structural Hierarchy

```text
Curriculum (_curriculum)
  -> Curriculum Subject (cs / _curriculum_subject)   [NOTE: != raw subject ID!]
     -> Training Type (_training_type)
        -> Resource Card (ESubjectFileResource, id)
           -> Resource Item (ESubjectFileResourceItem, item)
              -> Physical File(s) in files[] OR external url
```

| Route | Purpose | Notes |
|---|---|---|
| `/file-resource/index` | Global/filtered resource list | Newest `id DESC` first; sticky session filters! |
| `/file-resource/subject?cs=<cs_id>` | Subject-specific resource catalog | Note `/file-resource/subject` alone renders 1-col `Id` quirk |
| `/file-resource/edit` | Create new card + item (`#w0` + nested `#w1`) | `POST` -> `302 /file-resource/edit?id=<new_id>` |
| `/file-resource/edit?id=<res>` | Inspect/edit card | Reads full title, subject, training type, items |
| `/file-resource/edit?id=<res>&item=<item>` | Inspect specific item | Reads comment, external URL, attached `files` metadata |
| `/file-resource/edit?id=<res>&download=<res>[&index=<item>]` | Authenticated download | Multi-file items return ZIP archive; detect `200 text/html` failures |
| `/file-resource/edit?id=<res>&item=<item>&cancel=1` | **Delete item (write GET!)** | Never run casually; leaves physical elFinder file intact |
| `/file-resource/edit?id=<res>&delete=1` | **Delete parent resource (write GET!)** | Never run casually; leaves physical elFinder file intact |
| `/file-resource/edit?subject=1` (`POST`) | `DepDrop` allowed subjects query | Must pass `depdrop_all_params[_curriculum]` or returns HTML! |
| `/file-resource/edit?title=1&query=<q>&cs=<cs_id>` (`GET`) | Selectize topic title autocomplete | Matches syllabus phrasing (`max 156` chars) |
| `/files/connector` (`GET`/`POST`) | `elFinder 2.1069` storage backend | Personal `/file-resource/manager` & attach dialog |

## 2. Critical Pitfalls Before Auditing or Uploading

1. **Clear ALL 6 Filter Fields on `/file-resource/index`:**
   Previously selected filters stick across queries:
   `ESubjectFileResource[_curriculum]`, `ESubjectFileResource[_curriculum_subject]`, `ESubjectFileResource[_training_type]`, `ESubjectFileResource[_employee]`, `ESubjectFileResource[_department]`, `ESubjectFileResource[language]`.
   Leaving `_training_type=13` (`Amaliy`) active while switching subjects hides every `Laboratoriya` (`12`) card and falsely reports `0` resources!
2. **`per-page=100` Server Cap (`50` max):** Server caps `per-page` at `50`. Always read GridView summary & pagination links, fetch every page, deduplicate by card `id`, and confirm `len(collected_ids) == server_total`.
3. **Shared `curriculum_subject` vs Multiple Groups:** Groups using the same `curriculum` + `curriculum_subject` (e.g., `13-KI-25` and `13-KI-b-25` under one `cs`) may share the same resource catalog — do not upload duplicate files across groups sharing `cs`. Conversely, identical subject names on *different* curricula are separate catalogs!
4. **Author & Edit Permissions (`View != Edit`):** `/file-resource/index` shows resources from other university staff (`1,002` employees). Separate what already exists in the catalog from what you authored; never bypass disabled/missing edit controls.
5. **Personal Storage (`/file-resource/manager`) != Published Catalog (`/file-resource/subject`):**
   - Empty elFinder storage does **not** mean subject resources are empty.
   - Uploading a file to `/file-resource/manager` alone does **not** publish it to students — it must be attached to an `ESubjectFileResource` card and active (`Faol`).
6. **`Files=1` != Physical File Count:** The table column can count **items**, while 1 item holds 2+ files (`DOCX + ZIP`). Deduplicate repeating item links inside table rows before counting.

## 3. DepDrop & Exact Create/Edit Form Fields (`/file-resource/edit`)

**Step 1 — Check write authorization via DepDrop:**
`POST /file-resource/edit?subject=1` with `X-Requested-With: XMLHttpRequest`, `X-CSRF-Token`, `Origin`, `Referer`, and body:
- `depdrop_parents[0]=<curriculum_id>`
- `depdrop_all_params[_curriculum]=<curriculum_id>`
- `_csrf-backend=<token>`
*(Missing `depdrop_all_params` causes server to return HTML instead of JSON!)* Confirm target `cs` appears in `output`.

**Step 2 — Nested `#w0` / `#w1` Form Fields (submit `application/x-www-form-urlencoded`):**
- `_csrf-backend`: 88-char token from page fetch
- `ESubjectFileResource[_curriculum]`: integer curriculum ID
- `ESubjectFileResource[_curriculum_subject]`: integer curriculum-subject ID (DOM element `#_subject`, matches URL `cs=<id>` — **never** post as `[_subject]`!)
- `ESubjectFileResource[_training_type]`: `'11'` `Ma’ruza`, `'12'` `Laboratoriya`, `'13'` `Amaliy`, `'14'` `Seminar`, `'15'` `Trening`, `'16'` `Kurs ishi`, `'17'` `Mustaqil ta'lim`, `'18'` `Klinik mashg'ulot`, `'19'` `Yakka dars`, `'20'` `Boshqa`
- `ESubjectFileResource[title]`: max **`156`** chars (Selectize autocomplete uses `title=1&cs=<cs_id>`)
- `ESubjectFileResource[language]`: Selectize codes `'11'` `O‘zbek`, `'12'` `Rus`, etc. (`11..22`)
- `ESubjectFileResourceItem[_resource_type]`: `'10'` `Boshqa`, `'11'` `Video`, `'12'` `Audio`, `'13'` `Prezentasiya`, `'14'` `O'quv material`
- `ESubjectFileResourceItem[comment]`: max `2000` chars
- `ESubjectFileResourceItem[url]`: `http`/`https` external link, max `2000` chars *(writing a file path into `url` does NOT count as proper file attachment)*

## 4. `elFinder` Connector & Exact File Attachment Payload

- Open tree: `GET /files/connector?id=esubjectfileresourceitem-files&multiple=1&cmd=open&init=1` -> read folder `hash`, `options.url`, upload limits, and **connector `csrf`** (if present).
- **Hard constraints:**
  - `options.uploadMaxSize`: **`5242880` bytes (`5 MiB`)** per file (`cmd=upload` connector limit, even though top-level `uplMaxSize` reports `"500M"` and `uplMaxFile: "20"` — Telegram-sized larger files fail!).
  - Allowlist: `.txt, .doc, .docx, .wps, .zip, .rar, .gz, .pdf, .jpg, .jpeg, .png, .tiff, .ppt, .pptx, .xls, .xlsx, .psd, .cdr, .mp3, .mp4, .mpeg`. Auxiliary `.html`/Maple files can be packaged inside `.zip`.
  - **CRITICAL `+` Filename Bug:** Never include `+` in uploaded filenames! Direct `/static/files/...` may succeed, but HEMIS download routes decode `+` as space (` `) and fail (`404`/broken download).
- **Upload (`cmd=upload`):** Multipart `POST /files/connector` with `cmd=upload`, `target=<dir_hash>`, `upload[]=@<file>`, `_csrf-backend`, `X-CSRF-Token`, and **`X-elFinder-CSRF: <connector_csrf>`** (without `X-elFinder-CSRF`, writes can fail with `403` + `csrfReload:true`). Respect `uploadMaxConn: 3`.
- **Resolve exact attachment fields (`added.url` warning):** Do not trust `added.url` blindly; inspect `options.url` / `cmd=url&target=<file_hash>` and verify bytes via `cmd=file&target=<hash>&download=1`. Using `userId` from `/file-resource/edit` JS options:
  ```python
  user_str = str(userId)
  base_url = url_val[:url_val.index(user_str)]            # "https://hemis.namdtu.uz/static/files/"
  path = url_val[url_val.rindex(base_url) + len(base_url):]  # "<userId>/<url_encoded_rel_path>"
  ```
  Post per attached file `idx = 0, 1, ...`:
  `ESubjectFileResourceItem[files][{idx}][path]`, `[name]`, `[size]`, `[type]`, `[order]`, `[base_url]`.

## 5. Safe Uploads, Folder Moves & 6-Level Verification

### Upload Hygiene & Deduplication
- After timeout/ambiguous POST response, search `/file-resource/subject?cs=<id>` and `elFinder` **before** retrying so duplicates aren't created (deterministic hash prefixes help).
- Upload topics sequentially oldest-to-newest (`01 -> N`, `>=0.5s` delay) so default `id DESC` ordering stays clean; keep `1 topic = 1 resource`.
- Moving/organizing folders in elFinder breaks resource cards unless updated carefully. **Safe 6-step sequence:** `mkdir` -> copy files -> verify copy bytes -> update item `files` metadata on `/file-resource/edit` -> verify all downloads -> **only then** delete old copies.
- When handling broken old cards: separate old broken `id` vs new working replacement `id` in audits (don't overwrite locked cards without rights). Note multiple topics combined into one file (`3–4-lab`) or redundant obsolete+new entries on the same topic.

### 6-Level Resource Completeness & Integrity Verification
Upload success (`HTTP 200/302`) != published integrity. Verify at all relevant levels:
1. **List completeness:** Expected cards exist under `/file-resource/subject?cs=<cs_id>`.
2. **Topic match:** Card titles & training type match syllabus topics (check `<= 156` char title truncation & no combined gaps).
3. **File existence (`HEAD`):** Every URL inside `ESubjectFileResourceItem[files]` returns HTTP `200` (not `404`; watch for partial failure where 1 of 2 files in an item is `404`!).
4. **Download integrity (`GET`):** `GET /file-resource/edit?id=<res>&download=<res>[&index=<item>]` returns binary/ZIP (not `200 text/html`!), and extracted/downloaded SHA-256 matches original files byte-for-byte.
5. **Content completeness:** File inside contains expected theory, instructions, tasks, and variants.
6. **Student visibility:** Card is `Faol` (`checked`), and when requested/testable, visible in student account `/education/resources?subject=<id>`.


## 6. elFinder Operations, Folder Hygiene & Read-Back (verified in production)

### Folder first, upload second
Always inspect the live personal storage before uploading: reuse the subject folder if it exists, otherwise `mkdir`; then the
group/curriculum folder; then the topic folder. Upload into that exact folder — never leave files in the root.

```text
Subject/
  Group-or-Curriculum/
    Maruza/01 - Topic/<hash-prefix>_maruza_01.pdf
    Amaliy/01 - Topic/<hash-prefix>_amaliy_01.pdf
    Oquv dasturi/<hash-prefix>_oquv_dasturi.pdf
```
One verified asset that serves several catalogs is **attached**, not re-uploaded.

### Connector commands (all state-changing → explicit task + permission only)
| Intent | Request (Dual-CSRF, see `auth-http-pjax.md §4`) | Client helper |
|---|---|---|
| Read folder | `cmd=open&target=<hash>` | `elfinder_open(target)` |
| Create folder | `cmd=mkdir&target=<parent_hash>&name=<folder>` | `elfinder_mkdir(parent, name)` |
| Copy (never move first) | `cmd=paste&src=<hash>&dst=<hash>&targets[]=<file_hash>&cut=0` | `elfinder_copy(src, dst, hashes)` |
| Delete exact file | `cmd=rm&targets[]=<hash>` | intentionally **not** wrapped — do it only after byte-verified copy + explicit user approval |

After every write, `open` the folder again and check `name`, `mime=directory` / file `phash`. An elFinder hash identifies an object in
personal storage — it is **not** a subject ID, SHA-256, or cookie.

### Filenames, URLs and encoding
- Encode static paths exactly once: `unquote` then `quote(path, safe="/")` (`HemisOneIDClient.normalize_resource_path`). Re-quoting an
  already-encoded `%20` becomes `%2520` → false `404`.
- Spaces, Uzbek apostrophes (`'`, `‘`) and Cyrillic must be encoded; real names (even with a trailing apostrophe) come from the
  directory listing — never "fix" a Windows path by guessing.
- Never blind multi-decode names containing a literal `%`. `+` in names remains forbidden (download route turns it into a space).

### Verification details
- **Active flag** is not in the edit dialog; read the list page: `input[type=checkbox][data-id=<resource_id>]` → `checked`. Never toggle to test.
- **DOCX/PPTX are ZIP containers.** Compare the SHA-256 of the whole response with the expected single file first; only if it differs
  *and* it is a real multi-file archive, open it and hash each member.
- **Upload timeouts:** connect-timeout ≠ write-timeout. A profile `GET` can succeed while a large multipart `POST` raises
  `The write operation timed out`. Raise the write timeout (client: `upload_timeout`), then **search by deterministic filename** before
  any retry — the client never auto-resends a non-idempotent POST.
- **Exit code 0 / "uploaded" ≠ published.** Acceptance = exact record read-back + metadata + `active` + every attachment's bytes/SHA-256.
- **Role drift → 403:** see `network-and-recovery.md §4`.

## 7. Code Dictionaries — the same number means different things

| Dictionary | Examples |
|---|---|
| Training type (`_training_type`) | `11` Ma'ruza · `12` Laboratoriya · `13` Amaliy · `14` **Seminar** · `15` Trening · `16` Kurs ishi · `17` Mustaqil ta'lim · `18` Klinik · `19` Yakka dars · `20` Boshqa |
| Resource item type (`_resource_type`) | `10` Boshqa · `11` Video · `12` Audio · `13` Prezentasiya · `14` **O'quv material** |
| Language | `11` O'zbek, `12` Rus, … (`11..22`) |

Training-type `14` ≠ resource-type `14` ≠ language `11`. Never mix dictionaries; re-read `<option>` values from the live form
before writing. A syllabus ("O'quv dasturi") is published as training type `20 Boshqa` + item type `14 O'quv material`.

## 8. Publish Registry (reproducible, resumable, secret-free)

Persist one row per published topic so a dropped connection can resume from a checkpoint:

```text
curriculum_subject, curriculum, training_type, topic_ordinal, title,
original_file_hash, prepared_file_hash, remote_file_name, remote_file_path, remote_folder_hash,
resource_id, item_id, active, verified_download_hashes, verification_status
```
Never store cookies, passwords, OneID tokens or the connector CSRF in a registry; a file hash is not an auth secret. Keep original
sources untouched — report any corrected *publication copy* (e.g. fixed subject label in a PDF) explicitly.
