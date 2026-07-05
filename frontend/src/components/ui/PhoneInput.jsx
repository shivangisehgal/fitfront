import React, { useState, useEffect, useRef } from 'react';
import { ChevronDown, Search } from 'lucide-react';

/**
 * Phone input with inline country code selector.
 *
 * Outputs the full E.164 string (e.g. "+14155551234") via onChange.
 * Internally manages country code and national number separately so the
 * user doesn't have to type "+1" or "+91" — they just pick from a dropdown.
 *
 * Props:
 *   value         — full phone string ("+14155551234") or just digits ("4155551234")
 *   onChange       — (fullE164: string) => void
 *   defaultCountry — ISO 3166 code to pre-select (e.g. "US", "IN", "GB"). Default "US".
 *   placeholder    — placeholder for the number portion
 *   required       — boolean
 *   disabled       — boolean
 *   className      — extra classes for the outer wrapper
 *   icon           — optional lucide icon component shown before the country selector
 */

const COUNTRIES = [
  { code: 'US', dial: '+1',   flag: '🇺🇸', label: 'United States',   maxDigits: 10 },
  { code: 'CA', dial: '+1',   flag: '🇨🇦', label: 'Canada',          maxDigits: 10 },
  { code: 'IN', dial: '+91',  flag: '🇮🇳', label: 'India',           maxDigits: 10 },
  { code: 'GB', dial: '+44',  flag: '🇬🇧', label: 'United Kingdom',  maxDigits: 10 },
  { code: 'AU', dial: '+61',  flag: '🇦🇺', label: 'Australia',       maxDigits: 9  },
  { code: 'SG', dial: '+65',  flag: '🇸🇬', label: 'Singapore',       maxDigits: 8  },
  { code: 'AE', dial: '+971', flag: '🇦🇪', label: 'UAE',             maxDigits: 9  },
  { code: 'DE', dial: '+49',  flag: '🇩🇪', label: 'Germany',         maxDigits: 11 },
  { code: 'FR', dial: '+33',  flag: '🇫🇷', label: 'France',          maxDigits: 9  },
  { code: 'JP', dial: '+81',  flag: '🇯🇵', label: 'Japan',           maxDigits: 10 },
  { code: 'CN', dial: '+86',  flag: '🇨🇳', label: 'China',           maxDigits: 11 },
  { code: 'BR', dial: '+55',  flag: '🇧🇷', label: 'Brazil',          maxDigits: 11 },
  { code: 'MX', dial: '+52',  flag: '🇲🇽', label: 'Mexico',          maxDigits: 10 },
  { code: 'ZA', dial: '+27',  flag: '🇿🇦', label: 'South Africa',    maxDigits: 9  },
  { code: 'KR', dial: '+82',  flag: '🇰🇷', label: 'South Korea',     maxDigits: 11 },
  { code: 'SA', dial: '+966', flag: '🇸🇦', label: 'Saudi Arabia',    maxDigits: 9  },
  { code: 'NZ', dial: '+64',  flag: '🇳🇿', label: 'New Zealand',     maxDigits: 10 },
  { code: 'PH', dial: '+63',  flag: '🇵🇭', label: 'Philippines',     maxDigits: 10 },
  { code: 'MY', dial: '+60',  flag: '🇲🇾', label: 'Malaysia',        maxDigits: 10 },
  { code: 'TH', dial: '+66',  flag: '🇹🇭', label: 'Thailand',        maxDigits: 9  },
  { code: 'ID', dial: '+62',  flag: '🇮🇩', label: 'Indonesia',       maxDigits: 12 },
  { code: 'NG', dial: '+234', flag: '🇳🇬', label: 'Nigeria',         maxDigits: 10 },
  { code: 'KE', dial: '+254', flag: '🇰🇪', label: 'Kenya',           maxDigits: 9  },
  { code: 'EG', dial: '+20',  flag: '🇪🇬', label: 'Egypt',           maxDigits: 10 },
  { code: 'IL', dial: '+972', flag: '🇮🇱', label: 'Israel',          maxDigits: 9  },
];

