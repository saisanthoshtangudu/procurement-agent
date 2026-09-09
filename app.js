// ── Supabase Client ────────────────────────────────────
const SUPABASE_URL = "https://jhsyqlquulhlvyvtthtd.supabase.co";
const SUPABASE_KEY =
  "sb_publishable_OPNQ0_yz4BvigeFV3WZVaw_EQkoOYrh";

const db = window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY);

// ── DOM Helpers ────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// ── Navigation ─────────────────────────────────────────
function showPage(pageId) {
  $$(".page").forEach((p) => p.classList.remove("active"));
  $$("nav button").forEach((b) => b.classList.remove("active"));
  $(`#${pageId}`).classList.add("active");
  $(`[data-page="${pageId}"]`).classList.add("active");

  if (pageId === "page-compare") loadRfqDropdown("compare-rfq-select");
}

document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => showPage(btn.dataset.page));
});

// ── Toast ──────────────────────────────────────────────
function toast(message, type = "success") {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast ${type} show`;
  setTimeout(() => el.classList.remove("show"), 3000);
}

// ── Format Currency ────────────────────────────────────
function currency(n) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(n);
}

// ── Utility ────────────────────────────────────────────
function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

/**
 * Parse raw vendor string (which may contain "Name <email@example.com>" or "Name (email@example.com)").
 * Returns { name, email } uniquely for this vendor.
 */
function parseVendorInfo(rawVendor, rfqId = null) {
  let raw = (rawVendor || "").trim();
  let name = raw;
  let email = "";

  // 1. Angle-bracket format: "Name <email@domain.com>"
  const angleMatch = raw.match(/^(.*?)\s*<([^\s>]+@[^\s>]+)>/);
  if (angleMatch) {
    name = angleMatch[1].trim() || angleMatch[2];
    email = angleMatch[2].trim();
  } else {
    // 2. Parentheses format: "Name (email@domain.com)"
    const parenMatch = raw.match(/^(.*?)\s*\(([^\s)]+@[^\s)]+)\)/);
    if (parenMatch) {
      name = parenMatch[1].trim() || parenMatch[2];
      email = parenMatch[2].trim();
    } else {
      // 3. Standalone email: "email@example.com"
      const emailMatch = raw.match(/^[^\s@]+@[^\s@]+\.[^\s@]+$/);
      if (emailMatch) {
        email = emailMatch[0];
        name = email.split("@")[0];
      } else {
        // 4. Any embedded email address
        const embeddedMatch = raw.match(/([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/);
        if (embeddedMatch) {
          email = embeddedMatch[1];
          name = raw.replace(email, "").replace(/[<>()]/g, "").trim() || email;
        } else if (rfqId) {
          // 5. Match with invited vendors list ONLY if vendor name explicitly matches an invited address
          try {
            const stored = localStorage.getItem("rfq_vendors_" + rfqId);
            if (stored) {
              const list = JSON.parse(stored);
              if (Array.isArray(list) && list.length > 0) {
                const found = list.find((e) => {
                  const prefix = e.split("@")[0].toLowerCase();
                  return prefix.length >= 3 && raw.toLowerCase().includes(prefix);
                });
                if (found) {
                  email = found;
                }
              }
            }
          } catch (_) {}
        }
      }
    }
  }

  // Clean up any surrounding quotes or punctuation from vendor name
  name = name.replace(/^["']+|["']+$/g, "").trim();

  return { name: name || "Vendor", email };
}

// ═════════════════════════════════════════════════════════
// PAGE 1 – Create RFQ
// ═════════════════════════════════════════════════════════
// ═════════════════════════════════════════════════════════
// PAGE 1 – Create RFQ
// ═════════════════════════════════════════════════════════
const rfqForm = $("#rfq-form");

// Track the most recently created RFQ id so the vendor panel can use it
let lastCreatedRfqId = null;

rfqForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = rfqForm.querySelector('button[type="submit"]');
  btn.disabled = true;
  btn.textContent = "Saving…";

  const specsInput = $("#rfq-specifications");
  const specs = specsInput ? specsInput.value.trim() : "";

  const payload = {
    item: $("#rfq-item").value.trim(),
    specifications: specs || null,
    quantity: parseInt($("#rfq-quantity").value, 10),
    delivery_location: $("#rfq-location").value.trim(),
    budget: parseFloat($("#rfq-budget").value),
  };

  // Use .select() so Supabase returns the inserted row (including the new id)
  let { data, error } = await db
    .from("rfqs")
    .insert([payload])
    .select()
    .single();

  // Graceful fallback if the user has not yet run the SQL to add the specifications column
  if (error && error.message && error.message.toLowerCase().includes("specifications")) {
    console.warn("Column 'specifications' not found in Supabase rfqs table. Retrying insert without it...");
    const fallbackPayload = { ...payload };
    delete fallbackPayload.specifications;
    const retry = await db
      .from("rfqs")
      .insert([fallbackPayload])
      .select()
      .single();
    if (!retry.error) {
      data = retry.data;
      error = null;
      toast("RFQ created, but 'specifications' column is missing in Supabase. Please run the SQL command provided.", "error");
    }
  }

  btn.disabled = false;
  btn.textContent = "Create RFQ";

  if (error) {
    console.error(error);
    toast("Failed to create RFQ – " + error.message, "error");
    return;
  }

  toast("RFQ created successfully!");
  rfqForm.reset();
  loadRecentRfqs();

  // Reveal the "Send to Vendors" panel for this new RFQ
  lastCreatedRfqId = data.id;
  showSendVendorsPanel(data.id, payload.item);
});

// ── Send to Vendors panel ──────────────────────────────
function showSendVendorsPanel(rfqId, item) {
  const panel = $("#send-vendors-panel");
  $("#send-rfq-ref").textContent = `– RFQ-${rfqId}: ${item}`;
  $("#vendor-emails-input").value = "";
  $("#send-vendor-count").textContent = "";

  // Reset any previous result banner
  const resultEl = $("#send-result");
  resultEl.style.display = "none";
  resultEl.innerHTML = "";

  panel.style.display = "block";
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

/**
 * Parse a raw string of emails (newline and/or comma-separated) into a
 * clean, deduplicated array of lowercase addresses.
 */
function parseVendorEmails(raw) {
  // Split on newlines and commas, trim each entry, filter empties
  const tokens = raw
    .split(/[\n,]+/)
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s.length > 0);

  // Basic email format check: must contain @ and a dot after it
  const valid = tokens.filter((s) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s));
  const invalid = tokens.filter((s) => !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s));

  // Deduplicate while preserving order
  const unique = [...new Set(valid)];

  return { unique, invalid };
}

// Live count as user types
$("#vendor-emails-input").addEventListener("input", () => {
  const { unique, invalid } = parseVendorEmails(
    $("#vendor-emails-input").value
  );
  const parts = [`${unique.length} valid email${unique.length !== 1 ? "s" : ""}`];
  if (invalid.length > 0)
    parts.push(`${invalid.length} invalid skipped`);
  $("#send-vendor-count").textContent = parts.join(" · ");
});

// Send button handler
$("#btn-send-vendors").addEventListener("click", async () => {
  if (!lastCreatedRfqId) {
    toast("No RFQ selected. Please create an RFQ first.", "error");
    return;
  }

  const raw = $("#vendor-emails-input").value;
  const { unique: vendors, invalid } = parseVendorEmails(raw);

  if (vendors.length === 0) {
    toast("No valid email addresses found. Please check your input.", "error");
    return;
  }

  const btn = $("#btn-send-vendors");
  btn.disabled = true;
  btn.textContent = `Sending to ${vendors.length} vendor${vendors.length !== 1 ? "s" : ""}…`;

  // Hide any previous result
  const resultEl = $("#send-result");
  resultEl.style.display = "none";

  try {
    localStorage.setItem("rfq_vendors_" + lastCreatedRfqId, JSON.stringify(vendors));
  } catch (_) {}

  try {
    const resp = await fetch("http://localhost:5000/send-rfq", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rfq_id: lastCreatedRfqId, vendors }),
    });

    const result = await resp.json();

    if (!resp.ok && !result.sent && !result.failed) {
      // Server-level error (e.g. 500, missing credentials)
      throw new Error(result.error || `Server error ${resp.status}`);
    }

    renderSendResult(result, invalid);
  } catch (err) {
    renderSendResult(null, invalid, err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "📧 Send to Vendors";
  }
});

/** Render a colour-coded summary banner below the Send button. */
function renderSendResult(result, skippedInvalid = [], fetchError = null) {
  const el = $("#send-result");

  if (fetchError) {
    el.innerHTML = `
      <div style="padding:1rem 1.25rem; background:#fee2e2; border-left:4px solid #dc2626;
                  border-radius:0 8px 8px 0; font-size:0.9rem; color:#991b1b; line-height:1.6;">
        <strong>Could not reach the email server.</strong><br>
        ${escapeHtml(fetchError)}<br>
        <span style="font-size:0.8rem;">Make sure <code>python server.py</code> is running on port 5000.</span>
      </div>`;
    el.style.display = "block";
    return;
  }

  const { sent = 0, failed = 0, errors = [] } = result;
  const total = sent + failed;
  const allOk = failed === 0 && skippedInvalid.length === 0;
  const bg     = allOk ? "#dcfce7" : failed > 0 ? "#fef9c3" : "#f0f9ff";
  const border = allOk ? "#16a34a" : failed > 0 ? "#ca8a04" : "#0284c7";
  const color  = allOk ? "#166534" : failed > 0 ? "#713f12" : "#0c4a6e";

  let html = `
    <div style="padding:1rem 1.25rem; background:${bg}; border-left:4px solid ${border};
                border-radius:0 8px 8px 0; font-size:0.9rem; color:${color}; line-height:1.8;">
      <strong>Email send complete.</strong><br>
      ✅ Sent successfully: <strong>${sent}</strong> of ${total}<br>`;

  if (failed > 0) {
    html += `  ❌ Failed: <strong>${failed}</strong><br>`;
  }
  if (skippedInvalid.length > 0) {
    html += `  ⚠️ Invalid addresses skipped: <strong>${skippedInvalid.length}</strong><br>`;
  }

  // Show individual failure reasons (collapsed list)
  if (errors.length > 0) {
    html += `  <details style="margin-top:0.5rem;">
      <summary style="cursor:pointer; font-size:0.8rem;">Show failure details</summary>
      <ul style="margin:0.4rem 0 0 1rem; font-size:0.8rem; line-height:1.8;">`;
    errors.forEach(({ email, reason }) => {
      html += `<li><code>${escapeHtml(email)}</code> — ${escapeHtml(reason)}</li>`;
    });
    html += `</ul></details>`;
  }

  html += `</div>`;
  el.innerHTML = html;
  el.style.display = "block";
}

// Recent RFQs list
async function loadRecentRfqs() {
  const { data, error } = await db
    .from("rfqs")
    .select("*")
    .order("created_at", { ascending: false })
    .limit(10);

  const container = $("#recent-rfqs");
  if (error || !data || data.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="icon">📋</div>
        <p>No RFQs yet. Create your first one above.</p>
      </div>`;
    return;
  }

  container.innerHTML = data
    .map(
      (r) => `
    <div class="rfq-item">
      <div class="rfq-info">
        <span class="rfq-title">${escapeHtml(r.item)}</span>
        ${r.specifications ? `<span style="font-size:0.8rem; color:#475569; margin-top:0.15rem; line-height:1.4;">${escapeHtml(r.specifications)}</span>` : ''}
        <span class="rfq-meta">Qty: ${r.quantity} · ${escapeHtml(r.delivery_location)} · Budget: ${currency(r.budget)}</span>
      </div>
      <span class="badge badge-blue">RFQ-${r.id}</span>
    </div>`
    )
    .join("");
}

