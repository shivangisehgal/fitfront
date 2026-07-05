import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { Calendar, ChevronLeft, ChevronRight, X } from 'lucide-react';

/**
 * Themed calendar popover. Replaces native <input type="date">.
 * Renders the popover via a React portal so it is never clipped by
 * overflow:hidden ancestors.  Auto-flips above the trigger when there
 * is not enough space below.
 *
 * Props:
 *   value         — selected Date object (or null)
 *   onChange      — (Date) => void
 *   onClear       — () => void  (optional; renders an "x" clear button)
 *   placeholder   — text when no value (default: "Pick a date")
 *   min           — minimum selectable Date (optional)
 *   max           — maximum selectable Date (optional)
 *   accent        — 'amber' | 'primary' (default 'amber')
 *   buttonClassName — extra classes for the trigger button
 *   formatLabel   — custom label formatter (Date) => string
 */
export default function ThemedDatePicker({
  value,
  onChange,
  onClear,
  placeholder = 'Pick a date',
  min,
  max,
  accent = 'amber',
  buttonClassName = '',
  formatLabel,
}) {
  const [open, setOpen] = useState(false);
  const [popoverStyle, setPopoverStyle] = useState({});
  const [openUpward, setOpenUpward] = useState(false);

  const [viewMonth, setViewMonth] = useState(() => {
    const base = value || new Date();
    return new Date(base.getFullYear(), base.getMonth(), 1);
  });

  const triggerRef = useRef(null);

  // Keep view month in sync when an external value is set
  useEffect(() => {
    if (value) {
      setViewMonth(new Date(value.getFullYear(), value.getMonth(), 1));
    }
  }, [value]);

  // Compute portal position relative to the trigger button
  const computePosition = useCallback(() => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const POPOVER_HEIGHT = 320; // approximate calendar height in px
    const POPOVER_WIDTH = 288;  // w-72
    const GAP = 8;

    const spaceBelow = window.innerHeight - rect.bottom;
    const spaceAbove = rect.top;
    const flip = spaceBelow < POPOVER_HEIGHT + GAP && spaceAbove > spaceBelow;

    // Clamp left so it doesn't bleed off the right edge
    const left = Math.min(rect.left, window.innerWidth - POPOVER_WIDTH - 8);

    setOpenUpward(flip);
    setPopoverStyle({
      position: 'fixed',
      left: Math.max(8, left),
      ...(flip
        ? { bottom: window.innerHeight - rect.top + GAP }
        : { top: rect.bottom + GAP }),
      width: POPOVER_WIDTH,
      zIndex: 9999,
    });
  }, []);

  // Re-position on scroll / resize while open
  useEffect(() => {
    if (!open) return;
    computePosition();
    window.addEventListener('scroll', computePosition, true);
    window.addEventListener('resize', computePosition);
    return () => {
      window.removeEventListener('scroll', computePosition, true);
      window.removeEventListener('resize', computePosition);
    };
  }, [open, computePosition]);

  // Close on click outside / escape
  useEffect(() => {
    if (!open) return;
    function onDocClick(e) {
      if (
        triggerRef.current && !triggerRef.current.contains(e.target) &&
        !document.getElementById('themed-datepicker-portal')?.contains(e.target)
      ) {
        setOpen(false);
      }
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

  const accentClasses = accent === 'amber'
    ? {
        button: value
          ? 'border-amber-200 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 hover:bg-amber-100 dark:hover:bg-amber-900/50'
          : 'border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-600 shadow-sm',
        selectedDay: 'bg-amber-500 text-white font-semibold hover:bg-amber-600',
        todayRing: 'ring-2 ring-amber-400',
      }
    : {
        button: value
          ? 'border-indigo-200 dark:border-indigo-700 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/50'
          : 'border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-600 shadow-sm',
        selectedDay: 'bg-indigo-500 text-white font-semibold hover:bg-indigo-600',
        todayRing: 'ring-2 ring-indigo-400',
      };

  const displayLabel = value
    ? (formatLabel
        ? formatLabel(value)
        : value.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }))
    : placeholder;

  // ── Calendar grid construction ────────────────────────────────────────
  const monthStart = new Date(viewMonth.getFullYear(), viewMonth.getMonth(), 1);
  const monthEnd   = new Date(viewMonth.getFullYear(), viewMonth.getMonth() + 1, 0);
  const startWeekday = monthStart.getDay();
  const daysInMonth  = monthEnd.getDate();

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
      && a.getMonth()    === b.getMonth()
      && a.getDate()     === b.getDate();
  }

  function isDisabled(d) {
    if (!d) return true;
    if (min) { const m = new Date(min); m.setHours(0,0,0,0); if (d < m) return true; }
    if (max) { const m = new Date(max); m.setHours(0,0,0,0); if (d > m) return true; }
    return false;
  }

  function gotoPrevMonth() {
    setViewMonth(new Date(viewMonth.getFullYear(), viewMonth.getMonth() - 1, 1));
  }
  function gotoNextMonth() {
    setViewMonth(new Date(viewMonth.getFullYear(), viewMonth.getMonth() + 1, 1));
  }
  function gotoToday() {
    const t = new Date(); t.setHours(0,0,0,0);
    setViewMonth(new Date(t.getFullYear(), t.getMonth(), 1));
    onChange(t);
    setOpen(false);
  }
  function pickDay(d) {
    if (isDisabled(d)) return;
    onChange(d);
    setOpen(false);
  }

  const popover = open && (
    <div
      id="themed-datepicker-portal"
      style={popoverStyle}
      className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-xl p-3"
    >
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
        {['S','M','T','W','T','F','S'].map((d, i) => (
          <div key={i} className="text-center text-[10px] font-medium text-gray-400 uppercase py-1">
            {d}
          </div>
        ))}
      </div>

      {/* Day grid */}
      <div className="grid grid-cols-7 gap-1">
        {cells.map((d, idx) => {
          if (!d) return <div key={idx} />;
          const disabled   = isDisabled(d);
          const isToday    = sameDay(d, today);
          const isSelected = sameDay(d, value);
          const base = 'h-9 w-full rounded-lg text-sm flex items-center justify-center transition-colors';
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
            <button key={idx} type="button" disabled={disabled} onClick={() => pickDay(d)} className={cls}>
              {d.getDate()}
            </button>
          );
        })}
      </div>

      {/* Footer */}
      <div className="mt-3 flex items-center justify-between border-t border-gray-100 dark:border-gray-700 pt-2">
        <button
          type="button"
          onClick={gotoToday}
          className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:underline"
        >
          Today
        </button>
        {value && onClear && (
          <button
            type="button"
            onClick={() => { onClear(); setOpen(false); }}
            className="text-xs font-medium text-gray-500 dark:text-gray-400 hover:underline"
          >
            Clear
          </button>
        )}
      </div>
    </div>
  );

  return (
    <div ref={triggerRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-medium transition-colors ${accentClasses.button} ${buttonClassName}`}
      >
        <Calendar className="w-4 h-4 shrink-0" />
        <span>{displayLabel}</span>
        {value && onClear && (
          <X
            className="w-3.5 h-3.5 shrink-0 opacity-60 hover:opacity-100"
            onClick={(e) => { e.stopPropagation(); onClear(); }}
          />
        )}
      </button>

      {typeof document !== 'undefined' && createPortal(popover, document.body)}
    </div>
  );
}
