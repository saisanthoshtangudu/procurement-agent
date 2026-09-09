"""
send_rfq_emails.py
------------------
Fetches an RFQ from Supabase by ID and sends it via Gmail SMTP
to a list of vendor email addresses. Also generates and attaches
a professional PDF version of the RFQ to each email.

Usage:
    python send_rfq_emails.py --rfq-id 3 --vendors "alice@acme.com,bob@bsupply.com"

Requirements:
    pip install python-dotenv supabase reportlab
"""

import argparse
import base64
import io
import os
import re
import sys
import time
from datetime import date

import requests
from dotenv import load_dotenv
from supabase import create_client

# reportlab for PDF generation
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

# ── Load credentials from .env ─────────────────────────────────────────────────
# Use abspath so this resolves correctly regardless of the launch directory.
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(env_path)

EMAIL_ADDRESS      = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD     = os.getenv("EMAIL_APP_PASSWORD")   # Gmail App Password (used for local SMTP fallback)
SUPABASE_URL       = os.getenv("SUPABASE_URL", "https://jhsyqlquulhlvyvtthtd.supabase.co")
SUPABASE_KEY       = os.getenv("SUPABASE_KEY", "sb_publishable_OPNQ0_yz4BvigeFV3WZVaw_EQkoOYrh")

# Brevo HTTP API configuration (used on Render and cloud platforms to avoid blocked SMTP ports)
BREVO_API_URL      = "https://api.brevo.com/v3/smtp/email"
BREVO_API_KEY      = os.getenv("BREVO_API_KEY")
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL") or os.getenv("EMAIL_ADDRESS")
BREVO_SENDER_NAME  = os.getenv("BREVO_SENDER_NAME", "Procurement Team")


def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_rfq(rfq_id: int) -> dict:
    """Return the RFQ row from Supabase, or raise if not found."""
    db = get_supabase()
    response = db.table("rfqs").select("*").eq("id", rfq_id).single().execute()
    if not response.data:
        raise ValueError(f"RFQ with id={rfq_id} not found.")
    return response.data


def fetch_quote(quote_id: int) -> dict:
    """Return the Quote row from Supabase, or raise if not found."""
    db = get_supabase()
    response = db.table("quotes").select("*").eq("id", quote_id).single().execute()
    if not response.data:
        raise ValueError(f"Quote with id={quote_id} not found.")
    return response.data


