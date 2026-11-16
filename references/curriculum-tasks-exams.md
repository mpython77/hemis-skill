# Curriculum Topics, Tasks, Plans, Exams & Vedomost (`hemis.namdtu.uz`)

## 1. `Fanlar bazasi` Routes

| Feature | Route & Observed Params | Key Mechanics & Notes |
|---|---|---|
| `Fan mavzulari` | `/teacher/subject-topics`<br>`/teacher/subject-topic-info?id=<cs>`<br>`/teacher/subject-topic-info?id=<cs>&code=<topic_id>` | Syllabus topic bank per `curriculum_subject` with `[hours/hours]` planned vs assigned. `&code=<topic_id>` opens topic edit (`ECurriculumSubjectTopic[_curriculum, _training_type, name, topic_load]`). **Note:** Topic/hour completion = `100%` does **not** confirm resource files exist! |
| `Fan topshiriqlari` | `/teacher/subject-tasks`<br>`/teacher/subject-task-list?...`<br>`/teacher/subject-task-list?...&edit=1` | List columns: `#`, `Nomi`, `Savol / Fayl`, `Muddat`, `Yangi talaba`, `Talabalar`, `Faol`. `&edit=1` exposes `ESubjectTask[...]` (`_curriculum`, `_subject`, `_semester`, `_marking_category`, `_task_type` [`11` Topshiriq, `12` Test -> reveals `test_duration`, `question_count`, `random`], `_exam_type`, `name`, `comment`, `_subject_topic`, `deadline`, `max_ball`, `attempt_count`, `_language` [`11..22`], `filename` + `_fileinput_w1` via `/dashboard/file-upload?type=attachment&fileparam=_fileinput_w1`). `POST /ajax/get-exam-type-data` (`curriculum, semester, subject, exam_type`) returns `{"error":0,"max":...}` to bound `#max_ball`. **DANGER GET:** `changeAttribute(id, att)` calls state-mutating `GET /teacher/subject-task-list?id=<id>&attribute=<att>`. **Topshiriq = `0` != Resurs = `0`**. |
| `Kurs topshiriqlari` | `/teacher/subject-task-other` | Uses `ESubjectExamSchedule` filter (`_education_year`, `_semester`, `_group`, `_subject`). |
| `Kalendar reja` | `/teacher/calendar-plan` (`&download=1`) | Auto-pairs `ECurriculumSubjectTopic` rows per `(subject, group, training_type)` with logged `check-lesson` sessions. `&download=1` returns **`application/pdf`** (`Calendar_plan-<Subject>.pdf`). Crucial ground truth for topic order. |
| `Fan ma'lumotlari` | `/teacher/subject-info`<br>`/teacher/subject-info?curriculum=<id>&subject=<id>&semester=<id>` | Must send **`X-Requested-With: XMLHttpRequest`** to retrieve the modal passport fragment (`Fanlar bloklari`, `Fan turi`, `Qaydnoma turi`, `Kredit`, `Yakuniy baholash turi`, hours table, and `Nazorat turi` max scores `1-on`/`2-on`/`Yakuniy nazorat`/`Umumiy`); plain GET returns the parent list page. **Note:** `subject` ID here is distinct from `curriculum_subject` (`cs`)! |

**Architecture Note (Ownership vs Delivery):** Separate scopes divide syllabus topic ownership vs taught schedule rows (for example, one teacher account may have `6` rows in `subject-topics`, `10` in `subject-info`, `13` in `subject-tasks`, and `20` in `calendar-plan`). Always inspect each subject's exact `curriculum` + `cs` mapping rather than assuming two groups share topics or edit rights.

---

## 2. `O'quv jarayoni` (`/curriculum/*` & `/exam/*`)

