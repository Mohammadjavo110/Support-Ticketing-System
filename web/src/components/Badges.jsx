import { priorityLabel, statusLabel } from '../api.js';

export function StatusBadge({ status }) {
  const key = status || 'unknown';
  return <span className={`badge badge--status-${key}`}>{statusLabel(status)}</span>;
}

export function PriorityBadge({ priority }) {
  const level = Number(priority) || 2;
  return <span className={`badge badge--priority-${level}`}>{priorityLabel(level)}</span>;
}

export function TriageChip() {
  return <span className="chip chip--warn">نیاز به بازبینی</span>;
}
