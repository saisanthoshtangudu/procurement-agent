# AI Procurement Agent

A proof-of-concept system that automates bulk procurement sourcing for a company, school, or college.

Instead of manually emailing vendors, reading their replies one by one, and comparing quotes by hand, this system sends the requirement to multiple vendors automatically, reads and understands their quotations (email or PDF), and recommends the best vendor — not just the cheapest, but the best overall value.

## How it works

1. The company creates an RFQ — item, specifications, quantity, budget, delivery location.
2. The system emails the RFQ (with a generated PDF attachment) to multiple vendor addresses in one batch.
3. Vendors reply by email, either as plain text or as a PDF quotation, in a range of supported formats.
4. The system automatically reads unread replies, extracts unit price, bulk price, warranty, payment terms, and delivery days, and stores them in the database.
5. A scoring algorithm ranks every vendor out of 100 (Price 55 pts, Warranty 20 pts, Payment Terms 15 pts, Delivery 10 pts) and highlights the top 3.
6. The dashboard shows a recommendation explaining why the top vendor was chosen.
7. A Purchase Order PDF can be generated and emailed directly to the selected vendor to close the deal.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | HTML, CSS, JavaScript |
| Backend | Python, Flask |
| Database | Supabase (PostgreSQL) |
| Email automation | Gmail SMTP / IMAP |
| PDF generation | ReportLab |
| PDF reading | PyMuPDF |
| Built using | Google Antigravity (AI coding agent) |

## Running locally

```bash
pip install -r requirements.txt
python start.py
```

Then open `http://localhost:8000` in your browser. Requires a `.env` file (see `.env.example`) with your Gmail and Supabase credentials.

## Design notes

- The vendor-ranking logic is a transparent, rules-based scoring algorithm built with plain code — not an external AI/LLM API — per the project requirement to build a custom algorithm rather than call a third-party AI service.
- Replies the system cannot confidently parse are flagged for manual review instead of being silently dropped, so no vendor quote is ever lost.
- All tools used are free / open-source.
