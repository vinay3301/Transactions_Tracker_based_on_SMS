import os
import re
import shlex
import xml.etree.ElementTree as ET
import calendar
import gspread
import requests
import traceback
from datetime import datetime, timedelta
from oauth2client.service_account import ServiceAccountCredentials

# ============================================================
# CONFIGURATION
# ============================================================

backup_dir = "/data/data/com.termux/files/home/Script_for_SMS/"
json_file = "/data/data/com.termux/files/home/Script_for_SMS/fifth-composite-489705-v5-d06baf7f990f.json"
sheet_id = "1Gm3vbcktFlKPmHlNRePIVkvREQUJeXLaznjuX4c7iz8"
error_log_file = os.path.join(backup_dir, "sms_sync_errors.log")

# ============================================================
# CONTACT MAPPING
#
# Format:
# "PERSON'S STANDARD NAME": ["alias1", "alias2", "alias3"]
#
# Add any new name/UPI handle/account identifier to the
# appropriate person's list.
# ============================================================

CONTACT_MAP = {
    "MOM": [
        "7981540866",
    ],
    "KUNCHAKARLA ANAND": [
        "7013837412",
    ],
    "RAHUL": [
        "8885551234",
    ],
    "GANJI DIVYA CHANDRIKA": [
        "8328546071PTYES",
        "8328546071",
        "*2402@PTYES",
        "*2402@PTYES (UPI 616500092163)",
    ],
    "KINNERA MANASA": [
        "7842407396",
    ],
    "KOTA RATNA KUMARI": [
        "6301855293",
    ],
    "LAKKIPOGU SUBBARAO": [
        "9030846397-2",
        "9030846397",
    ],
    "DAD": [
        "9494966515",
        "SUNILKINNERA123@AXL",
        "SUNILKINNERA123",
        "*6515@AXL (UPI 652921594171)",
    ],
    "HAPPI ICE CREAMS FOOD": [
        "PARIMALA ENTERPRISES",
    ],
    "CURRIES/FOOD MIDDI CENTER": [
        "KONGARA MALLIKARJUNARAO",
    ],
    "CARE AND CURE MEDICINE": [
        "SURESH MEDICALS",
    ],
    "HOUSEHOLD EXPENSES STORE @ S S HOMES": [
        "MR TIRUVEEDHULA AD",
        "SRI LAKSHMI KIRANA GENERA",
    ],
    "HOUSEHOLD EXPENSES STORE near MANASA house": [
        "MUNAGALA VENKATA SATYA NA",
    ],
    "KANDULA KARTHIKEYA": [
        "KKS270819972",
        "KKS270819971",
    ],
    "HOUSEHOLD EXPENSES": [
        "RAMESH MANYAM",
    ],
    "CARE AND CURE HOSPITAL OP": [
        "CHILAKA VENKAIAH",
    ],
    "HOUSEHOLD EXPENSES STORE @5TH LINE": [
        "SATYAM UPPU",
    ],
    "TRUPTI FOOD COURT": [
        "MANYAM RAMU",
    ],
    "FUEL": [
        "ADHOC ANURADHA TRADER",
        "SUBHAM FILLING CENTRE",
    ],
    "MEDICINE SHOP AT MIDDE CENTER": [
        "NARASIMHA MURTHY BITRA",
    ],
}

# Build the alias lookup automatically.
NORMALIZED_CONTACT_MAP = {}
for person, aliases in CONTACT_MAP.items():
    normalized_person = person.strip().upper()
    for alias in aliases:
        normalized_alias = alias.strip().upper()
        if normalized_alias:
            NORMALIZED_CONTACT_MAP[normalized_alias] = normalized_person


# ============================================================
# NOTIFICATIONS AND ERROR LOGGING
# ============================================================

def check_internet():
    try:
        requests.get("https://www.google.com", timeout=5)
        return True
    except Exception:
        return False


def send_notify(title, content):
    """Safely quote notification text for the Termux shell."""
    command = (
        "termux-notification "
        f"-t {shlex.quote(str(title))} "
        f"-c {shlex.quote(str(content))}"
    )
    result = os.system(command)
    if result != 0:
        print("Warning: Termux notification command failed.")


