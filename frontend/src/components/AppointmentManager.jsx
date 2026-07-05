import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  CalendarDays,
  CalendarCheck,
  CalendarClock,
  Clock,
  User,
  UserCog,
  Users,
  Phone as PhoneIcon,
  Mail,
  X,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  RefreshCw,
  CheckCircle2,
  XCircle,
  FileText,
  Save,
  Check,
  History,
} from 'lucide-react';
import { apiFetch } from '../lib/api';
import { useModal } from '../contexts/ModalContext';
import { useAuth } from '../contexts/AuthContext';
import { formatDateTime, formatTime, isSameDay } from '../lib/timezone';
import ThemedDatePicker from './ui/ThemedDatePicker';
import ThemedDateTimePicker from './ui/ThemedDateTimePicker';
import TestDataToggle, { TestBadge } from './ui/TestDataToggle';

const STATUS_STYLES = {
  CONFIRMED: 'bg-green-100 text-green-700 border-green-200 dark:bg-green-900/30 dark:text-green-400 dark:border-green-800',
  CANCELLED: 'bg-red-100 text-red-700 border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800',
  RESCHEDULED: 'bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800',
  COMPLETED: 'bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-900/30 dark:text-emerald-400 dark:border-emerald-800',
  NO_SHOW: 'bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-400 dark:border-amber-800',
};

const STATUS_LABELS = {
  CONFIRMED: 'Confirmed',
  CANCELLED: 'Cancelled',
  RESCHEDULED: 'Rescheduled',
  COMPLETED: 'Attended',
  NO_SHOW: 'No Show',
};

const TYPE_COLORS = {
  'Trial Session': 'border-l-blue-400',
  'Personal Training': 'border-l-indigo-400',
  'Group Class': 'border-l-violet-400',
  'HIIT Class': 'border-l-orange-400',
  'Yoga Class': 'border-l-emerald-400',
  'Open Gym': 'border-l-amber-400',
  'Session': 'border-l-amber-400',
};

