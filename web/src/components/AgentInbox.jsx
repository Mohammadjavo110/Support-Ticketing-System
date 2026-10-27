import { useCallback, useEffect, useState } from 'react';
import { api, asList, categoryLine, customerName, needsManualTriage, waitingLabel } from '../api.js';
import { EmptyState, ErrorBanner, Loading } from './Ui.jsx';
import { PriorityBadge, StatusBadge, TriageChip } from './Badges.jsx';

export default function AgentInbox({ onOpenTicket }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.inbox();
      // Rows keep the order the API returned (triage flag, priority, waiting).
      setRows(asList(data).filter((row) => row && row.id));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="page" aria-labelledby="inbox-title">
      <div className="page__head">
        <h2 className="page__title" id="inbox-title">
          صندوق ورودی کارشناس
        </h2>
        <button type="button" className="btn btn--sm" onClick={load}>
          به‌روزرسانی
        </button>
      </div>

      <div className="card">
        <ErrorBanner message={error} onRetry={load} />
        {loading ? (
          <Loading />
        ) : error && rows.length === 0 ? (
          <EmptyState title="اطلاعاتی دریافت نشد" hint="پس از برقراری ارتباط با سرور دوباره تلاش کنید." />
        ) : rows.length === 0 ? (
          <EmptyState
            title="صندوق ورودی خالی است"
            hint="در حال حاضر تیکتی در انتظار بررسی نیست."
          />
        ) : (
          <ul>
            {rows.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  className="inbox-row"
                  onClick={() => onOpenTicket(row.id)}
                  aria-label={`باز کردن تیکت ${row.subject}`}
                >
                  <span className="inbox-row__subject">{row.subject}</span>
                  <span className="inbox-row__customer">{customerName(row)}</span>
                  <PriorityBadge priority={row.priority} />
                  <span className="inbox-row__wait">{waitingLabel(row.updated_at || row.created_at)}</span>
                  <span className="inbox-row__cat">{categoryLine(row)}</span>
                  {needsManualTriage(row) ? <TriageChip /> : null}
                  <StatusBadge status={row.status} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