def generate_rfq_pdf(rfq: dict) -> bytes:
    """
    Generate a corporate-grade RFQ document as a PDF.
    Features:
      - Dark navy company letterhead header band with ProcurementAgent branding
      - RFQ reference badge and issue date
      - Solicitations metadata card
      - Bordered requirement details table with alternating row zebra-striping
      - Distinct callout box for 'How to Respond' quotation template
      - Professional footer with thin rule, confidential notice, and page numbers
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.5*cm,
        rightMargin=1.5*cm,
        topMargin=1.2*cm,
        bottomMargin=2.0*cm
    )

    # Color Palette
    navy_dark   = colors.HexColor("#0f172a")  # Slate 900
    navy_brand  = colors.HexColor("#1e293b")  # Slate 800
    blue_accent = colors.HexColor("#2563eb")  # Blue 600
    blue_light  = colors.HexColor("#eff6ff")  # Blue 50
    border_gray = colors.HexColor("#cbd5e1")  # Slate 300
    grid_gray   = colors.HexColor("#e2e8f0")  # Slate 200
    text_dark   = colors.HexColor("#0f172a")
    text_muted  = colors.HexColor("#64748b")  # Slate 500
    row_even    = colors.HexColor("#f8fafc")  # Slate 50
    row_odd     = colors.HexColor("#ffffff")

    styles = getSampleStyleSheet()

    # Typography styles
    h1_left = ParagraphStyle(
        "HeaderBrand",
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.white
    )
    badge_cat = ParagraphStyle(
        "BadgeCat",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#93c5fd"),
        alignment=TA_RIGHT,
        spaceAfter=5
    )
    badge_id = ParagraphStyle(
        "BadgeId",
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=17,
        textColor=colors.white,
        alignment=TA_RIGHT,
        spaceAfter=5
    )
    badge_date = ParagraphStyle(
        "BadgeDate",
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#cbd5e1"),
        alignment=TA_RIGHT
    )

    sec_heading = ParagraphStyle(
        "SecHeading",
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=navy_dark,
        spaceAfter=4
    )
    sec_sub = ParagraphStyle(
        "SecSub",
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=10,
        textColor=text_muted
    )

    cell_label = ParagraphStyle(
        "CellLabel",
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=navy_brand
    )
    cell_val = ParagraphStyle(
        "CellVal",
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=text_dark
    )
    cell_val_bold = ParagraphStyle(
        "CellValBold",
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=navy_dark
    )

    box_heading = ParagraphStyle(
        "BoxHeading",
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=colors.HexColor("#1e40af")
    )
    box_intro = ParagraphStyle(
        "BoxIntro",
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#334155")
    )
    code_line = ParagraphStyle(
        "CodeLine",
        fontName="Courier-Bold",
        fontSize=8.5,
        leading=13,
        textColor=colors.HexColor("#0f172a")
    )

    terms_text = ParagraphStyle(
        "TermsText",
        fontName="Helvetica",
        fontSize=7.5,
        leading=10.5,
        textColor=text_muted
    )

    story = []

    # ── 1. Top Letterhead Band (Dark Navy) ──────────────────────────────────
    header_left = Paragraph(
        '<b><font size="18" color="#ffffff">Procurement</font><font size="18" color="#60a5fa">Agent</font></b><br/>'
        '<font size="7.5" color="#94a3b8">ENTERPRISE SOURCING &amp; VENDOR MANAGEMENT</font>',
        h1_left
    )
    header_right = [
        Paragraph("OFFICIAL RFQ", badge_cat),
        Paragraph(f"RFQ-{rfq['id']}", badge_id),
        Paragraph(f"Issued: {date.today().strftime('%B %d, %Y')}", badge_date)
    ]

    header_table = Table([[header_left, header_right]], colWidths=[10.5*cm, 7.5*cm])
    header_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), navy_dark),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING",   (0, 0), (-1, -1), 14),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 14),
    ]))
    story.append(header_table)

    # Decorative blue accent bar
    story.append(HRFlowable(width="100%", thickness=3, color=blue_accent, spaceBefore=0, spaceAfter=12))

    # ── 2. Solicitation Summary Strip ───────────────────────────────────────
    meta_items = [
        [Paragraph("SOLICITATION STATUS", sec_sub), Paragraph("ISSUED BY", sec_sub), Paragraph("TARGET RESPONSE TIME", sec_sub)],
        [
            Paragraph('<font color="#15803d"><b>OPEN FOR BIDDING</b></font>', cell_val_bold),
            Paragraph("Corporate Procurement Dept.", cell_val),
            Paragraph("Within 5 Business Days", cell_val)
        ]
    ]
    meta_table = Table(meta_items, colWidths=[6.0*cm, 6.0*cm, 6.0*cm])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), row_even),
        ("BOX",           (0, 0), (-1, -1), 0.5, border_gray),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, grid_gray),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 12))

    # ── 3. Requirement Specifications Table ─────────────────────────────────
    story.append(Paragraph("REQUIREMENT SPECIFICATIONS", sec_heading))
    story.append(Spacer(1, 4))

    specs = rfq.get("specifications")
    table_data = [
        [Paragraph("SPECIFICATION FIELD", cell_label), Paragraph("REQUIREMENT DETAILS", cell_label)],
        [Paragraph("Item / Product Name", cell_label), Paragraph(str(rfq["item"]), cell_val_bold)],
    ]
    if specs and str(specs).strip():
        specs_html = str(specs).strip().replace("\n", "<br/>")
        table_data.append([Paragraph("Specifications", cell_label), Paragraph(specs_html, cell_val)])

    table_data.extend([
        [Paragraph("Quantity Requested", cell_label), Paragraph(f"{rfq['quantity']:,} units", cell_val)],
        [Paragraph("Delivery Destination", cell_label), Paragraph(str(rfq["delivery_location"]), cell_val)],
        [Paragraph("Target Budget (Approx.)", cell_label), Paragraph(f"USD {float(rfq['budget']):,.2f}", cell_val_bold)],
        [Paragraph("Delivery Terms", cell_label), Paragraph("Door Delivery (DDP / Destination)", cell_val)],
    ])

    req_table = Table(table_data, colWidths=[5.5*cm, 12.5*cm])
    t_style = [
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
        ("BOX",           (0, 0), (-1, -1), 0.75, border_gray),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, grid_gray),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]
    for r_idx in range(1, len(table_data)):
        bg = row_even if r_idx % 2 == 0 else row_odd
        t_style.append(("BACKGROUND", (0, r_idx), (-1, r_idx), bg))
    req_table.setStyle(TableStyle(t_style))
    story.append(req_table)
    story.append(Spacer(1, 12))

    # ── 4. How to Respond (Distinct Shaded Box) ─────────────────────────────
    box_content = [
        [Paragraph("SUBMISSION GUIDELINES &amp; QUOTATION TEMPLATE", box_heading)],
        [Paragraph(
            "Please reply directly to this outreach email or submit an attached quotation document. "
            "To enable automated scoring and ranking by our procurement platform, your submission must "
            "clearly state the following structured fields:",
            box_intro
        )],
        [Paragraph(
            '<font color="#1e40af"><b>Unit Price   :</b></font> &lt;price per unit in USD&gt;<br/>'
            '<font color="#1e40af"><b>Bulk Price   :</b></font> &lt;total price in USD for requested quantity&gt;<br/>'
            '<font color="#1e40af"><b>Warranty     :</b></font> &lt;e.g., 2 years on-site or 18 months standard&gt;<br/>'
            '<font color="#1e40af"><b>Payment Terms:</b></font> &lt;e.g., Net 30, 50% advance, or COD&gt;<br/>'
            '<font color="#1e40af"><b>Delivery Days:</b></font> &lt;calendar days from order confirmation to delivery&gt;',
            code_line
        )]
    ]
    box_table = Table(box_content, colWidths=[18.0*cm])
    box_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), blue_light),
        ("BOX",           (0, 0), (-1, -1), 0.5, colors.HexColor("#bfdbfe")),
        ("LINELEFT",      (0, 0), (0, -1), 3.5, blue_accent),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
    ]))
    story.append(box_table)
    story.append(Spacer(1, 10))

    # ── 5. Evaluation Criteria Notice ───────────────────────────────────────
    eval_notice = Paragraph(
        "<b>Evaluation Criteria:</b> Submissions are weighted based on Total Price (55%), Warranty Period (20%), "
        "Payment Terms (15%), and Delivery Timeline (10%). All quotations must remain valid for at least 30 calendar days. "
        "Incomplete proposals may be delayed or omitted from scoring.",
        terms_text
    )
    story.append(eval_notice)

    # ── 6. Dynamic Footer Callback ──────────────────────────────────────────
    def add_footer(canvas, document):
        canvas.saveState()
        # Thin horizontal rule
        canvas.setStrokeColor(grid_gray)
        canvas.setLineWidth(0.5)
        canvas.line(document.leftMargin, 1.4*cm, document.pagesize[0] - document.rightMargin, 1.4*cm)
        # Left footer note
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(text_muted)
        canvas.drawString(document.leftMargin, 0.95*cm, "Generated by ProcurementAgent  |  Confidential Procurement Requisition")
        # Right footer: Page number
        canvas.drawRightString(document.pagesize[0] - document.rightMargin, 0.95*cm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=add_footer, onLaterPages=add_footer)
    return buffer.getvalue()



def get_rfq_email_content(rfq: dict, recipient: str = "") -> dict:
    """
    Generate subject, plain body, html body, and attached PDF for an RFQ email.
    Used by both Brevo HTTP API and MIME email builders.
    """
    subject = f"Request for Quotation - RFQ-{rfq['id']}: {rfq['item']}"

    specs = rfq.get("specifications")
    specs_plain = f"  Specifications   : {specs}\n" if specs and str(specs).strip() else ""
    specs_html_row = f"    <tr><td style=\"vertical-align:top;\"><strong>Specifications</strong></td><td>{str(specs).replace(chr(10), '<br/>')}</td></tr>\n" if specs and str(specs).strip() else ""

    plain_body = f"""Dear Vendor,