// ═════════════════════════════════════════════════════════
// RFQ Dropdown Loader (Used on Compare Quotes page)
// ═════════════════════════════════════════════════════════
async function loadRfqDropdown(selectId) {
  const select = $(`#${selectId}`);
  const { data, error } = await db
    .from("rfqs")
    .select("*")
    .order("created_at", { ascending: false });

  const prev = select.value;

  select.innerHTML = '<option value="">-- Select an RFQ --</option>';
  if (!error && data) {
    data.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.id;
      opt.textContent = `RFQ-${r.id}: ${r.item} (Qty ${r.quantity})`;
      select.appendChild(opt);
    });
  }

  if (prev) select.value = prev;
}

// ═════════════════════════════════════════════════════════
// PAGE 2 – Compare Quotes
// ═════════════════════════════════════════════════════════
$("#compare-rfq-select").addEventListener("change", loadComparison);
$("#btn-refresh-compare").addEventListener("click", loadComparison);

let isCheckingReplies = false;

// Manual check button (shows active loading state and toasts)
$("#btn-check-replies").addEventListener("click", async () => {
  if (isCheckingReplies) return;
  const btn = $("#btn-check-replies");
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Checking replies…";
  isCheckingReplies = true;

  try {
    const resp = await fetch("http://localhost:5000/check-replies", {
      method: "POST",
    });

    const result = await resp.json();

    if (!resp.ok) {
      throw new Error(result.error || `Server error ${resp.status}`);
    }

    if (result.processed > 0) {
      toast(`Found ${result.processed} new quote${result.processed !== 1 ? "s" : ""}`, "success");
    } else {
      toast("No new replies found", "success");
    }

    // Automatically refresh the quotes table so new results appear immediately
    await loadComparison();
  } catch (err) {
    console.error(err);
    toast("Failed to check replies (is python server.py running?): " + err.message, "error");
  } finally {
    isCheckingReplies = false;
    btn.disabled = false;
    btn.textContent = origText;
  }
});

