import React, { useState, useEffect, useRef } from 'react';
import { Clock } from 'lucide-react';

/**
 * Themed time picker popover. Replaces native <input type="time">.
 *
 * Value model: "HH:MM" (24-hour, same format as native time input)
 *
 * Props:
 *   value       — current value as "HH:MM" string (or '')
 *   onChange    — (newValue: string) => void
 *   minuteStep  — minutes between options (default 30)
 *   placeholder — text when no value (default: "Select time")
 *   disabled    — boolean
 */
export default function ThemedTimePicker({
  value,
  onChange,
  minuteStep = 30,
  placeholder = 'Select time',
  disabled = false,
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // Build time options list
  const options = [];
  for (let h = 0; h < 24; h++) {
    for (let m = 0; m < 60; m += minuteStep) {
      const hh = String(h).padStart(2, '0');
      const mm = String(m).padStart(2, '0');
      options.push(`${hh}:${mm}`);
    }
  }

  // Format for display: "8:00 AM"
  function formatDisplay(val) {
    if (!val) return placeholder;
    const [h, m] = val.split(':').map(Number);
    const ampm = h >= 12 ? 'PM' : 'AM';
    const hour = h % 12 || 12;
    return `${hour}:${String(m).padStart(2, '0')} ${ampm}`;
  }

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

  // Scroll selected option into view when opening
  const listRef = useRef(null);
  useEffect(() => {
    if (open && listRef.current && value) {
      const el = listRef.current.querySelector('[data-selected="true"]');
      if (el) el.scrollIntoView({ block: 'center' });
    }
  }, [open, value]);

  return (
    <div ref={ref} className="relative inline-block">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center gap-2 px-3 py-1.5 rounded-xl border text-sm transition-all outline-none
          ${value
            ? 'border-indigo-200 dark:border-indigo-700 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300'
            : 'border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-white/5 text-gray-500 dark:text-gray-400'}
          hover:border-indigo-300 dark:hover:border-indigo-600 focus:ring-2 focus:ring-indigo-500/40
          disabled:opacity-50 disabled:cursor-not-allowed`}
      >
        <Clock className="w-3.5 h-3.5 shrink-0" />
        <span>{formatDisplay(value)}</span>
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-36 bg-white dark:bg-zinc-800 border border-gray-200 dark:border-zinc-700 rounded-xl shadow-xl overflow-hidden">
          <div
            ref={listRef}
            className="max-h-48 overflow-y-auto py-1"
          >
            {options.map((opt) => (
              <button
                key={opt}
                type="button"
                data-selected={opt === value}
                onClick={() => {
                  onChange(opt);
                  setOpen(false);
                }}
                className={`w-full text-left px-3 py-1.5 text-sm transition-colors
                  ${opt === value
                    ? 'bg-indigo-500 text-white font-medium'
                    : 'text-gray-700 dark:text-gray-200 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 hover:text-indigo-600 dark:hover:text-indigo-300'}`}
              >
                {formatDisplay(opt)}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