def log_sheet_error(sheet_name, error_details):
    """
    Log failures locally. No Google Sheet outside JULY2026+
    is accessed or modified for logging.
    """
    try:
        with open(error_log_file, "a", encoding="utf-8") as log:
            log.write(
                f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"Sheet/Stage: {sheet_name}\n"
                f"{error_details}\n"
                + "-" * 60 + "\n"
            )
    except Exception as log_error:
        print(f"Could not write error log: {log_error}")
        print(error_details)


# ============================================================
# CONTACT NAME NORMALIZATION
# ============================================================

def map_contact_name(raw_name: str) -> str:
    """
    Map a raw transaction name to a standard person/merchant name.
    Any detected UPI reference is preserved.
    """
    if not raw_name:
        return raw_name

    clean_raw = raw_name.strip().upper()
    upi_match = re.search(r"\(UPI\s*(\d+)\)", clean_raw, re.IGNORECASE)
    upi_part = f" {upi_match.group(0)}" if upi_match else ""

    base_search_str = re.sub(
        r"\(UPI\s*\d+\)", "", clean_raw, flags=re.IGNORECASE
    ).strip()
    handle_stripped = base_search_str.split("@")[0].strip()

    # Exact matches first.
    for candidate in (clean_raw, base_search_str, handle_stripped):
        if candidate in NORMALIZED_CONTACT_MAP:
            return NORMALIZED_CONTACT_MAP[candidate] + upi_part

    # Then substring matching, preferring the longest alias to reduce
    # accidental matches when one alias is contained in another.
    matching_aliases = []
    for alias, person in NORMALIZED_CONTACT_MAP.items():
        if alias and (
            alias in clean_raw
            or alias in base_search_str
            or alias in handle_stripped
        ):
            matching_aliases.append((alias, person))

    if matching_aliases:
        matching_aliases.sort(key=lambda item: len(item[0]), reverse=True)
        return matching_aliases[0][1] + upi_part

    return raw_name


# ============================================================
# AMOUNT AND PURPOSE EXTRACTION
# ============================================================

def extract_amount(body: str) -> float:
    match = re.search(
        r"(?:Rs\.?|INR)\s*([\d,]+\.?\d*)", body, re.IGNORECASE
    )
    return (
        round(float(match.group(1).replace(",", "")), 2)
        if match else None
    )


def extract_purpose(body: str, bank: str = None) -> str:
    body_clean = " ".join(body.split())
    body_low = body_clean.lower()
    name = "CARD/WITHDRAWN"

    if "upi user" in body_low and "credited" in body_low:
        ref_match = re.search(
            r"Ref\s*No\s*(\d+)", body_clean, re.IGNORECASE
        )
        name = (
            f"BOB UPI CREDIT ({ref_match.group(1)})"
            if ref_match else "BOB UPI CREDIT"
        )

    elif "cr. to" in body_low and "dr. from" in body_low:
        match = re.search(
            r"Cr\.\s+to\s+(.*?)(?=\.|\s+Ref:|\s+AvlBal|\s+AvlB|\s+Avl|$)",
            body_clean, re.IGNORECASE
        )
        if match:
            name = match.group(1).strip().upper()

    elif "thru neft" in body_low:
        match = re.search(
            r"\bby\s+(.*?)(?=\.\s*Total|\.\s*Avlbl|\.\s*Avl|$)",
            body_clean, re.IGNORECASE
        )
        if match:
            name = match.group(1).strip().upper()

    elif "deposited in cash" in body_low:
        name = "CASH DEPOSIT"

    elif "transferred from" in body_low and "to:" in body_low:
        match = re.search(
            r"to:\s*(.*?)(?=\.\s*Total|\.\s*Avlbl|\.\s*Avl|$)",
            body_clean, re.IGNORECASE
        )
        if match:
            name = match.group(1).strip().upper()

    elif "debited" in body_low or "sent" in body_low:
        if (
            "self" in body_low
            or "to self" in body_low
            or "6498" in body_low
        ):
            name = "A/C **6498 (UPI to BOB)"
        else:
            match = re.search(
                r"(?:To|Info:)\s+(.*?)(?=\s+On|\s+\d{2}/\d{2}/\d{2}|\s+Ref|\.\s+Avl|$)",
                body_clean, re.IGNORECASE
            )
            if match:
                name = match.group(1).strip().upper()
                name = re.sub(
                    r"^ACH\s+[D|C]-\s*", "", name, flags=re.IGNORECASE
                )
                name = re.sub(r"-\d+$", "", name).strip()

    elif "vpa" in body_low:
        match = re.search(
            r"VPA\s+(.*?)(?:\s*\(UPI\s*(\d+)\)|\s*\(|$)",
            body_clean, re.IGNORECASE
        )
        if match:
            vpa_name = match.group(1).strip().upper()
            upi_ref = (
                match.group(2)
                if match.lastindex >= 2 and match.group(2)
                else ""
            )
            name = (
                f"{vpa_name} (UPI {upi_ref})".strip()
                if upi_ref else vpa_name
            )

    elif "imps" in body_low and "-" in body_clean:
        match = re.search(
            r"For\s+.*?\s*-\s*(.*?)\s*-", body_clean, re.IGNORECASE
        )
        if match:
            name = match.group(1).strip().upper()

    elif any(word in body_low for word in ("credited", "received", "refund")):
        name = "CREDITED"

    # Existing HDFC card merchant extraction.
    if bank == "HDFC" and any(
        term in body_low for term in ("spent", "purchase", "card")
    ):
        merchant_match = re.search(
            r"\bAt\s+(.+?)\s+\bOn\b", body_clean, re.IGNORECASE
        )
        if merchant_match:
            merchant = merchant_match.group(1).strip(" ._-\t").upper()
            if merchant:
                name = merchant

    return map_contact_name(name)


