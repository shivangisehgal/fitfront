// Shared label maps and status config for tenant admin UI

export const STATUS_CONFIG = {
  PENDING: {
    color: 'bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-400',
    dot: 'bg-amber-400',
    label: 'Pending',
  },
  APPROVED: {
    color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-400',
    dot: 'bg-blue-400',
    label: 'Approved',
  },
  ACTIVE: {
    color: 'bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-400',
    dot: 'bg-green-400',
    label: 'Active',
  },
  SUSPENDED: {
    color: 'bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-400',
    dot: 'bg-red-400',
    label: 'Suspended',
  },
  DEACTIVATED: {
    color: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',
    dot: 'bg-gray-400',
    label: 'Deactivated',
  },
};

export const STATUS_ACCENT = {
  PENDING: 'from-amber-400 to-orange-500',
  APPROVED: 'from-blue-400 to-indigo-500',
  ACTIVE: 'from-emerald-400 to-green-500',
  SUSPENDED: 'from-red-400 to-rose-500',
  DEACTIVATED: 'from-gray-400 to-gray-500',
};

export const BUSINESS_TYPE_LABELS = {
  fitness_studio: 'Fitness Studio',
  custom: 'Custom',
};

export const PLAN_LABELS = {
  starter: 'Starter',
  professional: 'Professional',
  enterprise: 'Enterprise',
};

export const TICKET_PRIORITY_LABELS = {
  LOW: 'Low',
  MEDIUM: 'Medium',
  HIGH: 'High',
  URGENT: 'Urgent',
};

export const TICKET_CATEGORY_LABELS = {
  GENERAL: 'General',
  BILLING: 'Billing',
  TECHNICAL: 'Technical',
  ACCOUNT: 'Account',
  FEATURE_REQUEST: 'Feature Request',
  VOICE_SETUP: 'Voice Setup',
  OTHER: 'Other',
};

export const TICKET_STATUS_LABELS = {
  OPEN: 'Open',
  IN_PROGRESS: 'In Progress',
  RESOLVED: 'Resolved',
  CLOSED: 'Closed',
  REOPENED: 'Reopened',
};
