/**
 * Comprehensive IANA timezone list for tenant registration.
 *
 * Uses the browser-native Intl.supportedValuesOf('timeZone') when available
 * (Chrome 99+, Safari 15.4+, Firefox 93+, Edge 99+) so the dropdown always
 * matches what the browser will actually accept in Date formatting calls.
 *
 * Falls back to a curated static list for older browsers.
 *
 * Each entry is grouped by IANA region for an easier-to-scan dropdown
 * (Africa, America, Asia, Atlantic, Australia, Europe, Indian, Pacific,
 * plus a UTC entry up top).
 */

// ── Fallback list for older browsers / SSR ──────────────────────────────────
const FALLBACK_ZONES = [
  'UTC',
  // Americas
  'America/Anchorage', 'America/Bogota', 'America/Buenos_Aires',
  'America/Chicago', 'America/Denver', 'America/Edmonton',
  'America/Halifax', 'America/Havana', 'America/Indiana/Indianapolis',
  'America/Lima', 'America/Los_Angeles', 'America/Mexico_City',
  'America/Montevideo', 'America/New_York', 'America/Phoenix',
  'America/Puerto_Rico', 'America/Regina', 'America/Santiago',
  'America/Sao_Paulo', 'America/St_Johns', 'America/Toronto',
  'America/Vancouver', 'America/Winnipeg',
  // Pacific
  'Pacific/Auckland', 'Pacific/Fiji', 'Pacific/Guam', 'Pacific/Honolulu',
  'Pacific/Midway', 'Pacific/Pago_Pago', 'Pacific/Port_Moresby',
  // Europe
  'Europe/Amsterdam', 'Europe/Athens', 'Europe/Belgrade', 'Europe/Berlin',
  'Europe/Brussels', 'Europe/Bucharest', 'Europe/Budapest',
  'Europe/Copenhagen', 'Europe/Dublin', 'Europe/Helsinki',
  'Europe/Istanbul', 'Europe/Kiev', 'Europe/Lisbon', 'Europe/London',
  'Europe/Madrid', 'Europe/Moscow', 'Europe/Oslo', 'Europe/Paris',
  'Europe/Prague', 'Europe/Rome', 'Europe/Stockholm', 'Europe/Vienna',
  'Europe/Warsaw', 'Europe/Zurich',
  // Africa
  'Africa/Cairo', 'Africa/Casablanca', 'Africa/Johannesburg',
  'Africa/Lagos', 'Africa/Nairobi',
  // Asia
  'Asia/Bangkok', 'Asia/Dhaka', 'Asia/Dubai', 'Asia/Hong_Kong',
  'Asia/Jakarta', 'Asia/Jerusalem', 'Asia/Karachi', 'Asia/Kolkata',
  'Asia/Kuala_Lumpur', 'Asia/Manila', 'Asia/Riyadh', 'Asia/Seoul',
  'Asia/Shanghai', 'Asia/Singapore', 'Asia/Taipei', 'Asia/Tehran',
  'Asia/Tokyo',
  // Australia
  'Australia/Adelaide', 'Australia/Brisbane', 'Australia/Darwin',
  'Australia/Hobart', 'Australia/Melbourne', 'Australia/Perth',
  'Australia/Sydney',
  // Indian / Atlantic
  'Atlantic/Azores', 'Atlantic/Cape_Verde', 'Atlantic/Reykjavik',
  'Indian/Maldives', 'Indian/Mauritius', 'Indian/Reunion',
];

// ── Build the actual zone list at module load ───────────────────────────────
function buildZones() {
  try {
    if (typeof Intl !== 'undefined' && typeof Intl.supportedValuesOf === 'function') {
      const browserZones = Intl.supportedValuesOf('timeZone');
      if (Array.isArray(browserZones) && browserZones.length > 0) {
        // Ensure UTC is first then everything else alphabetical
        const rest = browserZones.filter((z) => z !== 'UTC').sort();
        return ['UTC', ...rest];
      }
    }
  } catch {
    // fall through to static list
  }
  return [...FALLBACK_ZONES];
}

/** Full sorted list of IANA timezone identifiers ("America/Chicago", ...). */
export const ALL_TIMEZONES = buildZones();

/**
 * Compute a friendly UTC offset suffix for a zone, e.g. "(UTC-05:00)".
 * Returns "" if the offset can't be determined.
 */
export function formatOffset(zone) {
  try {
    const now = new Date();
    const fmt = new Intl.DateTimeFormat('en-US', {
      timeZone: zone,
      timeZoneName: 'shortOffset',
    });
    const parts = fmt.formatToParts(now);
    const tzPart = parts.find((p) => p.type === 'timeZoneName');
    if (tzPart?.value) {
      // "GMT-05:00" → "UTC-05:00", "GMT" → "UTC+00:00"
      const raw = tzPart.value.replace('GMT', 'UTC');
      if (raw === 'UTC') return '(UTC+00:00)';
      return `(${raw})`;
    }
  } catch {
    // ignore
  }
  return '';
}

/**
 * Dropdown-ready options: `{ value, label }` where label is the zone name
 * with underscores replaced by spaces, plus a UTC offset suffix.
 *
 * @returns {{value: string, label: string}[]}
 */
export function timezoneOptions() {
  return ALL_TIMEZONES.map((tz) => {
    const offset = formatOffset(tz);
    const friendly = tz.replace(/_/g, ' ');
    return {
      value: tz,
      label: offset ? `${friendly}  ${offset}` : friendly,
    };
  });
}