def is_transaction_sms(body: str) -> bool:
    body_low = body.lower()
    ignore_terms = [
        "is due on", "will be debited", "keep your account funded",
        "emi alert", "reminder", "avoid charges", "re-presentation"
    ]
    if any(term in body_low for term in ignore_terms):
        return False

    keywords = [
        "credited", "received", "deposit", "refund", "cashback",
        "debited", "withdrawn", "spent", "purchase", "payment made",
        "transfer", "sent", "dr.", "cr."
    ]
    return any(word in body_low for word in keywords)


# ============================================================
# DATE, TIME AND SHEET HELPERS
# ============================================================

def get_month_sheet_name(dt: datetime) -> str:
    return f"{calendar.month_name[dt.month].upper()}{dt.year}"


def format_time_str(t_raw: str) -> str:
    clean_t = str(t_raw).strip()
    if len(clean_t) == 5 and clean_t.count(":") == 1:
        return f"{clean_t}:00"
    if len(clean_t) >= 8:
        return clean_t[:8]
    return clean_t


def parse_num(val) -> float:
    try:
        return round(float(str(val).replace(",", "").strip()), 2)
    except Exception:
        return 0.0


def parse_time_seconds(t_str: str) -> int:
    try:
        parts = t_str.strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) > 2 else 0
        return hour * 3600 + minute * 60 + second
    except Exception:
        return 0


def parse_sheet_date(sheet_name: str):
    match = re.match(r"^([A-Z]+)(\d{4})$", sheet_name.strip().upper())
    if not match:
        return (0, 0)

    month_str, year_str = match.groups()
    month_map = {
        month.upper(): index
        for index, month in enumerate(calendar.month_name)
        if month
    }
    return int(year_str), month_map.get(month_str, 0)


def is_sheet_july2026_or_later(sheet_name: str) -> bool:
    year, month = parse_sheet_date(sheet_name)
    return year > 2026 or (year == 2026 and month >= 7)


def is_sheet_may2026_or_later(sheet_name: str) -> bool:
    year, month = parse_sheet_date(sheet_name)
    return year > 2026 or (year == 2026 and month >= 5)


def is_sheet_august2026_or_later(sheet_name: str) -> bool:
    year, month = parse_sheet_date(sheet_name)
    return year > 2026 or (year == 2026 and month >= 8)


