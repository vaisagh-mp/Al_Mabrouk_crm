import io
import os
import uuid
import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal
from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone
from django.db import transaction

from Admin.models import Employee, Notification
from finance.models import (
    Payroll, PayrollLine, SalaryAdvance, AdvanceInstalment,
    SalaryIncrement, Payslip
)

# Number to words helper for AED
_ONES = [
    '', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
    'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
    'Seventeen', 'Eighteen', 'Nineteen'
]
_TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _integer_to_words(n: int) -> str:
    if n == 0:
        return 'Zero'
    if n < 0:
        return 'Minus ' + _integer_to_words(-n)

    def _convert_hundred(val):
        parts = []
        hundreds = val // 100
        rem = val % 100
        if hundreds:
            parts.append(f"{_ONES[hundreds]} Hundred")
        if rem:
            if rem < 20:
                parts.append(_ONES[rem])
            else:
                tens = rem // 10
                units = rem % 10
                if units:
                    parts.append(f"{_TENS[tens]}-{_ONES[units]}")
                else:
                    parts.append(_TENS[tens])
        return ' '.join(parts)

    parts = []
    billions = n // 1000000000
    n %= 1000000000
    millions = n // 1000000
    n %= 1000000
    thousands = n // 1000
    remainder = n % 1000

    if billions:
        parts.append(f"{_convert_hundred(billions)} Billion")
    if millions:
        parts.append(f"{_convert_hundred(millions)} Million")
    if thousands:
        parts.append(f"{_convert_hundred(thousands)} Thousand")
    if remainder:
        parts.append(_convert_hundred(remainder))

    return ' '.join(parts)


def amount_to_words_aed(amount: Decimal) -> str:
    """
    Converts a Decimal amount into AED words, e.g.:
    AED 9,500.50 -> 'Nine Thousand Five Hundred Dirhams and Fifty Fils Only'
    AED 9,500.00 -> 'Nine Thousand Five Hundred Dirhams Only'
    """
    if amount is None:
        return "Zero Dirhams Only"
    amount = Decimal(str(amount))
    is_neg = amount < 0
    amount = abs(amount)
    dirhams = int(amount)
    fils = int(round((amount - Decimal(dirhams)) * 100))

    if dirhams == 0 and fils == 0:
        return "Zero Dirhams Only"

    d_words = _integer_to_words(dirhams)
    result = f"{d_words} Dirham{'s' if dirhams != 1 else ''}"

    if fils > 0:
        f_words = _integer_to_words(fils)
        result += f" and {f_words} Fils"

    result += " Only"
    if is_neg:
        result = "Minus " + result
    return result


def add_months(orig_date: date, months: int) -> date:
    """Adds months to a date, preserving 1st of month."""
    new_year = orig_date.year + (orig_date.month + months - 1) // 12
    new_month = (orig_date.month + months - 1) % 12 + 1
    return date(new_year, new_month, 1)


def generate_payslip_number(month_date: date) -> str:
    """
    Generates unique payslip number format PS-YYYYMM-0001.
    """
    month_prefix = f"PS-{month_date.strftime('%Y%m')}-"
    existing_payslips = Payslip.objects.filter(number__startswith=month_prefix).values_list('number', flat=True)
    max_seq = 0
    for num in existing_payslips:
        try:
            seq_str = num.split('-')[-1]
            seq_val = int(seq_str)
            if seq_val > max_seq:
                max_seq = seq_val
        except (ValueError, IndexError):
            pass
    return f"{month_prefix}{max_seq + 1:04d}"


def create_advance_schedule(advance: SalaryAdvance):
    """
    Creates scheduled instalments for an approved/active SalaryAdvance starting from start_month.
    """
    if advance.instalments_schedule.exists():
        return
    n = max(1, advance.instalments)
    total_amount = advance.amount
    base_deduction = round(total_amount / Decimal(n), 2)
    start_m = advance.start_month.replace(day=1)

    with transaction.atomic():
        accumulated = Decimal('0.00')
        for i in range(n):
            m = add_months(start_m, i)
            if i == n - 1:
                inst_amount = total_amount - accumulated
            else:
                inst_amount = base_deduction
                accumulated += inst_amount
            AdvanceInstalment.objects.create(
                advance=advance,
                month=m,
                amount=inst_amount,
                status='SCHEDULED',
                sort_order=i + 1
            )
        advance.monthly_deduction = base_deduction
        advance.save(update_fields=['monthly_deduction'])


