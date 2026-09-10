"""
server.py
---------
Flask API server for the Procurement Agent web app.

Endpoints:
  POST /send-rfq   { "rfq_id": 3, "vendors": ["a@x.com", "b@y.com"] }
                   -> { "sent": 45, "failed": 2,
                        "errors": [{"email": "...", "reason": "..."}] }

Usage:
  python server.py           # runs on http://localhost:5000
  python server.py --port 8080

Requirements:
  pip install flask flask-cors python-dotenv supabase
"""

import argparse
import base64
import io
import os
import re
import smtplib
import sys
import time

from dotenv import load_dotenv

# Explicitly load .env using absolute path relative to server.py,
# so this works regardless of which directory python is launched from.
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(env_path)

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

# Reuse helpers from send_rfq_emails.py and check_vendor_replies.py (same directory)
from send_rfq_emails import (
    fetch_rfq,
    fetch_quote,
    generate_po_pdf,
    build_email,
    build_po_email,
    EMAIL_ADDRESS,
    EMAIL_PASSWORD,
    SMTP_HOST,
    SMTP_PORT,
)
from check_vendor_replies import process_vendor_replies

# ── App setup ──────────────────────────────────────────────────────────────────
app = Flask(__name__)

# Allow requests from any origin (including Vercel frontend and local development)
CORS(app, resources={r"/*": {
    "origins": "*",
    "methods": ["GET", "POST", "OPTIONS"],
    "allow_headers": ["Content-Type", "Authorization"]
}})

# ── Startup diagnostic (safe – values never printed) ───────────────────────────
def _check_env():
    keys = {
        "EMAIL_ADDRESS":      os.getenv("EMAIL_ADDRESS"),
        "EMAIL_APP_PASSWORD": os.getenv("EMAIL_APP_PASSWORD"),
        "SUPABASE_URL":       os.getenv("SUPABASE_URL"),
        "SUPABASE_KEY":       os.getenv("SUPABASE_KEY"),
    }
    for name, val in keys.items():
        status = "OK" if val else "MISSING"
        print(f"  [ENV] {name}: {status}", flush=True)

_check_env()

# ── Helper ─────────────────────────────────────────────────────────────────────
def send_batch(rfq: dict, vendor_emails: list, delay_seconds: float = 1.0) -> dict:
    """
    Send the RFQ email to every address via Gmail SMTP.
    Returns a result dict: { sent, failed, errors }.
    Skips and records failures individually — never aborts the whole batch.
    """
    sent   = 0
    failed = 0
    errors = []

    email_addr_env = os.getenv("EMAIL_ADDRESS") or EMAIL_ADDRESS
    password_env = os.getenv("EMAIL_APP_PASSWORD") or EMAIL_PASSWORD

    if not email_addr_env or not password_env:
        return {
            "sent":   0,
            "failed": len(vendor_emails),
            "errors": [{"email": "*all*", "reason": "EMAIL_ADDRESS or EMAIL_APP_PASSWORD is not set in environment variables."}],
        }

    try:
        smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
        smtp.ehlo()
        smtp.starttls()
        smtp.login(email_addr_env, password_env)
    except Exception as exc:
        return {
            "sent":   0,
            "failed": len(vendor_emails),
            "errors": [{"email": "*all*", "reason": f"SMTP connection failed: {exc}"}],
        }

    try:
        total = len(vendor_emails)
        for i, email_addr in enumerate(vendor_emails):
            email_addr = email_addr.strip()
            if not email_addr:
                continue
            try:
                msg = build_email(rfq, email_addr)
                smtp.sendmail(email_addr_env, email_addr, msg.as_string())
                sent += 1
                print(f"  [OK] Sent RFQ to {email_addr} ({sent}/{total})")
            except Exception as exc:
                failed += 1
                errors.append({"email": email_addr, "reason": str(exc)})
                print(f"  [ERR] Failed sending to {email_addr}: {exc}")

            if i < total - 1 and delay_seconds > 0:
                time.sleep(delay_seconds)
    finally:
        try:
            smtp.quit()
        except Exception:
            pass

    return {"sent": sent, "failed": failed, "errors": errors}


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    """Quick health check — confirm the server is running."""
    return jsonify({"status": "ok"}), 200


