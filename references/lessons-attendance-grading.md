# Schedules, Topic Determination, Attendance/NB, Grading & Retakes

## 1. `Mashg'ulotlar` & Timezone Rules (`hemis.namdtu.uz`)

- **Mandatory Timezone (`Asia/Tashkent`, `UTC+05:00`):** Convert host time and any UNIX `lesson_date` values explicitly to `UTC+05:00` before computing `"bugun"`, `"ertaga"`, or `"shu hafta"`.

| Submenu | Route | Mechanics & Key Cross-Checks |
|---|---|---|
| `Bosh sahifa` | `/` (`/dashboard/index`) | Summary tiles for `Mashg'ulotlar` (`Davomat jurnali`, `Mening dars jadvalim`, `Darslar ro'yxati`) and `Nazoratlar` (`Oraliq`, `Yakuniy`, `Boshqa`). |
| `Dars jadvali` | `/teacher/time-table` | FullCalendar v3 (`hiddenDays: [0]`, inline JSON events with `id`, `title`, `start`, `nonstandard`, `url -> /teacher/check-lesson?id=...`, `savedMonth`). **Parser caution:** Exact JS layout can vary by version — if one regex fails, try alternative JSON parsing before assuming empty! |
| `Darslar ro'yxati` | `/teacher/training-list` | Independent row registry (`ESubjectSchedule[_education_year]=<year>&_semester&...&per-page=50`). **Best practice:** Cross-check lesson IDs & counts against `/teacher/time-table`! `QR davomat` column links to `/teacher/qr-attendance?id=<lesson_id>` only when active/eligible; once closed/saved it renders a muted `<i class="fa fa-qrcode text-muted" style="opacity:0.3">`, and direct `GET /teacher/qr-attendance?id=<saved_id>` redirects `302 -> /teacher/training-list` with flash warning (*"Ushbu dars uchun davomat allaqachon saqlangan"*). |
| `Davomat jurnali` | `/teacher/attendance-journal` | List view -> drill via `?education_year=...&semester=...&group=...&subject=...&training_type=...&page=<N>`. Horizontal date pagination (`4` dates/page), 3-row header (`№`, `Talabaning F.I.Sh.`, `S`, `SZ`, `Dars sanasi`), clickable green badges `<a class="badge bg-green" href="/teacher/check-lesson?id=...">` **only for your own lessons** (other teachers' group lessons appear as plain text dates), plus `Mavzular` accordion table (`№`, `Mavzular`, `Dars sanasi`, `O'tildi`). **Same-date cross-lesson quirk:** When two lessons occur on the same date (e.g. yours + another teacher's subgroup/parallel pair), the journal grid can render `<span class="text text-red">2</span>` on both columns even when `SZ` total is `2` — never sum date cells blindly; rely on `S`/`SZ` summary columns + `/teacher/check-lesson?id=<id>`! |
| `Baholash jurnali` | `/teacher/rating-journal` | List view -> drill via `?education_year=...&semester=...&group=...&subject=...&training_type=...&page=<N>`. Date headers link to `/teacher/check-grade?id=<lesson_id>`. **RBAC quirk:** Links for lessons taught by *another* teacher return **`404`** on `/teacher/check-grade?id=...`, while your own lessons return **`200`**. |

## 2. Schedule Analysis Rules (Parallel Lessons & Delivery Forms)

- **Parallel / Overlapping Lessons Test:** Check exact match AND partial overlap:
  `A.start < B.end and B.start < A.end`
  - Separate **overlapping slot count** from **involved lesson record count** (e.g., 1 parallel time slot with 2 lessons = 1 slot, 2 lessons).
  - Concurrent rows are not automatically errors (subgroups split labs/practices legitimately).
- **Historical vs Future Split:** Never count future scheduled pairs inside *"o'tilgan darslar"* history. Keep `Kunduzgi`, `Kechki`, and `Sirtqi` schedules distinct, and never treat stale local schedule spreadsheets as overriding live HEMIS (weekly slots move).

## 3. Exact Topic Selection Algorithm ("Qaysi mavzuni o'tish kerak?")

> [!IMPORTANT]
> Never blindly trust the pre-selected topic inside `/teacher/check-lesson?id=<id>` — it often stays stuck on the previous session's topic!

Compute the true topic step-by-step:
1. Identify the target lesson record (`date`, `time`, `subject`, `group`, `training_type`, `lesson_id`).
2. List and chronologically sort **all prior lessons** matching the exact same `subject + group + training_type`.
3. Account for cancelled, rescheduled, or multi-pair single topics to determine the true lesson sequence ordinal (`1..N`).
4. Fetch `/teacher/calendar-plan` for `(curriculum, semester, subject, group, training_type)` and match that ordinal.
5. Verify against already marked (`O'tildi = +`) topics in the calendar plan & journal.

## 4. Lesson Attendance (`/teacher/check-lesson?id=<id>`) & NB Counting

- **Topic & load fields:** `ESubjectSchedule[_subject_topic]` (`#esubjectschedule-_subject_topic`) and `ESubjectSchedule[load]` (`#load`, `disabled` once locked). Client-side `initHourType()` reads `parseInt($('#load').val())` and sets **both `min` and `max`** on `$('[id*="_s"]')` equal to `load` (e.g. `min="2" max="2"`).
- **Absent controls (`input[name^="sz["]`, `id="<sid>_s"`):** Marked absences show numeric `value` (academic hours, `min=max=load`) and `disabled` attributes when saved/locked. **Do not** infer attendance from element presence alone; inspect input `value` + `disabled` state and cross-check `attendance-journal` (`S` / `SZ` totals; empty `Mavzular` row in journal indicates attendance was committed without `_subject_topic`).
- **View bug:** Form renders literal `options='{"data-pjax":1}'` instead of `data-pjax="1"` -> submit performs full-page `POST`.
- **NB Accounting Standards:** Always separate:
  1. Missed sessions/records count vs academic hours (do **not** assume every record = `2` hours without inspecting duration!),
  2. Unexcused (`sababsiz`, REST `absent_off`) vs excused (`sababli`, REST `absent_on`),
  3. Current semester vs cumulative historical semesters,
  4. Per-subject breakdown (verify sum across subjects == grand total!).

## 5. Lesson Grading (`/teacher/check-grade?id=<id>`)

- **Form `#w0` (`POST /teacher/check-grade?id=<id>`):** Requires `ESubjectSchedule[_subject_topic]` (`#esubjectschedule-_subject_topic`) alongside per-student `grade[<student_id>]` (`id="<student_id>_s"`, `type="number" min="1" max="100" step="1"`).
- **Absent student lock:** Students marked absent (`NB` / `sz[...]`) automatically render `grade[<student_id>]` as `disabled` on the server so absent students cannot be given a lesson grade.
- **Ownership boundary:** Accessing `/teacher/check-grade?id=<id>` for another teacher's lesson (even if linked in `rating-journal`) returns `404`.

## 6. `Yakka darslar`, `Qayta o'qish` & `O'zlashtirish`

- **`Yakka darslar`:**
  - `/individual-training/subject-student`
  - `/individual-training/subject-schedule` (inline PJAX `#w1`, 3-stage `DepDrop`: `_curriculum + _education_year + _semester + _subject` -> `POST /ajax/get-curriculum-year-semester-subject` -> `#_employee` -> `POST /ajax/get-curriculum-year-semester-subject-employee` -> `#_training_type` -> `POST /ajax/get-curriculum-year-semester-subject-employee-training` -> `#_student`; inspect live HTML IDs for upstream DepDrop mismatch before scripting!)
  - `/individual-training/subject-attendance`
- **`Qayta o'qish`:**
  - `/retraining/retraining`
  - `/retraining/time-table`
  - `/retraining/teacher-subject`
  - `/retraining/midterm-exam-table`, `/retraining/final-exam-table`, `/retraining/other-exam-table`
- **`O'zlashtirish`:**
  - `/performance/ptt-fill` (individual `PTT` retake/debt sheets — visible sheet does not guarantee active edit window/permission!)
  - `/performance/request` (grade modification requests for locked sheets)
