"""
check_vendor_replies.py
------------------------
Connects to Gmail via IMAP, reads unread vendor reply emails,
parses quote details from the body (or a PDF attachment if present),
and inserts them into the Supabase "quotes" table.

Usage:
    python check_vendor_replies.py

    # Dry-run (parse & print without writing to Supabase):
    python check_vendor_replies.py --dry-run

Requirements:
    pip install python-dotenv supabase PyMuPDF
"""

import argparse
import email
import email.header
import imaplib
import re
import sys
from email.policy import default as email_default_policy

from dotenv import load_dotenv
import os
from supabase import create_client

# PyMuPDF for extracting text from PDF attachments
import fitz  # pip install PyMuPDF

# ── Load credentials ───────────────────────────────────────────────────────────
load_dotenv()

EMAIL_ADDRESS  = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
SUPABASE_URL   = os.getenv("SUPABASE_URL", "https://jhsyqlquulhlvyvtthtd.supabase.co")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY", "sb_publishable_OPNQ0_yz4BvigeFV3WZVaw_EQkoOYrh")

IMAP_HOST   = "imap.gmail.com"
IMAP_PORT   = 993
IMAP_FOLDER = "INBOX"


# ── Supabase ───────────────────────────────────────────────────────────────────
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ── IMAP helpers ───────────────────────────────────────────────────────────────
def connect_imap() -> imaplib.IMAP4_SSL:
    """Return an authenticated IMAP connection."""
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        raise ValueError(
            "EMAIL_ADDRESS and EMAIL_APP_PASSWORD must be set in your .env file."
        )
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    print(f"Connected to {IMAP_HOST}:{IMAP_PORT} as {EMAIL_ADDRESS}")
    return mail