// Automatic background polling for Compare Quotes page (every 30s)
async function pollRepliesSilently() {
  const comparePage = $("#page-compare");
  // Only run when the Compare Quotes page is actively displayed and not already checking
  if (!comparePage || !comparePage.classList.contains("active") || isCheckingReplies) {
    return;
  }

  isCheckingReplies = true;
  try {
    const resp = await fetch("http://localhost:5000/check-replies", {
      method: "POST",
    });
    if (!resp.ok) return;

    const result = await resp.json();
    if (result && result.processed > 0) {
      toast(`Found ${result.processed} new quote${result.processed !== 1 ? "s" : ""}`, "success");
      // Automatically refresh comparison table and scoring
      await loadComparison();
    }
  } catch (err) {
    // Keep background polling silent to avoid interrupting the user
    console.debug("Background poll check-replies:", err.message);
  } finally {
    isCheckingReplies = false;
  }
}

// Start 30-second background polling interval
setInterval(pollRepliesSilently, 30000);

async function loadComparison() {
  const rfqId = $("#compare-rfq-select").value;
  const container = $("#comparison-result");

  if (!rfqId) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="icon">📊</div>
        <p>Select an RFQ to view vendor quotes.</p>
      </div>`;
    return;
  }

  // Fetch RFQ details
  const { data: rfq } = await db
    .from("rfqs")
    .select("*")
    .eq("id", parseInt(rfqId, 10))
    .single();

  // Fetch all quotes for this RFQ
  const { data: quotes, error } = await db
    .from("quotes")
    .select("*")
    .eq("rfq_id", parseInt(rfqId, 10));

  if (error) {
    toast("Failed to load quotes – " + error.message, "error");
    return;
  }

  if (!quotes || quotes.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="icon">📭</div>
        <p>No quotes submitted for this RFQ yet.</p>
      </div>`;
    return;
  }

  // Separate valid quotes from unparseable ones that need manual review
  const validQuotes = quotes.filter((q) => q.status !== "needs_review" && q.price != null && !isNaN(q.price));
  const reviewQuotes = quotes.filter((q) => q.status === "needs_review" || q.price == null || isNaN(q.price));

  let scored = [];
  let bestPriceId = null;
  if (validQuotes.length > 0) {
    scored = scoreVendors(validQuotes);
    bestPriceId = scored.reduce((a, b) => (a.price <= b.price ? a : b)).id;
  }

  // RFQ info banner
  let rfqInfo = "";
  if (rfq) {
    const specsHtml = rfq.specifications
      ? `<div style="margin-top:0.4rem; padding-top:0.4rem; border-top:1px dashed #e2e8f0; font-size:0.825rem; color:#475569; line-height:1.5;"><strong>Specifications:</strong> ${escapeHtml(rfq.specifications)}</div>`
      : "";
    rfqInfo = `
      <div style="margin-bottom:1.25rem; padding:0.85rem 1rem; background:#f8fafc; border-radius:8px; border:1px solid #e2e8f0; font-size:0.875rem; color:#475569;">
        <div><strong>${escapeHtml(rfq.item)}</strong> · Qty: ${rfq.quantity} · Location: ${escapeHtml(rfq.delivery_location)} · Budget: ${currency(rfq.budget)}</div>
        ${specsHtml}
      </div>`;
  }

  // Review quotes warning banner
  let reviewBanner = "";
  if (reviewQuotes.length > 0) {
    reviewBanner = `
      <div style="margin-bottom:1.25rem; padding:0.85rem 1rem; background:#fffbeb; border-radius:8px; border:1px solid #fde68a; font-size:0.875rem; color:#92400e; display:flex; align-items:center; gap:0.6rem;">
        <span style="font-size:1.2rem;">⚠️</span>
        <div>
          <strong>${reviewQuotes.length} quote${reviewQuotes.length !== 1 ? "s" : ""} require manual review.</strong>
          Vendor replies were received, but pricing details could not be parsed automatically.
        </div>
      </div>`;
  }

  // Valid table rows sorted by score descending (no Action / Place Order button in full table)
  let tableRows = scored
    .map((q) => {
      const { name, email } = parseVendorInfo(q.vendor_name, q.rfq_id);
      return `
      <tr class="${q.id === bestPriceId ? "best-value" : ""}">
        <td style="font-weight:600; vertical-align:middle;">
          ${escapeHtml(name)}
          ${q.id === bestPriceId ? '<span class="badge badge-green" style="margin-left:0.5rem; font-size:0.7rem;">Lowest Price</span>' : ''}
        </td>
        <td style="color:#64748b; font-size:0.85rem; word-break:break-all; vertical-align:middle;">
          ${email ? escapeHtml(email) : '<span style="color:#94a3b8;">—</span>'}
        </td>
        <td style="font-weight:600; vertical-align:middle;">${currency(q.price)}</td>
        <td style="vertical-align:middle;">${escapeHtml(q.warranty)}</td>
        <td style="vertical-align:middle;">${escapeHtml(q.payment_terms)}</td>
        <td style="text-align:center; vertical-align:middle;">${q.delivery_days}</td>
        <td style="text-align:center; vertical-align:middle;">
          <span style="display:inline-block; padding:0.25rem 0.65rem; border-radius:999px; font-size:0.8rem; font-weight:700; background:${scoreBgColor(q.totalScore)}; color:${scoreTextColor(q.totalScore)};">
            ${Math.round(q.totalScore)}/100
          </span>
        </td>
      </tr>`;
    })
    .join("");

  // Append rows for review-needed quotes
  if (reviewQuotes.length > 0) {
    const reviewRows = reviewQuotes
      .map((q) => {
        const { name, email } = parseVendorInfo(q.vendor_name, q.rfq_id);
        const rawSnippet = q.raw_text
          ? `<details style="margin-top:0.35rem;"><summary style="cursor:pointer; font-size:0.75rem; color:#b45309;">View raw text</summary><pre style="white-space:pre-wrap; font-size:0.75rem; color:#334155; margin-top:0.25rem; background:#fff; padding:0.4rem; border:1px solid #fed7aa; border-radius:4px; max-height:120px; overflow-y:auto;">${escapeHtml(q.raw_text)}</pre></details>`
          : "";
        return `
        <tr style="background:#fffdf5;">
          <td style="font-weight:600; vertical-align:middle;">
            ${escapeHtml(name)}
            <span class="badge" style="background:#fef3c7; color:#92400e; margin-left:0.5rem; font-size:0.7rem; border:1px solid #fde68a;">Needs Review</span>
            ${rawSnippet}
          </td>
          <td style="color:#64748b; font-size:0.85rem; word-break:break-all; vertical-align:middle;">
            ${email ? escapeHtml(email) : '<span style="color:#94a3b8;">—</span>'}
          </td>
          <td style="color:#b45309; font-style:italic; vertical-align:middle;">Unparsed (Review Needed)</td>
          <td style="color:#94a3b8; vertical-align:middle;">${escapeHtml(q.warranty || "—")}</td>
          <td style="color:#94a3b8; vertical-align:middle;">${escapeHtml(q.payment_terms || "—")}</td>
          <td style="text-align:center; color:#94a3b8; vertical-align:middle;">—</td>
          <td style="text-align:center; color:#94a3b8; vertical-align:middle;">—</td>
        </tr>`;
      })
      .join("");
    tableRows += reviewRows;
  }

  // Top-3 summary cards & recommendation blurb
  let top3Html = "";
  let blurb = "";
  if (scored.length > 0) {
    top3Html = renderTopVendors(scored.slice(0, 3));
    blurb = renderRecommendation(scored[0]);
  }

  container.innerHTML = `
    ${rfqInfo}
    ${reviewBanner}
    ${blurb}
    ${top3Html}
    <h3 style="font-size:1rem; font-weight:600; margin:1.75rem 0 0.75rem;">All Vendor Quotes</h3>
    <table class="quotes-table">
      <thead>
        <tr>
          <th style="width:20%; text-align:left;">Vendor</th>
          <th style="width:23%; text-align:left;">Email</th>
          <th style="width:14%; text-align:left;">Price</th>
          <th style="width:14%; text-align:left;">Warranty</th>
          <th style="width:15%; text-align:left;">Payment Terms</th>
          <th style="width:7%; text-align:center;">Delivery (days)</th>
          <th style="width:7%; text-align:center;">Score</th>
        </tr>
      </thead>
      <tbody>${tableRows}</tbody>
    </table>
    <p style="margin-top:0.85rem; font-size:0.8rem; color:#64748b; line-height:1.6;">
      <strong>Score Weighting:</strong> Price (up to 55 pts) + Warranty (up to 20 pts) + Payment Terms (up to 15 pts) + Delivery (up to 10 pts) = 100 total pts.
    </p>`;
}