def create_or_update_payslip(payroll: Payroll, generated_by=None, bump_version=False) -> Payslip:
    """
    Generates or regenerates a branded PDF payslip for a PAID Payroll row.
    Saves to private storage and updates Payslip record.
    """
    from weasyprint import HTML
    from finance.services import get_month_range, get_month_attendance_metrics

    emp = payroll.employee
    first_day, last_day, _ = get_month_range(payroll.month)
    metrics_by_emp = get_month_attendance_metrics(first_day, last_day)
    metrics = metrics_by_emp.get(emp.id, {
        'worked_days': 0,
        'absent_days': 0,
        'unpaid_days': payroll.absent_days_unpaid,
        'leave_days': 0,
        'ot_hours': 0,
    })

    # Prepare earnings and deductions breakdowns
    lines = list(payroll.lines.all().order_by('sort_order', 'id'))
    earnings_lines = [l for l in lines if l.kind == 'EARNING']
    deductions_lines = [l for l in lines if l.kind == 'DEDUCTION']

    # Fallback if no lines exist (e.g. pre-V2 paid payrolls)
    if not earnings_lines:
        if payroll.basic_salary > 0:
            earnings_lines.append({'description': 'Basic Salary', 'amount': payroll.basic_salary, 'code': 'BASIC'})
        if payroll.allowances > 0:
            earnings_lines.append({'description': 'Allowances', 'amount': payroll.allowances, 'code': 'ALLOWANCES'})
        if payroll.ot_amount > 0:
            earnings_lines.append({'description': 'Overtime', 'amount': payroll.ot_amount, 'code': 'OT'})

    if not deductions_lines:
        eff_abs = payroll.effective_absence_deduction
        if eff_abs > 0:
            deductions_lines.append({'description': 'Absence Deduction', 'amount': eff_abs, 'code': 'ABSENCE'})
        if payroll.deductions > 0:
            deductions_lines.append({'description': 'Other Deductions / Advances', 'amount': payroll.deductions, 'code': 'OTHER'})

    total_earnings = payroll.gross
    total_deductions = (payroll.deductions or Decimal('0.00')) + payroll.effective_absence_deduction
    net_pay = payroll.net_pay
    net_pay_words = amount_to_words_aed(net_pay)

    # Active advance remaining balance
    advances = SalaryAdvance.objects.filter(employee=emp, status__in=['ACTIVE', 'APPROVED'])
    advance_balance_remaining = sum(a.balance for a in advances)

    payslip, created = Payslip.objects.get_or_create(
        payroll=payroll,
        defaults={
            'number': generate_payslip_number(payroll.month),
            'generated_by': generated_by,
            'version': 1,
        }
    )

    if not created and bump_version:
        payslip.version += 1

    if generated_by and not payslip.generated_by:
        payslip.generated_by = generated_by

    context = {
        'company_name': 'Blessed Marine Services',
        'payslip': payslip,
        'payroll': payroll,
        'employee': emp,
        'month_label': payroll.month.strftime('%B %Y'),
        'metrics': metrics,
        'earnings_lines': earnings_lines,
        'deductions_lines': deductions_lines,
        'total_earnings': total_earnings,
        'total_deductions': total_deductions,
        'net_pay': net_pay,
        'net_pay_words': net_pay_words,
        'advance_balance_remaining': advance_balance_remaining,
        'generated_at': timezone.localtime(),
    }

    html_content = render_to_string('payroll/payslip_pdf.html', context)
    pdf_bytes = HTML(string=html_content).write_pdf()

    emp_code = emp.employee_code or f"AMMS-{emp.id:04d}"
    filename = f"payslip_{emp_code}_{payroll.month.strftime('%Y%m')}.pdf"
    payslip.file.save(filename, ContentFile(pdf_bytes), save=False)
    payslip.save()

    return payslip

