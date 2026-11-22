/* Step 5: shared helpers the pages use to talk to the REST API (api/ in Flask).
 *
 *   api("GET", "/api/orders?page=2")      -> the JSON reply, or throws ApiError
 *   showState(el, "loading" | "empty" | "error", message, retryFn)
 *   toast("Order saved", "success")
 *   showFieldErrors(form, error.details)   -> red message under each wrong field
 *
 * The login token is put in the page by base.html (<meta name="api-token">) and sent as
 * "Authorization: Bearer <token>" on every call. */
"use strict";

class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status; this.code = code; this.details = details || {};
  }
}

const API_TOKEN = document.querySelector('meta[name="api-token"]')?.content || "";
const CAN_EDIT = document.querySelector('meta[name="can-edit"]')?.content === "true";

async function api(method, path, body) {
  const options = { method, headers: { Authorization: `Bearer ${API_TOKEN}` } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (e) {
    throw new ApiError(0, "network", "Cannot reach the server. Check that the app is running.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const err = data.error || {};
    if (response.status === 401) {  // token missing or expired: back to the login page
      toast("Your login has expired. Please log in again.", "danger");
      setTimeout(() => { location.href = "/login?next=" + encodeURIComponent(location.pathname); }, 1500);
    }
    throw new ApiError(response.status, err.code || "error", err.message || "Something went wrong.", err.details);
  }
  return data;
}

/* ---- small formatting helpers ---- */
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function num(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function zar(value, digits = 0) { return value === null || value === undefined ? "n/a" : "R " + num(value, digits); }
function pct(value, digits = 0) { return value === null || value === undefined ? "n/a" : num(value * 100, digits) + "%"; }
function monthLabel(iso) {  // "2026-10-01" or "2026-10" -> "Oct 2026"
  const [y, m] = String(iso).split("-");
  return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString("en-GB", { month: "short", year: "numeric" });
}

/* ---- loading / empty / error states for a table body or a box ---- */
function showState(el, state, message, retry) {
  const cols = el.tagName === "TBODY" ? el.closest("table").querySelectorAll("thead th").length : 0;
  const inner = {
    loading: `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>${esc(message || "Loading…")}`,
    empty: `<i class="bi bi-inbox me-2" aria-hidden="true"></i>${esc(message || "Nothing to show yet.")}`,
    error: `<i class="bi bi-exclamation-triangle me-2" aria-hidden="true"></i>${esc(message || "Could not load the data.")}` +
           (retry ? ' <button type="button" class="btn btn-sm btn-outline-danger ms-2 retry">Try again</button>' : ""),
  }[state];
  const cls = `state state-${state}${state === "error" ? " text-danger" : " text-muted"}`;
  el.innerHTML = cols ? `<tr><td colspan="${cols}" class="${cls}" role="status">${inner}</td></tr>`
                      : `<div class="${cls}" role="status">${inner}</div>`;
  if (retry) el.querySelector(".retry")?.addEventListener("click", retry);
}

/* ---- confirmation messages (Bootstrap toast, bottom right) ---- */
function toast(message, type = "success") {
  let box = document.getElementById("toast-box");
  if (!box) {
    box = document.createElement("div");
    box.id = "toast-box"; box.className = "toast-container position-fixed bottom-0 end-0 p-3";
    document.body.appendChild(box);
  }
  const t = document.createElement("div");
  t.className = `toast align-items-center text-bg-${type} border-0`;
  t.setAttribute("role", type === "danger" ? "alert" : "status");
  t.innerHTML = `<div class="d-flex"><div class="toast-body">${esc(message)}</div>` +
                `<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>`;
  box.appendChild(t);
  const bsToast = new bootstrap.Toast(t, { delay: 4000 });
  t.addEventListener("hidden.bs.toast", () => t.remove());
  bsToast.show();
}

/* ---- show the API's per-field validation messages on a form ---- */
function clearFieldErrors(form) {
  form.querySelectorAll(".is-invalid").forEach(el => el.classList.remove("is-invalid"));
  form.querySelectorAll(".api-feedback").forEach(el => el.remove());
  form.querySelector(".form-error")?.remove();
}
function showFieldErrors(form, err) {
  clearFieldErrors(form);
  let unplaced = [];
  for (const [field, message] of Object.entries(err.details || {})) {
    const input = form.querySelector(`[name="${field}"]`);
    if (!input) { unplaced.push(`${field}: ${message}`); continue; }
    input.classList.add("is-invalid");
    input.insertAdjacentHTML("afterend", `<div class="invalid-feedback api-feedback">${esc(message)}</div>`);
  }
  const text = err.details && Object.keys(err.details).length && !unplaced.length ? err.message
             : [err.message, ...unplaced].join(" ");
  form.insertAdjacentHTML("afterbegin", `<div class="alert alert-danger py-2 small form-error" role="alert">${esc(text)}</div>`);
  form.querySelector(".is-invalid")?.focus();
}

/* ---- disable a button while a request runs ---- */
async function busy(button, work) {
  const label = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Saving…';
  try { return await work(); } finally { button.disabled = false; button.innerHTML = label; }
}