export default function AppointmentManager() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { toast } = useModal();
  const tz = user?.timezone || 'America/Chicago';
  const [appointments, setAppointments] = useState([]);
  const [selectedApt, setSelectedApt] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState(null);
  const [viewYear, setViewYear] = useState(() => new Date().getFullYear());
  const [viewMonth, setViewMonth] = useState(() => new Date().getMonth());
  const [error, setError] = useState(null);
  const [providers, setProviders] = useState([]);
  const [selectedProvider, setSelectedProvider] = useState(null);
  const [showTestData, setShowTestData] = useState(false);
  // When set, the grid highlights this exact day inside the visible month.
  // Used by the calendar date picker so admins can jump to a date and have
  // it stand out in the month grid.
  const [highlightedDate, setHighlightedDate] = useState(null);

  // Track which day cells are expanded to show all appointments
  const [expandedDay, setExpandedDay] = useState(null);

  // Current time — ticks every minute for the time indicator bar
  const [currentTime, setCurrentTime] = useState(() => new Date());

  // Tenant config: holidays + business hours (fetched from /api/config)
  const [holidays, setHolidays] = useState([]);
  const [businessHours, setBusinessHours] = useState(null);

  // Notes editing state
  const [editingNotes, setEditingNotes] = useState(false);
  const [notesText, setNotesText] = useState('');
  const [savingNotes, setSavingNotes] = useState(false);

  // Status update state
  const [updatingStatus, setUpdatingStatus] = useState(false);
  const [confirmAction, setConfirmAction] = useState(null); // { action: 'attended'|'no-show'|'cancel', id: number }

  // Reschedule modal state
  const [rescheduleApt, setRescheduleApt] = useState(null);
  const [rescheduleTime, setRescheduleTime] = useState('');
  const [rescheduling, setRescheduling] = useState(false);

  // Status history timeline
  const [statusHistory, setStatusHistory] = useState([]);
  const [loadingHistory, setLoadingHistory] = useState(false);

  // Close detail drawer / confirmation modal on Escape key
  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape') {
        if (confirmAction) {
          setConfirmAction(null);
        } else if (selectedApt) {
          setSelectedApt(null);
        }
      }
    }
    if (selectedApt || confirmAction) {
      document.addEventListener('keydown', onKeyDown);
    }
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [selectedApt, confirmAction]);

  useEffect(() => {
    fetchAppointments();
    fetchProviders();
    fetchConfig();
    // Auto-refresh every 60s so new bookings flow in live
    const interval = setInterval(fetchAppointments, 60000);
    return () => clearInterval(interval);
  }, [showTestData]);

  // Tick every 60s to keep the current-time indicator bar moving
  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 60000);
    return () => clearInterval(timer);
  }, []);

  async function fetchProviders() {
    try {
      const data = await apiFetch('/api/trainers');
      setProviders(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error('Failed to fetch providers:', err);
    }
  }

  async function fetchConfig() {
    try {
      const data = await apiFetch('/api/config');
      setHolidays(Array.isArray(data.holidays) ? data.holidays : []);
      setBusinessHours(data.business_hours || null);
    } catch (err) {
      console.error('Failed to fetch config:', err);
    }
  }

  async function fetchAppointments(forceSync = false) {
    setError(null);
    if (forceSync) setRefreshing(true);
    try {
      const params = new URLSearchParams();
      if (forceSync) params.set('sync', '1');
      if (showTestData) params.set('include_test', 'true');
      const path = `/api/appointments${params.toString() ? '?' + params.toString() : ''}`;
      const data = await apiFetch(path);
      setAppointments(Array.isArray(data) ? data : data.items || []);
    } catch (err) {
      console.error('Failed to fetch appointments:', err);
      setError(err.message || 'Failed to load sessions');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  async function handleSyncGcal() {
    setError(null);
    setSyncResult(null);
    setSyncing(true);
    try {
      const data = await apiFetch('/api/appointments/sync-gcal', { method: 'POST' });
      setSyncResult(data);
      // Refresh the list after sync
      await fetchAppointments();
      // Auto-dismiss the success banner after 8 seconds
      setTimeout(() => setSyncResult(null), 8000);
    } catch (err) {
      console.error('Google Calendar sync failed:', err);
      setError(err.message || 'Google Calendar sync failed');
    } finally {
      setSyncing(false);
    }
  }

  async function handleCancel(id) {
    setUpdatingStatus(true);
    try {
      await apiFetch(`/api/appointments/${id}/cancel`, { method: 'POST' });
      setSelectedApt(null);
      setConfirmAction(null);
      fetchAppointments();
    } catch (err) {
      console.error('Cancel failed:', err);
      toast.error(err.message || 'Cancel failed');
    } finally {
      setUpdatingStatus(false);
    }
  }

  async function handleReschedule() {
    if (!rescheduleApt || !rescheduleTime) return;
    setRescheduling(true);
    try {
      // ThemedDateTimePicker gives "YYYY-MM-DDTHH:MM" (local time) — convert to UTC ISO
      const utcIso = new Date(rescheduleTime).toISOString();
      await apiFetch(`/api/appointments/${rescheduleApt.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ scheduled_at: utcIso }),
      });
      setRescheduleApt(null);
      setRescheduleTime('');
      setSelectedApt(null);
      fetchAppointments();
    } catch (err) {
      console.error('Reschedule failed:', err);
      toast.error(err.message || 'Reschedule failed');
    } finally {
      setRescheduling(false);
    }
  }

  async function handleStatusUpdate(id, newStatus) {
    setUpdatingStatus(true);
    try {
      const result = await apiFetch(`/api/appointments/${id}`, {
        method: 'PATCH',
        body: { status: newStatus },
      });
      // Update the selected appointment in-place
      setSelectedApt((prev) => prev ? { ...prev, status: result.current_status, notes: result.notes } : null);
      setConfirmAction(null);
      fetchAppointments();
      // Refresh timeline
      if (id) fetchHistory(id);
    } catch (err) {
      console.error('Status update failed:', err);
      toast.error(err.message || 'Status update failed');
    } finally {
      setUpdatingStatus(false);
    }
  }

  async function fetchHistory(id) {
    setLoadingHistory(true);
    try {
      const data = await apiFetch(`/api/appointments/${id}/history`);
      setStatusHistory(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error('Failed to fetch history:', err);
      setStatusHistory([]);
    } finally {
      setLoadingHistory(false);
    }
  }

  async function handleSaveNotes(id) {
    setSavingNotes(true);
    try {
      const result = await apiFetch(`/api/appointments/${id}`, {
        method: 'PATCH',
        body: { notes: notesText },
      });
      setSelectedApt((prev) => prev ? { ...prev, notes: result.notes } : null);
      setEditingNotes(false);
      fetchAppointments();
    } catch (err) {
      console.error('Save notes failed:', err);
      toast.error(err.message || 'Failed to save notes');
    } finally {
      setSavingNotes(false);
    }
  }

  // Compute the Monday of any given date (ISO week start).
  function mondayOf(date) {
    const d = new Date(date);
    d.setHours(0, 0, 0, 0);
    const dayIdx = d.getDay(); // 0 = Sun
    const diff = dayIdx === 0 ? -6 : 1 - dayIdx;
    d.setDate(d.getDate() + diff);
    return d;
  }

  // Jump to a specific date — switch to its month and highlight it.
  function jumpToDate(date) {
    if (!date) return;
    const target = new Date(date);
    target.setHours(0, 0, 0, 0);
    setViewYear(target.getFullYear());
    setViewMonth(target.getMonth());
    setHighlightedDate(target);
  }

  function navigateMonth(delta) {
    const d = new Date(viewYear, viewMonth + delta, 1);
    setViewYear(d.getFullYear());
    setViewMonth(d.getMonth());
  }

  const today = new Date();

  // Build full-month grid (Mon–Sun rows, includes leading/trailing days)
  const monthGridDays = (() => {
    const firstOfMonth = new Date(viewYear, viewMonth, 1);
    const lastOfMonth = new Date(viewYear, viewMonth + 1, 0);
    const startDay = mondayOf(firstOfMonth);
    const endDay = new Date(lastOfMonth);
    const endDow = endDay.getDay();
    if (endDow !== 0) endDay.setDate(endDay.getDate() + (7 - endDow));
    const days = [];
    const cursor = new Date(startDay);
    while (cursor <= endDay) {
      days.push(new Date(cursor));
      cursor.setDate(cursor.getDate() + 1);
    }
    return days;
  })();

  function getAppointmentsForDay(date) {
    return appointments.filter((a) => {
      if (!isSameDay(a.scheduled_at, date, tz)) return false;
      if (selectedProvider && a.provider_id !== selectedProvider) return false;
      return true;
    });
  }

  // Check if an appointment is in the past (for showing status actions)
  function isPastAppointment(apt) {
    return new Date(apt.scheduled_at) < new Date();
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="w-8 h-8 border-2 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="p-5 md:p-8 space-y-5 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
        <div>
          <h1 className="text-xl md:text-2xl font-bold text-gray-900 dark:text-white">Sessions</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            {appointments.filter((a) => {
              const d = new Date(a.scheduled_at);
              return d >= new Date() && d.getMonth() === viewMonth && d.getFullYear() === viewYear;
            }).length} sessions remaining this month
          </p>
        </div>
        <div className="flex items-center gap-2 md:gap-3 flex-wrap">
          <TestDataToggle enabled={showTestData} onChange={setShowTestData} />

          {/* Polished provider dropdown — replaces the basic <select> */}
          <ProviderPicker
            providers={providers}
            value={selectedProvider}
            onChange={setSelectedProvider}
          />

          {/* Date picker — jump to any date's month */}
          <ThemedDatePicker
            value={highlightedDate}
            onChange={jumpToDate}
            onClear={() => setHighlightedDate(null)}
            accent="amber"
            placeholder="Pick a date"
          />

          <button
            onClick={handleSyncGcal}
            disabled={syncing}
            title="Sync with Google Calendar"
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-indigo-200 dark:border-indigo-700 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/50 transition-colors disabled:opacity-50 text-sm font-medium"
          >
            <CalendarCheck className={`w-4 h-4 ${syncing ? 'animate-spin' : ''}`} />
            {syncing ? 'Syncing...' : 'Sync Google Calendar'}
          </button>
          <button
            onClick={() => fetchAppointments(true)}
            disabled={refreshing}
            title="Refresh sessions"
            className="p-2 rounded-lg border border-gray-200 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 text-gray-600 dark:text-gray-400 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={() => navigateMonth(-1)}
            className="p-2 rounded-lg border border-gray-200 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
          >
            <ChevronLeft className="w-4 h-4 text-gray-600 dark:text-gray-400" />
          </button>
          <button
            onClick={() => { setViewYear(new Date().getFullYear()); setViewMonth(new Date().getMonth()); setHighlightedDate(null); }}
            className="px-3 py-2 text-sm font-medium text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-900/30 rounded-lg hover:bg-indigo-100 dark:hover:bg-indigo-900/50 transition-colors"
          >
            Today
          </button>
          <button
            onClick={() => navigateMonth(1)}
            className="p-2 rounded-lg border border-gray-200 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
          >
            <ChevronRight className="w-4 h-4 text-gray-600 dark:text-gray-400" />
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-xl p-3 text-sm text-red-700 dark:text-red-400">
          {error}
        </div>
      )}

      {syncResult && (
        <div className="bg-indigo-50 dark:bg-indigo-900/30 border border-indigo-200 dark:border-indigo-800 rounded-xl p-3 text-sm text-indigo-700 dark:text-indigo-300 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CalendarCheck className="w-4 h-4" />
            <span>
              <strong>Google Calendar synced</strong> — {syncResult.pulled} pulled, {syncResult.pushed} pushed
              {syncResult.cancelled > 0 && <>, {syncResult.cancelled} cancellations synced</>}
              {syncResult.errors > 0 && <span className="text-amber-600 dark:text-amber-400 ml-1">({syncResult.errors} errors)</span>}
            </span>
          </div>
          <button onClick={() => setSyncResult(null)} className="p-1 hover:bg-indigo-100 dark:hover:bg-indigo-800 rounded">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}

      {/* Month label */}
      <p className="text-lg font-semibold text-gray-700 dark:text-gray-300">
        {new Date(viewYear, viewMonth).toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}
      </p>

      {/* Month calendar grid — horizontally scrollable on small screens */}
      <div className="overflow-x-auto -mx-4 md:mx-0 px-4 md:px-0">
      <div className="grid grid-cols-7 gap-1 min-w-[640px] md:min-w-0">
        {/* Day-of-week headers */}
        {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => (
          <div key={d} className="text-center text-[10px] md:text-[11px] font-semibold text-gray-400 dark:text-gray-500 uppercase py-2">
            {d}
          </div>
        ))}

        {monthGridDays.map((day) => {
          const dayApts = getAppointmentsForDay(day);
          const isToday = day.toDateString() === today.toDateString();
          const isHighlighted =
            highlightedDate && day.toDateString() === highlightedDate.toDateString();
          const isPast = day < today && !isToday;
          const isOutsideMonth = day.getMonth() !== viewMonth;

          // Check if this day is a configured holiday
          const dayStr = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`;
          const holiday = !isOutsideMonth ? holidays.find((h) => h.date === dayStr) : null;

          // Check if this day is closed per business hours
          const dowKeys = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
          const dowKey = dowKeys[day.getDay()];
          const dayHours = businessHours ? businessHours[dowKey] : null;
          const isClosed = holiday
            ? true
            : businessHours
            ? !dayHours
            : day.getDay() === 0;

          // Border / ring styling
          let borderRing = 'border-gray-100 dark:border-gray-700';
          if (holiday) {
            borderRing = 'border-rose-300 dark:border-rose-700 ring-1 ring-rose-200 dark:ring-rose-800';
          } else if (isHighlighted) {
            borderRing = 'border-amber-400 dark:border-amber-500 ring-2 ring-amber-300 dark:ring-amber-700';
          } else if (isToday) {
            borderRing = 'border-indigo-400 dark:border-indigo-500 ring-1 ring-indigo-200 dark:ring-indigo-800';
          }

          const MAX_VISIBLE = 3;
          const isDayExpanded = expandedDay === dayStr;
          const visibleApts = isDayExpanded ? dayApts : dayApts.slice(0, MAX_VISIBLE);
          const now = currentTime;

          return (
            <div
              key={day.toISOString()}
              className={`bg-white dark:bg-gray-800 rounded-lg border min-h-[110px] relative ${borderRing} ${
                isOutsideMonth ? 'opacity-30' : ''
              } ${isClosed && !isOutsideMonth ? 'opacity-60' : ''} ${
                isPast && !isOutsideMonth ? 'opacity-80' : ''
              }`}
            >
              {/* Day number header */}
              <div className={`px-2 py-1 flex items-center gap-1 ${
                holiday
                  ? 'bg-rose-50 dark:bg-rose-900/30'
                  : isToday
                  ? 'bg-indigo-50 dark:bg-indigo-900/30'
                  : ''
              }`}>
                <span className={`text-xs font-bold leading-none ${
                  isToday
                    ? 'text-white bg-indigo-500 rounded-full w-6 h-6 inline-flex items-center justify-center'
                    : holiday
                    ? 'text-rose-600 dark:text-rose-400'
                    : isHighlighted
                    ? 'text-amber-600 dark:text-amber-400'
                    : isOutsideMonth
                    ? 'text-gray-300 dark:text-gray-600'
                    : 'text-gray-700 dark:text-gray-300'
                }`}>
                  {day.getDate()}
                </span>
                {holiday && (
                  <span className="text-[9px] font-medium text-rose-500 dark:text-rose-400 truncate" title={holiday.name}>
                    🏖️ {holiday.name}
                  </span>
                )}
                {/* Appointment count badge for busy days */}
                {!holiday && !isClosed && dayApts.length > MAX_VISIBLE && (
                  <span className="ml-auto text-[9px] font-semibold text-gray-400 dark:text-gray-500 bg-gray-100 dark:bg-gray-700 px-1.5 rounded-full">
                    {dayApts.length}
                  </span>
                )}
              </div>

              {/* Day content */}
              {!isOutsideMonth && (
                <div className={`px-1 pb-1 space-y-0.5 ${isDayExpanded ? 'max-h-[200px] overflow-y-auto' : ''}`}>
                  {holiday ? (
                    <>
                      <p className="text-[10px] text-rose-400 dark:text-rose-500 text-center">Studio Closed</p>
                      {dayApts.length > 0 && (
                        <>
                          <p className="text-[10px] text-amber-500 dark:text-amber-400 font-medium text-center">
                            ⚠️ {dayApts.length} session{dayApts.length > 1 ? 's' : ''} need rescheduling
                          </p>
                          {dayApts.map((apt) => (
                            <button
                              key={apt.id}
                              onClick={() => {
                                setSelectedApt(apt);
                                setEditingNotes(false);
                                setNotesText(apt.notes || '');
                                fetchHistory(apt.id);
                              }}
                              className="w-full text-left px-1.5 py-0.5 rounded border-l-2 border-l-amber-400 bg-amber-50/60 dark:bg-amber-900/20 text-[11px] leading-tight hover:bg-amber-100 dark:hover:bg-amber-900/40 transition-colors truncate"
                            >
                              <span className="font-semibold text-amber-700 dark:text-amber-300">
                                {formatTime(apt.scheduled_at, tz)}
                              </span>
                              {' '}
                              <span className="text-amber-600 dark:text-amber-400">
                                {apt.client_name?.split(' ')[0]}
                              </span>
                            </button>
                          ))}
                        </>
                      )}
                    </>
                  ) : isClosed ? (
                    <p className="text-[10px] text-gray-400 dark:text-gray-500 text-center py-1">Closed</p>
                  ) : dayApts.length > 0 ? (
                    <>
                      {visibleApts.map((apt) => {
                        const aptTime = new Date(apt.scheduled_at);
                        const isCancelled = apt.status === 'CANCELLED';
                        const isPastApt = aptTime < now && !isCancelled;
                        const isUpcoming = aptTime >= now && apt.status === 'CONFIRMED';

                        // Color coding: green=upcoming, red=cancelled, muted=past
                        const typeDisplay = apt.appointment_type_display || apt.appointment_type;
                        const typeColor = TYPE_COLORS[typeDisplay] || TYPE_COLORS[apt.appointment_type] || 'border-l-gray-400';
                        const pillBg = isCancelled
                          ? 'bg-red-50 dark:bg-red-900/20 border-l-red-400'
                          : isUpcoming
                          ? 'bg-indigo-50/60 dark:bg-indigo-950/20 border-l-indigo-400'
                          : isPastApt
                          ? 'bg-gray-50 dark:bg-gray-700/50 ' + typeColor
                          : 'bg-gray-50 dark:bg-gray-700/50 ' + typeColor;

                        return (
                          <button
                            key={apt.id}
                            onClick={() => {
                              setSelectedApt(apt);
                              setEditingNotes(false);
                              setNotesText(apt.notes || '');
                              fetchHistory(apt.id);
                            }}
                            className={`w-full text-left px-1.5 py-0.5 rounded border-l-2 text-[11px] leading-tight hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors truncate ${pillBg} ${
                              isCancelled ? 'line-through opacity-60' : ''
                            }`}
                          >
                            <span className={`font-semibold ${
                              isCancelled
                                ? 'text-red-500 dark:text-red-400'
                                : isUpcoming
                                ? 'text-indigo-700 dark:text-indigo-300'
                                : 'text-gray-800 dark:text-gray-200'
                            }`}>
                              {formatTime(apt.scheduled_at, tz)}
                            </span>
                            {' '}
                            <span className={`${
                              isCancelled
                                ? 'text-red-400 dark:text-red-500'
                                : 'text-gray-500 dark:text-gray-400'
                            }`}>
                              {apt.client_name?.split(' ')[0]}
                            </span>
                          </button>
                        );
                      })}
                      {dayApts.length > MAX_VISIBLE && !isDayExpanded && (
                        <button
                          onClick={() => setExpandedDay(dayStr)}
                          className="text-[10px] text-indigo-500 dark:text-indigo-400 font-medium text-center cursor-pointer hover:text-indigo-600 dark:hover:text-indigo-300 w-full"
                        >
                          +{dayApts.length - MAX_VISIBLE} more
                        </button>
                      )}
                      {isDayExpanded && (
                        <button
                          onClick={() => setExpandedDay(null)}
                          className="text-[10px] text-gray-400 dark:text-gray-500 font-medium text-center cursor-pointer hover:text-gray-600 dark:hover:text-gray-300 w-full"
                        >
                          show less
                        </button>
                      )}
                    </>
                  ) : null}
                </div>
              )}

              {/* Current time indicator — horizontal line on today's cell */}
              {isToday && !isClosed && !holiday && dayHours && (() => {
                const openParts = dayHours.open?.split(':');
                const closeParts = dayHours.close?.split(':');
                if (!openParts || !closeParts) return null;
                const openMin = parseInt(openParts[0]) * 60 + parseInt(openParts[1] || 0);
                const closeMin = parseInt(closeParts[0]) * 60 + parseInt(closeParts[1] || 0);
                const totalRange = closeMin - openMin;
                if (totalRange <= 0) return null;

                // Convert current time to tenant timezone
                const nowInTz = new Date(now.toLocaleString('en-US', { timeZone: tz }));
                const nowMin = nowInTz.getHours() * 60 + nowInTz.getMinutes();

                // Only show if within business hours
                if (nowMin < openMin || nowMin > closeMin) return null;
                const pct = ((nowMin - openMin) / totalRange) * 100;

                // Format current time label (e.g. "2:45 PM")
                const timeLabel = nowInTz.toLocaleTimeString('en-US', {
                  hour: 'numeric',
                  minute: '2-digit',
                  hour12: true,
                });

                return (
                  <div
                    className="absolute left-0 right-0 pointer-events-none z-10 flex items-center"
                    style={{ top: `${20 + (pct * 0.8)}%` }}
                  >
                    <div className="w-2 h-2 rounded-full bg-red-500 -ml-1 shrink-0" />
                    <div className="flex-1 h-[2px] bg-red-500/70" />
                    <span className="text-[8px] font-bold text-red-500 bg-white dark:bg-gray-800 px-0.5 rounded leading-none whitespace-nowrap -mr-0.5">
                      {timeLabel}
                    </span>
                  </div>
                );
              })()}
            </div>
          );
        })}
      </div>
      </div>

      {/* Detail drawer */}
      {selectedApt && (
        <div className="fixed inset-0 bg-black/30 z-50 flex justify-end" onClick={() => setSelectedApt(null)}>
          <div className="w-full sm:w-[420px] bg-white dark:bg-gray-800 shadow-xl h-full overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="p-4 md:p-6 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
              <h3 className="text-lg font-bold text-gray-900 dark:text-white">Session Details</h3>
              <button
                onClick={() => setSelectedApt(null)}
                className="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                <X className="w-5 h-5 text-gray-500 dark:text-gray-400" />
              </button>
            </div>
            <div className="p-4 md:p-6 space-y-5">
              {/* Contact — clickable if caller_id available */}
              <div className="flex items-start gap-3">
                <User className="w-4 h-4 text-gray-400 dark:text-gray-500 mt-0.5" />
                <div>
                  <p className="text-xs text-gray-500 dark:text-gray-400">Member</p>
                  {selectedApt.caller_id ? (
                    <button
                      onClick={() => navigate(`/contacts/${selectedApt.caller_id}`)}
                      className="text-sm font-medium text-indigo-600 dark:text-indigo-400 hover:underline text-left"
                    >
                      {selectedApt.client_name}
                    </button>
                  ) : (
                    <p className="text-sm font-medium text-gray-900 dark:text-white">{selectedApt.client_name}</p>
                  )}
                </div>
              </div>
              {/* Phone with call + message actions */}
              <div className="flex items-start gap-3">
                <div className="flex-1">
                  <DetailRow icon={PhoneIcon} label="Phone" value={selectedApt.client_phone} />
                </div>
                {selectedApt.client_phone && (
                  <div className="flex gap-1.5 mt-0.5 shrink-0">
                    <a
                      href={`tel:${selectedApt.client_phone}`}
                      title="Call member"
                      className="flex items-center gap-1 px-2.5 py-1.5 bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-700 text-emerald-700 dark:text-emerald-400 rounded-lg text-xs font-medium hover:bg-emerald-100 dark:hover:bg-emerald-900/50 transition-colors"
                    >
                      <PhoneIcon className="w-3.5 h-3.5" />
                      Call
                    </a>
                    <a
                      href={`sms:${selectedApt.client_phone}`}
                      title="Send SMS"
                      className="flex items-center gap-1 px-2.5 py-1.5 bg-indigo-50 dark:bg-indigo-900/30 border border-indigo-200 dark:border-indigo-700 text-indigo-700 dark:text-indigo-400 rounded-lg text-xs font-medium hover:bg-indigo-100 dark:hover:bg-indigo-900/50 transition-colors"
                    >
                      <Mail className="w-3.5 h-3.5" />
                      SMS
                    </a>
                  </div>
                )}
              </div>
              <DetailRow icon={Mail} label="Email" value={selectedApt.client_email || '—'} />
              <DetailRow icon={CalendarDays} label="Type" value={selectedApt.appointment_type_display || selectedApt.appointment_type} />
              <DetailRow
                icon={UserCog}
                label="Trainer"
                value={selectedApt.provider_name
                  ? (selectedApt.provider_specialty
                      ? `${selectedApt.provider_name} · ${selectedApt.provider_specialty}`
                      : selectedApt.provider_name)
                  : '—'}
              />
              <DetailRow
                icon={Clock}
                label="Scheduled"
                value={formatDateTime(selectedApt.scheduled_at, tz)}
              />
              <DetailRow icon={Clock} label="Duration" value={`${selectedApt.duration_minutes} min`} />

              <div>
                <span
                  className={`inline-flex px-3 py-1.5 rounded-full text-xs font-medium ${
                    STATUS_STYLES[selectedApt.status] || 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300'
                  }`}
                >
                  {STATUS_LABELS[selectedApt.status] || selectedApt.status}
                </span>
                <span className="ml-2 text-xs text-gray-400 dark:text-gray-500">
                  Booked via {selectedApt.booked_via}
                </span>
              </div>

              {/* Notes section — editable */}
              <div className="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs font-medium text-gray-500 dark:text-gray-400 flex items-center gap-1">
                    <FileText className="w-3 h-3" />
                    Visit Notes
                  </p>
                  {!editingNotes && (
                    <button
                      onClick={() => {
                        setNotesText(selectedApt.notes || '');
                        setEditingNotes(true);
                      }}
                      className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
                    >
                      {selectedApt.notes ? 'Edit' : 'Add Notes'}
                    </button>
                  )}
                </div>
                {editingNotes ? (
                  <div className="space-y-2">
                    <textarea
                      value={notesText}
                      onChange={(e) => setNotesText(e.target.value)}
                      rows={4}
                      className="w-full p-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-sm text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-300 focus:border-indigo-300 resize-none"
                      placeholder="Add notes about this session (visible to AI on next call)..."
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleSaveNotes(selectedApt.id)}
                        disabled={savingNotes}
                        className="flex items-center gap-1 px-3 py-1.5 bg-indigo-600 text-white rounded-lg text-xs font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                      >
                        <Save className="w-3 h-3" />
                        {savingNotes ? 'Saving...' : 'Save'}
                      </button>
                      <button
                        onClick={() => setEditingNotes(false)}
                        className="px-3 py-1.5 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-600 rounded-lg transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-gray-700 dark:text-gray-300">
                    {selectedApt.notes || <span className="text-gray-400 dark:text-gray-500 italic">No notes yet</span>}
                  </p>
                )}
              </div>

              {/* Status History Timeline */}
              <div className="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg">
                <p className="text-xs font-medium text-gray-500 dark:text-gray-400 flex items-center gap-1 mb-3">
                  <History className="w-3 h-3" />
                  Status History
                </p>
                {loadingHistory ? (
                  <div className="flex justify-center py-2">
                    <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-indigo-500"></div>
                  </div>
                ) : statusHistory.length === 0 ? (
                  <p className="text-xs text-gray-400 dark:text-gray-500 italic">No history recorded yet.</p>
                ) : (
                  <div className="relative pl-4 space-y-3">
                    {/* Vertical line */}
                    <div className="absolute left-[5px] top-1 bottom-1 w-px bg-gray-200 dark:bg-gray-600" />
                    {statusHistory.map((entry) => {
                      const isCreation = !entry.old_status;
                      const statusLabel = STATUS_LABELS[entry.new_status] || entry.new_status;
                      return (
                        <div key={entry.id} className="relative flex items-start gap-2">
                          {/* Dot */}
                          <div className={`absolute -left-4 top-1 w-2.5 h-2.5 rounded-full border-2 border-white dark:border-gray-700 ${
                            entry.new_status === 'CONFIRMED' ? 'bg-green-400' :
                            entry.new_status === 'COMPLETED' ? 'bg-emerald-500' :
                            entry.new_status === 'CANCELLED' ? 'bg-red-400' :
                            entry.new_status === 'NO_SHOW' ? 'bg-amber-400' :
                            'bg-blue-400'
                          }`} />
                          <div className="min-w-0">
                            <p className="text-xs font-medium text-gray-700 dark:text-gray-300">
                              {isCreation ? (
                                <>Booked — <span className="text-green-600 dark:text-green-400">{statusLabel}</span></>
                              ) : (
                                <>{STATUS_LABELS[entry.old_status] || entry.old_status} → <span className={
                                  entry.new_status === 'COMPLETED' ? 'text-emerald-600 dark:text-emerald-400' :
                                  entry.new_status === 'CANCELLED' ? 'text-red-600 dark:text-red-400' :
                                  entry.new_status === 'NO_SHOW' ? 'text-amber-600 dark:text-amber-400' :
                                  'text-blue-600 dark:text-blue-400'
                                }>{statusLabel}</span></>
                              )}
                            </p>
                            <p className="text-[10px] text-gray-400 dark:text-gray-500 mt-0.5">
                              {entry.created_at ? formatDateTime(entry.created_at, tz) : ''}
                              {entry.changed_by && entry.changed_by !== 'system' && (
                                <span className="ml-1">· {entry.changed_by}</span>
                              )}
                            </p>
                            {entry.note && (
                              <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5 italic">{entry.note}</p>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Action buttons — context-sensitive */}
              <div className="space-y-2 pt-2 border-t border-gray-100 dark:border-gray-700">
                {/* Past CONFIRMED → mark attended or no-show */}
                {isPastAppointment(selectedApt) && selectedApt.status === 'CONFIRMED' && (
                  <>
                    <p className="text-xs text-amber-600 dark:text-amber-400 font-medium mb-2">
                      This session is in the past. Please update the outcome:
                    </p>
                    <div className="flex gap-2">
                      <button
                        onClick={() => setConfirmAction({ action: 'attended', id: selectedApt.id, status: 'COMPLETED' })}
                        disabled={updatingStatus}
                        className="flex-1 flex items-center justify-center gap-1.5 py-2.5 px-4 bg-indigo-50 dark:bg-indigo-950/30 text-indigo-600 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-800/50 rounded-lg text-sm font-medium hover:bg-indigo-100 dark:hover:bg-indigo-950/50 disabled:opacity-50 transition-colors"
                      >
                        <CheckCircle2 className="w-4 h-4" />
                        Attended
                      </button>
                      <button
                        onClick={() => setConfirmAction({ action: 'no-show', id: selectedApt.id, status: 'NO_SHOW' })}
                        disabled={updatingStatus}
                        className="flex-1 flex items-center justify-center gap-1.5 py-2.5 px-4 bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-800 rounded-lg text-sm font-medium hover:bg-amber-100 dark:hover:bg-amber-900/50 disabled:opacity-50 transition-colors"
                      >
                        <XCircle className="w-4 h-4" />
                        No Show
                      </button>
                    </div>
                  </>
                )}

                {/* Correction: NO_SHOW → mark as actually attended */}
                {selectedApt.status === 'NO_SHOW' && (
                  <button
                    onClick={() => setConfirmAction({ action: 'correct-attended', id: selectedApt.id, status: 'COMPLETED' })}
                    disabled={updatingStatus}
                    className="w-full flex items-center justify-center gap-1.5 py-2.5 px-4 bg-indigo-50 dark:bg-indigo-950/30 text-indigo-600 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-800/50 rounded-lg text-sm font-medium hover:bg-indigo-100 dark:hover:bg-indigo-950/50 disabled:opacity-50 transition-colors"
                  >
                    <CheckCircle2 className="w-4 h-4" />
                    Correct to Attended
                  </button>
                )}

                {/* Correction: COMPLETED → mark as actually no-show */}
                {selectedApt.status === 'COMPLETED' && (
                  <button
                    onClick={() => setConfirmAction({ action: 'correct-no-show', id: selectedApt.id, status: 'NO_SHOW' })}
                    disabled={updatingStatus}
                    className="w-full flex items-center justify-center gap-1.5 py-2.5 px-4 bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-800 rounded-lg text-sm font-medium hover:bg-amber-100 dark:hover:bg-amber-900/50 disabled:opacity-50 transition-colors"
                  >
                    <XCircle className="w-4 h-4" />
                    Correct to No Show
                  </button>
                )}

                {/* Future CONFIRMED → reschedule or cancel */}
                {selectedApt.status === 'CONFIRMED' && !isPastAppointment(selectedApt) && (
                  <div className="flex gap-2">
                    <button
                      onClick={() => {
                        // Pre-fill with current appointment time in local format
                        const dt = new Date(selectedApt.scheduled_at);
                        const pad = (n) => String(n).padStart(2, '0');
                        const localStr = `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
                        setRescheduleTime(localStr);
                        setRescheduleApt(selectedApt);
                      }}
                      className="flex-1 flex items-center justify-center gap-1.5 py-2.5 px-4 bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-800 rounded-lg text-sm font-medium hover:bg-blue-100 dark:hover:bg-blue-900/50 transition-colors"
                    >
                      <CalendarClock className="w-4 h-4" />
                      Reschedule
                    </button>
                    <button
                      onClick={() => setConfirmAction({ action: 'cancel', id: selectedApt.id })}
                      className="flex-1 py-2.5 px-4 bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-lg text-sm font-medium hover:bg-red-100 dark:hover:bg-red-900/50 transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Confirmation modal */}
      {confirmAction && (
        <div className="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4" onClick={() => setConfirmAction(null)}>
          <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-sm w-full p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-gray-900 dark:text-white">
              {confirmAction.action === 'cancel' ? 'Cancel Session?' : 'Update Status?'}
            </h3>
            <p className="text-sm text-gray-600 dark:text-gray-400">
              {confirmAction.action === 'attended' && 'Mark this session as attended?'}
              {confirmAction.action === 'no-show' && 'Mark this session as a no-show?'}
              {confirmAction.action === 'correct-attended' && 'Change status from No Show to Attended?'}
              {confirmAction.action === 'correct-no-show' && 'Change status from Attended to No Show?'}
              {confirmAction.action === 'cancel' && 'Are you sure you want to cancel this session?'}
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setConfirmAction(null)}
                disabled={updatingStatus}
                className="flex-1 px-4 py-2.5 border border-gray-200 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  if (confirmAction.action === 'cancel') {
                    handleCancel(confirmAction.id);
                  } else {
                    handleStatusUpdate(confirmAction.id, confirmAction.status);
                  }
                }}
                disabled={updatingStatus}
                className={`flex-1 px-4 py-2.5 rounded-lg text-sm font-medium disabled:opacity-50 transition-colors ${
                  confirmAction.action === 'cancel'
                    ? 'bg-red-500 text-white hover:bg-red-600'
                    : confirmAction.status === 'COMPLETED'
                    ? 'bg-indigo-500 text-white hover:bg-indigo-600'
                    : 'bg-amber-500 text-white hover:bg-amber-600'
                }`}
              >
                {updatingStatus ? 'Updating...' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reschedule modal */}
      {rescheduleApt && (
        <div className="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4" onClick={() => setRescheduleApt(null)}>
          <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-sm w-full p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-gray-900 dark:text-white">Reschedule Session</h3>
            <p className="text-sm text-gray-600 dark:text-gray-400">
              Pick a new date and time for <strong>{rescheduleApt.client_name}</strong>'s{' '}
              {rescheduleApt.appointment_type_display || rescheduleApt.appointment_type}.
            </p>
            <ThemedDateTimePicker
              value={rescheduleTime}
              onChange={setRescheduleTime}
              min={new Date()}
              placeholder="Pick new date & time"
              accent="primary"
              dropUp
            />
            <div className="flex gap-3">
              <button
                onClick={() => { setRescheduleApt(null); setRescheduleTime(''); }}
                disabled={rescheduling}
                className="flex-1 px-4 py-2.5 border border-gray-200 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleReschedule}
                disabled={rescheduling || !rescheduleTime}
                className="flex-1 px-4 py-2.5 bg-blue-500 text-white rounded-lg text-sm font-medium hover:bg-blue-600 disabled:opacity-50 transition-colors"
              >
                {rescheduling ? 'Rescheduling...' : 'Confirm Reschedule'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DetailRow({ icon: Icon, label, value }) {
  return (
    <div className="flex items-start gap-3">
      <Icon className="w-4 h-4 text-gray-400 dark:text-gray-500 mt-0.5" />
      <div>
        <p className="text-xs text-gray-500 dark:text-gray-400">{label}</p>
        <p className="text-sm font-medium text-gray-900 dark:text-white">{value}</p>
      </div>
    </div>
  );
}

// ── Polished provider picker (replaces the native <select>) ────────────────
function ProviderPicker({ providers, value, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const selected = providers.find((p) => p.id === value);

  // Close on outside click / Escape
  useEffect(() => {
    function onDoc(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    if (open) {
      document.addEventListener('mousedown', onDoc);
      document.addEventListener('keydown', onKey);
    }
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center gap-2 pl-3 pr-2 py-2 rounded-lg border text-sm font-medium transition-colors min-w-[140px] md:min-w-[180px] ${
          selected
            ? 'border-indigo-200 dark:border-indigo-700 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/50'
            : 'border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-600'
        }`}
      >
        <Users className="w-4 h-4 shrink-0" />
        <span className="flex-1 text-left truncate">
          {selected ? selected.name : 'All Staff'}
        </span>
        <ChevronDown
          className={`w-4 h-4 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 w-64 max-h-80 overflow-y-auto bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-xl z-30 py-1">
          <button
            type="button"
            onClick={() => { onChange(null); setOpen(false); }}
            className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${
              !value ? 'text-indigo-700 dark:text-indigo-300 font-medium' : 'text-gray-700 dark:text-gray-300'
            }`}
          >
            <Users className="w-4 h-4 text-gray-400 shrink-0" />
            <span className="flex-1">All Staff</span>
            {!value && <Check className="w-4 h-4 text-indigo-500 shrink-0" />}
          </button>
          {providers.length === 0 ? (
            <p className="px-3 py-2 text-xs text-gray-400 dark:text-gray-500 italic">
              No staff configured yet.
            </p>
          ) : (
            providers.map((p) => {
              const isSelected = p.id === value;
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => { onChange(p.id); setOpen(false); }}
                  className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${
                    isSelected ? 'text-indigo-700 dark:text-indigo-300 font-medium' : 'text-gray-700 dark:text-gray-300'
                  }`}
                >
                  <div className="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-100 to-indigo-200 dark:from-indigo-900/50 dark:to-indigo-800/50 flex items-center justify-center shrink-0">
                    <span className="text-xs font-semibold text-indigo-700 dark:text-indigo-300">
                      {(p.name || '?').charAt(0).toUpperCase()}
                    </span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="truncate">{p.name}</p>
                    {p.specialty && (
                      <p className="text-[11px] text-gray-400 dark:text-gray-500 truncate">{p.specialty}</p>
                    )}
                  </div>
                  {isSelected && <Check className="w-4 h-4 text-indigo-500 shrink-0" />}
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

