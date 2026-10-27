import { useCallback, useEffect, useState } from 'react';
import { api, asList, formatDateTime } from '../api.js';
import { EmptyState, ErrorBanner, Loading, Notice } from './Ui.jsx';
import { PriorityBadge, StatusBadge } from './Badges.jsx';

const EMPTY_FORM = { subject: '', body: '' };

export default function CustomerView({ onOpenTicket }) {
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [form, setForm] = useState(EMPTY_FORM);
  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.myTickets();
      const rows = asList(data).filter((t) => t && t.id);
      setTickets(rows);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function update(field) {
    return (event) => setForm((prev) => ({ ...prev, [field]: event.target.value }));
  }

  async function createTicket(event) {
    event.preventDefault();
    const subject = form.subject.trim();
    const body = form.body.trim();
    if (!subject) {
      setFormError('موضوع تیکت الزامی است.');
      return;
    }
    if (!body) {
      setFormError('متن تیکت الزامی است.');
      return;
    }
    setFormError('');
    setNotice('');
    setSubmitting(true);
    try {
      await api.createTicket({ subject, body });
      setForm(EMPTY_FORM);
      setNotice('تیکت شما ثبت شد و در صف بررسی کارشناسان قرار گرفت.');
      await load();
    } catch (err) {
      setFormError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="page" aria-labelledby="my-tickets-title">
      <div className="page__head">
        <h2 className="page__title" id="my-tickets-title">
          تیکت‌های من
        </h2>
        <button type="button" className="btn btn--sm" onClick={() => { setNotice(''); load(); }}>
          به‌روزرسانی
        </button>
      </div>

      <Notice message={notice} />

      <div className="split">
        <div className="card">
          <h3 className="section-title">فهرست تیکت‌ها</h3>
          <ErrorBanner message={error} onRetry={() => { setNotice(''); load(); }} />
          {loading ? (
            <Loading />
          ) : error && tickets.length === 0 ? (
            <EmptyState title="اطلاعاتی دریافت نشد" hint="خطای سرور را برطرف کنید و دوباره تلاش کنید." />
          ) : tickets.length === 0 ? (
            <EmptyState
              title="هنوز تیکتی ندارید"
              hint="از فرم کنار همین صفحه اولین تیکت خود را ثبت کنید."
            />
          ) : (
            <ul>
              {tickets.map((ticket) => (
                <li key={ticket.id}>
                  <button
                    type="button"
                    className="ticket-row"
                    onClick={() => onOpenTicket(ticket.id)}
                  >
                    <span className="ticket-row__main">
                      <span className="ticket-row__subject">{ticket.subject}</span>
                      <span className="ticket-row__meta">
                        {formatDateTime(ticket.updated_at || ticket.created_at)}
                      </span>
                    </span>
                    <span className="ticket-row__badges">
                      <StatusBadge status={ticket.status} />
                      <PriorityBadge priority={ticket.priority} />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <form className="card" onSubmit={createTicket} noValidate>
          <h3 className="section-title">تیکت جدید</h3>
          <div className="field">
            <label htmlFor="ticket-subject">موضوع</label>
            <input
              id="ticket-subject"
              type="text"
              value={form.subject}
              onChange={update('subject')}
              placeholder="مثلاً خطای درگاه پرداخت"
            />
          </div>
          <div className="field">
            <label htmlFor="ticket-body">متن تیکت</label>
            <textarea
              id="ticket-body"
              value={form.body}
              onChange={update('body')}
              rows={6}
              placeholder="مشکل خود را شرح دهید…"
            />
          </div>
          {formError ? (
            <p className="field-error" role="alert">
              {formError}
            </p>
          ) : null}
          <button type="submit" className="btn btn--primary btn--block" disabled={submitting}>
            {submitting ? 'در حال ثبت…' : 'ثبت تیکت'}
          </button>
        </form>
      </div>
    </section>
  );
}
