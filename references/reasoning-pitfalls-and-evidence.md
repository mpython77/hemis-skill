# Reasoning Pitfalls, Evidence Levels & Audit Counting

## 1. Evidence levels (label every claim)

- **Observed** — route/field/code seen live in a page or response.
- **Verified-by-read-back** — re-read from the server and hash/metadata checked.
- **User-context** — reported by the user/project, not re-tested.
- **Historical** — snapshot from an earlier check; state may have changed. Anything like "N cards exist", "session is live",
  "PAN IP is 10.x" is temporary; reusable knowledge is routes, parameters, errors and verification methods.
- **Unknown** — never fill with guesses (full admin permission matrix, DB schema, exact session idle/absolute TTL, university
  attendance rules, future page changes, …).

Ask for / run a fresh live check before any operational decision. Offline self-tests prove the code, **not** that HEMIS is reachable.

## 2. Wrong conclusion → correct reading

| You see | Wrong conclusion | Do instead |
|---|---|---|
| HTTP 200 | session alive | check URL, login form, identity marker |
| 302 → login | server down | session simply unauthenticated |
| timeout / DNS / TLS error | cookie expired | connection problem; session unknown |
| personal storage empty | no subject materials | storage ≠ published catalog |
| `Files=1` | one physical file | one *item* may hold DOCX+ZIP / PDF+PPTX |
| download `200 text/html` | file OK | HTML = error/form page |
| one file `404` | whole card empty | check the other file and any replacement card |
| many resources | every topic covered | match topics↔materials, detect duplicates/merged files |
| 0 tasks | 0 resources | separate modules |
| 100% topics/hours in plan | files uploaded | plan completeness ≠ file existence |
| `per-page=100` | 100 rows | server caps at 50 — read summary + paginate |
| same subject name | same catalog | curriculum + semester decide |
| two groups | upload twice | they may share one `curriculum_subject` |
| upload "success" | published correctly | verify card, active, catalog, real download |
| "1-on" exam | final exam | first midterm control |
| exam message text | exams started | compare first control date with live today |
| message says "tomorrow" | tomorrow from today | relative to the message date |
| message opened in an open cabinet | obligation of the cabinet owner | check the recipient inside the message |
| role link visible | edit permission | view ≠ edit; respect 403/disabled controls |

## 3. Counting rules for audits (avoid inflated totals)

- Deduplicate by ID **after** collecting all pages; compare with the server summary.
- Deduplicate repeated item links inside one row (different link texts can hit the same item).
- Remove duplicate topic titles within one catalog + training type before computing coverage.
- "No matching card" ≠ "material doesn't exist" (short/generic titles, merged files, different naming) — needs semantic re-check.
- `HEAD 200` proves the path exists, **not** that the download ZIP or the document content is intact.
- Issue categories overlap (wrong-type ∩ broken-file ∩ missing-file): report the **union of IDs**, never the sum of category counts.
- Never merge counts from different audit scopes (e.g. a 7-subject audit and a 27-catalog audit) into one figure.
- Distinguish user, resource author, authenticated account and file owner; do not rename another account's observations as the user's.

## 4. Acceptance & permission discipline

- Reading authorizes reading only. "Upload X" authorizes exactly that file/catalog — not other subjects or years.
- A task is done only when every agreed acceptance item is confirmed by read-back. After an interruption, list: done · verified ·
  unverified · remaining.
- Source of truth for exports: a "live" request needs a fresh fetch; "export what you checked" must reuse the exact checked records
  and timestamp (even after midnight). Verify exports by reopening them and re-comparing with the source rows.
- Never run old terminal commands containing secrets; scratch scripts are disposable — re-read parameters before reuse.
