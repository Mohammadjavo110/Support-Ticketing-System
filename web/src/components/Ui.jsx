// Small shared UI states: loading, empty, error and success banners.

export function Loading({ label = 'در حال بارگذاری…' }) {
  return (
    <p className="loading" role="status" aria-live="polite">
      {label}
    </p>
  );
}

export function EmptyState({ title, hint }) {
  return (
    <div className="empty" role="status">
      <p className="empty__title">{title}</p>
      {hint ? <p className="empty__hint">{hint}</p> : null}
    </div>
  );
}

export function ErrorBanner({ message, onRetry }) {
  if (!message) return null;
  return (
    <div className="banner banner--error" role="alert">
      <span>{message}</span>
      {onRetry ? (
        <button type="button" className="banner__retry" onClick={onRetry}>
          تلاش دوباره
        </button>
      ) : null}
    </div>
  );
}

export function Notice({ message }) {
  if (!message) return null;
  return (
    <div className="banner banner--success" role="status">
      <span>{message}</span>
    </div>
  );
}