def process_purpose_tag_formatting(
    purpose: str,
    tag: str,
    sheet_name: str,
    is_xml_script_generated: bool = False
) -> str:
    purpose_clean = purpose.strip()
    tag_clean = tag.strip()

    if not is_sheet_may2026_or_later(sheet_name) or not tag_clean:
        return purpose_clean

    if tag_clean.upper() == "BIKE TAXI EARNING":
        if (
            not purpose_clean
            or is_xml_script_generated
            or "@" in purpose_clean
            or purpose_clean.startswith("BOB UPI CREDIT")
        ):
            return tag_clean

    if tag_clean.lower() not in purpose_clean.lower():
        return f"{purpose_clean} for {tag_clean}" if purpose_clean else tag_clean

    return purpose_clean


# ============================================================
# EXPENSE SUMMARY
# ============================================================

def calculate_expenses(merged_data):
    today_str = datetime.now().strftime("%d/%m")
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%d/%m")
    today_total = 0
    yesterday_total = 0
    month_total = 0

    data = [
        row for row in merged_data
        if len(row) >= 4 and row[0].strip()
    ]

    for index, row in enumerate(data):
        try:
            value = parse_num(row[3])
            if value < 0:
                previous_value = (
                    parse_num(data[index - 1][3]) if index > 0 else None
                )
                next_value = (
                    parse_num(data[index + 1][3])
                    if index < len(data) - 1 else None
                )

                # Preserve the original paired-value exclusion rule.
                if previous_value == abs(value) or next_value == abs(value):
                    continue

                month_total += value
                if row[0] == today_str:
                    today_total += value
                elif row[0] == yesterday_str:
                    yesterday_total += value
        except Exception:
            continue

    return abs(today_total), abs(yesterday_total), abs(month_total)


def get_today_summary(merged_data):
    """
    Today's expenses include all negative transactions dated today.
    Return the five latest transactions from today, debit or credit.
    """
    today_str = datetime.now().strftime("%d/%m")
    today_rows = [
        row for row in merged_data
        if len(row) >= 5 and row[0].strip() == today_str
    ]

    today_expenses = round(
        sum(
            abs(parse_num(row[3]))
            for row in today_rows
            if parse_num(row[3]) < 0
        ),
        2
    )

    today_rows.sort(
        key=lambda row: parse_time_seconds(format_time_str(row[1])),
        reverse=True
    )
    return today_expenses, today_rows[:5]


def display_balance(value):
    try:
        cleaned = str(value).replace(",", "").strip()
        return f"{float(cleaned):,.2f}"
    except (ValueError, TypeError):
        return str(value) if value not in (None, "") else "N/A"


# ============================================================
# MAIN TRANSACTION UPDATE
# ============================================================