- `/curriculum/education-year` and `/curriculum/curriculum`: return **`403 Forbidden`** for `O'qituvchi` (Dean/Admin only).
- `Imtihonlar ro'yxati` (`/exam/index`, `/exam/edit`): online tests (`+ Imtihon yaratish`, `Savollar soni`, `Vaqti (daqiqa)`, `FaceID`, `Faol`) live here — **not** under `Nazoratlar`. Check write rights per active role; `check_face` / `active` toggles call `/dashboard/toggle`. Connects to `Nazoratlar` via `To'ldirish` (`?choose_exam=1`) inside `/teacher/check-overall-rating`.
  - `/exam/edit` uses `EExam[...]` (`name`, `comment`, `_curriculum`, `_education_year`, `_semester`, `_exam_type` [`13` Yakuniy nazorat, `14` Umumiy], `active`, `check_face`, `start_at`, `finish_at`, `duration`, `max_ball`, `attempts`, `question_count`, `random`, `_subject`) with DepDrop endpoints `/exam/edit?semester=1` (`depends: _curriculum, _education_year`) and `/exam/edit?subject=1` (`depends: _curriculum, _semester`). (`/exam/questions` and `/exam/results` standalone paths return `404`.)

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
  - `Ro'yxat`: `GET /teacher/check-overall-rating?id=<id>&access=1` -> downloads **`application/octet-stream` `.docx` Word roster** (`<group>-<subject>.docx`).
  - `Chop etish`: `GET /teacher/download-rating?code=<UUID>&performance=1` (redirects `302` if scores are not yet saved / `Bloklangan: (berilmagan)`).
  - `To'ldirish`: modal `.showModalButton` (`value="...&choose_exam=1"`, returns table fragment `Nomi | Guruhlar | Boshlanish | Tugash`) importing online test scores from `/exam/index`.
  - `Saqlash`: `button#submit[name="btn"]` — **ONE-SHOT IRREVERSIBLE LOCK (`Bloklangan: Ha`)** (note: bare `/teacher/check-overall-rating` without `?id=` returns `500`).

> [!CAUTION]
> **Irreversible Vedomost Lock:** Submitting `#assessment-form` locks editing (`Bloklangan: Ha`). **Never POST `/teacher/check-overall-rating` without explicit user confirmation.** Locked grades require `/performance/request` or administrative unlock.

- **Validation & Upstream JS Bugs:**
  1. Credit/non-5-point (`marking_system != 12`): checks `JN+ON >= (minimum_limit * limit / 100)`.
  2. 5-point (`marking_system == 12`): rejects `final == 1`.
  3. On `1-QAYDNOMA` (`final_exam_type == 11` only), `absents >= 25` (`absent_border = '25'`) resets `#final_<sid>` to `'0'` (skipped on `2-QAYDNOMA` `12`).
  4. Bugs in page JS: `$("#limit_" + id)` concatenates array `id`; `.on('change')` references undefined `$(ob)` during the `1-qaydnoma` 25% check.

---

## 4. `O'zlashtirish` (`/performance/*`) & `Yakka darslar` / `Qayta o'qish` Filter Models

- `/performance/ptt-fill` (`Shaxsiy qaydnoma kiritish`): filters `EStudentPttSubject[_student_ptt, _curriculum_subject, _student]`; columns include `Shaxsiy qaydnoma`, `Talaba`, `Ta'lim turi`, `Mutaxassislik`, `Fanlar`, `Semester`, `Fan turi`, `Yuklama / Kredit`, `Ball`, `Baho`, `O'qituvchi F.I.SH`, `Muddat`.
- `/performance/request` (`Baholash so'rovlari`): filters `EPerformanceEditRequest[_education_year, _employee, request_status, search]`.
- `/individual-training/subject-student` (`EIndividualTrainingSubjectTeacher`), `/individual-training/subject-schedule` (`EIndividualTrainingSubjectSchedule` + `toggleAttributes`), `/individual-training/subject-attendance`.
- `/retraining/time-table` (`FilterForm[_education_year]`), `/retraining/{midterm,final,other}-exam-table` (`ERetrainingExamSchedule`), `/retraining/teacher-subject` (`ERetrainingSubjectSchedule`).