// ═════════════════════════════════════════════════════════
// SCORING ALGORITHM
// ═════════════════════════════════════════════════════════

/**
 * Parse warranty string → numeric months.
 * Handles: "3 years", "2 yr", "18 months", "1 month", bare numbers (treated as years).
 */
function parseWarrantyMonths(str) {
  if (!str) return 0;
  const s = str.toLowerCase();
  const num = parseFloat(s.match(/[\d.]+/)?.[0] ?? "0");
  if (s.includes("month")) return num;
  return num * 12; // year / yr / bare number → convert to months
}

/**
 * Parse payment terms string → numeric days.
 * Handles: "Net 30", "Net-60", "30 days", "45 day", "COD" → 0, plain numbers.
 */
function parsePaymentDays(str) {
  if (!str) return 0;
  const s = str.toLowerCase();
  if (s.includes("cod") || s.includes("advance") || s.includes("upfront")) return 0;
  const match = s.match(/[\d.]+/);
  return match ? parseFloat(match[0]) : 0;
}

/**
 * Score all vendors. Returns array sorted by totalScore descending.
 * Weights: Price 55 | Warranty 20 | Payment terms 15 | Delivery 10
 */
function scoreVendors(quotes) {
  const parsed = quotes.map((q) => ({
    ...q,
    _warrantyMonths: parseWarrantyMonths(q.warranty),
    _paymentDays: parsePaymentDays(q.payment_terms),
  }));

  const minPrice    = Math.min(...parsed.map((q) => q.price));
  const maxPrice    = Math.max(...parsed.map((q) => q.price));
  const maxWarranty = Math.max(...parsed.map((q) => q._warrantyMonths));
  const maxPayment  = Math.max(...parsed.map((q) => q._paymentDays));
  const minDelivery = Math.min(...parsed.map((q) => q.delivery_days));
  const maxDelivery = Math.max(...parsed.map((q) => q.delivery_days));

  const scored = parsed.map((q) => {
    // Lower price → higher score
    const priceScore =
      maxPrice === minPrice ? 55 : ((maxPrice - q.price) / (maxPrice - minPrice)) * 55;

    // Longer warranty → higher score
    const warrantyScore =
      maxWarranty === 0 ? 0 : (q._warrantyMonths / maxWarranty) * 20;

    // More payment days → higher score
    const paymentScore =
      maxPayment === 0 ? 0 : (q._paymentDays / maxPayment) * 15;

    // Fewer delivery days → higher score
    const deliveryScore =
      maxDelivery === minDelivery
        ? 10
        : ((maxDelivery - q.delivery_days) / (maxDelivery - minDelivery)) * 10;

    const totalScore = priceScore + warrantyScore + paymentScore + deliveryScore;

    return { ...q, priceScore, warrantyScore, paymentScore, deliveryScore, totalScore };
  });

  return scored.sort((a, b) => b.totalScore - a.totalScore);
}

