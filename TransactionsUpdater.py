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
#
# "PERSON'S STANDARD NAME": [
#     "alias1",
#     "alias2",
#     "alias3"
# ]
#
# You can add different names, phone numbers, UPI IDs,
# merchant names, etc. under the same standard name.
#
# IMPORTANT:
# For changing UPI domains, you can add:
#
# "*7396@AXL"
#
# The script will also recognize:
#
# "*7396@YBL"
# "*7396@OKAXIS"
# "*7396@ANYTHING"
#
# because it also checks the stable part before @.
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
        "*7396@",
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


# ============================================================
# CONTACT LOOKUP BUILDING
# ============================================================

NORMALIZED_CONTACT_MAP = {}
UPI_PREFIX_CONTACT_MAP = {}


def normalize_contact_text(value: str) -> str:
    """Normalize contact/alias text for matching."""
    if not value:
        return ""

    value = value.strip().upper()

    # Remove UPI reference portion.
    value = re.sub(
        r"\s*\(UPI\s*\d+\)",
        "",
        value,
        flags=re.IGNORECASE
    )

    # Normalize multiple spaces.
    value = " ".join(value.split())

    return value.strip()


for person, aliases in CONTACT_MAP.items():

    normalized_person = person.strip().upper()

    for alias in aliases:

        normalized_alias = normalize_contact_text(alias)

        if not normalized_alias:
            continue

        # Normal complete alias lookup.
        NORMALIZED_CONTACT_MAP[normalized_alias] = normalized_person

        # If the alias contains @, also store the stable part before @.
        #
        # Example:
        # *7396@AXL  -> *7396
        # SUNILKINNERA123@AXL -> SUNILKINNERA123
        #
        # This allows matching when only the UPI domain changes.
        if "@" in normalized_alias:

            upi_prefix = normalized_alias.split("@", 1)[0].strip()

            if upi_prefix:
                UPI_PREFIX_CONTACT_MAP[
                    upi_prefix
                ] = normalized_person


# ============================================================
# NOTIFICATIONS AND ERROR LOGGING
# ============================================================

