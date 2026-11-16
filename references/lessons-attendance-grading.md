# Schedules, Topic Determination, Attendance/NB, Grading & Retakes

## 1. `Mashg'ulotlar` & Timezone Rules (`hemis.namdtu.uz`)

- **Mandatory Timezone (`Asia/Tashkent`, `UTC+05:00`):** Convert host time and any UNIX `lesson_date` values explicitly to `UTC+05:00` before computing `"bugun"`, `"ertaga"`, or `"shu hafta"`.

| Submenu | Route | Mechanics & Key Cross-Checks |
|---|---|---|
| `Dars jadvali` | `/teacher/time-table` | FullCalendar v3 (`hiddenDays: [0]`, inline JSON events with `id`, `title`, `start`, `nonstandard`, `url -> /teacher/check-lesson?id=...`, `savedMonth`). **Parser caution:** Exact JS layout can vary by version — if one regex fails, try alternative JSON parsing before assuming empty! |
| `Darslar ro'yxati` | `/teacher/training-list` | Independent row registry (`ESubjectSchedule[_education_year]=<year>&per-page=50`). **Best practice:** Cross-check lesson IDs & counts against `/teacher/time-table`! Also check `QR davomat` column. |
| `Davomat jurnali` | `/teacher/attendance-journal` | Aggregated attendance ledger per subject/group/lesson. |
| `Reyting jurnali` | `/teacher/rating-journal` | Continuous control (`JN`) & lesson grade ledger (`grade` grid). |

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

- **Topic linkage:** `ESubjectSchedule[_subject_topic]`.
- **Absent controls (`input[name^="sz["]`):** Marked absences show numeric `value` (academic hours, `min=max=load`) and sometimes `disabled` attributes. **Do not** infer attendance from element presence alone; inspect both input state/attributes and saved attendance status!
- **View bug:** Form renders literal `options='{"data-pjax":1}'` instead of `data-pjax="1"` -> submit performs full-page `POST`.
- **NB Accounting Standards:** Always separate:
  1. Missed sessions/records count vs academic hours (do **not** assume every record = `2` hours without inspecting duration!),
  2. Unexcused (`sababsiz`, REST `absent_off`) vs excused (`sababli`, REST `absent_on`),
  3. Current semester vs cumulative historical semesters,
  4. Per-subject breakdown (verify sum across subjects == grand total!).

## 5. Lesson Grading (`/teacher/check-grade?id=<id>`)

- Per-student lesson rating field: `grade[<student_id>]` (valid `1..100`), submitted via `POST` to `/teacher/check-grade?id=<id>`.

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
