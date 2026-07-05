import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Calendar, Clock, ChevronLeft, ChevronRight, X } from 'lucide-react';

/**
 * Themed datetime popover. Replaces native <input type="datetime-local">.
 *
 * Value model — uses the same string format the native input emits so
 * callers don't have to rewrite their state handling:
 *
 *   "YYYY-MM-DDTHH:MM"   (no timezone, no seconds)
 *
 * Props:
 *   value         — current value as "YYYY-MM-DDTHH:MM" string (or '')
 *   onChange      — (newValue: string) => void
 *   onClear       — () => void  (optional; renders an "x" clear button)
 *   placeholder   — text when no value (default: "Pick date & time")
 *   min           — minimum selectable Date (optional, time portion ignored)
 *   max           — maximum selectable Date (optional, time portion ignored)
 *   minuteStep    — minutes between time options (default 5)
 *   accent        — 'amber' | 'primary' (default 'primary')
 *   buttonClassName — extra classes for the trigger button
 *   formatLabel   — custom label formatter (dateStr) => string
 *   timezoneHint  — optional small text under the time picker
 */
export default function ThemedDateTimePicker({
  value,
  onChange,
  onClear,
  placeholder = 'Pick date & time',
  min,
  max,
  minuteStep = 5,
  accent = 'primary',
  buttonClassName = '',
  formatLabel,
  timezoneHint,
  dropUp = false,
}) {
  const [open, setOpen] = useState(false);

  // Parse the controlled "YYYY-MM-DDTHH:MM" into { date: Date|null, hh, mm }
  const parsed = useMemo(() => parseLocalDateTime(value), [value]);

  // Month being viewed (independent of selected value)
  const [viewMonth, setViewMonth] = useState(() => {
    const base = parsed.date || new Date();
    return new Date(base.getFullYear(), base.getMonth(), 1);
  });

  const ref = useRef(null);

  // Sync view month when external value changes
  useEffect(() => {
    if (parsed.date) {
      setViewMonth(new Date(parsed.date.getFullYear(), parsed.date.getMonth(), 1));
    }
  }, [parsed.date]);

  // Close on click outside / escape
  useEffect(() => {
    if (!open) return;
    function onDocClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // ── Accent palette ────────────────────────────────────────────────────
  const accentClasses = accent === 'amber'
    ? {
        button: value
          ? 'border-amber-200 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 hover:bg-amber-100 dark:hover:bg-amber-900/50'
          : 'border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-600',
        selectedDay: 'bg-amber-500 text-white font-semibold hover:bg-amber-600',
        todayRing: 'ring-2 ring-amber-400',
        timePill: 'bg-amber-500 text-white hover:bg-amber-600',
      }
    : {
        button: value
          ? 'border-indigo-200 dark:border-indigo-700 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/50'
          : 'border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-600',
        selectedDay: 'bg-indigo-500 text-white font-semibold hover:bg-indigo-600',
        todayRing: 'ring-2 ring-indigo-400',
        timePill: 'bg-indigo-500 text-white hover:bg-indigo-600',
      };

  // ── Trigger label ─────────────────────────────────────────────────────
  const displayLabel = value
    ? (formatLabel
        ? formatLabel(value)
        : formatDefault(parsed.date, parsed.hh, parsed.mm))
    : placeholder;

  // ── Calendar grid construction ────────────────────────────────────────
  const monthStart = new Date(viewMonth.getFullYear(), viewMonth.getMonth(), 1);
  const monthEnd = new Date(viewMonth.getFullYear(), viewMonth.getMonth() + 1, 0);
  const startWeekday = monthStart.getDay();
  const daysInMonth = monthEnd.getDate();

  const cells = [];
  for (let i = 0; i < startWeekday; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) {
    cells.push(new Date(viewMonth.getFullYear(), viewMonth.getMonth(), d));
  }
  while (cells.length % 7 !== 0) cells.push(null);

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  function sameDay(a, b) {
    return a && b
      && a.getFullYear() === b.getFullYear()
      && a.getMonth() === b.getMonth()
      && a.getDate() === b.getDate();
  }

  function isDateDisabled(d) {
    if (!d) return true;
    if (min) {
      const minD = new Date(min); minD.setHours(0, 0, 0, 0);
      if (d < minD) return true;
    }
    if (max) {
      const maxD = new Date(max); maxD.setHours(0, 0, 0, 0);
      if (d > maxD) return true;
    }
    return false;
  }

  function gotoPrevMonth() {
    setViewMonth(new Date(viewMonth.getFullYear(), viewMonth.getMonth() - 1, 1));
  }
  function gotoNextMonth() {
    setViewMonth(new Date(viewMonth.getFullYear(), viewMonth.getMonth() + 1, 1));
  }

  function emit(date, hh, mm) {
    onChange(serialize(date, hh, mm));
  }

  function pickDay(d) {
    if (isDateDisabled(d)) return;
    // Default to 09:00 the first time a day is picked
    const hh = parsed.hh ?? 9;
    const mm = parsed.mm ?? 0;
    emit(d, hh, mm);
  }

  function pickHour(h) {
    if (!parsed.date) {
      // No date yet — assume today
      const t = new Date();
      t.setHours(0, 0, 0, 0);
      emit(t, h, parsed.mm ?? 0);
    } else {
      emit(parsed.date, h, parsed.mm ?? 0);
    }
  }

  function pickMinute(m) {
    if (!parsed.date) {
      const t = new Date();
      t.setHours(0, 0, 0, 0);
      emit(t, parsed.hh ?? 9, m);
    } else {
      emit(parsed.date, parsed.hh ?? 9, m);
    }
  }

  function gotoNow() {
    const n = new Date();
    // Round minutes to nearest step
    const roundedMin = Math.round(n.getMinutes() / minuteStep) * minuteStep;
    const dayOnly = new Date(n.getFullYear(), n.getMonth(), n.getDate());
    emit(dayOnly, n.getHours(), Math.min(roundedMin, 60 - minuteStep));
    setViewMonth(new Date(n.getFullYear(), n.getMonth(), 1));
  }

  // ── Time grid (hours + minutes) ───────────────────────────────────────
  const hours = Array.from({ length: 24 }, (_, i) => i);
  const minutes = [];
  for (let m = 0; m < 60; m += minuteStep) minutes.push(m);

  return (
    <div ref={ref} className="relative inline-block w-full">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center gap-2 w-full px-3 py-2 rounded-lg border text-sm font-medium transition-colors ${accentClasses.button} ${buttonClassName}`}
      >
        <Calendar className="w-4 h-4 shrink-0" />
        <span className="flex-1 text-left truncate">{displayLabel}</span>
        {value && onClear && (
          <X
            className="w-3.5 h-3.5 shrink-0 opacity-60 hover:opacity-100"
            onClick={(e) => {
              e.stopPropagation();
              onClear();
            }}
          />
        )}
      </button>

      {open && (
        <div className={`absolute z-50 w-[22rem] max-w-[95vw] rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-xl p-3 ${dropUp ? 'bottom-full mb-2' : 'mt-2'}`}>
          {/* Header — month/year + nav */}
          <div className="flex items-center justify-between mb-2">
            <button
              type="button"
              onClick={gotoPrevMonth}
              className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400"
              aria-label="Previous month"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <p className="text-sm font-semibold text-gray-800 dark:text-gray-100">
              {viewMonth.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}
            </p>
            <button
              type="button"
              onClick={gotoNextMonth}
              className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400"
              aria-label="Next month"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>

          {/* Weekday labels */}
          <div className="grid grid-cols-7 gap-1 mb-1">
            {['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((d, i) => (
              <div key={i} className="text-center text-[10px] font-medium text-gray-400 uppercase py-1">
                {d}
              </div>
            ))}
          </div>

          {/* Day grid */}
          <div className="grid grid-cols-7 gap-1">
            {cells.map((d, idx) => {
              if (!d) return <div key={idx} />;
              const disabled = isDateDisabled(d);
              const isToday = sameDay(d, today);
              const isSelected = sameDay(d, parsed.date);
              const base = 'h-8 w-full rounded-lg text-sm flex items-center justify-center transition-colors';
              let cls;
              if (isSelected) {
                cls = `${base} ${accentClasses.selectedDay}`;
              } else if (isToday) {
                cls = `${base} ${accentClasses.todayRing} text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700`;
              } else if (disabled) {
                cls = `${base} text-gray-300 dark:text-gray-600 cursor-not-allowed`;
              } else {
                cls = `${base} text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700`;
              }
              return (
                <button
                  key={idx}
                  type="button"
                  disabled={disabled}
                  onClick={() => pickDay(d)}
                  className={cls}
                >
                  {d.getDate()}
                </button>
              );
            })}
          </div>

          {/* Time pickers — hour and minute scrollable columns */}
          <div className="mt-3 border-t border-gray-100 dark:border-gray-700 pt-3">
            <div className="flex items-center gap-2 mb-2">
              <Clock className="w-4 h-4 text-gray-500 dark:text-gray-400" />
              <span className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                Time
              </span>
              <span className="ml-auto text-sm font-mono text-gray-700 dark:text-gray-200">
                {to12h(parsed.hh ?? 9)}:{pad2(parsed.mm ?? 0)}
                <span className="ml-1 text-xs text-gray-400">
                  {hour12Label(parsed.hh ?? 9)}
                </span>
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {/* Hours column */}
              <div>
                <p className="text-[10px] font-medium text-gray-400 uppercase mb-1 text-center">
                  Hour
                </p>
                <div className="h-32 overflow-y-auto rounded-lg border border-gray-100 dark:border-gray-700 p-1 space-y-0.5">
                  {hours.map((h) => {
                    const active = (parsed.hh ?? 9) === h;
                    return (
                      <button
                        key={h}
                        type="button"
                        onClick={() => pickHour(h)}
                        className={`w-full text-xs py-1 rounded-md font-mono transition-colors ${
                          active
                            ? accentClasses.timePill
                            : 'text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700'
                        }`}
                      >
                        {to12h(h)} {hour12Label(h)}
                      </button>
                    );
                  })}
                </div>
              </div>
              {/* Minutes column */}
              <div>
                <p className="text-[10px] font-medium text-gray-400 uppercase mb-1 text-center">
                  Minute
                </p>
                <div className="h-32 overflow-y-auto rounded-lg border border-gray-100 dark:border-gray-700 p-1 space-y-0.5">
                  {minutes.map((m) => {
                    const active = (parsed.mm ?? 0) === m;
                    return (
                      <button
                        key={m}
                        type="button"
                        onClick={() => pickMinute(m)}
                        className={`w-full text-xs py-1 rounded-md font-mono transition-colors ${
                          active
                            ? accentClasses.timePill
                            : 'text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700'
                        }`}
                      >
                        :{pad2(m)}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
            {timezoneHint && (
              <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-2">
                {timezoneHint}
              </p>
            )}
          </div>

          {/* Footer */}
          <div className="mt-3 flex items-center justify-between border-t border-gray-100 dark:border-gray-700 pt-2">
            <button
              type="button"
              onClick={gotoNow}
              className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:underline"
            >
              Now
            </button>
            <div className="flex items-center gap-3">
              {value && onClear && (
                <button
                  type="button"
                  onClick={() => { onClear(); setOpen(false); }}
                  className="text-xs font-medium text-gray-500 dark:text-gray-400 hover:underline"
                >
                  Clear
                </button>
              )}
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:underline"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function pad2(n) {
  return String(n).padStart(2, '0');
}

function hour12Label(h) {
  if (h === 0) return 'AM';
  if (h === 12) return 'PM';
  return h < 12 ? 'AM' : 'PM';
}

/** Convert 24-hour integer (0–23) to 12-hour display string ("12", "1"…"11") */
function to12h(h) {
  const h12 = h % 12;
  return String(h12 === 0 ? 12 : h12);
}

/**
 * Parse a "YYYY-MM-DDTHH:MM" string into a Date + hour + minute. Returns
 * `{ date: null, hh: null, mm: null }` if value is empty or malformed so
 * the picker can show its placeholder state.
 */
function parseLocalDateTime(value) {
  if (!value || typeof value !== 'string') {
    return { date: null, hh: null, mm: null };
  }
  // Accept both "YYYY-MM-DDTHH:MM" and "YYYY-MM-DDTHH:MM:SS"
  const m = value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!m) return { date: null, hh: null, mm: null };
  const [_, y, mo, d, hh, mm] = m;
  // Build a *local* Date (no UTC shift) so calendar grid matches what the
  // user typed.
  const date = new Date(Number(y), Number(mo) - 1, Number(d));
  return { date, hh: Number(hh), mm: Number(mm) };
}

/**
 * Serialize a Date + hh + mm back into "YYYY-MM-DDTHH:MM" (no TZ, no seconds)
 * — matches what the native datetime-local input emits.
 */
function serialize(date, hh, mm) {
  if (!date) return '';
  const y = date.getFullYear();
  const mo = pad2(date.getMonth() + 1);
  const d = pad2(date.getDate());
  return `${y}-${mo}-${d}T${pad2(hh)}:${pad2(mm)}`;
}

function formatDefault(date, hh, mm) {
  if (!date) return '';
  const dateLabel = date.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
  const h12 = hh % 12 === 0 ? 12 : hh % 12;
  const ampm = hh < 12 ? 'AM' : 'PM';
  return `${dateLabel} · ${h12}:${pad2(mm)} ${ampm}`;
}
