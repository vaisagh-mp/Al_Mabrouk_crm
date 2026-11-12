# HR Module Upgrade — Go-Live & Operations Guide

> **Timezone requirement:** All attendance, payroll, leave, and cron jobs run in `TZ=Asia/Dubai` (`UTC+04:00`).

---

## 1. Migrations List

Run migrations in standard Django order (`python manage.py migrate`) after deploying code:

### `Admin` (`0012` – `0025`)
1. `Admin/0012_branch_department_employee_company_email_and_more` (Phase 1: Department, Branch, Employee HR master fields, SalaryStructure, EmployeeChangeLog)
2. `Admin/0013_assign_employee_codes_and_salary_structures` (Phase 1: Backfill employee codes `AMMS-XXXX` and initial SalaryStructure rows)
3. `Admin/0014_dutyschedule_alter_holiday_options_holiday_branch_and_more` (Phase 2: DutySchedule, EmployeeSchedule, RestDayRule, RosterDay, Holiday branch scope)
4. `Admin/0015_attendance_missing_punch_resolved_and_more` (Phase 3: AttendanceCorrection, `missing_punch_resolved`)
5. `Admin/0016_leavetype_leave_approver_leave_clarification_request_and_more` (Phase 4: LeaveType, LeaveLedger, LeaveYearReview, Leave workflow fields)
6. `Admin/0017_seed_leave_types_and_backfill` (Phase 4: Seed default LeaveTypes & backfill Leave -> LeaveType refs)
7. `Admin/0018_alter_leaveledger_entry_type` (Phase 4: `YEAR_END_CLOSE`, `ENTITLEMENT` entry types)
8. `Admin/0019_workordertime_attachment_workordertime_break_minutes_and_more` (Phase 5: WorkOrderTime break/travel/description/attachment, OTApproval)
9. `Admin/0020_employee_last_working_day_employee_notice_start_date_and_more` (Phase 6: DocumentType, EmployeeDocument, CompanyDocument, DocumentReminderLog, exit/notice fields)
10. `Admin/0021_seed_document_types` (Phase 6: Seed standard employee & company document types)
11. `Admin/0022_companydocument_created_at_and_more` (Phase 6: Document metadata & reminder log refinements)
12. `Admin/0023_alter_documentreminderlog_unique_together_and_more` (Phase 6: Company/Employee reminder threshold tracking)
13. `Admin/0024_repair_salary_structure_overlaps` (Phase 7: Close/align overlapping historical SalaryStructure date ranges)
14. `Admin/0025_notification_link_notification_read_at_and_more` (Phase 8: Typed Notification fields `type`, `link`, `related_kind`, `related_id`, `read_at`)

### `finance` (`0010`)
1. `finance/0010_payrollline_payslip_salaryadvance_advanceinstalment_and_more` (Phase 7: PayrollLine, Payslip, SalaryAdvance, AdvanceInstalment, SalaryIncrement)

### `service_logs` (`0005` – `0006`)
1. `service_logs/0005_employeeservicelog_approval_status_and_more` (Phase 5: Timesheet approval status & audit fields)
2. `service_logs/0006_backfill_approved_service_logs` (Phase 5: Mark pre-upgrade historical service logs as `APPROVED`)

---

## 2. Feature Switches & Recommended Activation Order

All switches live in `Al_Mabrouk_crm/settings.py` and default to `False` so deployments can migrate safely before flipping HR policies live:

1. **Pre-switch baseline (immediately after deployment)**
   - **Phases 1, 3, 6, 8** (Departments, Branches, Attendance Corrections, Documents & Expiry, HR Dashboard, Typed Notifications, Reports Center, Analytics) are immediately active without breaking legacy calculations.
   - Verify Duty Schedules, Weekly Rest Days, Branches/Departments, and Public Holidays are configured.
   - Run the read-only engine comparison (`compare_attendance_engine`) before enabling V2 attendance.