def fetch_unread_emails(mail: imaplib.IMAP4_SSL) -> list:
    """
    Return a list of dicts, one per unread message:
      { "uid": str, "subject": str, "sender": str, "body": str, "pdf_bytes": bytes|None }
    Marks each fetched message as read (\\Seen).
    """
    select_status, select_data = mail.select(IMAP_FOLDER, readonly=False)
    total_emails = select_data[0].decode("utf-8", errors="replace") if select_data and select_data[0] else "0"

    search_criteria = "UNSEEN"
    search_status, uid_data = mail.uid("search", None, search_criteria)

    print("=" * 70)
    print("  [DEBUG IMAP SEARCH]")
    print(f"  Folder                 : {IMAP_FOLDER} (select status: {select_status})")
    print(f"  1. Total emails        : {total_emails} (read and unread combined)")
    print(f"  2. Search criteria/cmd : mail.uid('search', None, '{search_criteria}')")
    print(f"  3. Raw server response : status={search_status}, data={uid_data}")
    print("=" * 70)

    uids = uid_data[0].split() if uid_data and uid_data[0] else []

    if not uids:
        print("No unread emails found.")
        return []

    print(f"Found {len(uids)} unread email(s).\n")
    results = []

    for idx, uid in enumerate(uids, 1):
        _, msg_data = mail.uid("fetch", uid, "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw, policy=email_default_policy)

        raw_from  = msg.get("From", "")
        subject   = _decode_header(msg["Subject"] or "")
        sender    = _decode_header(raw_from or "")
        extracted_email = extract_sender_email(sender)
        body      = _extract_text_body(msg)
        pdf_bytes = _extract_pdf_attachment(msg)

        print(f"  [EMAIL #{idx}] IMAP UID: {uid.decode()}")
        print(f"    1. Raw 'From' Header : {repr(raw_from)}")
        print(f"    2. Extracted Email   : {repr(extracted_email)}")
        print(f"    3. Subject           : {repr(subject)}")

        results.append({
            "uid":       uid.decode(),
            "subject":   subject,
            "sender":    sender,
            "raw_from":  raw_from,
            "body":      body,
            "pdf_bytes": pdf_bytes,
        })

    return results


def _decode_header(value: str) -> str:
    """Decode RFC2047-encoded email header values."""
    parts = email.header.decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _extract_text_body(msg) -> str:
    """Walk MIME parts and return the first plain-text body found."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                return part.get_content()
        # Fallback: try HTML if no plain part
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html = part.get_content()
                # Strip tags crudely
                return re.sub(r"<[^>]+>", " ", html)
    else:
        return msg.get_content()
    return ""


def _extract_pdf_attachment(msg) -> bytes | None:
    """
    Walk MIME parts looking for the first PDF attachment.
    Returns the raw bytes of the PDF, or None if no PDF found.
    """
    for part in msg.walk():
        ct = part.get_content_type()
        cd = str(part.get("Content-Disposition", ""))
        filename = part.get_filename("") or ""
        # Accept parts that are explicitly PDF or whose filename ends in .pdf
        if ct == "application/pdf" or filename.lower().endswith(".pdf"):
            payload = part.get_payload(decode=True)
            if payload:
                return payload
    return None


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extract and concatenate all text from a PDF given as raw bytes.
    Uses PyMuPDF (fitz).  Returns empty string on failure.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages_text = []
        for page in doc:
            pages_text.append(page.get_text())
        doc.close()
        return "\n".join(pages_text)
    except Exception as exc:
        print(f"  [WARN] Could not extract text from PDF: {exc}")
        return ""


# ── Quote parser ───────────────────────────────────────────────────────────────
def extract_rfq_id(subject: str) -> int | None:
    """
    Extract RFQ ID from a subject line like:
      "Re: Request for Quotation - RFQ-5: Dell Laptops"
    Returns None if no match.
    """
    match = re.search(r"RFQ-(\d+)", subject, re.IGNORECASE)
    return int(match.group(1)) if match else None


def extract_sender_email(from_header: str) -> str:
    """Pull the raw email address out of a 'From:' header string."""
    match = re.search(r"<([^>]+)>", from_header)
    if match:
        return match.group(1).strip()
    match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", from_header)
    if match:
        return match.group(0).strip()
    return from_header.strip()


def extract_vendor_name(from_header: str) -> str:
    """
    Use the display name if present ("Acme Inc <a@acme.com>"),
    otherwise derive a readable name from the email address domain.
    """
    # Display name before angle-bracket
    match = re.match(r'^"?([^"<]+?)"?\s*<', from_header)
    if match:
        name = match.group(1).strip()
        if name:
            return name

    # Fallback: use the part before @ in the email address
    addr = extract_sender_email(from_header)
    local = addr.split("@")[0]
    # Convert dots/underscores/plus to spaces and title-case
    return re.sub(r"[._+]", " ", local).title()


def parse_quote_from_body(body: str, quantity: int | None = None) -> dict:
    """
    Extract structured quote fields from free-text email body or PDF text using regex.
    Handles multiple formats:

    1. Same-line format (with or without colon/dash, optional currency code/symbol, case-insensitive):
        UNIT PRICE INR 615
        BULK PRICE INR 123000
        WARRANTY 2 years standard
        PAYMENT TERMS Net 25
        DELIVERY DAYS 8 days

    2. Colon-separated format:
        Unit Price   : $950
        Bulk Price   : $190000
        Warranty     : 3 years on-site
        Payment Terms: Net 30
        Delivery Days: 14

    3. Table-style format (label on one line, value on the very next non-empty line):
        Unit Price
        950
        Bulk Price
        190000

    Returns a dict with keys: price, unit_price, bulk_price, warranty, payment_terms, delivery_days.
    """
    CURRENCY_PREFIX = r'(?:(?:[A-Z]{3}|US\$|AU\$|CA\$|Rs\.?|[\$€£¥₹])\s*)*'
    lines = [l.strip() for l in body.splitlines()]

    def safe_int(raw: str | None) -> int | None:
        if not raw:
            return None
        cleaned = re.sub(r'[^\d.]', '', raw)
        try:
            return round(float(cleaned))
        except ValueError:
            return None

    def find_number(label_pattern: str) -> int | None:
        # 1. Same-line: optional colon/dash, optional currency prefix, followed by number
        same_line_re = re.compile(
            rf"^[*\-\s]*{label_pattern}\s*[:\-]?\s*{CURRENCY_PREFIX}([\d,]+(?:\.\d+)?)",
            re.IGNORECASE | re.MULTILINE
        )
        m = same_line_re.search(body)
        if m:
            return safe_int(m.group(1))

        # 2. Table-style: label on one line, number on next line
        label_re = re.compile(rf"^{label_pattern}\s*[:\-]?\s*$", re.IGNORECASE)
        num_re = re.compile(rf"^{CURRENCY_PREFIX}([\d,]+(?:\.\d+)?)", re.IGNORECASE)
        for i, line in enumerate(lines):
            if label_re.match(line):
                for j in range(i + 1, len(lines)):
                    cand = lines[j]
                    if not cand:
                        continue
                    nm = num_re.match(cand)
                    if nm:
                        return safe_int(nm.group(1))
                    break
        return None

    def find_text(label_pattern: str) -> str | None:
        # 1. Same-line with optional colon/dash, followed by non-empty text
        same_line_re = re.compile(
            rf"^[*\-\s]*{label_pattern}\s*[:\-]?\s*(\S.*)$",
            re.IGNORECASE | re.MULTILINE
        )
        for m in same_line_re.finditer(body):
            val = m.group(1).strip()
            if val:
                return val

        # 2. Table-style: label on one line, value on next line
        label_re = re.compile(rf"^{label_pattern}\s*[:\-]?\s*$", re.IGNORECASE)
        label_keywords = re.compile(
            r'^(unit[\s_]?price|bulk[\s_]?price|total[\s_]?price|price|warranty|'
            r'payment[\s_]?terms?|delivery[\s_]?days?|delivery)\b',
            re.IGNORECASE
        )
        for i, line in enumerate(lines):
            if label_re.match(line):
                for j in range(i + 1, len(lines)):
                    cand = lines[j]
                    if not cand:
                        continue
                    if label_keywords.match(cand):
                        break
                    return cand
        return None

    unit_price = find_number(r"(?:unit[\s_]?price|price[\s_]?per[\s_]?unit|unit[\s_]?rate)")
    bulk_price = find_number(r"(?:bulk[\s_]?price|total[\s_]?price|total[\s_]?amount|bulk[\s_]?amount|total[\s_]?cost|grand[\s_]?total)")
    general_price = find_number(r"price(?!\s*(?:per\b|\/|\bunit\b))")

    if bulk_price is not None:
        price = bulk_price
    elif general_price is not None:
        price = general_price
    elif unit_price is not None and quantity:
        price = round(unit_price * quantity)
    elif unit_price is not None:
        price = unit_price
    else:
        price = None

    warranty = find_text(r"warranty")
    payment_terms = find_text(r"payment[\s_]?terms?")

    # Delivery Days: handle inline or next-line
    raw_delivery = None
    deliv_same = re.search(
        r"^[*\-\s]*(?:delivery[\s_]?days?|delivery[\s_]?time|lead[\s_]?time|delivery[\s_]?period|delivery)\s*[:\-]?\s*(?:within\s*)?(\d+)",
        body,
        re.IGNORECASE | re.MULTILINE
    )
    if deliv_same:
        raw_delivery = deliv_same.group(1)
    else:
        deliv_label = re.compile(
            r"^(?:delivery[\s_]?days?|delivery[\s_]?time|lead[\s_]?time|delivery[\s_]?period|delivery)\s*[:\-]?\s*$",
            re.IGNORECASE
        )
        for i, line in enumerate(lines):
            if deliv_label.match(line):
                for j in range(i + 1, len(lines)):
                    cand = lines[j]
                    if not cand:
                        continue
                    dm = re.search(r'(\d+)', cand)
                    if dm:
                        raw_delivery = dm.group(1)
                    break
                if raw_delivery:
                    break

    delivery_days = int(raw_delivery) if raw_delivery else None

    return {
        "price":         price,
        "unit_price":    unit_price,
        "bulk_price":    bulk_price,
        "warranty":      warranty,
        "payment_terms": payment_terms,
        "delivery_days": delivery_days,
    }


# ── Supabase insert ────────────────────────────────────────────────────────────
def insert_quote(rfq_id: int, vendor_name: str, quote: dict, raw_text: str = "", status: str = "valid", dry_run: bool = False) -> dict | None:
    """Insert one quote row into Supabase, or print it if dry_run."""
    row = {
        "rfq_id":        rfq_id,
        "vendor_name":   vendor_name,
        "price":         quote.get("price"),
        "unit_price":    quote.get("unit_price"),
        "bulk_price":    quote.get("bulk_price") or quote.get("price"),
        "warranty":      quote.get("warranty") or ("Manual review needed" if status == "needs_review" else "Not specified"),
        "payment_terms": quote.get("payment_terms") or ("Manual review needed" if status == "needs_review" else "Not specified"),
        "delivery_days": quote.get("delivery_days") or 0,
        "status":        status,
        "raw_text":      raw_text,
    }

    if dry_run:
        print("  [DRY RUN] Would insert:", row)
        return row

    db = get_supabase()
    try:
        resp = db.table("quotes").insert(row).execute()
        if resp.data:
            print(f"  [OK]  Inserted quote id={resp.data[0]['id']} for vendor '{vendor_name}' (status={status})")
            return resp.data[0]
        else:
            print(f"  [ERR] Supabase insert failed: {resp}")
            return None
    except Exception as exc:
        err_msg = str(exc)
        # Fallback if 'status' or 'raw_text' columns are not yet created in Supabase table
        if any(col in err_msg for col in ("raw_text", "status", "PGRST204", "schema cache")):
            print(f"  [WARN] Supabase schema missing status/raw_text columns. Retrying insert without them...")
            fallback_row = {k: v for k, v in row.items() if k not in ("status", "raw_text")}
            try:
                resp = db.table("quotes").insert(fallback_row).execute()
                if resp.data:
                    print(f"  [OK]  Inserted quote id={resp.data[0]['id']} for vendor '{vendor_name}' (schema fallback)")
                    return resp.data[0]
            except Exception as retry_exc:
                print(f"  [ERR] Schema fallback insert also failed: {retry_exc}")
                return None
        print(f"  [ERR] Supabase insert failed: {exc}")
        return None


# ── Core reply processing logic ───────────────────────────────────────────────
def process_vendor_replies(dry_run: bool = False) -> dict:
    """
    Connects to IMAP, reads unread emails, parses quotes, and inserts into Supabase.
    Can be called directly or via server.py endpoint.
    """
    print(f"Connecting to {IMAP_HOST} ...")
    mail = connect_imap()
    try:
        messages = fetch_unread_emails(mail)
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    if not messages:
        print("Nothing to process.")
        return {
            "processed": 0,
            "skipped": 0,
            "total_unread": 0,
            "quotes": []
        }

    processed = 0
    skipped = 0
    inserted_quotes = []

    for msg in messages:
        print("=" * 70)
        print(f"  [DEBUG FROM HEADER] Exact raw 'From:' header: {msg['sender']}")
        print(f"  [DEBUG SUBJECT]     Subject: {msg['subject']}")

        # 1. Identify which RFQ this is a reply to
        rfq_id = extract_rfq_id(msg["subject"])
        if rfq_id is None:
            print("  [SKIP] Could not find RFQ-<id> in subject line.\n")
            skipped += 1
            continue

        # 2. Extract vendor identity
        vendor_name = extract_vendor_name(msg["sender"])
        sender_addr = extract_sender_email(msg["sender"])
        vendor_record = f"{vendor_name} <{sender_addr}>" if sender_addr and "@" in sender_addr else vendor_name
        print(f"  [DEBUG VENDOR]      Parsed Vendor Name : '{vendor_name}'")
        print(f"  [DEBUG VENDOR]      Parsed Sender Email: '{sender_addr}'")
        print(f"  [DEBUG VENDOR]      Stored as vendor_name in DB: '{vendor_record}'")
        print("=" * 70)

        # 3. Determine text source: PDF attachment takes priority
        quote = None
        source_label = ""
        pdf_text = ""
        raw_text_for_db = ""

        if msg.get("pdf_bytes"):
            print("  [PDF]  PDF attachment found — extracting text ...")
            pdf_text = _extract_text_from_pdf(msg["pdf_bytes"])
            print("=" * 70)
            print("  [DEBUG] RAW EXTRACTED PDF TEXT BEGIN:")
            print("-" * 70)
            print(pdf_text if pdf_text.strip() else "(No text extracted or empty PDF)")
            print("-" * 70)
            print("  [DEBUG] RAW EXTRACTED PDF TEXT END")
            print("=" * 70)

            if pdf_text.strip():
                quote = parse_quote_from_body(pdf_text)
                if quote["price"] is not None:
                    source_label = "PDF attachment"
                    raw_text_for_db = pdf_text
                    print("  [PDF]  Successfully extracted quote from PDF attachment.")
                else:
                    print("  [PDF]  PDF parsed but no price found — falling back to email body.")
                    quote = None
            else:
                print("  [PDF]  Could not extract text from PDF — falling back to email body.")

        if quote is None:
            if msg.get("pdf_bytes"):
                # Already tried PDF, now try body as backup
                print("  [BODY] Parsing email body text as backup ...")
            else:
                print("  [BODY] No PDF attachment — parsing plain-text email body.")
            quote = parse_quote_from_body(msg["body"])
            source_label = "email body"
            raw_text_for_db = pdf_text if (pdf_text and pdf_text.strip()) else msg.get("body", "")

        print(f"  Source     : {source_label}")
        print(f"  Price      : {quote['price']}")
        if quote.get("unit_price"):
            print(f"  Unit Price : {quote['unit_price']}")
        if quote.get("bulk_price"):
            print(f"  Bulk Price : {quote['bulk_price']}")
        print(f"  Warranty   : {quote['warranty']}")
        print(f"  Pmt Terms  : {quote['payment_terms']}")
        print(f"  Delivery   : {quote['delivery_days']} days")

        # 4. Validate critical fields / Fallback for unparseable quotes
        vendor_record = f"{vendor_name} <{sender_addr}>" if sender_addr and "@" in sender_addr else vendor_name

        if quote["price"] is None:
            print("  [WARN] No price found in email or PDF after trying all formats.")
            print(f"  [FALLBACK] Inserting quote with status='needs_review' for RFQ-{rfq_id}")
            inserted = insert_quote(rfq_id, vendor_record, quote, raw_text=raw_text_for_db, status="needs_review", dry_run=dry_run)
            if inserted:
                inserted_quotes.append({
                    "rfq_id": rfq_id,
                    "vendor_name": vendor_record,
                    "price": None,
                    "status": "needs_review"
                })
            processed += 1
            print()
            continue

        # 5. Insert valid quote into Supabase
        print(f"  [DB INSERT] Inserting quote for RFQ-{rfq_id} with vendor_name = '{vendor_record}'")
        inserted = insert_quote(rfq_id, vendor_record, quote, raw_text=raw_text_for_db, status="valid", dry_run=dry_run)
        if inserted:
            inserted_quotes.append({
                "rfq_id": rfq_id,
                "vendor_name": vendor_record,
                "price": quote["price"],
                "status": "valid"
            })
        processed += 1
        print()


    print(f"\nSummary: {processed} quote(s) inserted, {skipped} email(s) skipped.")
    return {
        "processed": processed,
        "skipped": skipped,
        "total_unread": len(messages),
        "quotes": inserted_quotes
    }


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Fetch unread vendor reply emails and insert parsed quotes into Supabase."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and print results without writing to Supabase."
    )
    args = parser.parse_args()

    if args.dry_run:
        print("[DRY RUN MODE] No data will be written to Supabase.\n")

    try:
        process_vendor_replies(dry_run=args.dry_run)
    except Exception as exc:
        sys.exit(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
