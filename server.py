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
import re
import sys
import time

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from dotenv import load_dotenv
import os

# Reuse helpers from send_rfq_emails.py and check_vendor_replies.py (same directory)
from send_rfq_emails import (
    fetch_rfq,
    fetch_quote,
    generate_po_pdf,
    send_po_email_brevo,
    send_rfq_email_brevo,
    EMAIL_ADDRESS,
    BREVO_API_KEY,
)
from check_vendor_replies import process_vendor_replies

# ── App setup ──────────────────────────────────────────────────────────────────
load_dotenv()

app = Flask(__name__)

# Allow requests from any origin so the webpage (e.g. localhost:8000 or
# file://) can call this server without browser CORS errors.
CORS(app, resources={r"/*": {"origins": "*"}})

# ── Helper ─────────────────────────────────────────────────────────────────────
def send_batch(rfq: dict, vendor_emails: list, delay_seconds: float = 0.5) -> dict:
    """
    Send the RFQ email to every address via Brevo API.
    Returns a result dict: { sent, failed, errors }.
    Skips and records failures individually — never aborts the whole batch.
    """
    sent   = 0
    failed = 0
    errors = []

    api_key = os.getenv("BREVO_API_KEY") or BREVO_API_KEY
    sender_email = os.getenv("BREVO_SENDER_EMAIL") or os.getenv("EMAIL_ADDRESS")
    if not api_key:
        return {
            "sent":   0,
            "failed": len(vendor_emails),
            "errors": [{"email": "*all*", "reason": "BREVO_API_KEY is not set in environment variables."}],
        }
    if not sender_email:
        return {
            "sent":   0,
            "failed": len(vendor_emails),
            "errors": [{"email": "*all*", "reason": "BREVO_SENDER_EMAIL is not set in environment variables."}],
        }

    total = len(vendor_emails)
    for i, email_addr in enumerate(vendor_emails):
        email_addr = email_addr.strip()
        if not email_addr:
            continue
        try:
            send_rfq_email_brevo(rfq, email_addr)
            sent += 1
            print(f"  [OK] Sent RFQ to {email_addr} ({sent}/{total})")
        except Exception as exc:
            failed += 1
            errors.append({"email": email_addr, "reason": str(exc)})
            print(f"  [ERR] Failed sending to {email_addr}: {exc}")

        if i < total - 1 and delay_seconds > 0:
            time.sleep(delay_seconds)

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
    - Sends the email to every vendor address via Brevo HTTPS API
    - Skips bad addresses, collects errors, always returns a summary
    """
    if not (os.getenv("BREVO_API_KEY") or BREVO_API_KEY):
        return jsonify({
            "error": "Server is missing BREVO_API_KEY in environment variables (.env)"
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

    # If action is 'email', send the PO via Brevo
    if action == "email":
        if not vendor_email:
            raw_v = quote.get("vendor_name", "")
            m = re.search(r'<([^\s>]+@[^\s>]+)>', raw_v)
            if m:
                vendor_email = m.group(1).strip()

        if not vendor_email or "@" not in vendor_email:
            return jsonify({"error": "A valid 'vendor_email' address is required to send confirmation email"}), 400

        if not (os.getenv("BREVO_API_KEY") or BREVO_API_KEY):
            return jsonify({"error": "Server is missing BREVO_API_KEY in environment variables (.env)"}), 500

        try:
            send_po_email_brevo(rfq, quote, vendor_email, po_pdf_bytes)
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
