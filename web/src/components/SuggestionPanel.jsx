import { useCallback, useEffect, useState } from 'react';
import {
  api,
  categoryLabel,
  confidencePercent,
  toFa,
  unwrapSuggestion,
} from '../api.js';
import { ErrorBanner, Loading, Notice } from './Ui.jsx';
import { TriageChip } from './Badges.jsx';

const DECISION_MESSAGES = {
  accepted: 'پذیرش انجام شد؛ پاسخ برای مشتری ارسال و وضعیت تیکت به «پاسخ داده شد» تغییر کرد.',
  edited: 'پاسخ ویرایش‌شده ثبت شد و برای مشتری ارسال گردید.',
  rejected: 'پیشنهاد رد شد و رویداد آن ثبت گردید.',
};

export default function SuggestionPanel({ ticketId, onChanged }) {
  const [suggestion, setSuggestion] = useState(null);
  const [hasRow, setHasRow] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [success, setSuccess] = useState('');
  const [mode, setMode] = useState('view');
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getSuggestion(ticketId);
      const row = unwrapSuggestion(data);
      setSuggestion(row);
      setHasRow(Boolean(row));
    } catch (err) {
      if (err.status === 404) {
        // No suggestion row for this ticket yet — not an error.
        setSuggestion(null);
        setHasRow(false);
        setError('');
      } else {
        setError(err.message);
      }
    } finally {
      setLoading(false);
    }
  }, [ticketId]);

  useEffect(() => {
    load();
  }, [load]);

  const draftText = suggestion && suggestion.draft_text ? String(suggestion.draft_text).trim() : '';
  const draftStatus = suggestion ? suggestion.draft_status : null;
  const hasDraft = Boolean(draftText);

  // Reset the inline editor whenever a fresh suggestion arrives.
  useEffect(() => {
    setMode('view');
    setDraft(draftText);
    setActionError('');
  }, [draftText]);

  async function decide(decision, text) {
    setBusy(true);
    setActionError('');
    setSuccess('');
    try {
      await api.decide(ticketId, decision, text);
      setSuccess(DECISION_MESSAGES[decision] || 'تصمیم شما ثبت شد.');
      setMode('view');
      await load();
      if (onChanged) onChanged();
    } catch (err) {
      setActionError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <section className="card" aria-labelledby="suggest-title">
        <div className="suggest__head">
          <h3 className="suggest__title" id="suggest-title">
            پیشنهاد هوشمند
          </h3>
        </div>
        <Loading label="در حال بارگذاری پیشنهاد…" />
      </section>
    );
  }

  const pct = hasRow ? confidencePercent(suggestion.category_confidence) : null;
  const info = hasRow ? (
    <div className="suggest__info">
      <span>
        دسته: {categoryLabel(suggestion.category)}
        {pct === null ? '' : ` · اطمینان ${toFa(pct)}٪`}
      </span>
      {suggestion.needs_manual_triage ? <TriageChip /> : null}
    </div>
  ) : null;

  let draftBlock;
  let note = '';

  if (!hasRow) {
    draftBlock = <p className="suggest__none">پیشنهادی موجود نیست</p>;
    note = 'برای این تیکت هنوز پیشنهادی ثبت نشده است؛ می‌توانید با «ویرایش» پاسخ خودتان را بنویسید.';
  } else if (draftStatus === 'failed') {
    draftBlock = <p className="suggest__none">پیشنهادی موجود نیست</p>;
    note = 'پیشنهاد هوشمند متن پیش‌نویس نساخت (خطای مدل)؛ با «ویرایش» متن پاسخ را خودتان بنویسید یا پیشنهاد را رد کنید.';
  } else if (!hasDraft) {
    draftBlock = <p className="suggest__draft">پیشنهاد در حال آماده‌سازی است…</p>;
    note = 'پیش‌نویس هنوز آماده نیست؛ کمی بعد این بخش را به‌روزرسانی کنید.';
  } else {
    draftBlock = <p className="suggest__draft">{draftText}</p>;
  }

  return (
    <section className="card" aria-labelledby="suggest-title">
      <div className="suggest__head">
        <h3 className="suggest__title" id="suggest-title">
          پیشنهاد هوشمند
        </h3>
        <button type="button" className="btn btn--sm" onClick={load} disabled={busy}>
          به‌روزرسانی
        </button>
      </div>

      <ErrorBanner message={error} onRetry={load} />
      <Notice message={success} />
      <ErrorBanner message={actionError} />

      {info}
      {draftBlock}
      {note ? <p className="suggest__note">{note}</p> : null}

      {mode === 'edit' ? (
        <div className="suggest__editor">
          <label htmlFor="suggest-edit">متن پاسخ</label>
          <textarea
            id="suggest-edit"
            rows={6}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="متن پاسخ را ویرایش کنید…"
          />
          <div className="suggest__actions">
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy || !draft.trim()}
              onClick={() => decide('edited', draft.trim())}
            >
              {busy ? 'در حال ثبت…' : 'ثبت ویرایش'}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={busy}
              onClick={() => {
                setMode('view');
                setDraft(draftText);
                setActionError('');
              }}
            >
              انصراف
            </button>
          </div>
        </div>
      ) : (
        <div className="suggest__actions">
          <button
            type="button"
            className="btn btn--primary"
            disabled={!hasDraft || busy}
            title={hasDraft ? undefined : 'پیش‌نویسی برای پذیرش موجود نیست'}
            onClick={() => decide('accepted')}
          >
            پذیرش
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => setMode('edit')}>
            ویرایش
          </button>
          <button
            type="button"
            className="btn btn--danger"
            disabled={busy}
            onClick={() => decide('rejected')}
          >
            رد
          </button>
        </div>
      )}
    </section>
  );
}
