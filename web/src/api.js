// ---------------------------------------------------------------------------
// API layer for the support-ticket frontend.
// - Talks to the api service (default http://localhost:8000).
// - Every request carries the HttpOnly session cookie (credentials: 'include').
// - No token is ever stored, printed or logged here (the session lives in a
//   HttpOnly cookie we cannot read anyway).
// - Set VITE_API_BASE="" to go through the Vite dev proxy (/api -> api
//   service) instead of the absolute URL.
// ---------------------------------------------------------------------------

const API_BASE = String(import.meta.env.VITE_API_BASE ?? 'http://localhost:8000').replace(/\/+$/, '');

const FA_DIGITS = ['۰', '۱', '۲', '۳', '۴', '۵', '۶', '۷', '۸', '۹'];

const STATUS_LABELS = {
  open: 'باز',
  in_progress: 'در حال بررسی',
  answered: 'پاسخ داده شد',
  closed: 'بسته شده',
};

const PRIORITY_LABELS = {
  1: 'زیاد',
  2: 'معمولی',
  3: 'کم',
};

const CATEGORY_LABELS = {
  technical: 'فنی',
  tech: 'فنی',
  bug: 'خطا',
  billing: 'صورتحساب',
  payment: 'پرداخت',
  account: 'حساب کاربری',
  login: 'ورود',
  other: 'سایر',
  unknown: 'نامشخص',
};

/** Render a value with Persian digits (idempotent for non-numeric text). */
export function toFa(value) {
  return String(value).replace(/\d/g, (d) => FA_DIGITS[Number(d)]);
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || status || 'نامشخص';
}

export function priorityLabel(priority) {
  return PRIORITY_LABELS[Number(priority)] || 'نامشخص';
}

export function categoryLabel(raw) {
  if (!raw) return 'نامشخص';
  const key = String(raw).toLowerCase();
  return CATEGORY_LABELS[key] || String(raw);
}

/** 401 means the session cookie is gone/expired: tell the app to log out. */
function notifyExpired() {
  if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    window.dispatchEvent(new CustomEvent('auth:expired'));
  }
}

/** Turn a FastAPI error payload into a Persian, human readable message. */
function messageFromPayload(data, status) {
  const detail = data && data.detail;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => (typeof d === 'string' ? d : (d && (d.msg || d.message)) || ''))
      .filter(Boolean);
    if (parts.length) return parts.join(' ؛ ');
  }
  if (data && typeof data.message === 'string' && data.message.trim()) {
    return data.message.trim();
  }
  return `خطای سرور (کد ${toFa(status)})`;
}

async function request(path, options = {}) {
  const method = options.method || 'GET';
  const body = options.body;

  let res;
  try {
    res = await fetch(API_BASE + path, {
      method,
      credentials: 'include',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    const error = new Error('ارتباط با سرور برقرار نشد؛ سرویس در دسترس نیست.');
    error.network = true;
    throw error;
  }

  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (err) {
      data = null;
    }
  }

  if (res.status === 401) notifyExpired();

  if (!res.ok) {
    const error = new Error(messageFromPayload(data, res.status));
    error.status = res.status;
    throw error;
  }
  return data;
}

export const api = {
  register: (payload) => request('/api/auth/register', { method: 'POST', body: payload }),
  login: (payload) => request('/api/auth/login', { method: 'POST', body: payload }),
  logout: () => request('/api/auth/logout', { method: 'POST' }),

  createTicket: (payload) => request('/api/tickets', { method: 'POST', body: payload }),
  myTickets: () => request('/api/tickets/my'),
  getTicket: (id) => request(`/api/tickets/${encodeURIComponent(id)}`),
  postMessage: (id, body) =>
    request(`/api/tickets/${encodeURIComponent(id)}/messages`, { method: 'POST', body: { body } }),

  inbox: () => request('/api/inbox'),
  getSuggestion: (ticketId) => request(`/api/suggestions/${encodeURIComponent(ticketId)}`),
  decide: (ticketId, decision, text) =>
    request(`/api/suggestions/${encodeURIComponent(ticketId)}/decision`, {
      method: 'POST',
      body: text ? { decision, text } : { decision },
    }),

  health: () => request('/healthz'),
};

// --- response normalisation (the API may wrap payloads in different shapes) ---

/** Accept a bare array or a common envelope ({items|tickets|inbox: [...]}). */
export function asList(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.items)) return data.items;
  if (data && Array.isArray(data.tickets)) return data.tickets;
  if (data && Array.isArray(data.inbox)) return data.inbox;
  if (data && Array.isArray(data.messages)) return data.messages;
  return [];
}