def run_transaction_update():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    credentials = ServiceAccountCredentials.from_json_keyfile_name(
        json_file, scope
    )
    client = gspread.authorize(credentials)
    workbook = client.open_by_key(sheet_id)

    # Keep the existing behavior: select the latest XML file and delete
    # older XML backup files.
    xml_files = [
        filename for filename in os.listdir(backup_dir)
        if filename.endswith(".xml")
    ]
    latest_file = None

    if xml_files:
        latest_file = max(
            xml_files,
            key=lambda filename: os.path.getmtime(
                os.path.join(backup_dir, filename)
            )
        )
        for xml_file in xml_files:
            if xml_file != latest_file:
                try:
                    os.remove(os.path.join(backup_dir, xml_file))
                except Exception as delete_error:
                    print(
                        f"[!] Failed to delete older backup "
                        f"{xml_file}: {delete_error}"
                    )

    sms_items = []

    if latest_file:
        try:
            tree = ET.parse(os.path.join(backup_dir, latest_file))
            root = tree.getroot()

            for sms in root.findall("sms"):
                sender = sms.attrib.get("address", "").lower()
                body = sms.attrib.get("body", "")
                bank = None

                if is_transaction_sms(body):
                    if "hdfc" in sender or "hdfcbk" in sender:
                        bank = "HDFC"
                    elif "bob" in sender or "baroda" in sender:
                        bank = "BOB"

                if not bank:
                    continue

                dt = datetime.fromtimestamp(
                    int(sms.attrib.get("date", "0")) // 1000
                )
                if dt < datetime(2026, 3, 1):
                    continue

                amount = extract_amount(body)
                purpose = extract_purpose(body, bank=bank)
                if amount is None:
                    continue

                body_low = body.lower()
                is_debit = any(
                    word in body_low
                    for word in (
                        "debited", "spent", "withdrawn", "dr.",
                        "dr from", "transferred from"
                    )
                )
                is_credit = (
                    not is_debit
                    and any(
                        word in body_low
                        for word in (
                            "credited", "received", "deposit",
                            "refund", "cashback"
                        )
                    )
                )
                final_amount = round(
                    float(amount if is_credit else -amount), 2
                )

                sms_items.append({
                    "date": dt.strftime("%d/%m"),
                    "time": dt.strftime("%H:%M:%S"),
                    "mode": "ONLINE",
                    "amt": final_amount,
                    "purpose": purpose,
                    "tag": "",
                    "bank": bank,
                    "dt_obj": dt
                })

        except Exception:
            error_details = traceback.format_exc()
            print("ERROR WHILE READING OR PARSING XML:")
            print(error_details)
            log_sheet_error("XML PARSING", error_details)

    grouped_xml = {}
    for item in sms_items:
        sheet_name = get_month_sheet_name(item["dt_obj"])
        grouped_xml.setdefault(sheet_name, []).append(item)

    # Strict cutoff: no Google Sheet reads, clears, or writes for sheets
    # before JULY2026. Error logging is local to this device.
    all_worksheets = [worksheet.title for worksheet in workbook.worksheets()]
    target_sheet_names = {
        name
        for name in (set(grouped_xml) | set(all_worksheets))
        if re.match(r"^[A-Z]+\d{4}$", name.strip())
        and is_sheet_july2026_or_later(name)
    }

    updated_entries = []
    balances = None
    current_month = get_month_sheet_name(datetime.now())
    current_year = datetime.now().year
    final_merged = []

    for sheet_name in sorted(target_sheet_names, key=parse_sheet_date):
        if not is_sheet_july2026_or_later(sheet_name):
            continue

        xml_list = grouped_xml.get(sheet_name, [])

        try:
            worksheet = workbook.worksheet(sheet_name)
            all_values = worksheet.get_all_values()

            start_row = (
                7 if is_sheet_august2026_or_later(sheet_name) else 4
            )
            merged_list = []

            # STEP 1: Read existing rows and apply contact mapping.
            for row in all_values[start_row - 1:]:
                if (
                    len(row) >= 4
                    and row[0].strip()
                    and row[1].strip()
                    and row[3].strip()
                ):
                    date_value = row[0].strip()
                    time_value = format_time_str(row[1])
                    mode_value = row[2].strip()
                    amount_value = parse_num(row[3])
                    purpose_value = map_contact_name(
                        row[4].strip() if len(row) >= 5 else ""
                    )
                    tag_value = row[5].strip() if len(row) >= 6 else ""

                    purpose_lower = purpose_value.lower()
                    ignore_phrases = [
                        "avoid charges", "re-presentation", "is due on",
                        "will be debited", "emi alert"
                    ]
                    if any(
                        phrase in purpose_lower for phrase in ignore_phrases
                    ):
                        continue

                    bank_tag = (
                        "CASH" if mode_value.upper() == "CASH" else "HDFC"
                    )
                    if len(row) >= 9 and row[8].strip():
                        bank_tag = row[8].strip().upper()

                    merged_list.append([
                        date_value, time_value, mode_value, amount_value,
                        purpose_value, tag_value, bank_tag
                    ])

            # STEP 2: Merge fresh XML transactions.
            for item in xml_list:
                xml_date = item["date"]
                xml_time = item["time"]
                xml_amount = item["amt"]
                xml_seconds = parse_time_seconds(xml_time)

                xml_ref_match = re.search(
                    r"\(UPI\s*(\d+)\)", item["purpose"], re.IGNORECASE
                )
                xml_ref = xml_ref_match.group(1) if xml_ref_match else ""
                matched = False

                for row in merged_list:
                    r