/** Badge background colour based on score band. */
function scoreBgColor(score) {
  if (score >= 75) return "#dcfce7";
  if (score >= 50) return "#fef9c3";
  return "#fee2e2";
}

/** Badge text colour based on score band. */
function scoreTextColor(score) {
  if (score >= 75) return "#166534";
  if (score >= 50) return "#854d0e";
  return "#991b1b";
}

/** Render professional quotation summary cards for the top-3 vendors. */
function renderTopVendors(top3) {
  const rankLabels = ["Rank 1", "Rank 2", "Rank 3"];
  const rankClasses = ["rank-1", "rank-2", "rank-3"];

  const cards = top3
    .map((q, i) => {
      const { name, email } = parseVendorInfo(q.vendor_name, q.rfq_id);
      return `
      <div class="vendor-summary-card ${rankClasses[i]}">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem;">
          <span class="rank-badge ${rankClasses[i]}">${rankLabels[i]}</span>
          <span style="font-size:0.725rem; color:#64748b; font-weight:600; text-transform:uppercase; letter-spacing:0.04em;">Overall Score</span>
        </div>

        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:0.85rem; padding-bottom:0.75rem; border-bottom:1px solid #f1f5f9;">
          <div style="max-width:70%;">
            <h4 style="font-size:1.05rem; font-weight:700; color:#0f172a; margin:0; word-break:break-word;">${escapeHtml(name)}</h4>
            ${email ? `<div style="font-size:0.75rem; color:#64748b; margin-top:0.2rem; word-break:break-all;">${escapeHtml(email)}</div>` : ''}
          </div>
          <div style="font-size:1.35rem; font-weight:800; color:#1e293b; text-align:right;">
            ${Math.round(q.totalScore)}<span style="font-size:0.85rem; font-weight:600; color:#64748b;">/100</span>
          </div>
        </div>

        <div style="margin-bottom:0.85rem;">
          <div class="subscore-row">
            <span class="subscore-label">Price Score</span>
            <span class="subscore-value">${Math.round(q.priceScore)} / 55 pts</span>
          </div>
          <div class="subscore-row">
            <span class="subscore-label">Warranty Score</span>
            <span class="subscore-value">${Math.round(q.warrantyScore)} / 20 pts</span>
          </div>
          <div class="subscore-row">
            <span class="subscore-label">Payment Terms</span>
            <span class="subscore-value">${Math.round(q.paymentScore)} / 15 pts</span>
          </div>
          <div class="subscore-row">
            <span class="subscore-label">Delivery Score</span>
            <span class="subscore-value">${Math.round(q.deliveryScore)} / 10 pts</span>
          </div>
        </div>

        <div style="margin-top:auto; padding-top:0.75rem; border-top:1px solid #f1f5f9; font-size:0.78rem; color:#64748b; line-height:1.6;">
          <div><strong style="color:#334155;">Quoted Price:</strong> ${currency(q.price)}</div>
          <div><strong style="color:#334155;">Warranty:</strong> ${escapeHtml(q.warranty)}</div>
          <div><strong style="color:#334155;">Payment Terms:</strong> ${escapeHtml(q.payment_terms)}</div>
          <div><strong style="color:#334155;">Delivery:</strong> ${q.delivery_days} days</div>
        </div>

        <div style="margin-top:0.85rem; padding-top:0.75rem; border-top:1px solid #f1f5f9;">
          <button class="btn btn-primary btn-place-order" 
                  style="width:100%; font-size:0.825rem; padding:0.45rem 0.75rem; text-align:center;" 
                  data-quote-id="${q.id}" 
                  data-rfq-id="${q.rfq_id}" 
                  data-vendor-name="${escapeHtml(name)}" 
                  data-vendor-email="${escapeHtml(email)}" 
                  data-rank="${rankLabels[i]}">
            Place Order
          </button>
        </div>
      </div>`;
    })
    .join("");

  return `
    <h3 style="font-size:1rem; font-weight:600; margin:1.25rem 0 0.75rem;">Top Vendors Summary</h3>
    <div class="top-vendors-grid">${cards}</div>`;
}

