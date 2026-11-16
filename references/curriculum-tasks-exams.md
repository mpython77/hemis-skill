# Curriculum Topics, Tasks, Plans, Exams & Vedomost (`hemis.namdtu.uz`)

## 1. `Fanlar bazasi` Routes

| Feature | Route & Observed Params | Key Mechanics & Notes |
|---|---|---|
| `Fan mavzulari` | `/teacher/subject-topics`<br>`/teacher/subject-topic-info?id=<curriculum_subject_id>` | Syllabus topic bank per `curriculum_subject` with `[hours/hours]` planned vs assigned. **Note:** Topic/hour completion = `100%` does **not** confirm resource files exist! |
| `Fan topshiriqlari` | `/teacher/subject-tasks`<br>`/teacher/subject-task-list?...` | Assignment/quiz bank grouped by `(curriculum, semester, subject, training_type, education_lang)` (`yiiUploadKit` -> `/dashboard/file-upload`). `POST /ajax/get-exam-type-data` triggers on `_training_type`/`_task_type` combos. **Topshiriq = `0` != Resurs = `0`** (independent modules; task content/deadlines/submissions are separate checks). |
| `Kurs topshiriqlari` | `/teacher/subject-task-other` | Uses `ESubjectExamSchedule` filter (`_curriculum` & `_group` sortable; `Fanlar` plain header). |
| `Kalendar reja` | `/teacher/calendar-plan` (`&download=1`) | Auto-pairs `ECurriculumSubjectTopic` rows per `(subject, group, training_type)` with logged `check-lesson` sessions. Crucial ground truth for topic order. |
| `Fan ma'lumotlari` | `/teacher/subject-info`<br>`/teacher/subject-info?curriculum=<id>&subject=<id>&semester=<id>` | Modal passport: block/type, credit/hours breakdown (`Ma'ruza`/`Lab`/`Amaliy`/`Mustaqil`), max scores (`1-on`/`2-on`/`YN`). **Note:** `subject` ID here is distinct from `curriculum_subject` (`cs`)! |

**Architecture Note (Ownership vs Delivery):** Separate scopes divide syllabus topic ownership vs taught schedule rows (for example, one teacher account may have `6` rows in `subject-topics`, `10` in `subject-info`, `13` in `subject-tasks`, and `20` in `calendar-plan`). Always inspect each subject's exact `curriculum` + `cs` mapping rather than assuming two groups share topics or edit rights.

---

## 2. `O'quv jarayoni` (`/curriculum/education-year` & `/exam/*`)

- `/curriculum/education-year`: returns **`403 Forbidden`** for `O'qituvchi` (Dean/Admin only).
- `Imtihonlar ro'yxati` (`/exam/index`, `/exam/edit`): online tests (`+ Imtihon yaratish`, `Savollar soni`, `Vaqti (daqiqa)`, `FaceID`, `Faol`) live here — **not** under `Nazoratlar`. Check write rights per active role; `check_face` / `active` toggles call `/dashboard/toggle`. Connects to `Nazoratlar` via `To'ldirish` (`?choose_exam=1`) inside `/teacher/check-overall-rating`.

---

## 3. `Nazoratlar` (Exam Schedules & Vedomost Grading)

### 3.1 Schedule Submenus (Read-Only for `O'qituvchi`)
- `Oraliq nazorat`: `/teacher/midterm-exam-table` (`1-on` = first midterm, **never** label as final exam)
- `Yakuniy nazorat`: `/teacher/final-exam-table`
- `Boshqa nazoratlar`: `/teacher/other-exam-table`
- **Filter Model (`ESubjectExamSchedule` via PJAX `#admin-grid` / `yiiGridView` GET):** `_education_year`, `_semester`, `_group`, `_subject`, `final_exam_type` (`'11'` `1-qaydnoma`, `'12'` `2-qaydnoma`).
- *UI quirks:* `YN turini tanlang` placeholder appears even on midterm/other; `midterm-exam-table` omits `Bloklangan` column (9 cols vs 10) and leaves `Fanlar` non-sortable.
- **"Imtihonlar boshlandi?" rule:** Only state exams have started if the actual earliest control date is before live `UTC+05:00` today (never infer from old announcements; cross-check student web vs REST API if needed).
- **NB-based exam exclusion warning:** Never conclude *"imtihonga qo'yilmaydi"* from raw NB count/percent alone without checking subject load, institutional rules, and live HEMIS `ruxsat` status.

### 3.2 Vedomost (`Umumiy reyting`): `/teacher/check-overall-rating?id=<id>`
- **Form:** `<form id="assessment-form" method="post">` (posts back to same URL).
- **Hidden Config Fields:** `_csrf-backend`, `minimum_limit`, `minimum_limit_current`, `limit`, `final` (`#max_ball`), `marking_system` (`"13"` Credit, `"12"` 5-point, `"11"` legacy), `min_border`, `min_border_current`, `final_exam_type` (`"11"` `1-QAYDNOMA`, `"12"` `2-QAYDNOMA`).
- **Row Inputs (`<sid>`):** `student_id[<sid>][10]` (`#limit_<sid>`), `[11]` (`#current_<sid>n`, `JN`), `[12]` (`#midterm_<sid>`, `ON`), `[13]` (`#final_<sid>`, editable `YN`), `[14]` (`Umumiy`), `absents[<sid>]` (`#absent_<sid>`).
- **Action Buttons:**
  - `Ro'yxat`: `GET /teacher/check-overall-rating?id=<id>&access=1` (`ruxsatnoma`)
  - `Chop etish`: `GET /teacher/download-rating?code=<UUID>&performance=1`
  - `To'ldirish`: modal `.showModalButton` (`value="...&choose_exam=1"`) importing online test scores from `/exam/index`
  - `Saqlash`: `button#submit[name="btn"]` — **ONE-SHOT IRREVERSIBLE LOCK (`Bloklangan: Ha`)**

> [!CAUTION]
> **Irreversible Vedomost Lock:** Submitting `#assessment-form` locks editing (`Bloklangan: Ha`). **Never POST `/teacher/check-overall-rating` without explicit user confirmation.** Locked grades require `/performance/request` or administrative unlock.

- **Validation & Upstream JS Bugs:**
  1. Credit/non-5-point (`marking_system != 12`): checks `JN+ON >= (minimum_limit * limit / 100)`.
  2. 5-point (`marking_system == 12`): rejects `final == 1`.
  3. On `1-QAYDNOMA` (`final_exam_type == 11` only), `absents >= 25` (`absent_border = '25'`) resets `#final_<sid>` to `'0'` (skipped on `2-QAYDNOMA` `12`).
  4. Bugs in page JS: `$("#limit_" + id)` concatenates array `id`; `.on('change')` references undefined `$(ob)` during the `1-qaydnoma` 25% check.