2. **Step 1 — Attendance Engine V2 (`HR_ATTENDANCE_ENGINE_V2 = True`)**
   - Enables schedule-aware absent / missing punch / rest day / holiday / OT calculations.
   - Companion settings:
     - `HR_TRAVEL_COUNTS_AS_WORK = True` (adjust to `False` if travel minutes should not count toward worked hours).
     - `HR_BASIC_SALARY_FROM = date(YYYY, M, D)` (optional date from which OT base rate uses split Basic Salary instead of gross salary; set to `None` until payroll split cutover).

3. **Step 2 — Leave V2 (`HR_LEAVE_V2 = True`)**
   - **Prerequisite:** HR must first import opening balances (Annual Leave opening ledger) and grant initial annual entitlements (Sick Leave, etc.) before turning on.
   - Once enabled, leave balance validation, working-day/holiday exclusions, half-day/clarification workflows, and continuous `LeaveLedger` deductions take effect.

4. **Step 3 — Vessel / Site Timesheet & OT Approval (`HR_TIMESHEET_APPROVAL = True`)**
   - Set **`HR_TIMESHEET_APPROVAL_FROM = date(YYYY, M, D)`** (recommended: 1st of the cutover month, e.g. `date(2026, 11, 1)`).
   - **Important:** Do **not** leave `HR_TIMESHEET_APPROVAL_FROM = None` if historical `DRAFT` logs exist from before cutover, otherwise only `APPROVED` logs will count toward payroll and job cost. Setting `HR_TIMESHEET_APPROVAL_FROM` keeps all logs prior to that date counting automatically while enforcing approval from the cutover date onwards.

5. **Step 4 — Payroll V2 (`HR_PAYROLL_V2 = True`)**
   - **Prerequisite:** Salary structures (Basic / Housing / Transport / Other Allowance split) must be verified for all active staff, and `HR_BASIC_SALARY_FROM` set if OT should use basic salary from the cutover month.
   - Enables split salary lines, approved salary increments, scheduled advance deductions, prorated allowances, and automatic branded PDF payslip generation on Mark Paid.

---

## 3. Required Cron Jobs (`TZ=Asia/Dubai`)

Configure on the production server crontab (adjust paths to virtualenv and project root `/path/to/Al_Mabrouk_crm`):

```cron
CRON_TZ=Asia/Dubai
DJANGO_MANAGE=/path/to/venv/bin/python /path/to/Al_Mabrouk_crm/manage.py

# 1. Auto punch-out stale open office sessions daily at 00:15 (local Dubai time)
15 0 * * * $DJANGO_MANAGE auto_punch_out >> /var/log/almabrouk/auto_punch_out.log 2>&1

# 2. Apply salary structures whose effective_from <= today daily at 00:30
30 0 * * * $DJANGO_MANAGE apply_salary_structures >> /var/log/almabrouk/apply_salary_structures.log 2>&1

# 3. Document expiry reminders daily at 08:00 (90, 60, 30, and 0/expired thresholds)
0 8 * * * $DJANGO_MANAGE send_document_reminders >> /var/log/almabrouk/send_document_reminders.log 2>&1

# 4a. Monthly leave accrual on the 1st at 01:00 — credits the month that has just ENDED
#     (running it for the current month would credit days before they are earned)
0 1 1 * * $DJANGO_MANAGE accrue_leave --month $(date -d "last month" +\%Y-\%m) >> /var/log/almabrouk/accrue_leave_monthly.log 2>&1

# 4b. Yearly entitlements (e.g. Sick Leave 15d) — daily at 01:15; idempotent, so it also
#     picks up mid-year joiners (pro-rated) without duplicating anyone's grant
15 1 * * * $DJANGO_MANAGE accrue_leave --entitlements-for $(date +\%Y) >> /var/log/almabrouk/accrue_leave_entitlements.log 2>&1

# 5. Year-end leave close for the PREVIOUS year on 1st January at 01:30 — after 4a has credited
#    December's accrual, so the carry-forward / excess review uses the full year's balance
30 1 1 1 * $DJANGO_MANAGE close_leave_year --year $(date -d "last year" +\%Y) >> /var/log/almabrouk/close_leave_year.log 2>&1
```