// Map timezone prefix → country code for auto-detection
const TZ_TO_COUNTRY = {
  'America/New_York': 'US', 'America/Chicago': 'US', 'America/Denver': 'US',
  'America/Los_Angeles': 'US', 'America/Phoenix': 'US', 'US/': 'US',
  'America/Toronto': 'CA', 'America/Vancouver': 'CA', 'Canada/': 'CA',
  'Asia/Kolkata': 'IN', 'Asia/Calcutta': 'IN', 'Asia/Mumbai': 'IN',
  'Europe/London': 'GB',
  'Australia/': 'AU',
  'Asia/Singapore': 'SG',
  'Asia/Tokyo': 'JP',
  'Asia/Shanghai': 'CN',
  'Europe/Berlin': 'DE', 'Europe/Frankfurt': 'DE',
  'Europe/Paris': 'FR',
  'Asia/Seoul': 'KR',
  'America/Sao_Paulo': 'BR',
  'America/Mexico_City': 'MX',
  'Africa/Johannesburg': 'ZA',
  'Asia/Dubai': 'AE',
};

/** Derive country code from an IANA timezone string. */
export function countryFromTimezone(tz) {
  if (!tz) return 'US';
  // Exact match first
  if (TZ_TO_COUNTRY[tz]) return TZ_TO_COUNTRY[tz];
  // Prefix match
  for (const [prefix, code] of Object.entries(TZ_TO_COUNTRY)) {
    if (prefix.endsWith('/') && tz.startsWith(prefix)) return code;
  }
  // Fallback: if tz starts with "America/" it's likely US/CA
  if (tz.startsWith('America/')) return 'US';
  if (tz.startsWith('Europe/')) return 'GB';
  if (tz.startsWith('Asia/')) return 'IN';
  if (tz.startsWith('Australia/')) return 'AU';
  if (tz.startsWith('Africa/')) return 'ZA';
  return 'US';
}

/** Parse an incoming value like "+919876543210" into { countryCode, nationalNumber }. */
function parsePhone(value, defaultCountry = 'US') {
  if (!value) return { countryCode: defaultCountry, nationalNumber: '' };

  const cleaned = value.replace(/[\s\-()]/g, '');

  // Try to match a dial code prefix (longest first to handle +971 before +9)
  if (cleaned.startsWith('+')) {
    const sorted = [...COUNTRIES].sort((a, b) => b.dial.length - a.dial.length);
    for (const c of sorted) {
      if (cleaned.startsWith(c.dial)) {
        return { countryCode: c.code, nationalNumber: cleaned.slice(c.dial.length) };
      }
    }
  }

  // No dial code — assume just the national number
  return { countryCode: defaultCountry, nationalNumber: cleaned.replace(/^\+/, '') };
}


