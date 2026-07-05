import React, { useState, useEffect, useRef } from 'react';
import { ChevronDown, Check, Search } from 'lucide-react';

/**
 * Themed dropdown that replaces native <select>.
 *
 * Props:
 *   value      — currently selected option value (string|number)
 *   onChange   — (value) => void
 *   options    — array of { value, label, sublabel?, icon? }
 *               or array of strings (treated as { value: s, label: s })
 *   placeholder — text when no value selected
 *   className   — extra classes for the button wrapper
 *   disabled    — boolean
 *   menuClassName — extra classes for the open menu (e.g. width)
 *   icon       — optional lucide icon component for the button (left-aligned)
 *   searchable — boolean, if true shows a search input to filter options
 *   searchPlaceholder — placeholder text for the search input
 */
export default function ThemedSelect({
  value,
  onChange,
  options = [],
  placeholder = 'Select...',
  className = '',
  disabled = false,
  menuClassName = '',
  icon: LeadingIcon = null,
  searchable = false,
  searchPlaceholder = 'Search...',
}) {
  const [open, setOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const ref = useRef(null);
  const searchInputRef = useRef(null);

  // Normalize options: allow array of strings or array of objects
  const normalized = options.map((opt) =>
    typeof opt === 'object' && opt !== null
      ? opt
      : { value: opt, label: String(opt) }
  );

  const selected = normalized.find((o) => o.value === value);

  // Filter options when searchable
  const filtered = searchable && searchQuery.trim()
    ? normalized.filter((opt) => {
        const q = searchQuery.toLowerCase();
        return (
          opt.label.toLowerCase().includes(q) ||
          String(opt.value).toLowerCase().includes(q) ||
          (opt.sublabel && opt.sublabel.toLowerCase().includes(q))
        );
      })
    : normalized;

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

  // Focus search input when menu opens
  useEffect(() => {
    if (open && searchable && searchInputRef.current) {
      searchInputRef.current.focus();
    }
    if (!open) {
      setSearchQuery('');
    }
  }, [open, searchable]);

  return (
    <div ref={ref} className={`relative ${className}`}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => !disabled && setOpen((o) => !o)}
        className={`w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg border text-sm transition-colors ${
          disabled
            ? 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-400 cursor-not-allowed'
            : 'border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 hover:border-indigo-300 dark:hover:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400'
        }`}
      >
        <span className="flex items-center gap-2 min-w-0 flex-1">
          {LeadingIcon && <LeadingIcon className="w-4 h-4 shrink-0 text-gray-400" />}
          <span className={`truncate ${selected ? '' : 'text-gray-400'}`}>
            {selected ? selected.label : placeholder}
          </span>
        </span>
        <ChevronDown className={`w-4 h-4 text-gray-400 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && !disabled && (
        <div
          className={`absolute z-30 mt-1 w-full max-h-72 overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg flex flex-col ${menuClassName}`}
        >
          {/* Search input */}
          {searchable && (
            <div className="p-2 border-b border-gray-100 dark:border-gray-700 shrink-0">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
                <input
                  ref={searchInputRef}
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={searchPlaceholder}
                  className="w-full pl-8 pr-3 py-1.5 border border-gray-200 dark:border-gray-600 rounded-md text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500 focus:ring-1 focus:ring-indigo-400 focus:border-indigo-400 outline-none"
                />
              </div>
            </div>
          )}

          {/* Options */}
          <div className="overflow-y-auto py-1 flex-1">
            {filtered.length === 0 ? (
              <p className="px-3 py-2 text-sm text-gray-400">
                {searchQuery ? 'No matches found' : 'No options available'}
              </p>
            ) : (
              filtered.map((opt) => {
                const isSelected = opt.value === value;
                const Icon = opt.icon;
                return (
                  <button
                    key={String(opt.value)}
                    type="button"
                    onClick={() => {
                      onChange(opt.value);
                      setOpen(false);
                    }}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${
                      isSelected
                        ? 'text-indigo-700 dark:text-indigo-300 font-medium bg-indigo-50/40 dark:bg-indigo-900/20'
                        : 'text-gray-700 dark:text-gray-300'
                    }`}
                  >
                    {Icon && <Icon className="w-4 h-4 shrink-0" />}
                    <div className="flex-1 min-w-0">
                      <p className="truncate">{opt.label}</p>
                      {opt.sublabel && (
                        <p className="text-[11px] text-gray-400 dark:text-gray-500 truncate">{opt.sublabel}</p>
                      )}
                    </div>
                    {isSelected && <Check className="w-4 h-4 text-indigo-500 shrink-0" />}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
