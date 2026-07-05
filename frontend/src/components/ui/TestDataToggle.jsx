import React from 'react';
import { FlaskConical } from 'lucide-react';

/**
 * Reusable toggle for showing/hiding test data across views.
 * Renders as a small pill button that changes color when active.
 */
export default function TestDataToggle({ enabled, onChange }) {
  return (
    <button
      onClick={() => onChange(!enabled)}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
        enabled
          ? 'bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-400 border border-violet-300 dark:border-violet-700'
          : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 border border-gray-200 dark:border-gray-600 hover:bg-gray-200 dark:hover:bg-gray-600'
      }`}
      title={enabled ? 'Showing test data — click to hide' : 'Test data hidden — click to show'}
    >
      <FlaskConical className="w-3.5 h-3.5" />
      {enabled ? 'Test data shown' : 'Show test data'}
    </button>
  );
}

/**
 * Small inline badge to mark individual records as test data.
 */
export function TestBadge() {
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-violet-100 dark:bg-violet-900/40 text-violet-700 dark:text-violet-400 border border-violet-200 dark:border-violet-700">
      <FlaskConical className="w-2.5 h-2.5" />
      TEST
    </span>
  );
}