export default function PhoneInput({
  value = '',
  onChange,
  defaultCountry = 'US',
  placeholder = '(512) 555-0100',
  required = false,
  disabled = false,
  className = '',
  icon: LeadingIcon = null,
}) {
  const parsed = parsePhone(value, defaultCountry);
  const [countryCode, setCountryCode] = useState(parsed.countryCode);
  const [nationalNumber, setNationalNumber] = useState(parsed.nationalNumber);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [search, setSearch] = useState('');
  const wrapperRef = useRef(null);
  const searchRef = useRef(null);

  const country = COUNTRIES.find((c) => c.code === countryCode) || COUNTRIES[0];

  // Sync from parent value changes (e.g. "Same as owner phone" checkbox)
  useEffect(() => {
    const p = parsePhone(value, defaultCountry);
    setCountryCode(p.countryCode);
    setNationalNumber(p.nationalNumber);
  }, [value, defaultCountry]);

  // Close dropdown on click outside
  useEffect(() => {
    if (!dropdownOpen) return;
    const handler = (e) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setDropdownOpen(false);
        setSearch('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [dropdownOpen]);

  // Focus search when dropdown opens
  useEffect(() => {
    if (dropdownOpen && searchRef.current) searchRef.current.focus();
  }, [dropdownOpen]);

  const emitChange = (code, number) => {
    const c = COUNTRIES.find((x) => x.code === code) || COUNTRIES[0];
    const digits = number.replace(/\D/g, '');
    onChange?.(digits ? `${c.dial}${digits}` : '');
  };

  const handleCountrySelect = (code) => {
    setCountryCode(code);
    setDropdownOpen(false);
    setSearch('');
    emitChange(code, nationalNumber);
  };

  const handleNumberChange = (e) => {
    const raw = e.target.value;
    // Strip anything that's not a digit
    const digits = raw.replace(/\D/g, '');
    setNationalNumber(digits);
    emitChange(countryCode, digits);
  };

  const filtered = search.trim()
    ? COUNTRIES.filter(
        (c) =>
          c.label.toLowerCase().includes(search.toLowerCase()) ||
          c.dial.includes(search) ||
          c.code.toLowerCase().includes(search.toLowerCase())
      )
    : COUNTRIES;

  return (
    <div ref={wrapperRef} className={`relative flex ${className}`}>
      {/* Country code selector */}
      <button
        type="button"
        disabled={disabled}
        onClick={() => setDropdownOpen(!dropdownOpen)}
        className={`
          flex items-center gap-1 px-2.5 border border-r-0 rounded-l-lg
          bg-gray-50 dark:bg-gray-700/50
          border-gray-300 dark:border-gray-600
          text-sm text-gray-700 dark:text-gray-300
          hover:bg-gray-100 dark:hover:bg-gray-700
          focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:z-10
          ${disabled ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer'}
          transition-colors shrink-0
        `}
      >
        {LeadingIcon && <LeadingIcon className="w-4 h-4 text-gray-400 mr-0.5" />}
        <span className="text-base leading-none">{country.flag}</span>
        <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
          {country.dial}
        </span>
        <ChevronDown className="w-3 h-3 text-gray-400" />
      </button>

      {/* National number input */}
      <input
        type="tel"
        value={nationalNumber}
        onChange={handleNumberChange}
        placeholder={placeholder}
        required={required}
        disabled={disabled}
        maxLength={country.maxDigits + 2}
        className={`
          flex-1 min-w-0 rounded-r-lg border
          border-gray-300 dark:border-gray-600
          bg-white dark:bg-gray-800
          text-gray-900 dark:text-gray-100
          px-3 py-2.5 text-sm
          placeholder:text-gray-400 dark:placeholder:text-gray-500
          focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500
          ${disabled ? 'opacity-60 cursor-not-allowed' : ''}
        `}
      />

      {/* Dropdown */}
      {dropdownOpen && (
        <div className="absolute left-0 top-full mt-1 w-72 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-50 max-h-64 overflow-hidden flex flex-col">
          {/* Search */}
          <div className="p-2 border-b border-gray-100 dark:border-gray-700">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
              <input
                ref={searchRef}
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search countries..."
                className="w-full pl-8 pr-3 py-1.5 text-sm rounded-md bg-gray-50 dark:bg-gray-700/50 border border-gray-200 dark:border-gray-600 text-gray-900 dark:text-gray-100 placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
          </div>
          {/* Options */}
          <div className="overflow-y-auto flex-1">
            {filtered.map((c) => (
              <button
                key={c.code}
                type="button"
                onClick={() => handleCountrySelect(c.code)}
                className={`w-full flex items-center gap-2.5 px-3 py-2 text-sm hover:bg-gray-50 dark:hover:bg-gray-700/60 transition-colors ${
                  c.code === countryCode ? 'bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-300' : 'text-gray-700 dark:text-gray-300'
                }`}
              >
                <span className="text-base leading-none">{c.flag}</span>
                <span className="flex-1 text-left">{c.label}</span>
                <span className="text-xs text-gray-400 dark:text-gray-500 font-mono">{c.dial}</span>
              </button>
            ))}
            {filtered.length === 0 && (
              <p className="text-sm text-gray-400 text-center py-3">No countries found</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
