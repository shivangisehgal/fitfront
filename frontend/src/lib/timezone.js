/**
 * Timezone-aware date formatting utilities.
 *
 * All dates from the backend are in UTC.  These helpers convert them to
 * the tenant's configured timezone (from /api/auth/me → user.timezone)
 * before displaying, so a user in America/Chicago always sees "9:00 AM CST"
 * regardless of what timezone the browser is running in.
 *
 * Usage in components:
 *   import { useAuth } from '../contexts/AuthContext';
 *   import { formatDateTime, formatTime, formatDate } from '../lib/timezone';
 *
 *   const { user } = useAuth();
 *   const tz = user?.timezone || 'America/Chicago';
 *
 *   formatDateTime(apt.scheduled_at, tz)   → "Mon, May 6, 2026, 9:00 AM"
 *   formatTime(apt.scheduled_at, tz)       → "9:00 AM"
 *   formatDate(apt.scheduled_at, tz)       → "May 6, 2026"
 */

/**
 * Format a UTC ISO string as a full date+time in the tenant's timezone.
 *
 * @param {string|Date} isoOrDate  — ISO string or Date object (UTC)
 * @param {string} tz              — IANA timezone (e.g. "America/Chicago")
 * @param {Intl.DateTimeFormatOptions} [overrides] — optional format overrides
 * @returns {string}
 */
export function formatDateTime(isoOrDate, tz, overrides = {}) {
  if (!isoOrDate) return '';
  const date = typeof isoOrDate === 'string' ? new Date(isoOrDate) : isoOrDate;
  return date.toLocaleString('en-US', {
    timeZone: tz,
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    ...overrides,
  });
}

/**
 * Format only the time portion in the tenant's timezone.
 *
 * @param {string|Date} isoOrDate
 * @param {string} tz
 * @returns {string}  e.g. "9:00 AM"
 */
export function formatTime(isoOrDate, tz) {
  if (!isoOrDate) return '';
  const date = typeof isoOrDate === 'string' ? new Date(isoOrDate) : isoOrDate;
  return date.toLocaleTimeString('en-US', {
    timeZone: tz,
    hour: 'numeric',
    minute: '2-digit',
  });
}

/**
 * Format only the date portion in the tenant's timezone.
 *
 * @param {string|Date} isoOrDate
 * @param {string} tz
 * @param {Intl.DateTimeFormatOptions} [overrides]
 * @returns {string}  e.g. "May 6, 2026"
 */
export function formatDate(isoOrDate, tz, overrides = {}) {
  if (!isoOrDate) return '';
  const date = typeof isoOrDate === 'string' ? new Date(isoOrDate) : isoOrDate;
  return date.toLocaleDateString('en-US', {
    timeZone: tz,
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    ...overrides,
  });
}

/**
 * Format a relative time string ("just now", "5m ago", "3d ago").
 * The cutoff dates compare in the tenant's timezone so "today" is correct.
 *
 * @param {string} isoString
 * @param {string} tz
 * @returns {string}
 */
export function formatRelativeTime(isoString, tz) {
  if (!isoString) return '';
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`;
  return formatDate(isoString, tz);
}

/**
 * Check whether a UTC ISO datetime falls on a given local-timezone date.
 * Replaces the bug-prone `new Date(iso).getDate() === day.getDate()` pattern
 * which compares in the browser's timezone instead of the tenant's.
 *
 * @param {string} isoString — UTC ISO datetime from the backend
 * @param {Date} localDate   — the day to check (from the calendar grid)
 * @param {string} tz        — IANA timezone
 * @returns {boolean}
 */
export function isSameDay(isoString, localDate, tz) {
  if (!isoString) return false;
  // Format the UTC datetime as a date string in the tenant's timezone
  const d = new Date(isoString);
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(d);

  const year = parts.find((p) => p.type === 'year')?.value;
  const month = parts.find((p) => p.type === 'month')?.value;
  const day = parts.find((p) => p.type === 'day')?.value;

  return (
    localDate.getFullYear() === Number(year) &&
    localDate.getMonth() + 1 === Number(month) &&
    localDate.getDate() === Number(day)
  );
}
