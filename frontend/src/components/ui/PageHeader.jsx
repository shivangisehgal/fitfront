import React from 'react';

/**
 * Shared page header used across all inner app pages.
 * Usage:
 *   <PageHeader title="Appointments" description="Manage your calendar">
 *     {/* optional right-side actions *\/}
 *   </PageHeader>
 */
export default function PageHeader({ title, description, children, icon: Icon, iconStyle = 'bg-indigo-500' }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
      <div className="flex items-center gap-3">
        {Icon && (
          <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 shadow-lg ${iconStyle}`}>
            <Icon className="w-5 h-5 text-white" />
          </div>
        )}
        <div>
          <h1 className="text-xl md:text-2xl font-bold text-gray-900 dark:text-white">{title}</h1>
          {description && (
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">{description}</p>
          )}
        </div>
      </div>
      {children && (
        <div className="flex items-center gap-2 shrink-0">
          {children}
        </div>
      )}
    </div>
  );
}

/** Reusable card wrapper */
export function PageCard({ children, className = '' }) {
  return (
    <div className={`bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 ${className}`}>
      {children}
    </div>
  );
}

/** Primary action button */
export function PrimaryBtn({ children, onClick, disabled, type = 'button', className = '' }) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold text-white bg-indigo-500 shadow-lg shadow-indigo-500/20 hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-all btn-press ${className}`}
    >
      {children}
    </button>
  );
}

/** Secondary / ghost button */
export function SecondaryBtn({ children, onClick, disabled, type = 'button', className = '' }) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium border border-gray-200 dark:border-white/10 bg-white dark:bg-white/5 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-white/8 disabled:opacity-40 transition-all btn-press ${className}`}
    >
      {children}
    </button>
  );
}