/** GET /api/tickets/{id} may return the ticket directly or under `ticket`. */
export function unwrapTicket(data) {
  if (!data || typeof data !== 'object') return { ticket: null, messages: [] };
  if (data.ticket && typeof data.ticket === 'object') {
    const t = data.ticket;
    const messages = Array.isArray(data.messages)
      ? data.messages
      : Array.isArray(t.messages)
        ? t.messages
        : [];
    return { ticket: t, messages };
  }
  return { ticket: data, messages: Array.isArray(data.messages) ? data.messages : [] };
}

/** GET /api/suggestions/{id} may return the row directly or under `suggestion`. */
export function unwrapSuggestion(data) {
  if (!data || typeof data !== 'object') return null;
  if (Object.prototype.hasOwnProperty.call(data, 'suggestion')) return data.suggestion || null;
  if (Object.prototype.hasOwnProperty.call(data, 'ai_suggestion')) return data.ai_suggestion || null;
  if (data.detail) return null;
  return data;
}

/** Latest suggestion attached to an inbox row, if the API joined one in. */
export function suggestionOf(row) {
  if (!row || typeof row !== 'object') return null;
  return row.suggestion || row.latest_suggestion || row.ai_suggestion || null;
}

export function customerName(row) {
  if (!row || typeof row !== 'object') return 'نامعلوم';
  if (typeof row.customer === 'string' && row.customer.trim()) return row.customer.trim();
  if (row.customer_name) return row.customer_name;
  if (row.customer_display_name) return row.customer_display_name;
  if (row.customer && row.customer.display_name) return row.customer.display_name;
  if (row.customer && row.customer.email) return row.customer.email;
  if (row.customer_email) return row.customer_email;
  if (row.display_name) return row.display_name;
  if (row.email) return row.email;
  return 'نامعلوم';
}

function confidenceOf(row) {
  const s = suggestionOf(row);
  const value = s ? s.category_confidence : undefined;
  const raw = value !== undefined && value !== null ? value : row.category_confidence;
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : null;
}

export function confidencePercent(raw) {
  if (raw === null || raw === undefined) return null;
  // Accept both 0..1 and 0..100 styles.
  const pct = raw <= 1 ? Math.round(raw * 100) : Math.round(raw);
  return Math.max(0, Math.min(100, pct));
}

/** e.g. "دسته: فنی · اطمینان ۸۱٪" */
export function categoryLine(row) {
  const s = suggestionOf(row);
  const raw = s && s.category ? s.category : row && row.category;
  const cat = categoryLabel(raw);
  const pct = confidencePercent(confidenceOf(row));
  return pct === null ? `دسته: ${cat}` : `دسته: ${cat} · اطمینان ${toFa(pct)}٪`;
}

export function needsManualTriage(row) {
  if (!row || typeof row !== 'object') return false;
  if (typeof row.needs_manual_triage === 'boolean') return row.needs_manual_triage;
  const s = suggestionOf(row);
  return Boolean(s && s.needs_manual_triage);
}

/** e.g. "۲ ساعت انتظار" */
export function waitingLabel(iso) {
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return '—';
  const hours = Math.floor(ms / 3600000);
  if (hours < 1) return 'کمتر از یک ساعت انتظار';
  return `${toFa(hours)} ساعت انتظار`;
}

export function formatDateTime(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('fa-IR', {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch (err) {
    return '';
  }
}
