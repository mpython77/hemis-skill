# Student Web Portal (`student.namdtu.uz`) & Student REST API (`/rest/v1`)

> **Portal Separation Rule:** `student.namdtu.uz` (Student Web, cookies `frontend` + `_csrf-frontend`) and `student.namdtu.uz/rest/v1` (Student REST JSON API, Bearer JWT) are strictly separate from Teacher Web (`hemis.namdtu.uz`). Always verify identity first (`GET /dashboard/profile` or `GET /rest/v1/account/me`).

## 1. Student Web Portal (`https://student.namdtu.uz`)

- **Auth & Keepalive:** Cookies `frontend`, `_csrf-frontend`. Web login fields: `FormStudentLogin[login]`, `FormStudentLogin[password]`, `FormStudentLogin[rememberMe]` (observed flows lacked image CAPTCHA, not guaranteed across updates). Student frontend polls `/keepalive/ping` roughly every 4 minutes.

### Known Route Map
| Category | Routes (`https://student.namdtu.uz`) | Critical Notes / Side-Effects |
|---|---|---|
| **O'quv jarayoni** | `/education/curriculum`, `/education/time-table`, `/education/exam-table`, `/education/subjects`, `/education/resources?subject=<id>`, `/education/tasks?subject=<id>`, `/education/attendance`, `/education/attendance-report`, `/education/performance`, `/education/ptt`, `/education/academic-data`, `/education/subject-choose`, `/test/exams` | `/education/time-table` uses `StudentSchedule[_week]` & defaults to week 1 — match exact option text date range (never reuse last year's week ID!). Web `/education/time-table` != REST `/education/schedule`. `/test/exams` may enforce FaceID. |
| **Qayta o'qish** | `/retraining/application`, `/retraining/time-table`, `/retraining/exam-table`, `/retraining/performance` | Application/payment/exam flows separate from main term. |
| **Talaba ma'lumotlari** | `/student/personal-data`, `/student/resume`, `/student/decree`, `/student/contract`, `/student/reference`, `/student/document`, `/student/document/0` … `/student/document/3`, `/student/circulation-sheet`, `/student/gpa`, `/student/foreign-certificate`, `/student/graduate-qualifying`, `/student/social-activity`, `/student/social-activity-rating`, `/student/grant-application`, `/student/grant-application-rating` | **`/student/reference?get=1` IS STATE-MUTATING** (issues a new official document!); read existing via `/student/reference?file=<id>`. `/student/document/0` … `/student/document/3` labels come from live UI. |
| **Plagiat** | `/student-data/plagiarism`, `/student-data/upload-plagiarism` | Uploading consumes free quota (e.g., 2 observed free checks is not universal — always check current limit first). |
| **Moliya & Ijtimoiy** | `/student-data/contract`, `/student-data/stipend`, `/student-data/rent`, `/student-data/rent-sync`, `/student-data/residence`, `/student-data/credit-module` | `/student-data/stipend` uses `StudentDataStipend[_education_year]` — inspect **each year separately**, and remember **HEMIS stipend record != bank deposit confirmation**! `/student-data/rent-sync` is state-mutating! Never display full card/account numbers. |
| **Xabarlar & Tizim** | `/message/my-messages`, `/message/compose`, `/message/compose/<teacher_id>`, `/dashboard/profile`, `/dashboard/poll`, `/student/survey`, `/dashboard/logins`, `/keepalive/ping` | `/dashboard/logins` shows login IP/history. Page existence on grants/dorm/rent does not equal approved eligibility. |

---

## 2. Student REST API (`https://student.namdtu.uz/rest/v1`)

> **URL Rule:** Base already ends in `/v1` — **never add `/v1` a second time**!
> Web cookies do **not** work on REST endpoints (`401`). Always verify `HTTP status` + JSON payload flags + `data` before calculating.

### Authenticated Student Endpoints (`Authorization: Bearer <token>`)
| Method | Endpoint (relative to `/rest/v1`) | Purpose & Known Gotchas |
|---|---|---|
| `POST` | `/auth/login` | Body `{"login": "<...>", "password": "<...>"}` -> returns token in `data.token` (never log/save raw token). |
| `GET` | `/account/me` | **Run first** to verify account identity, active semester context, and `data.avg_gpa` (overall GPA — preserve API precision, do not round arbitrarily; don't label semester GPA without `/student/gpa`). |
| `GET` | `/education/schedule` | Weekly/lesson entries for **that student only** (not university-wide); `lesson_date` can be UNIX seconds -> convert in `UTC+05:00` (`Asia/Tashkent`). |
| `GET` | `/education/subject-list` | Subjects, semesters, and load allocation. |
| `GET` | `/education/subjects` | Subject topics/tasks summary. |
| `GET` | `/education/attendance` | Returns `absent_off` (unexcused academic hours), `absent_on` (excused hours), `lesson_date`, `semester.name`, `subject.name`, `trainingType.name`, `employee.name`. |
| `GET` | `/education/performance` | Student ratings/grades. |
| `GET` | `/education/semesters` | Term list. **Gotcha:** Multiple items may have `current=true` — do not blindly pick index `0`! Cross-check `/account/me`. |
| `GET` | `/education/exam-table?semester=<id>` | **Requires `semester` query param** (returns `400` without it!). Empty REST response does **not** prove web `/education/exam-table` is empty. |
| `GET` | `/student/decree` | Student decrees (count can differ from web `/student/decree`). |
| `GET` | `/student/reference` | Student reference records. |
| `GET` | `/student/contract` | **Expected `400` ("no data") for state-grant students** — do not misdiagnose as auth failure! |

- **Never invent unverified REST paths** (e.g. do not guess `/education/gpa` or `/student/gpa` just because `/student/gpa` exists on web).
- For **unauthenticated public university statistics**, use `https://student.namdtu.uz/rest/v1/public/stat-{structure,student,employee}` (documented in [science-messages-system-api.md](science-messages-system-api.md)).