/** Plain-English professional recommendation banner for the top-scored vendor. */
function renderRecommendation(top) {
  if (!top) return "";

  const { name: topVendorName } = parseVendorInfo(top.vendor_name, top.rfq_id);
  const score = Math.round(top.totalScore);
  const highlights = [];

  if (top._warrantyMonths > 0) {
    const yrs = top._warrantyMonths / 12;
    const label =
      yrs === Math.floor(yrs)
        ? `${yrs} year${yrs !== 1 ? "s" : ""}`
        : `${top._warrantyMonths} months`;
    highlights.push(`extended warranty of ${label}`);
  }
  if (top._paymentDays > 0)
    highlights.push(`favorable payment terms of ${top._paymentDays} days`);
  if (top.delivery_days)
    highlights.push(`expedited delivery in ${top.delivery_days} day${top.delivery_days !== 1 ? "s" : ""}`);

  let detail = "";
  if (highlights.length === 1) {
    detail = ` along with ${highlights[0]}`;
  } else if (highlights.length === 2) {
    detail = ` alongside ${highlights[0]} and ${highlights[1]}`;
  } else if (highlights.length >= 3) {
    const last = highlights.pop();
    detail = ` combined with ${highlights.join(", ")}, and ${last}`;
  }

  const tier = score >= 80 ? "outstanding" : score >= 65 ? "strong" : "competitive";

  return `
    <div class="recommendation-banner">
      <div class="recommendation-tag">Recommended</div>
      <div style="font-size: 0.9rem; color: #1e293b; line-height: 1.6;">
        <strong>${escapeHtml(topVendorName)}</strong> is recommended as the top vendor with the highest overall score of 
        <strong>${score}/100</strong>, delivering a ${tier} commercial package with competitive pricing at ${currency(top.price)}${detail}.
      </div>
    </div>`;
}