@app.route("/send-rfq", methods=["POST"])
def send_rfq():
    """
    POST /send-rfq
    Body (JSON): { "rfq_id": 3, "vendors": ["a@x.com", ...] }

    - Fetches the RFQ from Supabase
    - Sends the email to every vendor address via Gmail SMTP
    - Skips bad addresses, collects errors, always returns a summary
    """
    if not (os.getenv("EMAIL_ADDRESS") and os.getenv("EMAIL_APP_PASSWORD")):
        return jsonify({
            "error": "Server is missing EMAIL_ADDRESS or EMAIL_APP_PASSWORD in environment variables (.env)"
        }), 500

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    rfq_id = data.get("rfq_id")
    vendors = data.get("vendors", [])

    if not rfq_id:
        return jsonify({"error": "'rfq_id' is required"}), 400
    if not isinstance(vendors, list) or len(vendors) == 0:
        return jsonify({"error": "'vendors' must be a non-empty list of email addresses"}), 400

    # Deduplicate and strip whitespace server-side (belt-and-suspenders)
    clean_vendors = list(dict.fromkeys(
        v.strip().lower() for v in vendors if v.strip()
    ))

    # Fetch RFQ from Supabase
    try:
        rfq = fetch_rfq(int(rfq_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": f"Supabase error: {exc}"}), 500

    # Send batch
    result = send_batch(rfq, clean_vendors)

    status_code = 200 if result["failed"] == 0 else 207  # 207 = multi-status
    return jsonify(result), status_code


@app.route("/check-replies", methods=["POST"])
def check_replies():
    """
    POST /check-replies
    Checks Gmail IMAP for unread vendor reply emails, parses quote details,
    and inserts them into the Supabase quotes table.
    """
    try:
        result = process_vendor_replies(dry_run=False)
        return jsonify(result), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/place-order", methods=["POST"])
def place_order():
    """
    POST /place-order
    Body (JSON): {
        "rfq_id": 19,
        "quote_id": 31,
        "vendor_email": "vendor@example.com",
        "action": "download" | "email"
    }

    Generates the official Purchase Order PDF and:
    - If action == 'download': returns the PDF file directly for browser download.
    - If action == 'email': sends the PO PDF to the vendor's email, and returns JSON
      with status and base64-encoded PDF so client can also download it.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    rfq_id = data.get("rfq_id")
    quote_id = data.get("quote_id")
    vendor_email = (data.get("vendor_email") or "").strip()
    action = (data.get("action") or "download").strip().lower()

    if not rfq_id:
        return jsonify({"error": "'rfq_id' is required"}), 400
    if not quote_id:
        return jsonify({"error": "'quote_id' is required"}), 400

    try:
        rfq = fetch_rfq(int(rfq_id))
    except Exception as exc:
        return jsonify({"error": f"Failed to fetch RFQ-{rfq_id}: {exc}"}), 404

    try:
        quote = fetch_quote(int(quote_id))
    except Exception as exc:
        return jsonify({"error": f"Failed to fetch Quote-{quote_id}: {exc}"}), 404

    # Generate the Purchase Order PDF
    try:
        po_pdf_bytes = generate_po_pdf(rfq, quote, vendor_email=vendor_email)
    except Exception as exc:
        return jsonify({"error": f"Failed to generate PO PDF: {exc}"}), 500

    # If action is 'download', stream the PDF file directly
    if action == "download":
        return send_file(
            io.BytesIO(po_pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"PO-{rfq_id}.pdf"
        )

    # If action is 'email', send the PO via Gmail SMTP
    if action == "email":
        if not vendor_email:
            raw_v = quote.get("vendor_name", "")
            m = re.search(r'<([^\s>]+@[^\s>]+)>', raw_v)
            if m:
                vendor_email = m.group(1).strip()

        if not vendor_email or "@" not in vendor_email:
            return jsonify({"error": "A valid 'vendor_email' address is required to send confirmation email"}), 400

        email_addr_env = os.getenv("EMAIL_ADDRESS") or EMAIL_ADDRESS
        password_env = os.getenv("EMAIL_APP_PASSWORD") or EMAIL_PASSWORD

        if not email_addr_env or not password_env:
            return jsonify({"error": "Server is missing EMAIL_ADDRESS or EMAIL_APP_PASSWORD in environment variables (.env)"}), 500

        try:
            msg = build_po_email(rfq, quote, vendor_email, po_pdf_bytes)
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(email_addr_env, password_env)
                smtp.sendmail(email_addr_env, vendor_email, msg.as_string())
            print(f"  [OK] Sent PO to {vendor_email} via Gmail SMTP")
        except Exception as exc:
            return jsonify({"error": f"Failed to send PO email to {vendor_email}: {exc}"}), 500

        return jsonify({
            "status": "ok",
            "message": f"Purchase Order PO-{rfq_id} successfully sent to {vendor_email}",
            "po_number": f"PO-{rfq_id}",
            "vendor_email": vendor_email,
            "pdf_base64": base64.b64encode(po_pdf_bytes).decode("utf-8")
        }), 200

    return jsonify({"error": f"Unsupported action '{action}'. Use 'download' or 'email'."}), 400


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Procurement Agent API server")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    print(f"Starting Procurement Agent API on http://{args.host}:{args.port}")
    print(f"  POST /send-rfq       – send RFQ emails to a batch of vendors")
    print(f"  POST /check-replies  – check Gmail for vendor replies & insert quotes")
    print(f"  GET  /health         – server health check\n")
    app.run(host=args.host, port=args.port, debug=args.debug)
