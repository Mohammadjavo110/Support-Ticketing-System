import { useCallback, useEffect, useRef, useState } from 'react';
import { api, formatDateTime, unwrapTicket } from '../api.js';
import { EmptyState, ErrorBanner, Loading, Notice } from './Ui.jsx';
import { PriorityBadge, StatusBadge } from './Badges.jsx';
import SuggestionPanel from './SuggestionPanel.jsx';

function authorLabel(message) {
  if (!message || typeof message !== 'object') return '';
  if (message.author_name) return message.author_name;
  if (message.display_name) return message.display_name;
  if (message.author && message.author.display_name) return message.author.display_name;
  if (message.author && message.author.email) return message.author.email;
  if (message.author_email) return message.author_email;
  return '';
}

export default function TicketThread({ ticketId, role, onBack }) {
  const [ticket, setTicket] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reply, setReply] = useState('');
  const [replyError, setReplyError] = useState('');
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState('');
  const [statusBusy, setStatusBusy] = useState(false);
  const [statusError, setStatusError] = useState('');
  const listRef = useRef(null);

  const load = useCallback(
    async ({ silent = false } = {}) => {
      if (!silent) setLoading(true);
      setError('');
      try {
        const data = await api.getTicket(ticketId);
        const unwrapped = unwrapTicket(data);
        setTicket(unwrapped.ticket);
        setMessages(unwrapped.messages);
      } catch (err) {
        setError(err.message);
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [ticketId]
  );

  useEffect(() => {
    load();
  }, [load]);

  // Poll for new replies (e.g. the agent answer) while the thread is open.
  useEffect(() => {
    const timer = setInterval(() => {
      load({ silent: true });
    }, 15000);
    return () => clearInterval(timer);
  }, [load]);

  // Keep the newest message visible.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, ticketId]);

  // Agent-only status moves along the state machine (open → in_progress →
  // closed). `answered` is not offered here: it comes from the decision panel.
  async function changeStatus(next) {
    setStatusBusy(true);
    setStatusError('');
    setNotice('');
    try {
      await api.updateStatus(ticketId, next);
      setNotice(
        next === 'in_progress'
          ? 'تیکت در حال بررسی قرار گرفت.'
          : 'تیکت بسته شد؛ مشتری دیگر نمی‌تواند در آن پاسخ دهد.'
      );
      await load({ silent: true });
    } catch (err) {
      setStatusError(err.message);
    } finally {
      setStatusBusy(false);
    }
  }

  async function sendReply(event) {
    event.preventDefault();
    const body = reply.trim();
    if (!body) {
      setReplyError('متن پاسخ الزامی است.');
      return;
    }
    setReplyError('');
    setNotice('');
    setSending(true);
    try {
      await api.postMessage(ticketId, body);
      setReply('');
      setNotice('پاسخ شما ارسال شد.');
      await load({ silent: true });
    } catch (err) {
      setReplyError(err.message);
    } finally {
      setSending(false);
    }
  }

  const firstMessage = messages.length > 0 ? messages[0] : null;
  const showTicketBody = Boolean(ticket && ticket.body) &&
    (!firstMessage || firstMessage.body !== ticket.body);

  return (
    <section className="page" aria-labelledby="thread-title">
      <div className="thread-head">
        <button type="button" className="btn btn--ghost btn--sm" onClick={onBack}>
          بازگشت
        </button>
        <h2 className="thread-head__title" id="thread-title">
          {ticket ? ticket.subject : 'جزئیات تیکت'}
        </h2>
        <span className="thread-badges">
          {ticket ? <StatusBadge status={ticket.status} /> : null}
          {ticket ? <PriorityBadge priority={ticket.priority} /> : null}
        </span>
      </div>

      <ErrorBanner message={error} onRetry={() => load()} />
      <Notice message={notice} />

      {loading ? (
        <div className="card">
          <Loading />
        </div>
      ) : !ticket ? (
        <div className="card">
          {error ? (
            <EmptyState
              title="اطلاعاتی دریافت نشد"
              hint="ارتباط با سرور برقرار نیست؛ پس از برطرف شدن خطا «تلاش دوباره» را بزنید."
            />
          ) : (
            <EmptyState title="تیکت پیدا نشد" hint="شاید این تیکت حذف شده باشد." />
          )}
        </div>
      ) : (
        <>
          <div className="card conversation" ref={listRef}>
            {showTicketBody ? (
              <div className="ticket-body">
                <div className="ticket-body__label">متن تیکت</div>
                <p className="ticket-body__text">{ticket.body}</p>
              </div>
            ) : null}

            {messages.length === 0 && !showTicketBody ? (
              <EmptyState title="هنوز پیامی در این گفتگو نیست" />
            ) : (
              messages.map((message, index) => {
                const direction = message.direction === 'out' ? 'out' : 'in';
                const author = authorLabel(message);
                return (
                  <div
                    key={message.id || `${ticketId}-${index}`}
                    className={`msg-row msg-row--${direction}`}
                  >
                    <div className="msg">
                      <div className="msg__meta">
                        <span>{direction === 'in' ? 'دریافتی' : 'ارسالی'}</span>
                        {author ? <span>{author}</span> : null}
                        <span>{formatDateTime(message.created_at)}</span>
                      </div>
                      <p className="msg__text">{message.body}</p>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {role === 'agent' ? (
            <div className="card status-actions" aria-labelledby="status-actions-title">
              <h3 className="status-actions__title" id="status-actions-title">
                وضعیت تیکت
              </h3>
              <p className="muted">
                جابه‌جایی وضعیت فقط مطابق قواعد است: باز ← در حال بررسی ← پاسخ داده شد ← بسته.
                پس از «پاسخ داده شد» فقط بستن مجاز است و تیکت بسته‌شده هرگز باز نمی‌شود.
              </p>
              <div className="status-actions__buttons">
                {ticket.status === 'open' ? (
                  <button
                    type="button"
                    className="btn btn--sm"
                    disabled={statusBusy}
                    onClick={() => changeStatus('in_progress')}
                  >
                    {statusBusy ? 'در حال ثبت…' : 'شروع بررسی'}
                  </button>
                ) : null}
                {ticket.status === 'in_progress' || ticket.status === 'answered' ? (
                  <button
                    type="button"
                    className="btn btn--danger btn--sm"
                    disabled={statusBusy}
                    onClick={() => changeStatus('closed')}
                  >
                    {statusBusy ? 'در حال ثبت…' : 'بستن تیکت'}
                  </button>
                ) : null}
                {ticket.status === 'closed' ? (
                  <p className="muted">این تیکت بسته شده است و وضعیت آن دیگر تغییر نمی‌کند.</p>
                ) : null}
              </div>
              {statusError ? (
                <p className="field-error" role="alert">
                  {statusError}
                </p>
              ) : null}
            </div>
          ) : null}

          {role === 'agent' ? (
            <p className="muted">
              به‌عنوان کارشناس، پاسخ نهایی از طریق پنل «پیشنهاد هوشمند» ثبت می‌شود.
            </p>
          ) : (
            <form className="card reply" onSubmit={sendReply} noValidate>
              <div className="field">
                <label htmlFor="reply-body">پاسخ دهید</label>
                <textarea
                  id="reply-body"
                  rows={3}
                  value={reply}
                  onChange={(event) => setReply(event.target.value)}
                  placeholder="متن پاسخ خود را بنویسید…"
                />
              </div>
              {replyError ? (
                <p className="field-error" role="alert">
                  {replyError}
                </p>
              ) : null}
              <div className="reply__actions">
                <button type="submit" className="btn btn--primary" disabled={sending}>
                  {sending ? 'در حال ارسال…' : 'پاسخ دهید'}
                </button>
              </div>
            </form>
          )}

          {role === 'agent' ? (
            <SuggestionPanel ticketId={ticketId} onChanged={() => load({ silent: true })} />
          ) : null}
        </>
      )}
    </section>
  );
}