We invite you to submit a quotation for the following requirement:

  Item             : {rfq['item']}
{specs_plain}  Quantity         : {rfq['quantity']}
  Delivery Location: {rfq['delivery_location']}
  Budget (approx.) : USD {rfq['budget']:,.2f}
  RFQ Reference    : RFQ-{rfq['id']}

Please reply to this email with your best quote.
To allow automatic processing, include these fields in your reply:

  Unit Price   : <price per unit in USD>
  Bulk Price   : <total price in USD>
  Warranty     : <e.g. "2 years on-site" or "18 months">
  Payment Terms: <e.g. "Net 30" or "50% advance">
  Delivery Days: <number of days from order to delivery>

A PDF version of this RFQ is attached for your reference.

Kind regards,
Procurement Team
"""

    html_body = f"""
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#1e293b;">
  <p>Dear Vendor,</p>
  <p>We invite you to submit a quotation for the following requirement:</p>
  <table border="0" cellpadding="6" cellspacing="0"
         style="border-collapse:collapse;background:#f8fafc;border-radius:8px;margin-bottom:16px;">
    <tr><td><strong>Item</strong></td><td>{rfq['item']}</td></tr>
{specs_html_row}    <tr><td><strong>Quantity</strong></td><td>{rfq['quantity']}</td></tr>
    <tr><td><strong>Delivery Location</strong></td><td>{rfq['delivery_location']}</td></tr>
    <tr><td><strong>Budget (approx.)</strong></td><td>USD {rfq['budget']:,.2f}</td></tr>
    <tr><td><strong>RFQ Reference</strong></td><td><strong>RFQ-{rfq['id']}</strong></td></tr>
  </table>
  <p>Please reply using the template below so we can process your quote automatically:</p>
  <pre style="background:#f1f5f9;padding:12px;border-radius:6px;font-size:13px;line-height:1.8;">Unit Price   : &lt;price per unit in USD&gt;