def check_internet():

    try:
        requests.get(
            "https://www.google.com",
            timeout=5
        )
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
    Log failures locally.
    No Google Sheet outside JULY2026+ is accessed
    or modified for logging.
    """

    try:

        with open(
            error_log_file,
            "a",
            encoding="utf-8"
        ) as log:

            log.write(
                f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"Sheet/Stage: {sheet_name}\n"
                f"{error_details}\n"
                + "-" * 60
                + "\n"
            )

    except Exception as log_error:

        print(f"Could not write error log: {log_error}")
        print(error_details)


# ============================================================
# CONTACT NAME NORMALIZATION AND MAPPING
# ============================================================

def map_contact_name(raw_name: str) -> str:
    """
    Map a raw transaction name to a standard person/merchant name.

    Supports:
    1. Exact alias matching
    2. Matching without UPI reference number
    3. Matching the stable part before @ for changing UPI domains
    4. Partial/substring matching
    5. Preserving detected UPI reference numbers
    """

    if not raw_name:
        return raw_name

    original_raw = raw_name.strip()

    clean_raw = normalize_contact_text(original_raw)

    # Preserve UPI reference if present.
    upi_match = re.search(
        r"\(UPI\s*(\d+)\)",
        original_raw,
        re.IGNORECASE
    )

    upi_part = (
        f" (UPI {upi_match.group(1)})"
        if upi_match
        else ""
    )

    # Remove UPI reference from search text.
    base_search_str = re.sub(
        r"\s*\(UPI\s*\d+\)",
        "",
        clean_raw,
        flags=re.IGNORECASE
    ).strip()

    # Full value before @.
    #
    # Example:
    # *7396@AXL -> *7396
    # *7396@YBL -> *7396
    #
    handle_prefix = (
        base_search_str.split("@", 1)[0].strip()
        if "@" in base_search_str
        else ""
    )

    # --------------------------------------------------------
    # STEP 1: Exact matching
    # --------------------------------------------------------

    exact_candidates = [
        clean_raw,
        base_search_str
    ]

    for candidate in exact_candidates:

        if candidate in NORMALIZED_CONTACT_MAP:

            return (
                NORMALIZED_CONTACT_MAP[candidate]
                + upi_part
            )

    # --------------------------------------------------------
    # STEP 2: UPI stable-prefix matching
    #
    # Example:
    #
    # Alias in CONTACT_MAP:
    # *7396@AXL
    #
    # Incoming:
    # *7396@YBL (UPI 605901347690)
    #
    # Both use:
    # *7396
    # --------------------------------------------------------

    if handle_prefix:

        if handle_prefix in UPI_PREFIX_CONTACT_MAP:

            return (
                UPI_PREFIX_CONTACT_MAP[handle_prefix]
                + upi_part
            )

    # --------------------------------------------------------
    # STEP 3: Longest alias substring matching
    #
    # Longest alias is preferred to reduce accidental matches.
    # --------------------------------------------------------

    matching_aliases = []

    for alias, person in NORMALIZED_CONTACT_MAP.items():

        if not alias:
            continue

        if (
            alias in clean_raw
            or alias in base_search_str
        ):
            matching_aliases.append(
                (alias, person)
            )

    if matching_aliases:

        matching_aliases.sort(
            key=lambda item: len(item[0]),
            reverse=True
        )

        return (
            matching_aliases[0][1]
            + upi_part
        )

    # --------------------------------------------------------
    # STEP 4: Partial UPI prefix matching
    # --------------------------------------------------------

    if handle_prefix:

        prefix_matches = []

        for prefix, person in UPI_PREFIX_CONTACT_MAP.items():

            # Avoid extremely short prefixes.
            if len(prefix) < 4:
                continue

            if (
                prefix == handle_prefix
                or prefix in handle_prefix
                or handle_prefix in prefix
            ):
                prefix_matches.append(
                    (prefix, person)
                )

        if prefix_matches:

            prefix_matches.sort(
                key=lambda item: len(item[0]),
                reverse=True
            )

            return (
                prefix_matches[0][1]
                + upi_part
            )

    return original_raw


# ============================================================
# AMOUNT AND PURPOSE EXTRACTION
# ============================================================

def extract_amount(body: str) -> float:

    match = re.search(
        r"(?:Rs\.?|INR)\s*([\d,]+\.?\d*)",
        body,
        re.IGNORECASE
    )

    return (
        round(
            float(
                match.group(1).replace(",", "")
            ),
            2
        )
        if match
        else None
    )


def extract_purpose(body: str, bank: str = None) -> str:

    body_clean = " ".join(body.split())
    body_low = body_clean.lower()

    name = "CARD/WITHDRAWN"

    if "upi user" in body_low and "credited" in body_low:

        ref_match = re.search(
            r"Ref\s*No\s*(\d+)",
            body_clean,
            re.IGNORECASE
        )

        name = (
            f"BOB UPI CREDIT ({ref_match.group(1)})"
            if ref_match
            else "BOB UPI CREDIT"
        )

    elif "cr. to" in body_low and "dr. from" in body_low:

        match = re.search(
            r"Cr\.\s+to\s+(.*?)(?=\.|\s+Ref:|\s+AvlBal|\s+AvlB|\s+Avl|$)",
            body_clean,
            re.IGNORECASE
        )

        if match:
            name = match.group(1).strip().upper()

    elif "thru neft" in body_low:

        match = re.search(
            r"\bby\s+(.*?)(?=\.\s*Total|\.\s*Avlbl|\.\s*Avl|$)",
            body_clean,
            re.IGNORECASE
        )

        if match:
            name = match.group(1).strip().upper()

    elif "deposited in cash" in body_low:

        name = "CASH DEPOSIT"

    elif "transferred from" in body_low and "to:" in body_low:

        match = re.search(
            r"to:\s*(.*?)(?=\.\s*Total|\.\s*Avlbl|\.\s*Avl|$)",
            body_clean,
            re.IGNORECASE
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
                body_clean,
                re.IGNORECASE
            )

            if match:

                name = match.group(1).strip().upper()

                name = re.sub(
                    r"^ACH\s+[D|C]-\s*",
                    "",
                    name,
                    flags=re.IGNORECASE
                )

                name = re.sub(
                    r"-\d+$",
                    "",
                    name
                ).strip()

    elif "vpa" in body_low:

        match = re.search(
            r"VPA\s+(.*?)(?:\s*\(UPI\s*(\d+)\)|\s*\(|$)",
            body_clean,
            re.IGNORECASE
        )

        if match:

            vpa_name = match.group(1).strip().upper()

            upi_ref = (
                match.group(2)
                if match.lastindex >= 2
                and match.group(2)
                else ""
            )

            name = (
                f"{vpa_name} (UPI {upi_ref})".strip()
                if upi_ref
                else vpa_name
            )

    elif "imps" in body_low and "-" in body_clean:

        match = re.search(
            r"For\s+.*?\s*-\s*(.*?)\s*-",
            body_clean,
            re.IGNORECASE
        )

        if match:
            name = match.group(1).strip().upper()

    elif any(
        word in body_low
        for word in (
            "credited",
            "received",
            "refund"
        )
    ):

        name = "CREDITED"

    # Existing HDFC card merchant extraction.
    if (
        bank == "HDFC"
        and any(
            term in body_low
            for term in (
                "spent",
                "purchase",
                "card"
            )
        )
    ):

        merchant_match = re.search(
            r"\bAt\s+(.+?)\s+\bOn\b",
            body_clean,
            re.IGNORECASE
        )

        if merchant_match:

            merchant = merchant_match.group(1).strip(
                " ._-\t"
            ).upper()

            if merchant:
                name = merchant

    return map_contact_name(name)


def is_transaction_sms(body: str) -> bool:

    body_low = body.lower()

    ignore_terms = [
        "is due on",
        "will be debited",
        "keep your account funded",
        "emi alert",
        "reminder",
        "avoid charges",
        "re-presentation"
    ]

    if any(
        term in body_low
        for term in ignore_terms
    ):
        return False

    keywords = [
        "credited",
        "received",
        "deposit",
        "refund",
        "cashback",
        "debited",
        "withdrawn",
        "spent",
        "purchase",
        "payment made",
        "transfer",
        "sent",
        "dr.",
        "cr."
    ]

    return any(
        word in body_low
        for word in keywords
    )


# ============================================================
# DATE, TIME AND SHEET HELPERS
# ============================================================

def get_month_sheet_name(dt: datetime) -> str:

    return (
        f"{calendar.month_name[dt.month].upper()}"
        f"{dt.year}"
    )


def format_time_str(t_raw: str) -> str:

    clean_t = str(t_raw).strip()

    if (
        len(clean_t) == 5
        and clean_t.count(":") == 1
    ):
        return f"{clean_t}:00"

    if len(clean_t) >= 8:
        return clean_t[:8]

    return clean_t


def parse_num(val) -> float:

    try:
        return round(
            float(
                str(val)
                .replace(",", "")
                .strip()
            ),
            2
        )

    except Exception:
        return 0.0


def parse_time_seconds(t_str: str) -> int:

    try:

        parts = t_str.strip().split(":")

        hour = int(parts[0])
        minute = int(parts[1])

        second = (
            int(parts[2])
            if len(parts) > 2
            else 0
        )

        return (
            hour * 3600
            + minute * 60
            + second
        )

    except Exception:
        return 0


def parse_sheet_date(sheet_name: str):

    match = re.match(
        r"^([A-Z]+)(\d{4})$",
        sheet_name.strip().upper()
    )

    if not match:
        return (0, 0)

    month_str, year_str = match.groups()

    month_map = {
        month.upper(): index
        for index, month in enumerate(
            calendar.month_name
        )
        if month
    }

    return (
        int(year_str),
        month_map.get(month_str, 0)
    )


def is_sheet_july2026_or_later(sheet_name: str) -> bool:

    year, month = parse_sheet_date(sheet_name)

    return (
        year > 2026
        or (
            year == 2026
            and month >= 7
        )
    )


def is_sheet_may2026_or_later(sheet_name: str) -> bool:

    year, month = parse_sheet_date(sheet_name)

    return (
        year > 2026
        or (
            year == 2026
            and month >= 5
        )
    )


def is_sheet_august2026_or_later(sheet_name: str) -> bool:

    year, month = parse_sheet_date(sheet_name)

    return (
        year > 2026
        or (
            year == 2026
            and month >= 8
        )
    )


def process_purpose_tag_formatting(
    purpose: str,
    tag: str,
    sheet_name: str,
    is_xml_script_generated: bool = False
) -> str:

    purpose_clean = purpose.strip()
    tag_clean = tag.strip()

    if (
        not is_sheet_may2026_or_later(sheet_name)
        or not tag_clean
    ):
        return purpose_clean

    if tag_clean.upper() == "BIKE TAXI EARNING":

        if (
            not purpose_clean
            or is_xml_script_generated
            or "@" in purpose_clean
            or purpose_clean.startswith(
                "BOB UPI CREDIT"
            )
        ):
            return tag_clean

    if tag_clean.lower() not in purpose_clean.lower():

        return (
            f"{purpose_clean} for {tag_clean}"
            if purpose_clean
            else tag_clean
        )

    return purpose_clean


# ============================================================
# EXPENSE SUMMARY
# ============================================================

def calculate_expenses(merged_data):

    today_str = datetime.now().strftime("%d/%m")

    yesterday_str = (
        datetime.now()
        - timedelta(days=1)
    ).strftime("%d/%m")

    today_total = 0
    yesterday_total = 0
    month_total = 0

    data = [
        row
        for row in merged_data
        if len(row) >= 4
        and row[0].strip()
    ]

    for index, row in enumerate(data):

        try:

            value = parse_num(row[3])

            if value < 0:

                previous_value = (
                    parse_num(data[index - 1][3])
                    if index > 0
                    else None
                )

                next_value = (
                    parse_num(data[index + 1][3])
                    if index < len(data) - 1
                    else None
                )

                # Preserve original paired-value exclusion rule.
                if (
                    previous_value == abs(value)
                    or next_value == abs(value)
                ):
                    continue

                month_total += value

                if row[0] == today_str:
                    today_total += value

                elif row[0] == yesterday_str:
                    yesterday_total += value

        except Exception:
            continue

    return (
        abs(today_total),
        abs(yesterday_total),
        abs(month_total)
    )


def get_today_summary(merged_data):

    """
    Today's expenses include all negative transactions dated today.
    Return the five latest transactions from today,
    debit or credit.
    """

    today_str = datetime.now().strftime("%d/%m")

    today_rows = [
        row
        for row in merged_data
        if len(row) >= 5
        and row[0].strip() == today_str
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
        key=lambda row: parse_time_seconds(
            format_time_str(row[1])
        ),
        reverse=True
    )

    return (
        today_expenses,
        today_rows[:5]
    )


def display_balance(value):

    try:

        cleaned = str(value).replace(
            ",",
            ""
        ).strip()

        return f"{float(cleaned):,.2f}"

    except (ValueError, TypeError):

        return (
            str(value)
            if value not in (None, "")
            else "N/A"
        )


# ============================================================
# MAIN TRANSACTION UPDATE
# ============================================================

def run_transaction_update():

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    credentials = (
        ServiceAccountCredentials
        .from_json_keyfile_name(
            json_file,
            scope
        )
    )

    client = gspread.authorize(credentials)

    workbook = client.open_by_key(sheet_id)

    # Keep existing behavior:
    # Select latest XML file and delete older XML backups.
    xml_files = [
        filename
        for filename in os.listdir(backup_dir)
        if filename.endswith(".xml")
    ]

    latest_file = None

    if xml_files:

        latest_file = max(
            xml_files,
            key=lambda filename: os.path.getmtime(
                os.path.join(
                    backup_dir,
                    filename
                )
            )
        )

        for xml_file in xml_files:

            if xml_file != latest_file:

                try:

                    os.remove(
                        os.path.join(
                            backup_dir,
                            xml_file
                        )
                    )

                except Exception as delete_error:

                    print(
                        f"[!] Failed to delete older backup "
                        f"{xml_file}: {delete_error}"
                    )

    sms_items = []

    if latest_file:

        try:

            tree = ET.parse(
                os.path.join(
                    backup_dir,
                    latest_file
                )
            )

            root = tree.getroot()

            for sms in root.findall("sms"):

                sender = sms.attrib.get(
                    "address",
                    ""
                ).lower()

                body = sms.attrib.get(
                    "body",
                    ""
                )

                bank = None

                if is_transaction_sms(body):

                    if (
                        "hdfc" in sender
                        or "hdfcbk" in sender
                    ):
                        bank = "HDFC"

                    elif (
                        "bob" in sender
                        or "baroda" in sender
                    ):
                        bank = "BOB"

                if not bank:
                    continue

                dt = datetime.fromtimestamp(
                    int(
                        sms.attrib.get(
                            "date",
                            "0"
                        )
                    ) // 1000
                )

                if dt < datetime(2026, 3, 1):
                    continue

                amount = extract_amount(body)

                purpose = extract_purpose(
                    body,
                    bank=bank
                )

                if amount is None:
                    continue

                body_low = body.lower()

                is_debit = any(
                    word in body_low
                    for word in (
                        "debited",
                        "spent",
                        "withdrawn",
                        "dr.",
                        "dr from",
                        "transferred from"
                    )
                )

                is_credit = (
                    not is_debit
                    and any(
                        word in body_low
                        for word in (
                            "credited",
                            "received",
                            "deposit",
                            "refund",
                            "cashback"
                        )
                    )
                )

                final_amount = round(
                    float(
                        amount
                        if is_credit
                        else -amount
                    ),
                    2
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

            print(
                "ERROR WHILE READING OR PARSING XML:"
            )

            print(error_details)

            log_sheet_error(
                "XML PARSING",
                error_details
            )

    grouped_xml = {}

    for item in sms_items:

        sheet_name = get_month_sheet_name(
            item["dt_obj"]
        )

        grouped_xml.setdefault(
            sheet_name,
            []
        ).append(item)

    # Strict cutoff:
    # No Google Sheet reads, clears, or writes
    # before JULY2026.

    all_worksheets = [
        worksheet.title
        for worksheet in workbook.worksheets()
    ]

    target_sheet_names = {
        name
        for name in (
            set(grouped_xml)
            | set(all_worksheets)
        )
        if re.match(
            r"^[A-Z]+\d{4}$",
            name.strip()
        )
        and is_sheet_july2026_or_later(name)
    }

    updated_entries = []

    balances = None

    current_month = get_month_sheet_name(
        datetime.now()
    )

    current_year = datetime.now().year

    final_merged = []

    for sheet_name in sorted(
        target_sheet_names,
        key=parse_sheet_date
    ):

        if not is_sheet_july2026_or_later(sheet_name):
            continue

        xml_list = grouped_xml.get(
            sheet_name,
            []
        )

        try:

            worksheet = workbook.worksheet(
                sheet_name
            )

            all_values = worksheet.get_all_values()

            start_row = (
                7
                if is_sheet_august2026_or_later(
                    sheet_name
                )
                else 4
            )

            merged_list = []

            # =================================================
            # STEP 1:
            # Read existing rows and apply contact mapping.
            # =================================================

            for row in all_values[start_row - 1:]:

                if (
                    len(row) >= 4
                    and row[0].strip()
                    and row[1].strip()
                    and row[3].strip()
                ):

                    date_value = row[0].strip()

                    time_value = format_time_str(
                        row[1]
                    )

                    mode_value = row[2].strip()

                    amount_value = parse_num(
                        row[3]
                    )

                    purpose_value = map_contact_name(
                        row[4].strip()
                        if len(row) >= 5
                        else ""
                    )

                    tag_value = (
                        row[5].strip()
                        if len(row) >= 6
                        else ""
                    )

                    purpose_lower = purpose_value.lower()

                    ignore_phrases = [
                        "avoid charges",
                        "re-presentation",
                        "is due on",
                        "will be debited",
                        "emi alert"
                    ]

                    if any(
                        phrase in purpose_lower
                        for phrase in ignore_phrases
                    ):
                        continue

                    bank_tag = (
                        "CASH"
                        if mode_value.upper() == "CASH"
                        else "HDFC"
                    )

                    if (
                        len(row) >= 9
                        and row[8].strip()
                    ):
                        bank_tag = row[8].strip().upper()

                    merged_list.append([
                        date_value,
                        time_value,
                        mode_value,
                        amount_value,
                        purpose_value,
                        tag_value,
                        bank_tag
                    ])

            # =================================================
            # STEP 2:
            # Merge fresh XML transactions.
            # =================================================

            for item in xml_list:

                xml_date = item["date"]
                xml_time = item["time"]
                xml_amount = item["amt"]

                xml_seconds = parse_time_seconds(
                    xml_time
                )

                xml_ref_match = re.search(
                    r"\(UPI\s*(\d+)\)",
                    item["purpose"],
                    re.IGNORECASE
                )

                xml_ref = (
                    xml_ref_match.group(1)
                    if xml_ref_match
                    else ""
                )

                matched = False

                for row in merged_list:

                    row_date = row[0]

                    row_seconds = parse_time_seconds(
                        row[1]
                    )

                    row_amount = parse_num(
                        row[3]
                    )

                    row_ref_match = re.search(
                        r"\(UPI\s*(\d+)\)",
                        row[4],
                        re.IGNORECASE
                    )

                    row_ref = (
                        row_ref_match.group(1)
                        if row_ref_match
                        else ""
                    )

                    same_sign = (
                        (
                            row_amount > 0
                            and xml_amount > 0
                        )
                        or (
                            row_amount < 0
                            and xml_amount < 0
                        )
                        or (
                            row_amount == xml_amount == 0
                        )
                    )

                    amount_difference = abs(
                        abs(row_amount)
                        - abs(xml_amount)
                    )

                    reference_match = bool(
                        xml_ref
                        and row_ref
                        and xml_ref == row_ref
                    )

                    if (
                        row_date == xml_date
                        and same_sign
                    ):

                        if (
                            reference_match
                            or (
                                abs(
                                    row_seconds
                                    - xml_seconds
                                ) <= 1
                                and amount_difference < 0.001
                            )
                        ):

                            matched = True

                            row[1] = xml_time
                            row[3] = xml_amount
                            row[6] = item["bank"]

                            if (
                                item["purpose"]
                                and (
                                    not row[4]
                                    or row[4]
                                    .strip()
                                    .upper()
                                    == "CARD/WITHDRAWN"
                                )
                            ):
                                row[4] = item["purpose"]

                            if row[5]:

                                row[4] = (
                                    process_purpose_tag_formatting(
                                        row[4]
                                        or item["purpose"],
                                        row[5],
                                        sheet_name,
                                        is_xml_script_generated=True
                                    )
                                )

                            elif not row[4]:

                                row[4] = item["purpose"]

                            break

                if not matched:

                    merged_list.append([
                        xml_date,
                        xml_time,
                        item["mode"],
                        xml_amount,
                        item["purpose"],
                        item["tag"],
                        item["bank"]
                    ])

                    updated_entries.append(
                        f"• {xml_date} | "
                        f"Rs.{xml_amount:.2f} "
                        f"[{item['bank']}] -> "
                        f"{item['purpose']}"
                    )

            # =================================================
            # STEP 3:
            # Deduplicate.
            # =================================================

            final_unique = []

            for item in merged_list:

                date_value = item[0]
                time_value = item[1]

                amount_value = parse_num(
                    item[3]
                )

                purpose_value = item[4]

                seconds_value = parse_time_seconds(
                    time_value
                )

                upi_match = re.search(
                    r"\(UPI\s*(\d+)\)",
                    purpose_value,
                    re.IGNORECASE
                )

                upi_ref = (
                    upi_match.group(1)
                    if upi_match
                    else ""
                )

                if item[5]:

                    item[4] = (
                        process_purpose_tag_formatting(
                            item[4],
                            item[5],
                            sheet_name,
                            is_xml_script_generated=(
                                "@"
                                in item[4]
                                or item[4].startswith(
                                    "BOB UPI CREDIT"
                                )
                            )
                        )
                    )

                is_duplicate = False

                for existing in final_unique:

                    existing_date = existing[0]

                    existing_seconds = (
                        parse_time_seconds(
                            existing[1]
                        )
                    )

                    existing_amount = parse_num(
                        existing[3]
                    )

                    existing_ref_match = re.search(
                        r"\(UPI\s*(\d+)\)",
                        existing[4],
                        re.IGNORECASE
                    )

                    existing_ref = (
                        existing_ref_match.group(1)
                        if existing_ref_match
                        else ""
                    )

                    same_sign = (
                        (
                            existing_amount > 0
                            and amount_value > 0
                        )
                        or (
                            existing_amount < 0
                            and amount_value < 0
                        )
                        or (
                            existing_amount
                            == amount_value
                            == 0
                        )
                    )

                    if (
                        existing_date == date_value
                        and same_sign
                    ):

                        if (
                            upi_ref
                            and existing_ref
                            and upi_ref == existing_ref
                        ):

                            is_duplicate = True
                            break

                        time_difference = abs(
                            existing_seconds
                            - seconds_value
                        )

                        if (
                            time_difference <= 1
                            and abs(
                                abs(existing_amount)
                                - abs(amount_value)
                            ) <= 0.001
                        ):

                            if (
                                upi_ref
                                and existing_ref
                                and upi_ref != existing_ref
                            ):
                                continue

                            is_duplicate = True
                            break

                if not is_duplicate:

                    final_unique.append(item)

            # =================================================
            # STEP 4:
            # Chronological sort.
            # =================================================

            def sort_transaction(row):

                day, month = map(
                    int,
                    row[0].split("/")
                )

                hour, minute, second = map(
                    int,
                    format_time_str(
                        row[1]
                    ).split(":")
                )

                return datetime(
                    current_year,
                    month,
                    day,
                    hour,
                    minute,
                    second
                )

            final_unique.sort(
                key=sort_transaction
            )

            if sheet_name == current_month:

                final_merged = final_unique

            # =================================================
            # STEP 5:
            # Clear and rewrite A:F and source column I.
            # =================================================

            output_rows = []

            bank_tag_rows = []

            last_date = None

            for row in final_unique:

                if (
                    last_date
                    and last_date != row[0]
                ):

                    output_rows.append(
                        [""] * 6
                    )

                    bank_tag_rows.append(
                        [""]
                    )

                row[1] = format_time_str(
                    row[1]
                )

                row[3] = (
                    f"{parse_num(row[3]):.2f}"
                )

                output_rows.append(
                    row[:6]
                )

                mode_string = (
                    row[2]
                    .strip()
                    .upper()
                )

                source_value = (
                    "CASH"
                    if mode_string == "CASH"
                    else (
                        row[6]
                        if row[6]
                        else "HDFC"
                    )
                )

                bank_tag_rows.append([
                    source_value
                ])

                last_date = row[0]

            if output_rows:

                max_row = max(
                    len(all_values),
                    len(output_rows) + start_row
                )

                worksheet.batch_clear([
                    (
                        f"A{start_row}:"
                        f"F{max_row}"
                    ),
                    (
                        f"I{start_row}:"
                        f"I{max_row}"
                    )
                ])

                worksheet.update(
                    range_name=(
                        f"A{start_row}:"
                        f"F{start_row + len(output_rows) - 1}"
                    ),
                    values=output_rows,
                    value_input_option="USER_ENTERED"
                )

                worksheet.update(
                    range_name=(
                        f"I{start_row}:"
                        f"I{start_row + len(bank_tag_rows) - 1}"
                    ),
                    values=bank_tag_rows,
                    value_input_option="USER_ENTERED"
                )

            # =================================================
            # BALANCES
            # Read only from current month's sheet.
            # =================================================

            if sheet_name == current_month:

                balances = {
                    "hdfc": (
                        worksheet.acell("B1").value
                        or "N/A"
                    ),
                    "bob": (
                        worksheet.acell("E1").value
                        or "N/A"
                    ),
                    "cash": (
                        worksheet.acell("A1").value
                        or "N/A"
                    ),
                    "total": (
                        worksheet.acell("F1").value
                        or "N/A"
                    )
                }

        except Exception:

            error_details = traceback.format_exc()

            print(
                f"\n[{datetime.now()}] "
                f"ERROR PROCESSING ELIGIBLE SHEET: "
                f"{sheet_name}"
            )

            print(error_details)

            log_sheet_error(
                sheet_name,
                error_details
            )

            continue

    return (
        updated_entries,
        balances,
        final_merged
    )


# ============================================================
# COMPACT TERMUX NOTIFICATION
# ============================================================

def build_notification(
    balances,
    final_data,
    updates
):

    (
        today_expenses,
        today_transactions
    ) = get_today_summary(final_data)

    hdfc_balance = (
        balances.get("hdfc", "N/A")
        if balances
        else "N/A"
    )

    bob_balance = (
        balances.get("bob", "N/A")
        if balances
        else "N/A"
    )

    cash_balance = (
        balances.get("cash", "N/A")
        if balances
        else "N/A"
    )

    total_balance = (
        balances.get("total", "N/A")
        if balances
        else "N/A"
    )

    # Two balances per line to save notification space.
    lines = [

        f"HDFC: Rs.{display_balance(hdfc_balance)} | "
        f"BOB: Rs.{display_balance(bob_balance)}",

        f"CASH: Rs.{display_balance(cash_balance)} | "
        f"TOTAL: Rs.{display_balance(total_balance)}",

        f"TODAY'S EXPENSES: "
        f"Rs.{today_expenses:.2f}",
    ]

    # No separate heading for recent transactions.
    for row in today_transactions[:5]:

        time_text = format_time_str(
            row[1]
        )[:5]

        amount = parse_num(
            row[3]
        )

        purpose = (
            row[4]
            or "Transaction"
        )

        lines.append(
            f"{time_text} | "
            f"Rs.{amount:.2f} | "
            f"{purpose}"
        )

    if updates:

        lines.append("")

        lines.append(
            "NEW TRANSACTIONS ADDED:"
        )

        for entry in updates[-3:]:

            lines.append(entry)

        if len(updates) > 3:

            lines.append(
                f"...and "
                f"{len(updates) - 3} more."
            )

    return "\n".join(lines)


# ============================================================
# SCRIPT ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        if check_internet():

            (
                updates,
                balances,
                final_data
            ) = run_transaction_update()

            if final_data or balances:

                notification_content = (
                    build_notification(
                        balances,
                        final_data,
                        updates
                    )
                )

                send_notify(
                    "SMS Sync Engine: Active",
                    notification_content
                )

            else:

                print(
                    f"[{datetime.now()}]: "
                    "Sync checked. "
                    "No current-month data or balances."
                )

        else:

            print(
                f"[{datetime.now()}]: "
                "Device offline; "
                "skipping spreadsheet operations."
            )

    except Exception:

        error_details = traceback.format_exc()

        print(
            f"\n[{datetime.now()}] "
            "CRITICAL GLOBAL SCRIPT ERROR:"
        )

        print(error_details)

        log_sheet_error(
            "GLOBAL",
            error_details
        )