> Always test cron commands with `--dry-run` first where supported (`apply_salary_structures --dry-run`, `send_document_reminders --dry-run`, `accrue_leave --dry-run`, `close_leave_year --year YYYY --dry-run`).

---

## 4. One-Off Cutover Commands & Steps

1. **Compare Attendance Engines (Read-Only Audit before enabling V2):**
   ```bash
   TZ=Asia/Dubai python manage.py compare_attendance_engine --month 2026-10 --csv /tmp/att_compare_2026_10.csv
   ```
   Review differences in worked days, absent days, missing punches, and OT hours with HR before flipping `HR_ATTENDANCE_ENGINE_V2 = True`.

2. **Import Leave Opening Balances (before enabling `HR_LEAVE_V2`):**
   - Navigate to **HR / Admin → Import Opening Balances** (`/hr/import-opening-balances/` or `/admin-panel/import-opening-balances/`).
   - Download/prepare the Excel sheet (`employee_code`, `leave_type`, `balance`, `as_of_date`), preview validation, and commit opening `LeaveLedger` entries.

3. **Grant Initial Annual Entitlements for Cutover Year:**
   ```bash
   TZ=Asia/Dubai python manage.py accrue_leave --entitlements-for 2026 --dry-run
   TZ=Asia/Dubai python manage.py accrue_leave --entitlements-for 2026
   ```
   Also run monthly accrual for completed months in the current year if backfilling ledger accruals (`--month YYYY-MM`).

---

## 5. `private_media` Storage & Backups

Confidential HR documents (passports, visas, Emirates IDs, labour cards, company trade licenses) and generated PDF payslips are stored under `settings.PRIVATE_MEDIA_ROOT` (`<BASE_DIR>/private_media/`), **never** under `MEDIA_ROOT`.

- **Web Server Security:** Do **not** expose `private_media/` via Nginx/Apache alias or Django `static()`. All downloads go through permission-checked Django views (`can_view_document` / `can_view_salary` / payslip owner checks).
- **Backup Policy:** Include `<BASE_DIR>/private_media/` in daily encrypted off-site backups alongside the PostgreSQL database dump:
  ```bash
  # Example daily backup snippet (run after pg_dump)
  tar -czf /backups/private_media_$(date +%F).tar.gz -C /path/to/Al_Mabrouk_crm private_media
  ```
- **Permissions:** Ensure the OS directory `private_media/` is owned by the application service user (`chmod 700`).

---

## 6. Open Client Decisions

Confirm the following business/policy items with the client before full production cutover:

1. **External Email / WhatsApp Notifications (D18):** Phase 8 delivers full typed in-app notifications (`Admin/notifications.py`). Decide SMTP credentials, sender domain, and which notification types (`LEAVE`, `DOCUMENT_EXPIRY`, `PAYSLIP`, etc.) should trigger external emails.
2. **OT Base Salary Cutover (`HR_BASIC_SALARY_FROM`):** Confirm whether overtime calculations switch from total salary to basic salary on a specific cutover date (`HR_BASIC_SALARY_FROM = date(YYYY, M, D)`) or remain on total salary.
3. **Timesheet Approval Cutover (`HR_TIMESHEET_APPROVAL_FROM`):** Confirm exact go-live date from which Vessel / Site timesheets and daily OT require supervisor approval before counting toward payroll and job cost.
4. **Travel Time Policy (`HR_TRAVEL_COUNTS_AS_WORK`):** Confirm whether travel minutes on vessel work orders count toward daily worked/OT hours (`True`, current default) or are tracked for reporting only (`False`).
5. **Leave Carry-Forward Cap & Payout Rules:** Currently capped at 5 days carried forward automatically on 31/12 with excess flagged in `LeaveYearReview` for HR decision (encash / forfeit / manual carry). Confirm if any grade/designation uses a different cap.
6. **Gratuity / End-of-Service Settlement:** Confirm whether end-of-service benefit (UAE Labour Law) calculation should be added as a future Phase 9 enhancement on `deactivate_employee`.