Bulk Price   : &lt;total price in USD&gt;
Warranty     : &lt;e.g. "2 years on-site"&gt;
Payment Terms: &lt;e.g. "Net 30"&gt;
Delivery Days: &lt;number of days&gt;</pre>
  <p><em>A PDF version of this RFQ is attached for your reference.</em></p>
  <p>Kind regards,<br><strong>Procurement Team</strong></p>
</body></html>
"""

    pdf_bytes = generate_rfq_pdf(rfq)
    filename = f"RFQ-{rfq['id']}.pdf"

    return {
        "subject": subject,
        "plain_body": plain_body,
        "html_body": html_body,
        "pdf_bytes": pdf_bytes,
        "filename": filename,
    }



def send_email_via_brevo(recipient: str, subject: str, text_body: str, html_body: str, pdf_bytes: bytes, filename: str) -> None:
    api_key = os.getenv("BREVO_API_KEY")
    if not api_key:
        raise ValueError("BREVO_API_KEY is missing from environment variables. Please set BREVO_API_KEY.")

    sender_email = os.getenv("BREVO_SENDER_EMAIL") or os.getenv("EMAIL_ADDRESS")
    if not sender_email:
        raise ValueError("BREVO_SENDER_EMAIL (or EMAIL_ADDRESS) is missing from environment variables. Please set BREVO_SENDER_EMAIL.")

    sender_name = os.getenv("BREVO_SENDER_NAME", "Procurement Team")

    payload = {
        "sender": {
            "name": sender_name,
            "email": sender_email.strip(),
        },
        "to": [
            {"email": recipient.strip()}
        ],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": text_body,
    }

    reply_to = os.getenv("EMAIL_ADDRESS") or sender_email
    if reply_to and reply_to.strip():
        payload["replyTo"] = {"email": reply_to.strip()}

    if pdf_bytes and filename:
        payload["attachment"] = [
            {
                "name": filename,
                "content": base64.b64encode(pdf_bytes).decode("utf-8"),
            }
        ]

    headers = {
        "accept": "application/json",
        "api-key": api_key.strip(),
        "content-type": "application/json",
    }

    response = None
    try:
        response = requests.post(BREVO_API_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        details = ""
        if response is not None:
            try:
                err_data = response.json()
                msg = err_data.get("message") or response.text
                details = f" - Brevo error: {msg}"
            except Exception:
                if response.text:
                    details = f" - Brevo error: {response.text[:300]}"
        raise RuntimeError(f"Brevo API request failed: {exc}{details}") from exc


def send_rfq_email_brevo(rfq: dict, recipient: str) -> None:
    content = get_rfq_email_content(rfq, recipient)
    send_email_via_brevo(
        recipient=recipient,
        subject=content["subject"],
        text_body=content["plain_body"],
        html_body=content["html_body"],
        pdf_bytes=content["pdf_bytes"],
        filename=content["filename"]
    )


def generate_po_pdf(rfq: dict, quote: dict, vendor_email: str = "") -> bytes:
    """
    Generate a corporate-grade Purchase Order document as a PDF.
    Features:
      - Dark navy company letterhead header band with ProcurementAgent branding
      - Purchase Order reference badge (PO-{rfq_id}) and issue date
      - Official confirmation statement
      - Bordered Commercial & Vendor Details table with zebra striping
      - Shipping, fulfillment, and invoicing instructions callout
      - Dynamic footer with page numbers and corporate notice
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.5*cm,
        rightMargin=1.5*cm,
        topMargin=1.2*cm,
        bottomMargin=2.0*cm
    )

    # Color Palette
    navy_dark   = colors.HexColor("#0f172a")  # Slate 900
    navy_brand  = colors.HexColor("#1e293b")  # Slate 800
    blue_accent = colors.HexColor("#2563eb")  # Blue 600
    blue_light  = colors.HexColor("#eff6ff")  # Blue 50
    border_gray = colors.HexColor("#cbd5e1")  # Slate 300
    grid_gray   = colors.HexColor("#e2e8f0")  # Slate 200
    text_dark   = colors.HexColor("#0f172a")
    text_muted  = colors.HexColor("#64748b")  # Slate 500
    row_even    = colors.HexColor("#f8fafc")  # Slate 50
    row_odd     = colors.HexColor("#ffffff")

    # Typography styles
    h1_left = ParagraphStyle(
        "POHeaderBrand",
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.white
    )
    badge_cat = ParagraphStyle(
        "POBadgeCat",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#93c5fd"),
        alignment=TA_RIGHT,
        spaceAfter=5
    )
    badge_id = ParagraphStyle(
        "POBadgeId",
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=17,
        textColor=colors.white,
        alignment=TA_RIGHT,
        spaceAfter=5
    )
    badge_date = ParagraphStyle(
        "POBadgeDate",
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#cbd5e1"),
        alignment=TA_RIGHT
    )

    sec_heading = ParagraphStyle(
        "POSecHeading",
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=navy_dark,
        spaceAfter=4
    )
    sec_sub = ParagraphStyle(
        "POSecSub",
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=10,
        textColor=text_muted
    )

    cell_label = ParagraphStyle(
        "POCellLabel",
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=navy_brand
    )
    cell_val = ParagraphStyle(
        "POCellVal",
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=text_dark
    )
    cell_val_bold = ParagraphStyle(
        "POCellValBold",
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=navy_dark
    )

    box_heading = ParagraphStyle(
        "POBoxHeading",
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=12,
        textColor=colors.HexColor("#1e40af")
    )
    box_body = ParagraphStyle(
        "POBoxBody",
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#1e293b")
    )

    terms_text = ParagraphStyle(
        "POTermsText",
        fontName="Helvetica",
        fontSize=7.5,
        leading=10.5,
        textColor=text_muted
    )

    story = []

    # ── 1. Top Letterhead Band (Dark Navy) ──────────────────────────────────
    header_left = Paragraph(
        '<b><font size="18" color="#ffffff">Procurement</font><font size="18" color="#60a5fa">Agent</font></b><br/>'
        '<font size="7.5" color="#94a3b8">OFFICIAL PURCHASE ORDER &amp; VENDOR CONTRACT</font>',
        h1_left
    )
    po_ref = f"PO-{rfq['id']}"
    header_right = [
        Paragraph("OFFICIAL PURCHASE ORDER", badge_cat),
        Paragraph(po_ref, badge_id),
        Paragraph(f"Issued: {date.today().strftime('%B %d, %Y')}", badge_date)
    ]

    header_table = Table([[header_left, header_right]], colWidths=[10.5*cm, 7.5*cm])
    header_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), navy_dark),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING",   (0, 0), (-1, -1), 14),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 14),
    ]))
    story.append(header_table)

    # Decorative blue accent bar
    story.append(HRFlowable(width="100%", thickness=3, color=blue_accent, spaceBefore=0, spaceAfter=12))

    # ── 2. PO Summary Strip ─────────────────────────────────────────────────
    meta_items = [
        [Paragraph("PO STATUS", sec_sub), Paragraph("ISSUED BY", sec_sub), Paragraph("CONTRACT TYPE", sec_sub)],
        [
            Paragraph('<font color="#15803d"><b>CONFIRMED &amp; ISSUED</b></font>', cell_val_bold),
            Paragraph("Corporate Procurement Dept.", cell_val),
            Paragraph("Fixed-Price Purchase Order", cell_val)
        ]
    ]
    meta_table = Table(meta_items, colWidths=[6.0*cm, 6.0*cm, 6.0*cm])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), row_even),
        ("BOX",           (0, 0), (-1, -1), 0.5, border_gray),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, grid_gray),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10))

    # ── 3. Official Selection & Confirmation Callout Box ────────────────────
    raw_vendor = quote.get("vendor_name", "Vendor")
    vm = re.match(r'^(.*?)\s*<([^\s>]+@[^\s>]+)>', raw_vendor)
    if vm:
        vendor_name = vm.group(1).strip() or vm.group(2)
        if not vendor_email:
            vendor_email = vm.group(2).strip()
    else:
        vendor_name = raw_vendor

    confirm_box = [
        [Paragraph("ORDER CONFIRMATION STATEMENT", box_heading)],
        [Paragraph(
            f"This purchase order confirms the selection of <b>{vendor_name}</b> "
            f"for the above requirement under <b>RFQ-{rfq['id']}</b>. "
            f"Please proceed with fulfillment in accordance with the agreed specifications, pricing, "
            f"and delivery timeline detailed below.",
            box_body
        )]
    ]
    confirm_table = Table(confirm_box, colWidths=[18.0*cm])
    confirm_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), blue_light),
        ("BOX",           (0, 0), (-1, -1), 0.5, colors.HexColor("#bfdbfe")),
        ("LINELEFT",      (0, 0), (0, -1), 3.5, blue_accent),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
    ]))
    story.append(confirm_table)
    story.append(Spacer(1, 12))

    # ── 4. Commercial & Order Specifications Table ──────────────────────────
    story.append(Paragraph("PURCHASE ORDER &amp; COMMERCIAL SPECIFICATIONS", sec_heading))
    story.append(Spacer(1, 4))

    qty = int(rfq.get("quantity", 1))
    unit_p = quote.get("unit_price")
    bulk_p = quote.get("bulk_price") or quote.get("price")
    if unit_p is not None:
        unit_str = f"USD {float(unit_p):,.2f}"
    elif bulk_p and qty > 0:
        unit_str = f"USD {float(bulk_p) / qty:,.2f}"
    else:
        unit_str = "As Quoted"

    total_str = f"USD {float(bulk_p):,.2f}" if bulk_p is not None else "As Quoted"
    v_email_str = vendor_email.strip() if vendor_email else "On File"

    table_data = [
        [Paragraph("ORDER PARAMETER", cell_label), Paragraph("COMMITTED DETAILS", cell_label)],
        [Paragraph("Selected Vendor", cell_label), Paragraph(f"<b>{vendor_name}</b>", cell_val_bold)],
        [Paragraph("Vendor Email / Contact", cell_label), Paragraph(v_email_str, cell_val)],
        [Paragraph("Item / Product Name", cell_label), Paragraph(str(rfq["item"]), cell_val_bold)],
    ]
    if rfq.get("specifications") and str(rfq["specifications"]).strip():
        specs_po_html = str(rfq["specifications"]).strip().replace("\n", "<br/>")
        table_data.append([Paragraph("Specifications", cell_label), Paragraph(specs_po_html, cell_val)])

    table_data.extend([
        [Paragraph("Quantity Ordered", cell_label), Paragraph(f"{qty:,} units", cell_val)],
        [Paragraph("Quoted Unit Price", cell_label), Paragraph(unit_str, cell_val)],
        [Paragraph("Total Purchase Order Value", cell_label), Paragraph(f"<b>{total_str}</b>", cell_val_bold)],
        [Paragraph("Agreed Warranty Terms", cell_label), Paragraph(str(quote.get("warranty", "Standard")), cell_val)],
        [Paragraph("Agreed Payment Terms", cell_label), Paragraph(str(quote.get("payment_terms", "Standard")), cell_val)],
        [Paragraph("Committed Delivery Timeline", cell_label), Paragraph(f"{quote.get('delivery_days', 0)} calendar days from issuance", cell_val)],
        [Paragraph("Delivery Destination", cell_label), Paragraph(str(rfq["delivery_location"]), cell_val)],
    ])

    po_table = Table(table_data, colWidths=[5.5*cm, 12.5*cm])
    po_style = [
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
        ("BOX",           (0, 0), (-1, -1), 0.75, border_gray),
        ("INNERGRID",     (0, 0), (-1, -1), 0.5, grid_gray),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]
    for r_idx in range(1, len(table_data)):
        # Highlight total price row
        is_total_row = "Total Purchase Order Value" in getattr(table_data[r_idx][0], 'text', '')
        if is_total_row:
            bg = colors.HexColor("#f0fdf4")
        else:
            bg = row_even if r_idx % 2 == 0 else row_odd
        po_style.append(("BACKGROUND", (0, r_idx), (-1, r_idx), bg))
    po_table.setStyle(TableStyle(po_style))
    story.append(po_table)
    story.append(Spacer(1, 10))

    # ── 5. Invoicing & Fulfillment Terms ─────────────────────────────────────
    instructions_text = Paragraph(
        f"<b>Fulfillment &amp; Invoicing Instructions:</b> All goods must be dispatched to {rfq['delivery_location']} "
        f"within {quote.get('delivery_days', 0)} calendar days. Commercial invoices and shipping documentation "
        f"must quote reference <b>{po_ref}</b>. Payment will be disbursed under agreed terms ({quote.get('payment_terms', 'Standard')}) "
        "following inspection and acceptance of delivery.",
        terms_text
    )
    story.append(instructions_text)

    # ── 6. Dynamic Footer Callback ──────────────────────────────────────────
    def add_footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(grid_gray)
        canvas.setLineWidth(0.5)
        canvas.line(document.leftMargin, 1.4*cm, document.pagesize[0] - document.rightMargin, 1.4*cm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(text_muted)
        canvas.drawString(document.leftMargin, 0.95*cm, f"Purchase Order: {po_ref} | ProcurementAgent Enterprise Platform")
        canvas.drawRightString(
            document.pagesize[0] - document.rightMargin,
            0.95*cm,
            f"Page {document.page}"
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=add_footer, onLaterPages=add_footer)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


def send_po_email_brevo(rfq: dict, quote: dict, recipient: str, po_pdf_bytes: bytes) -> None:
    """Build and send an official Purchase Order confirmation email with attached PO PDF via Brevo."""
    po_ref = f"PO-{rfq['id']}"
    subject = f"Purchase Order Confirmation - {po_ref}: {rfq['item']}"
    raw_vendor = quote.get("vendor_name", "Vendor")
    vm = re.match(r'^(.*?)\s*<([^\s>]+@[^\s>]+)>', raw_vendor)
    vendor_name = vm.group(1).strip() if vm and vm.group(1).strip() else (vm.group(2) if vm else raw_vendor)
    total_val = quote.get("bulk_price") or quote.get("price") or 0

    text_body = (
        f"Dear {vendor_name},\n\n"
        f"We are pleased to confirm that your quotation has been accepted. Attached is official Purchase Order {po_ref}.\n\n"
        f"Order Summary:\n"
        f"  Reference         : {po_ref} (RFQ-{rfq['id']})\n"
        f"  Item              : {rfq['item']}\n"
        f"  Quantity          : {rfq['quantity']:,} units\n"
        f"  Total Agreed Price: USD {float(total_val):,.2f}\n"
        f"  Delivery Days     : {quote.get('delivery_days', 0)} calendar days\n"
        f"  Delivery Location : {rfq['delivery_location']}\n"
        f"  Payment Terms     : {quote.get('payment_terms', 'Standard')}\n"
        f"  Warranty          : {quote.get('warranty', 'Standard')}\n\n"
        f"Please find your official Purchase Order PDF attached.\n\n"
        f"Sincerely,\n"
        f"Corporate Procurement Team\n"
        f"ProcurementAgent\n"
    )

    html_body = f"""\
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #1e293b; margin: 0; padding: 20px; background-color: #f8fafc;">
  <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden;">
    <div style="background-color: #0f172a; padding: 24px; text-align: left;">
      <h2 style="margin: 0; color: #ffffff; font-size: 20px;">Purchase Order Confirmation</h2>
      <p style="margin: 4px 0 0 0; color: #94a3b8; font-size: 13px;">Official PO Reference: <strong>{po_ref}</strong></p>
    </div>
    <div style="padding: 24px;">
      <p style="font-size: 15px;">Dear <strong>{vendor_name}</strong>,</p>
      <p style="font-size: 14px; color: #334155;">
        This purchase order confirms the selection of <strong>{vendor_name}</strong> for the procurement requirement specified below.
        Please find your official Purchase Order document attached.
      </p>
      <table style="width: 100%; border-collapse: collapse; margin: 18px 0; font-size: 13px;">
        <tr style="background: #f8fafc; border-bottom: 1px solid #e2e8f0;">
          <td style="padding: 8px 12px; font-weight: bold; color: #475569;">Item:</td>
          <td style="padding: 8px 12px; color: #0f172a;"><strong>{rfq['item']}</strong></td>
        </tr>
        <tr style="border-bottom: 1px solid #e2e8f0;">
          <td style="padding: 8px 12px; font-weight: bold; color: #475569;">Quantity:</td>
          <td style="padding: 8px 12px; color: #0f172a;">{rfq['quantity']:,} units</td>
        </tr>
        <tr style="background: #f8fafc; border-bottom: 1px solid #e2e8f0;">
          <td style="padding: 8px 12px; font-weight: bold; color: #475569;">Total Order Value:</td>
          <td style="padding: 8px 12px; color: #15803d; font-weight: bold;">USD {float(total_val):,.2f}</td>
        </tr>
        <tr style="border-bottom: 1px solid #e2e8f0;">
          <td style="padding: 8px 12px; font-weight: bold; color: #475569;">Delivery Location:</td>
          <td style="padding: 8px 12px; color: #0f172a;">{rfq['delivery_location']}</td>
        </tr>
        <tr style="background: #f8fafc; border-bottom: 1px solid #e2e8f0;">
          <td style="padding: 8px 12px; font-weight: bold; color: #475569;">Committed Delivery:</td>
          <td style="padding: 8px 12px; color: #0f172a;">{quote.get('delivery_days', 0)} calendar days</td>
        </tr>
        <tr style="border-bottom: 1px solid #e2e8f0;">
          <td style="padding: 8px 12px; font-weight: bold; color: #475569;">Payment Terms:</td>
          <td style="padding: 8px 12px; color: #0f172a;">{quote.get('payment_terms', 'Standard')}</td>
        </tr>
      </table>
      <p style="font-size: 13px; color: #64748b;">
        Please review the attached PDF for complete commercial terms and shipping instructions.
      </p>
    </div>
    <div style="background-color: #f8fafc; padding: 14px 24px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #64748b;">
      Corporate Procurement Department · ProcurementAgent
    </div>
  </div>
</body>
</html>
"""

    filename = f"{po_ref}.pdf"
    send_email_via_brevo(
        recipient=recipient,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        pdf_bytes=po_pdf_bytes,
        filename=filename
    )


def send_emails(rfq: dict, vendors: list) -> None:
    """Send RFQ email to every vendor address via Brevo HTTP API."""
    if not os.getenv("BREVO_API_KEY"):
        sys.exit("ERROR: BREVO_API_KEY must be set in your .env file.")

    print(f"\nSending via Brevo API ...")
    total = len(vendors)
    for i, vendor_email in enumerate(vendors):
        vendor_email = vendor_email.strip()
        if not vendor_email:
            continue
        try:
            send_rfq_email_brevo(rfq, vendor_email)
            print(f"  [OK]  Sent to {vendor_email} ({i+1}/{total})")
        except Exception as exc:
            print(f"  [ERR] Failed to send to {vendor_email}: {exc}")

        if i < total - 1:
            time.sleep(0.5)

    print("\nDone.")


def main():
    parser = argparse.ArgumentParser(
        description="Send an RFQ email to one or more vendor addresses."
    )
    parser.add_argument(
        "--rfq-id", type=int, required=True,
        help="Numeric ID of the RFQ in the Supabase 'rfqs' table."
    )
    parser.add_argument(
        "--vendors", required=True,
        help="Comma-separated list of vendor email addresses."
    )
    args = parser.parse_args()

    vendor_list = [v.strip() for v in args.vendors.split(",") if v.strip()]
    if not vendor_list:
        sys.exit("ERROR: No valid vendor email addresses provided.")

    print(f"Fetching RFQ-{args.rfq_id} from Supabase ...")
    rfq = fetch_rfq(args.rfq_id)
    print(f"  Item    : {rfq['item']}")
    print(f"  Qty     : {rfq['quantity']}")
    print(f"  Location: {rfq['delivery_location']}")
    print(f"  Budget  : USD {rfq['budget']:,.2f}")
    print(f"\nSending to {len(vendor_list)} vendor(s): {', '.join(vendor_list)}")
    send_emails(rfq, vendor_list)


if __name__ == "__main__":
    main()