// ═════════════════════════════════════════════════════════
// Purchase Order Modal & Placement Logic
// ═════════════════════════════════════════════════════════
let currentOrderContext = null;

function triggerDownload(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.style.display = "none";
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  }, 200);
}

function base64ToBlob(base64, mimeType = "application/pdf") {
  const byteCharacters = atob(base64);
  const byteNumbers = new Array(byteCharacters.length);
  for (let i = 0; i < byteCharacters.length; i++) {
    byteNumbers[i] = byteCharacters.charCodeAt(i);
  }
  const byteArray = new Uint8Array(byteNumbers);
  return new Blob([byteArray], { type: mimeType });
}

function guessVendorEmail(vendorName, rfqId) {
  return parseVendorInfo(vendorName, rfqId).email;
}

function openPlaceOrderModal(rfqId, quoteId, vendorName, rank, explicitEmail = "") {
  currentOrderContext = { rfqId, quoteId, vendorName, rank };

  const guessedEmail = explicitEmail || guessVendorEmail(vendorName, rfqId);
  const modal = $("#po-modal");
  const emailInput = $("#po-modal-email");
  const questionEl = $("#po-modal-question");
  const infoEl = $("#po-modal-vendor-info");
  const titleEl = $("#po-modal-title");

  titleEl.textContent = `Place Purchase Order – ${rank}`;
  infoEl.innerHTML = `Selected Vendor: <strong>${escapeHtml(vendorName)}</strong> (Quote #${quoteId}) · <strong>RFQ-${rfqId}</strong>`;
  emailInput.value = guessedEmail;

  function updateQuestion() {
    const em = emailInput.value.trim();
    questionEl.textContent = em
      ? `Send order confirmation to ${em}?`
      : `Send order confirmation to this vendor?`;
  }
  updateQuestion();
  emailInput.oninput = updateQuestion;

  modal.style.display = "flex";
}

