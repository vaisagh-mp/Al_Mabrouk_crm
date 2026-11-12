import csv
import sys
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from calendar import monthrange
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.test import override_settings
from django.db.models import Q

from Admin.models import Employee, Holiday
from Admin.attendance_register import build_attendance_register
from finance.services import get_month_attendance_metrics, PAYROLL_DAYS_DIVISOR
from service_logs.models import EmployeeServiceLog
from service_logs.ot import (
    employee_office_attendances,
    split_day_ot,
    ot_multiplier,
    calculate_office_ot_cost
)
from Admin.attendance_engine import is_rest_day, pay_base_salary


class Command(BaseCommand):
    help = "Compare attendance register and payroll metrics between V1 and V2 engine (strictly read-only)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--month',
            type=str,
            required=True,
            help="Month to evaluate in YYYY-MM format (e.g. 2026-10)."
        )
        parser.add_argument(
            '--employee',
            type=str,
            default=None,
            help="Optional employee code (e.g. AMMS-0001) or ID to filter by."
        )
        parser.add_argument(
            '--csv',
            type=str,
            default=None,
            help="Optional file path to output CSV comparison."
        )

    def compute_employee_metrics(self, emp, year, month, start_date, end_date, v2_on):
        with override_settings(HR_ATTENDANCE_ENGINE_V2=v2_on):
            # 1. Attendance register rows
            all_rows = build_attendance_register(start_date, end_date)
            reg_rows = [r for r in all_rows if r['employee'].id == emp.id]

            # 2. Status counts from register
            rest_days = sum(1 for r in reg_rows if r.get('status_code') in ['SUNDAY', 'REST_DAY'])
            holiday_days = sum(1 for r in reg_rows if r.get('status_code') == 'HOLIDAY')
            absent_days = sum(1 for r in reg_rows if r.get('status_code') == 'ABSENT')
            
            missing_punch_inc = sum(
                1 for r in reg_rows
                if r.get('status_code') == 'MISSING_PUNCH' and r.get('missing_punch_type') == 'INCOMPLETE'
            )
            missing_punch_nop = sum(
                1 for r in reg_rows
                if r.get('status_code') == 'MISSING_PUNCH' and r.get('missing_punch_type') == 'NO_PUNCH'
            )
            missing_punch_total = missing_punch_inc + missing_punch_nop

            # 3. Finance attendance metrics (unpaid days)
            all_metrics = get_month_attendance_metrics(start_date, end_date)
            metrics = all_metrics.get(emp.id, {'unpaid_days': 0, 'missing_punch_dates': []})
            unpaid_days = metrics.get('unpaid_days', 0)

            # Base salary & daily rate
            if v2_on:
                base_sal = pay_base_salary(emp, start_date)
            else:
                base_sal = emp.salary or Decimal('0.00')

            divisor = Decimal(str(PAYROLL_DAYS_DIVISOR))
            daily_rate = (base_sal / divisor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            absence_ded = (Decimal(str(unpaid_days)) * daily_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

            # 4. Office OT cost
            office_ot_cost = calculate_office_ot_cost(emp.salary or 0, reg_rows, employee=emp)

            # 5. Service-log OT (computed in-memory without DB write)
            service_ot_cost = Decimal('0.00')
            logs_qs = list(
                EmployeeServiceLog.objects.filter(
                    employee=emp,
                    date__range=(start_date, end_date)
                ).select_related('employee', 'employee__branch').order_by('date', 'start_time', 'id')
            )
            logs_by_day = defaultdict(list)
            for l in logs_qs:
                logs_by_day[l.date].append(l)

            for d, day_logs in logs_by_day.items():
                if v2_on:
                    is_hol = Holiday.objects.filter(
                        date=d,
                        is_active=True
                    ).filter(
                        Q(branch__isnull=True) | Q(branch_id=emp.branch_id)
                    ).exists()
                    rest_flag = is_rest_day(emp, d)
                    sal = pay_base_salary(emp, d)
                else:
                    is_hol = Holiday.objects.filter(date=d).exists()
                    rest_flag = None
                    sal = emp.salary

                rate = (sal / Decimal('208')) if sal else Decimal('0.00')
                if rate > 0:
                    attendances = employee_office_attendances(emp.id, d)
                    split_info = split_day_ot(attendances, day_logs, d)
                    v_map = split_info['vessel_ot_by_log_id']
                    for l in day_logs:
                        if l.end_time is not None:
                            v_ot = v_map.get(l.id, Decimal('0.00'))
                            if v_ot > 0:
                                is_public_hol = bool(l.is_holiday or is_hol)
                                mult = ot_multiplier(d, is_public_hol, is_rest_day=rest_flag)
                                service_ot_cost += round(v_ot * rate * mult, 2)

            return {
                'rest_days': rest_days,
                'holiday_days': holiday_days,
                'absent_days': absent_days,
                'missing_punch_total': missing_punch_total,
                'missing_punch_inc': missing_punch_inc,
                'missing_punch_nop': missing_punch_nop,
                'unpaid_days': unpaid_days,
                'absence_ded': absence_ded,
                'office_ot_cost': Decimal(str(office_ot_cost)),
                'service_ot_cost': service_ot_cost,
            }

    def handle(self, *args, **options):
        month_str = options['month']
        emp_filter = options.get('employee')
        csv_path = options.get('csv')

        try:
            year, month = map(int, month_str.split('-'))
            start_date = date(year, month, 1)
            _, num_days = monthrange(year, month)
            end_date = date(year, month, num_days)
        except Exception:
            raise CommandError("Invalid month format. Please use YYYY-MM (e.g. 2026-10).")

        employees = Employee.objects.all().select_related('user', 'department', 'branch').order_by('employee_code')
        if emp_filter:
            if emp_filter.isdigit():
                employees = employees.filter(id=int(emp_filter))
            else:
                employees = employees.filter(employee_code=emp_filter)

        if not employees.exists():
            self.stdout.write(self.style.WARNING(f"No employees found matching filter for {month_str}."))
            return

        self.stdout.write("=" * 80)
        self.stdout.write(f"ATTENDANCE ENGINE COMPARISON: {start_date.strftime('%B %Y')} (V1 vs V2)")
        self.stdout.write("Strictly Read-Only Analysis")
        self.stdout.write("=" * 80)

        results = []

        total_v1_ded = Decimal('0.00')
        total_v2_ded = Decimal('0.00')
        total_v1_off_ot = Decimal('0.00')
        total_v2_off_ot = Decimal('0.00')
        total_v1_srv_ot = Decimal('0.00')
        total_v2_srv_ot = Decimal('0.00')

        for emp in employees:
            v1_data = self.compute_employee_metrics(emp, year, month, start_date, end_date, v2_on=False)
            v2_data = self.compute_employee_metrics(emp, year, month, start_date, end_date, v2_on=True)

            has_diff = (
                v1_data['rest_days'] != v2_data['rest_days'] or
                v1_data['holiday_days'] != v2_data['holiday_days'] or
                v1_data['absent_days'] != v2_data['absent_days'] or
                v1_data['missing_punch_total'] != v2_data['missing_punch_total'] or
                v1_data['unpaid_days'] != v2_data['unpaid_days'] or
                v1_data['absence_ded'] != v2_data['absence_ded'] or
                v1_data['office_ot_cost'] != v2_data['office_ot_cost'] or
                v1_data['service_ot_cost'] != v2_data['service_ot_cost']
            )

            total_v1_ded += v1_data['absence_ded']
            total_v2_ded += v2_data['absence_ded']
            total_v1_off_ot += v1_data['office_ot_cost']
            total_v2_off_ot += v2_data['office_ot_cost']
            total_v1_srv_ot += v1_data['service_ot_cost']
            total_v2_srv_ot += v2_data['service_ot_cost']

            results.append({
                'employee': emp,
                'v1': v1_data,
                'v2': v2_data,
                'has_diff': has_diff
            })

            # Print single employee comparison card / block
            flag = " [DIFF]" if has_diff else " [IDENTICAL]"
            emp_title = f"{emp.employee_code or 'NO-CODE'} - {emp.user.get_full_name() or emp.user.username}{flag}"
            if has_diff:
                self.stdout.write(self.style.WARNING(emp_title))
            else:
                self.stdout.write(self.style.SUCCESS(emp_title))

            self.stdout.write(
                f"  Rest days:       V1={v1_data['rest_days']:<2} | V2={v2_data['rest_days']:<2} "
                f"  Holidays:     V1={v1_data['holiday_days']:<2} | V2={v2_data['holiday_days']:<2}"
            )
            self.stdout.write(
                f"  Absent:          V1={v1_data['absent_days']:<2} | V2={v2_data['absent_days']:<2} "
                f"  Unpaid days:  V1={v1_data['unpaid_days']:<2} | V2={v2_data['unpaid_days']:<2}"
            )
            self.stdout.write(
                f"  Missing punch:   V1={v1_data['missing_punch_total']} (inc:{v1_data['missing_punch_inc']}/no:{v1_data['missing_punch_nop']}) | "
                f"V2={v2_data['missing_punch_total']} (inc:{v2_data['missing_punch_inc']}/no:{v2_data['missing_punch_nop']})"
            )
            self.stdout.write(
                f"  Absence ded:     V1=AED {v1_data['absence_ded']:<8} | V2=AED {v2_data['absence_ded']:<8} (Diff: AED {v2_data['absence_ded'] - v1_data['absence_ded']})"
            )
            self.stdout.write(
                f"  Office OT:       V1=AED {v1_data['office_ot_cost']:<8} | V2=AED {v2_data['office_ot_cost']:<8} (Diff: AED {v2_data['office_ot_cost'] - v1_data['office_ot_cost']})"
            )
            self.stdout.write(
                f"  Service Log OT:  V1=AED {v1_data['service_ot_cost']:<8} | V2=AED {v2_data['service_ot_cost']:<8} (Diff: AED {v2_data['service_ot_cost'] - v1_data['service_ot_cost']})"
            )
            self.stdout.write("-" * 80)

        # Totals Summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("TOTALS SUMMARY:")
        self.stdout.write(f"  Employees evaluated: {len(results)}")
        self.stdout.write(f"  Absence Deduction: V1 = AED {total_v1_ded:.2f} | V2 = AED {total_v2_ded:.2f} | Diff = AED {total_v2_ded - total_v1_ded:+.2f}")
        self.stdout.write(f"  Office Overtime:   V1 = AED {total_v1_off_ot:.2f} | V2 = AED {total_v2_off_ot:.2f} | Diff = AED {total_v2_off_ot - total_v1_off_ot:+.2f}")
        self.stdout.write(f"  Service Log OT:    V1 = AED {total_v1_srv_ot:.2f} | V2 = AED {total_v2_srv_ot:.2f} | Diff = AED {total_v2_srv_ot - total_v1_srv_ot:+.2f}")
        net_financial_diff = (total_v2_off_ot + total_v2_srv_ot - total_v2_ded) - (total_v1_off_ot + total_v1_srv_ot - total_v1_ded)
        self.stdout.write(f"  Net Employer Cost Impact (OT - Absence Ded): AED {net_financial_diff:+.2f}")
        self.stdout.write("=" * 80)

        # CSV Export if requested
        if csv_path:
            with open(csv_path, mode='w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'Employee Code', 'Employee Name', 'Department', 'Branch',
                    'V1 Rest Days', 'V2 Rest Days',
                    'V1 Holidays', 'V2 Holidays',
                    'V1 Absent', 'V2 Absent',
                    'V1 Missing Punch', 'V2 Missing Punch',
                    'V1 Unpaid Days', 'V2 Unpaid Days',
                    'V1 Absence Ded (AED)', 'V2 Absence Ded (AED)',
                    'V1 Office OT (AED)', 'V2 Office OT (AED)',
                    'V1 Service OT (AED)', 'V2 Service OT (AED)',
                    'Has Difference'
                ])
                for r in results:
                    emp = r['employee']
                    v1 = r['v1']
                    v2 = r['v2']
                    writer.writerow([
                        emp.employee_code or '',
                        emp.user.get_full_name() or emp.user.username,
                        emp.department.name if emp.department else '',
                        emp.branch.name if emp.branch else '',
                        v1['rest_days'], v2['rest_days'],
                        v1['holiday_days'], v2['holiday_days'],
                        v1['absent_days'], v2['absent_days'],
                        v1['missing_punch_total'], v2['missing_punch_total'],
                        v1['unpaid_days'], v2['unpaid_days'],
                        str(v1['absence_ded']), str(v2['absence_ded']),
                        str(v1['office_ot_cost']), str(v2['office_ot_cost']),
                        str(v1['service_ot_cost']), str(v2['service_ot_cost']),
                        'YES' if r['has_diff'] else 'NO'
                    ])
            self.stdout.write(self.style.SUCCESS(f"Comparison report written to {csv_path}"))