function closePlaceOrderModal() {
  const modal = $("#po-modal");
  modal.style.display = "none";
  currentOrderContext = null;
}

// Delegation for Place Order clicks on Rank 1, 2, 3 cards and All Vendor Quotes table rows
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".btn-place-order");
  if (!btn) return;
  const quoteId = parseInt(btn.dataset.quoteId, 10);
  const rfqId = parseInt(btn.dataset.rfqId, 10);
  const vendorName = btn.dataset.vendorName || "Vendor";
  const vendorEmail = btn.dataset.vendorEmail || "";
  const rank = btn.dataset.rank || "Selected Vendor";
  openPlaceOrderModal(rfqId, quoteId, vendorName, rank, vendorEmail);
});

$("#po-modal-close").addEventListener("click", closePlaceOrderModal);
$("#po-btn-cancel").addEventListener("click", closePlaceOrderModal);

// Close modal when clicking on backdrop outside box
$("#po-modal").addEventListener("click", (e) => {
  if (e.target.id === "po-modal") closePlaceOrderModal();
});

// "No (Download Only)" handler
$("#po-btn-no").addEventListener("click", async () => {
  if (!currentOrderContext) return;
  const { rfqId, quoteId } = currentOrderContext;
  const vendorEmail = $("#po-modal-email").value.trim();
  const btn = $("#po-btn-no");
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Downloading…";

  try {
    const resp = await fetch("http://localhost:5000/place-order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        rfq_id: rfqId,
        quote_id: quoteId,
        vendor_email: vendorEmail,
        action: "download",
      }),
    });

    if (!resp.ok) {
      const errJson = await resp.json().catch(() => ({}));
      throw new Error(errJson.error || `Server error ${resp.status}`);
    }

    const blob = await resp.blob();
    triggerDownload(blob, `PO-${rfqId}.pdf`);
    toast(`Purchase Order PO-${rfqId}.pdf downloaded`, "success");
    closePlaceOrderModal();
  } catch (err) {
    console.error(err);
    toast("Failed to download PO: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
});

// "Yes (Send Email & Download)" handler
$("#po-btn-yes").addEventListener("click", async () => {
  if (!currentOrderContext) return;
  const { rfqId, quoteId } = currentOrderContext;
  const vendorEmail = $("#po-modal-email").value.trim();

  if (!vendorEmail || !vendorEmail.includes("@")) {
    toast("Please provide a valid vendor email address to send confirmation.", "error");
    $("#po-modal-email").focus();
    return;
  }

  const btn = $("#po-btn-yes");
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Sending PO…";

  try {
    const resp = await fetch("http://localhost:5000/place-order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        rfq_id: rfqId,
        quote_id: quoteId,
        vendor_email: vendorEmail,
        action: "email",
      }),
    });

    const result = await resp.json();
    if (!resp.ok) {
      throw new Error(result.error || `Server error ${resp.status}`);
    }

    toast(`Purchase Order PO-${rfqId} sent to ${vendorEmail}`, "success");

    // Automatically trigger PDF download as well
    if (result.pdf_base64) {
      const blob = base64ToBlob(result.pdf_base64, "application/pdf");
      triggerDownload(blob, `PO-${rfqId}.pdf`);
    }

    closePlaceOrderModal();
  } catch (err) {
    console.error(err);
    toast("Failed to send PO email: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
});

// ── Init ───────────────────────────────────────────────
showPage("page-rfq");
loadRecentRfqs();
