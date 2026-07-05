import React, { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Users,
  Search,
  Phone,
  Mail,
  Calendar,
  Clock,
  AlertCircle,
  ChevronRight,
  ArrowLeft,
  UserCog,
  MessageSquare,
  FileText,
  Star,
  Save,
  CheckCircle,
  CheckCircle2,
  XCircle,
  CalendarClock,
  User,
  ArrowUp,
  ArrowDown,
  Hash,
  RefreshCw,
  SortAsc,
  Send,
  Trash2,
  Check,
} from 'lucide-react';
import { apiFetch } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';
import { useModal } from '../contexts/ModalContext';
import { formatDateTime, formatDate, formatRelativeTime as fmtRelative } from '../lib/timezone';
import ThemedSelect from './ui/ThemedSelect';
import ThemedDateTimePicker from './ui/ThemedDateTimePicker';
import TestDataToggle, { TestBadge } from './ui/TestDataToggle';

// ── Status badge colors ────────────────────────────────────────────────────
const STATUS_COLORS = {
  CONFIRMED: { bg: 'bg-green-50 dark:bg-green-900/30', text: 'text-green-700 dark:text-green-400', dot: 'bg-green-500' },
  COMPLETED: { bg: 'bg-blue-50 dark:bg-blue-900/30', text: 'text-blue-700 dark:text-blue-400', dot: 'bg-blue-500' },
  CANCELLED: { bg: 'bg-red-50 dark:bg-red-900/30', text: 'text-red-600 dark:text-red-400', dot: 'bg-red-400' },
  RESCHEDULED: { bg: 'bg-amber-50 dark:bg-amber-900/30', text: 'text-amber-700 dark:text-amber-400', dot: 'bg-amber-500' },
  NO_SHOW: { bg: 'bg-amber-50 dark:bg-amber-900/30', text: 'text-amber-700 dark:text-amber-400', dot: 'bg-amber-500' },
};

const STATUS_LABELS = {
  CONFIRMED: 'Confirmed',
  CANCELLED: 'Cancelled',
  RESCHEDULED: 'Rescheduled',
  COMPLETED: 'Attended',
  NO_SHOW: 'No Show',
};

export default function CallerCRM() {
  const { user } = useAuth();
  const { confirm, prompt, toast } = useModal();
  const navigate = useNavigate();
  const { id: selectedCallerId } = useParams();
  const tz = user?.timezone || 'America/Chicago';
  const [callers, setCallers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState('recent');
  const [showTestData, setShowTestData] = useState(false);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [deleting, setDeleting] = useState(false);
  const searchTimeout = useRef(null);

  const fetchCallers = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (search.trim()) params.set('search', search.trim());
      params.set('sort', sort);
      if (showTestData) params.set('include_test', 'true');
      const url = `/api/callers${params.toString() ? '?' + params.toString() : ''}`;
      const data = await apiFetch(url);
      setCallers(data || []);
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to load members');
    } finally {
      setLoading(false);
    }
  }, [search, sort, showTestData]);

  useEffect(() => {
    setLoading(true);
    // Debounce search
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    searchTimeout.current = setTimeout(() => fetchCallers(), 300);
    return () => clearTimeout(searchTimeout.current);
  }, [fetchCallers]);

  // Clear selections when caller list changes
  useEffect(() => {
    setSelectedIds(new Set());
  }, [callers]);

  function toggleSelect(id) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    if (selectedIds.size === callers.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(callers.map((p) => p.id)));
    }
  }

  async function handleBulkDelete() {
    const count = selectedIds.size;
    if (count === 0) return;

    const names = callers
      .filter((p) => selectedIds.has(p.id))
      .map((p) => p.name || p.phone);

    // Step 1: First confirmation
    const confirmed = await confirm({
      title: `Delete ${count} member${count > 1 ? 's' : ''}?`,
      message: `This will permanently delete ${count > 3 ? `${count} members` : names.join(', ')} and all their sessions, waitlist entries, and SMS messages. This cannot be undone.`,
      confirmText: 'Continue',
      variant: 'danger',
    });
    if (!confirmed) return;

    // Step 2: Type DELETE to confirm
    const typed = await prompt({
      title: 'Final confirmation',
      message: `Type DELETE to permanently remove ${count} member${count > 1 ? 's' : ''} and all related data.`,
      placeholder: 'Type DELETE',
      confirmText: 'Delete permanently',
      variant: 'danger',
    });
    if (typed !== 'DELETE') {
      if (typed !== null) toast.warning('Deletion cancelled — you must type DELETE exactly.');
      return;
    }

    setDeleting(true);
    try {
      const result = await apiFetch('/api/callers/bulk-delete', {
        method: 'POST',
        body: { caller_ids: [...selectedIds] },
      });
      toast.success(
        `Deleted ${result.deleted.callers} member${result.deleted.callers > 1 ? 's' : ''} and ${result.total - result.deleted.callers} related records.`
      );
      setSelectedIds(new Set());
      setSelectMode(false);
      await fetchCallers();
    } catch (err) {
      toast.error(err.message || 'Failed to delete members');
    } finally {
      setDeleting(false);
    }
  }

  if (selectedCallerId) {
    return (
      <CallerProfile
        callerId={selectedCallerId}
        tz={tz}
        onBack={() => {
          fetchCallers();
          navigate('/contacts');
        }}
      />
    );
  }

  return (
    <div className="p-5 md:p-8 space-y-5 max-w-5xl mx-auto animate-fade-in">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-xl md:text-2xl font-bold text-gray-900 dark:text-white">Members</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            Members and prospects from AI bookings, calls, and SMS.
          </p>
        </div>
        <button
          onClick={() => {
            setSelectMode(!selectMode);
            if (selectMode) setSelectedIds(new Set());
          }}
          className={`inline-flex items-center gap-1.5 px-4 py-2.5 border rounded-xl text-sm font-medium transition-all btn-press ${
            selectMode
              ? 'bg-indigo-50 dark:bg-indigo-900/20 border-indigo-300 dark:border-indigo-700 text-indigo-700 dark:text-indigo-400'
              : 'bg-white dark:bg-white/5 border-gray-200 dark:border-white/10 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/8'
          }`}
        >
          <CheckCircle2 className="w-4 h-4" />
          {selectMode ? 'Cancel Selection' : 'Select'}
        </button>
      </div>

      {/* Search & Sort bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or phone..."
            className="w-full pl-10 pr-4 py-2.5 border border-gray-200 dark:border-white/10 rounded-xl text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 bg-white dark:bg-white/5 dark:text-white placeholder-gray-400 dark:placeholder-white/25 transition-all"
          />
        </div>
        <div className="flex items-center gap-2">
          <TestDataToggle enabled={showTestData} onChange={setShowTestData} />
          <ThemedSelect
            value={sort}
            onChange={setSort}
            options={[
              { value: 'recent', label: 'Most Recent' },
              { value: 'name', label: 'Name A-Z' },
              { value: 'visits', label: 'Most Visits' },
            ]}
            className="w-44"
          />
        </div>
      </div>

      {/* Bulk delete toolbar — shows when callers are selected */}
      {selectMode && selectedIds.size > 0 && (
        <div className="flex items-center justify-between bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl px-4 py-3">
          <span className="text-sm font-medium text-red-700 dark:text-red-400">
            {selectedIds.size} member{selectedIds.size > 1 ? 's' : ''} selected
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setSelectedIds(new Set())}
              className="px-3 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
            >
              Clear
            </button>
            <button
              onClick={handleBulkDelete}
              disabled={deleting}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-red-500 text-white rounded-lg text-xs font-medium hover:bg-red-600 disabled:opacity-50 transition-colors"
            >
              <Trash2 className="w-3.5 h-3.5" />
              {deleting ? 'Deleting...' : `Delete ${selectedIds.size}`}
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-xl p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 mt-0.5 shrink-0" />
          <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="w-8 h-8 border-2 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin"></div>
        </div>
      ) : callers.length === 0 ? (
        <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-12 text-center">
          <Users className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">
            {search ? 'No members match your search' : 'No members yet'}
          </h3>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {search
              ? 'Try a different name or phone number.'
              : 'Member records are created automatically when the AI books sessions.'}
          </p>
        </div>
      ) : (
        <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden">
          {/* Table header — desktop only */}
          <div className="hidden md:flex items-center gap-3 px-4 py-3 bg-gray-50 dark:bg-gray-700/50 border-b border-gray-200 dark:border-gray-700 text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
            {selectMode && (
              <div className="shrink-0 w-5">
                <button
                  type="button"
                  onClick={toggleSelectAll}
                  className={`w-4 h-4 rounded flex items-center justify-center border transition-colors ${
                    selectedIds.size === callers.length && callers.length > 0
                      ? 'bg-indigo-500 border-indigo-500'
                      : 'bg-white dark:bg-gray-800 border-gray-300 dark:border-gray-600 hover:border-indigo-400'
                  }`}
                >
                  {selectedIds.size === callers.length && callers.length > 0 && (
                    <Check className="w-2.5 h-2.5 text-white" strokeWidth={3} />
                  )}
                </button>
              </div>
            )}
            <div className="grid grid-cols-12 gap-3 flex-1">
              <div className="col-span-4">Member</div>
              <div className="col-span-2">Phone</div>
              <div className="col-span-2 text-center">Visits</div>
              <div className="col-span-2">Last Seen</div>
              <div className="col-span-2 text-center">Upcoming</div>
            </div>
          </div>

          {/* Rows — desktop table / mobile card */}
          {callers.map((p, idx) => (
            <div
              key={p.id}
              className={`w-full hover:bg-indigo-50/50 dark:hover:bg-indigo-900/20 transition-colors ${
                idx > 0 ? 'border-t border-gray-100 dark:border-gray-700' : ''
              } ${selectMode && selectedIds.has(p.id) ? 'bg-red-50/50 dark:bg-red-900/10' : ''}`}
            >
              {/* Desktop row */}
              <div className="hidden md:flex items-center gap-3 px-4 py-3.5">
                {selectMode && (
                  <div className="shrink-0 w-5" onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      onClick={() => toggleSelect(p.id)}
                      className={`w-4 h-4 rounded flex items-center justify-center border transition-colors ${
                        selectedIds.has(p.id)
                          ? 'bg-indigo-500 border-indigo-500'
                          : 'bg-white dark:bg-gray-800 border-gray-300 dark:border-gray-600 hover:border-indigo-400'
                      }`}
                    >
                      {selectedIds.has(p.id) && (
                        <Check className="w-2.5 h-2.5 text-white" strokeWidth={3} />
                      )}
                    </button>
                  </div>
                )}
                <button
                  onClick={() => navigate(`/contacts/${p.id}`)}
                  className="grid grid-cols-12 gap-3 flex-1 items-center text-left"
                >
                  {/* Name + type */}
                  <div className="col-span-4 flex items-center gap-3 min-w-0">
                    <div
                      className={`w-9 h-9 rounded-full flex items-center justify-center text-sm font-semibold shrink-0 ${
                        p.is_new_caller
                          ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-400'
                          : 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/50 dark:text-indigo-400'
                      }`}
                    >
                      {(p.name || '?').charAt(0).toUpperCase()}
                    </div>
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-gray-900 dark:text-white truncate flex items-center gap-1.5">
                        {p.name}
                        {p.is_test && <TestBadge />}
                      </p>
                      <div className="flex items-center gap-1.5">
                        {p.is_new_caller && (
                          <span className="text-xs text-amber-600 font-medium">New</span>
                        )}
                        {p.preferred_appointment_type && (
                          <span className="text-xs text-gray-400 truncate">
                            {p.preferred_appointment_type.replace(/_/g, ' ')}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="col-span-2 text-xs text-gray-500 dark:text-gray-400 space-y-0.5 min-w-0">
                    <p className="truncate">{p.phone}</p>
                    {p.email && <p className="truncate text-gray-400">{p.email}</p>}
                  </div>
                  <div className="col-span-2 text-center">
                    <span className="text-sm font-semibold text-gray-900 dark:text-white">{p.visit_count}</span>
                    {p.no_show_count > 0 && (
                      <span className="ml-1 text-xs text-red-400">({p.no_show_count} NS)</span>
                    )}
                  </div>
                  <div className="col-span-2 text-xs text-gray-500 dark:text-gray-400">
                    {p.last_appointment_at
                      ? fmtRelative(p.last_appointment_at, tz)
                      : <span className="text-gray-300">Never</span>}
                  </div>
                  <div className="col-span-2 flex items-center justify-center gap-1">
                    {p.upcoming_count > 0 ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded-full text-xs font-medium">
                        <Calendar className="w-3 h-3" />
                        {p.upcoming_count}
                      </span>
                    ) : (
                      <span className="text-xs text-gray-300">—</span>
                    )}
                    <ChevronRight className="w-4 h-4 text-gray-300" />
                  </div>
                </button>
              </div>

              {/* Mobile card */}
              <div className="md:hidden px-4 py-3.5 flex items-center gap-3">
                {selectMode && (
                  <div onClick={(e) => e.stopPropagation()} className="shrink-0">
                    <button
                      type="button"
                      onClick={() => toggleSelect(p.id)}
                      className={`w-4 h-4 rounded flex items-center justify-center border transition-colors ${
                        selectedIds.has(p.id)
                          ? 'bg-indigo-500 border-indigo-500'
                          : 'bg-white dark:bg-gray-800 border-gray-300 dark:border-gray-600 hover:border-indigo-400'
                      }`}
                    >
                      {selectedIds.has(p.id) && (
                        <Check className="w-2.5 h-2.5 text-white" strokeWidth={3} />
                      )}
                    </button>
                  </div>
                )}
                <button
                  onClick={() => navigate(`/contacts/${p.id}`)}
                  className="flex-1 flex items-center gap-3 text-left min-w-0"
                >
                  <div
                    className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-semibold shrink-0 ${
                      p.is_new_caller
                        ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-400'
                        : 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/50 dark:text-indigo-400'
                    }`}
                  >
                    {(p.name || '?').charAt(0).toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-semibold text-gray-900 dark:text-white truncate">{p.name}</p>
                      {p.is_test && <TestBadge />}
                      {p.is_new_caller && (
                        <span className="text-[10px] bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-400 px-1.5 py-0.5 rounded-full font-medium shrink-0">New</span>
                      )}
                    </div>
                    <p className="text-xs text-gray-500 dark:text-gray-400 truncate mt-0.5">{p.phone}</p>
                    <div className="flex items-center gap-3 mt-1 text-xs text-gray-400">
                      <span>{p.visit_count} visit{p.visit_count !== 1 ? 's' : ''}</span>
                      {p.upcoming_count > 0 && (
                        <span className="text-green-600 dark:text-green-400 font-medium">{p.upcoming_count} upcoming</span>
                      )}
                      {p.last_appointment_at && (
                        <span>{fmtRelative(p.last_appointment_at, tz)}</span>
                      )}
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-gray-300 shrink-0" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Caller count */}
      {!loading && callers.length > 0 && (
        <p className="text-xs text-gray-400 text-center">
          {callers.length} member{callers.length !== 1 ? 's' : ''} total
        </p>
      )}
    </div>
  );
}


// ── Caller Profile (detail view) ──────────────────────────────────────────

function CallerProfile({ callerId, tz, onBack }) {
  const { toast, confirm, prompt } = useModal();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('appointments');
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [deletingCaller, setDeletingCaller] = useState(false);

  // Status update state (mirrors AppointmentManager.jsx)
  const [updatingStatus, setUpdatingStatus] = useState(false);
  const [confirmAction, setConfirmAction] = useState(null); // { action, id, status }
  const [expandedApptId, setExpandedApptId] = useState(null);

  const fetchProfile = useCallback(async () => {
    try {
      const result = await apiFetch(`/api/callers/${callerId}`);
      setData(result);
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to load member');
    } finally {
      setLoading(false);
    }
  }, [callerId]);

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  async function handleSaveNotes() {
    setSaving(true);
    try {
      await apiFetch(`/api/callers/${callerId}`, {
        method: 'PUT',
        body: editForm,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      setEditing(false);
      await fetchProfile();
    } catch (err) {
      setError(err.message || 'Failed to save');
    } finally {
      setSaving(false);
    }
  }

  // ── Appointment status handlers (mirror AppointmentManager.jsx) ──────────
  async function handleStatusUpdate(id, newStatus) {
    setUpdatingStatus(true);
    try {
      await apiFetch(`/api/appointments/${id}`, {
        method: 'PATCH',
        body: { status: newStatus },
      });
      setConfirmAction(null);
      await fetchProfile();
    } catch (err) {
      console.error('Status update failed:', err);
      toast.error(err.message || 'Status update failed');
    } finally {
      setUpdatingStatus(false);
    }
  }

  async function handleCancelAppt(id) {
    setUpdatingStatus(true);
    try {
      await apiFetch(`/api/appointments/${id}/cancel`, { method: 'POST' });
      setConfirmAction(null);
      await fetchProfile();
    } catch (err) {
      console.error('Cancel failed:', err);
      toast.error(err.message || 'Cancel failed');
    } finally {
      setUpdatingStatus(false);
    }
  }

  async function handleDeleteCaller() {
    const name = data?.caller?.name || 'this member';

    // Step 1: First confirmation
    const confirmed = await confirm({
      title: `Delete ${name}?`,
      message: `This will permanently delete ${name} and all their sessions, waitlist entries, and SMS messages. This action cannot be undone.`,
      confirmText: 'Continue',
      variant: 'danger',
    });
    if (!confirmed) return;

    // Step 2: Type DELETE to confirm
    const typed = await prompt({
      title: 'Final confirmation',
      message: `Type DELETE to permanently remove ${name} and all related data.`,
      placeholder: 'Type DELETE',
      confirmText: 'Delete permanently',
      variant: 'danger',
    });
    if (typed !== 'DELETE') {
      if (typed !== null) toast.warning('Deletion cancelled — you must type DELETE exactly.');
      return;
    }

    setDeletingCaller(true);
    try {
      const result = await apiFetch(`/api/callers/${callerId}`, { method: 'DELETE' });
      toast.success(
        `Deleted ${result.caller_name} and ${result.total - 1} related records.`
      );
      onBack(); // Return to contact list
    } catch (err) {
      toast.error(err.message || 'Failed to delete member');
    } finally {
      setDeletingCaller(false);
    }
  }

  function isPastAppointment(apt) {
    return new Date(apt.scheduled_at) < new Date();
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin"></div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-8">
        <button onClick={onBack} className="flex items-center gap-2 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 mb-4">
          <ArrowLeft className="w-4 h-4" /> Back to members
        </button>
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-xl p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500" />
          <p className="text-sm text-red-700 dark:text-red-400">{error || 'Member not found.'}</p>
        </div>
      </div>
    );
  }

  const p = data.caller;
  const appointments = data.appointments || [];
  const smsMessages = data.sms_messages || [];
  const calls = data.calls || [];

  const upcomingAppts = appointments.filter(
    (a) => a.status === 'CONFIRMED' && new Date(a.scheduled_at) > new Date()
  );
  const pastAppts = appointments.filter(
    (a) => a.status !== 'CONFIRMED' || new Date(a.scheduled_at) <= new Date()
  );

  const TABS = [
    { key: 'appointments', label: 'Sessions', icon: Calendar, count: appointments.length },
    { key: 'calls', label: 'Calls', icon: Phone, count: calls.length },
    { key: 'sms', label: 'SMS', icon: MessageSquare, count: smsMessages.length },
  ];

  return (
    <div className="p-4 md:p-8 max-w-5xl mx-auto space-y-4 md:space-y-6">
      {/* Back button */}
      <button
        onClick={onBack}
        className="flex items-center gap-2 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition-colors text-sm"
      >
        <ArrowLeft className="w-4 h-4" /> All Members
      </button>

      {saved && (
        <div className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded-xl p-3 flex items-center gap-2">
          <CheckCircle className="w-4 h-4 text-green-500" />
          <span className="text-sm text-green-700 dark:text-green-400">Member updated.</span>
        </div>
      )}

      {/* Caller header card */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-4 md:p-6">
        <div className="flex flex-col sm:flex-row items-start gap-4">
          {/* Avatar */}
          <div
            className={`w-12 h-12 md:w-14 md:h-14 rounded-full flex items-center justify-center text-lg md:text-xl font-bold shrink-0 ${
              p.is_new_caller
                ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-400'
                : 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/50 dark:text-indigo-400'
            }`}
          >
            {(p.name || '?').charAt(0).toUpperCase()}
          </div>

          {/* Info */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-xl font-bold text-gray-900 dark:text-white">{p.name}</h2>
              {p.is_new_caller ? (
                <span className="px-2 py-0.5 bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-400 rounded-full text-xs font-medium">
                  New Member
                </span>
              ) : (
                <span className="px-2 py-0.5 bg-indigo-50 dark:bg-indigo-900/50 text-indigo-700 dark:text-indigo-400 rounded-full text-xs font-medium">
                  Returning · {p.visit_count} visits
                </span>
              )}
            </div>

            <div className="flex items-center gap-4 mt-2 text-sm text-gray-500 dark:text-gray-400 flex-wrap">
              <a
                href={`tel:${p.phone}`}
                className="flex items-center gap-1 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors"
                onClick={(e) => e.stopPropagation()}
              >
                <Phone className="w-3.5 h-3.5" /> {p.phone}
              </a>
              {p.email && (
                <a
                  href={`mailto:${p.email}`}
                  className="flex items-center gap-1 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors"
                  onClick={(e) => e.stopPropagation()}
                >
                  <Mail className="w-3.5 h-3.5" /> {p.email}
                </a>
              )}
              {p.date_of_birth && (
                <span className="flex items-center gap-1">
                  <Calendar className="w-3.5 h-3.5" /> DOB: {p.date_of_birth}
                </span>
              )}
            </div>

            {/* Member extra data */}
            {p.extra_data && (p.extra_data.client_name || p.extra_data.grade || p.extra_data.board || p.extra_data.target_exam || p.extra_data.candidate_type || p.extra_data.attempt_number || p.extra_data.mode_preference || p.extra_data.medium) && (
              <div className="flex items-center gap-3 mt-2 flex-wrap">
                {p.extra_data.client_name && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 rounded-full text-xs font-medium">
                    <User className="w-3 h-3" /> Member: {p.extra_data.client_name}
                  </span>
                )}
                {p.extra_data.grade && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-sky-50 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300 rounded-full text-xs font-medium">
                    Grade {p.extra_data.grade}
                  </span>
                )}
                {p.extra_data.board && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-indigo-50 dark:bg-indigo-950/30 text-indigo-700 dark:text-indigo-300 rounded-full text-xs font-medium">
                    {p.extra_data.board}
                  </span>
                )}
                {p.extra_data.target_exam && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 rounded-full text-xs font-medium">
                    <FileText className="w-3 h-3" /> {p.extra_data.target_exam}
                  </span>
                )}
                {p.extra_data.candidate_type && (
                  <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                    p.extra_data.candidate_type === 'dropper'
                      ? 'bg-orange-50 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300'
                      : 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300'
                  }`}>
                    {p.extra_data.candidate_type === 'dropper' ? 'Dropper' : 'Fresher'}
                    {p.extra_data.attempt_number ? ` · Attempt ${p.extra_data.attempt_number}` : ''}
                  </span>
                )}
                {p.extra_data.mode_preference && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-purple-50 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 rounded-full text-xs font-medium capitalize">
                    {p.extra_data.mode_preference}
                  </span>
                )}
                {p.extra_data.medium && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-teal-50 dark:bg-teal-900/30 text-teal-700 dark:text-teal-300 rounded-full text-xs font-medium">
                    {p.extra_data.medium}
                  </span>
                )}
              </div>
            )}

            {/* Quick stats */}
            <div className="flex items-center gap-2 md:gap-4 mt-3 flex-wrap">
              <StatBadge
                label="Total Visits"
                value={p.visit_count}
                icon={Hash}
              />
              <StatBadge
                label="Upcoming"
                value={upcomingAppts.length}
                icon={Calendar}
                color={upcomingAppts.length > 0 ? 'green' : 'gray'}
              />
              <StatBadge
                label="SMS"
                value={smsMessages.length}
                icon={MessageSquare}
              />
              {p.first_seen_at && (
                <StatBadge
                  label="Member Since"
                  value={formatDate(p.first_seen_at, tz, { month: 'short', year: 'numeric', day: undefined })}
                  icon={Star}
                />
              )}
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => {
                setEditing(!editing);
                setEditForm({
                  notes: p.notes || '',
                });
              }}
              className="px-3 py-2 text-sm font-medium text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-700 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
            >
              {editing ? 'Cancel' : 'Edit Notes'}
            </button>
            <button
              onClick={handleDeleteCaller}
              disabled={deletingCaller}
              className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50 transition-colors"
              title="Delete member and all related data"
            >
              <Trash2 className="w-4 h-4" />
              {deletingCaller ? 'Deleting...' : 'Delete'}
            </button>
          </div>
        </div>

        {/* Edit form */}
        {editing && (
          <div className="mt-4 pt-4 border-t border-gray-100 dark:border-gray-700 space-y-3">
            <div className="grid grid-cols-1 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Staff Notes</label>
              <textarea
                value={editForm.notes || ''}
                onChange={(e) => setEditForm((f) => ({ ...f, notes: e.target.value }))}
                rows={2}
                className="w-full px-3 py-2 border border-gray-200 dark:border-gray-600 rounded-lg text-sm outline-none focus:border-indigo-500 resize-none dark:bg-gray-700 dark:text-white"
                placeholder="Internal notes about this member (visible to AI on next call)..."
              />
            </div>
            <button
              onClick={handleSaveNotes}
              disabled={saving}
              className="flex items-center gap-2 px-4 py-2 bg-indigo-500 text-white rounded-lg text-sm font-medium hover:bg-indigo-600 disabled:opacity-50 transition-colors"
            >
              <Save className="w-4 h-4" />
              {saving ? 'Saving...' : 'Save Changes'}
            </button>
            </div>
          </div>
        )}

        {/* Display notes if set and not editing */}
        {!editing && p.notes && (
          <div className="mt-4 pt-4 border-t border-gray-100 dark:border-gray-700 flex gap-6 text-sm">
            <div>
              <span className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">Notes</span>
              <p className="text-gray-700 dark:text-gray-300 mt-0.5">{p.notes}</p>
            </div>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-gray-200 dark:border-gray-700">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.key;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                isActive
                  ? 'border-indigo-500 text-indigo-700 dark:text-indigo-400'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
              }`}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
              <span
                className={`px-1.5 py-0.5 rounded-full text-xs ${
                  isActive ? 'bg-indigo-100 dark:bg-indigo-900/50 text-indigo-700 dark:text-indigo-400' : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
                }`}
              >
                {tab.count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      {activeTab === 'appointments' && (
        <AppointmentsTab
          upcoming={upcomingAppts}
          past={pastAppts}
          tz={tz}
          expandedApptId={expandedApptId}
          setExpandedApptId={setExpandedApptId}
          isPastAppointment={isPastAppointment}
          setConfirmAction={setConfirmAction}
          updatingStatus={updatingStatus}
          onRescheduleSuccess={fetchProfile}
        />
      )}
      {activeTab === 'calls' && <CallsTab calls={calls} tz={tz} />}
      {activeTab === 'sms' && <SMSTab messages={smsMessages} tz={tz} callerPhone={p.phone} onMessageSent={fetchProfile} />}

      {/* Confirmation modal (mirrors AppointmentManager.jsx) */}
      {confirmAction && (
        <div
          className="fixed inset-0 bg-black/50 z-[60] flex items-center justify-center p-4"
          onClick={() => setConfirmAction(null)}
        >
          <div
            className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-sm w-full p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
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
                    handleCancelAppt(confirmAction.id);
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
    </div>
  );
}


// ── Sub-components ──────────────────────────────────────────────────────────

function StatBadge({ label, value, icon: Icon, color = 'gray' }) {
  const colors = {
    gray: 'bg-gray-50 dark:bg-gray-700/50 text-gray-700 dark:text-gray-300',
    green: 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400',
  };
  return (
    <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg ${colors[color] || colors.gray}`}>
      <Icon className="w-3.5 h-3.5 opacity-50" />
      <span className="text-xs font-medium">{value}</span>
      <span className="text-xs text-gray-400">{label}</span>
    </div>
  );
}

function AppointmentsTab({
  upcoming,
  past,
  tz,
  expandedApptId,
  setExpandedApptId,
  isPastAppointment,
  setConfirmAction,
  updatingStatus,
  onRescheduleSuccess,
}) {
  const rowProps = {
    tz,
    expandedApptId,
    setExpandedApptId,
    isPastAppointment,
    setConfirmAction,
    updatingStatus,
    onRescheduleSuccess,
  };
  return (
    <div className="space-y-4">
      {upcoming.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-2">
            <Calendar className="w-4 h-4 text-green-500" />
            Upcoming ({upcoming.length})
          </h4>
          <div className="space-y-2">
            {upcoming.map((a) => (
              <AppointmentRow key={a.id} appt={a} {...rowProps} />
            ))}
          </div>
        </div>
      )}
      {past.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-2">
            <Clock className="w-4 h-4 text-gray-400" />
            History ({past.length})
          </h4>
          <div className="space-y-2">
            {past.map((a) => (
              <AppointmentRow key={a.id} appt={a} {...rowProps} />
            ))}
          </div>
        </div>
      )}
      {upcoming.length === 0 && past.length === 0 && (
        <EmptyState icon={Calendar} message="No session history yet." />
      )}
    </div>
  );
}

function AppointmentRow({
  appt,
  tz,
  expandedApptId,
  setExpandedApptId,
  isPastAppointment,
  setConfirmAction,
  updatingStatus,
  onRescheduleSuccess,
}) {
  const status = STATUS_COLORS[appt.status] || STATUS_COLORS.CONFIRMED;
  const isExpanded = expandedApptId === appt.id;
  const isPast = isPastAppointment(appt);

  // Reschedule local state
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [rescheduleTime, setRescheduleTime] = useState('');
  const [rescheduling, setRescheduling] = useState(false);

  async function handleReschedule() {
    if (!rescheduleTime) return;
    setRescheduling(true);
    try {
      const utcIso = new Date(rescheduleTime).toISOString();
      await apiFetch(`/api/appointments/${appt.id}`, {
        method: 'PATCH',
        body: { scheduled_at: utcIso },
      });
      setRescheduleOpen(false);
      setRescheduleTime('');
      onRescheduleSuccess?.();
    } catch (err) {
      console.error('Reschedule failed:', err);
    } finally {
      setRescheduling(false);
    }
  }

  // Decide whether any actions are available for this appointment
  const hasPastPendingActions = isPast && appt.status === 'CONFIRMED';
  const hasCorrectAttended = appt.status === 'NO_SHOW';
  const hasCorrectNoShow = appt.status === 'COMPLETED';
  const hasReschedule = (appt.status === 'CONFIRMED' || appt.status === 'RESCHEDULED') && !isPast;
  const hasCancel = appt.status === 'CONFIRMED' && !isPast;
  const hasAnyActions =
    hasPastPendingActions || hasCorrectAttended || hasCorrectNoShow || hasReschedule || hasCancel;
  const hasHistory = appt.status_history && appt.status_history.length > 0;
  const isExpandable = hasAnyActions || hasHistory;

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
      <button
        type="button"
        onClick={() => isExpandable && setExpandedApptId(isExpanded ? null : appt.id)}
        disabled={!isExpandable}
        className={`w-full p-3 flex items-center gap-4 text-left transition-colors ${
          isExpandable ? 'hover:bg-gray-50 dark:hover:bg-gray-700/40 cursor-pointer' : 'cursor-default'
        }`}
      >
        <div className={`w-2 h-2 rounded-full ${status.dot} shrink-0`}></div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-gray-900 dark:text-white">
              {appt.appointment_type_display || appt.appointment_type?.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
            </span>
            <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${status.bg} ${status.text}`}>
              {STATUS_LABELS[appt.status] || appt.status}
            </span>
            {appt.booked_via === 'AI' && (
              <span className="px-1.5 py-0.5 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400 rounded text-xs font-medium">
                AI Booked
              </span>
            )}
            {appt.confirmed_by_client === true && (
              <span className="px-1.5 py-0.5 bg-green-50 dark:bg-green-900/30 text-green-600 dark:text-green-400 rounded text-xs font-medium">
                ✓ Confirmed
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            {appt.scheduled_at ? formatDateTime(appt.scheduled_at, tz) : ''}{' '}
            · {appt.duration_minutes} min
            {appt.provider_name && (
              <span className="ml-1">· <UserCog className="w-3 h-3 inline -mt-0.5" /> {appt.provider_name}{appt.provider_specialty ? ` · ${appt.provider_specialty}` : ''}</span>
            )}
          </p>
        </div>
        {isExpandable && (
          <ChevronRight
            className={`w-4 h-4 text-gray-300 shrink-0 transition-transform ${
              isExpanded ? 'rotate-90' : ''
            }`}
          />
        )}
      </button>

      {/* Expanded section — history + actions */}
      {isExpanded && isExpandable && (
        <div className="border-t border-gray-100 dark:border-gray-700 p-3 bg-gray-50 dark:bg-gray-700/30 space-y-3">
          {/* Status History Timeline */}
          {hasHistory && (
            <div>
              <p className="text-[10px] font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wide mb-2">
                History
              </p>
              <div className="relative pl-4 space-y-2">
                <div className="absolute left-[5px] top-0.5 bottom-0.5 w-px bg-gray-200 dark:bg-gray-600" />
                {appt.status_history.map((entry, idx) => {
                  const isCreation = !entry.old_status;
                  const label = STATUS_LABELS[entry.new_status] || entry.new_status;
                  return (
                    <div key={idx} className="relative flex items-start gap-2">
                      <div className={`absolute -left-4 top-0.5 w-2 h-2 rounded-full border-2 border-white dark:border-gray-700 ${
                        entry.new_status === 'CONFIRMED' ? 'bg-green-400' :
                        entry.new_status === 'COMPLETED' ? 'bg-emerald-500' :
                        entry.new_status === 'CANCELLED' ? 'bg-red-400' :
                        entry.new_status === 'NO_SHOW' ? 'bg-amber-400' :
                        'bg-blue-400'
                      }`} />
                      <div className="min-w-0">
                        <p className="text-[11px] text-gray-600 dark:text-gray-400">
                          {isCreation ? (
                            <span className="text-green-600 dark:text-green-400">Booked</span>
                          ) : (
                            <>{STATUS_LABELS[entry.old_status] || entry.old_status} → <span className={
                              entry.new_status === 'COMPLETED' ? 'text-emerald-600 dark:text-emerald-400' :
                              entry.new_status === 'CANCELLED' ? 'text-red-600 dark:text-red-400' :
                              entry.new_status === 'NO_SHOW' ? 'text-amber-600 dark:text-amber-400' :
                              'text-blue-600 dark:text-blue-400'
                            }>{label}</span></>
                          )}
                          {entry.created_at && (
                            <span className="text-gray-400 dark:text-gray-500 ml-1">
                              · {fmtRelative(entry.created_at, tz)}
                            </span>
                          )}
                        </p>
                        {entry.note && (
                          <p className="text-[10px] text-gray-400 dark:text-gray-500 italic mt-0.5">{entry.note}</p>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {hasPastPendingActions && (
            <>
              <p className="text-xs text-amber-600 dark:text-amber-400 font-medium">
                This session is in the past. Please update the outcome:
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() =>
                    setConfirmAction({ action: 'attended', id: appt.id, status: 'COMPLETED' })
                  }
                  disabled={updatingStatus}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 px-3 bg-indigo-50 dark:bg-indigo-950/30 text-indigo-600 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-800/50 rounded-lg text-xs font-medium hover:bg-indigo-100 dark:hover:bg-indigo-950/50 disabled:opacity-50 transition-colors"
                >
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  Attended
                </button>
                <button
                  onClick={() =>
                    setConfirmAction({ action: 'no-show', id: appt.id, status: 'NO_SHOW' })
                  }
                  disabled={updatingStatus}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2 px-3 bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-800 rounded-lg text-xs font-medium hover:bg-amber-100 dark:hover:bg-amber-900/50 disabled:opacity-50 transition-colors"
                >
                  <XCircle className="w-3.5 h-3.5" />
                  No Show
                </button>
              </div>
            </>
          )}

          {hasCorrectAttended && (
            <button
              onClick={() =>
                setConfirmAction({ action: 'correct-attended', id: appt.id, status: 'COMPLETED' })
              }
              disabled={updatingStatus}
              className="w-full flex items-center justify-center gap-1.5 py-2 px-3 bg-indigo-50 dark:bg-indigo-950/30 text-indigo-600 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-800/50 rounded-lg text-xs font-medium hover:bg-indigo-100 dark:hover:bg-indigo-950/50 disabled:opacity-50 transition-colors"
            >
              <CheckCircle2 className="w-3.5 h-3.5" />
              Correct to Attended
            </button>
          )}

          {hasCorrectNoShow && (
            <button
              onClick={() =>
                setConfirmAction({ action: 'correct-no-show', id: appt.id, status: 'NO_SHOW' })
              }
              disabled={updatingStatus}
              className="w-full flex items-center justify-center gap-1.5 py-2 px-3 bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-800 rounded-lg text-xs font-medium hover:bg-amber-100 dark:hover:bg-amber-900/50 disabled:opacity-50 transition-colors"
            >
              <XCircle className="w-3.5 h-3.5" />
              Correct to No Show
            </button>
          )}

          {hasReschedule && (
            <div className="space-y-2">
              {!rescheduleOpen ? (
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      // Pre-fill with current scheduled time
                      if (appt.scheduled_at) {
                        const d = new Date(appt.scheduled_at);
                        const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000)
                          .toISOString().slice(0, 16);
                        setRescheduleTime(local);
                      }
                      setRescheduleOpen(true);
                    }}
                    className="flex-1 flex items-center justify-center gap-1.5 py-2 px-3 bg-blue-50 dark:bg-blue-950/30 text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-800/50 rounded-lg text-xs font-medium hover:bg-blue-100 dark:hover:bg-blue-950/50 transition-colors"
                  >
                    <CalendarClock className="w-3.5 h-3.5" />
                    Reschedule
                  </button>
                  {hasCancel && (
                    <button
                      onClick={() => setConfirmAction({ action: 'cancel', id: appt.id })}
                      disabled={updatingStatus}
                      className="flex-1 py-2 px-3 bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-lg text-xs font-medium hover:bg-red-100 dark:hover:bg-red-900/50 disabled:opacity-50 transition-colors"
                    >
                      Cancel
                    </button>
                  )}
                </div>
              ) : (
                <>
                  <button
                    onClick={() => setRescheduleOpen(false)}
                    className="w-full py-2 px-3 border border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 rounded-lg text-xs font-medium hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                  >
                    Cancel Reschedule
                  </button>
                  {createPortal(
                    <div
                      className="fixed inset-0 z-[200] flex items-center justify-center p-4 bg-black/50"
                      onClick={() => setRescheduleOpen(false)}
                    >
                      <div
                        className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-md p-5 space-y-4"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <p className="text-sm font-semibold text-blue-700 dark:text-blue-400">Pick a new date & time:</p>
                        <ThemedDateTimePicker
                          value={rescheduleTime}
                          onChange={setRescheduleTime}
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={handleReschedule}
                            disabled={!rescheduleTime || rescheduling}
                            className="flex-1 py-2 px-3 bg-blue-500 text-white rounded-lg text-xs font-medium hover:bg-blue-600 disabled:opacity-50 transition-colors"
                          >
                            {rescheduling ? 'Saving…' : 'Confirm Reschedule'}
                          </button>
                          <button
                            onClick={() => setRescheduleOpen(false)}
                            className="py-2 px-3 border border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 rounded-lg text-xs font-medium hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    </div>,
                    document.body
                  )}
                </>
              )}
            </div>
          )}

          {!hasReschedule && hasCancel && (
            <button
              onClick={() => setConfirmAction({ action: 'cancel', id: appt.id })}
              disabled={updatingStatus}
              className="w-full py-2 px-3 bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-lg text-xs font-medium hover:bg-red-100 dark:hover:bg-red-900/50 disabled:opacity-50 transition-colors"
            >
              Cancel Session
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function CallsTab({ calls, tz }) {
  const [expandedId, setExpandedId] = useState(null);
  if (calls.length === 0) {
    return <EmptyState icon={Phone} message="No calls recorded." />;
  }
  return (
    <div className="space-y-2">
      {calls.map((call) => {
        const isExpanded = expandedId === call.id;
        const durationMin = call.duration_seconds ? Math.floor(call.duration_seconds / 60) : null;
        const durationSec = call.duration_seconds ? call.duration_seconds % 60 : null;
        const durationLabel = durationMin !== null
          ? `${durationMin}m ${durationSec}s`
          : null;
        const outcomeColors = {
          booked: 'text-green-600 dark:text-green-400',
          transferred: 'text-blue-600 dark:text-blue-400',
          voicemail: 'text-amber-600 dark:text-amber-400',
          no_answer: 'text-gray-400',
          completed: 'text-emerald-600 dark:text-emerald-400',
        };
        const outcomeLabel = call.outcome
          ? call.outcome.charAt(0).toUpperCase() + call.outcome.slice(1).replace(/_/g, ' ')
          : 'Unknown';
        return (
          <div key={call.id} className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden">
            <button
              className="w-full flex items-center gap-3 p-4 text-left hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
              onClick={() => setExpandedId(isExpanded ? null : call.id)}
            >
              <div className="flex-shrink-0 w-8 h-8 rounded-full bg-indigo-50 dark:bg-indigo-900/30 flex items-center justify-center">
                <Phone className="w-4 h-4 text-indigo-500" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-900 dark:text-white">
                  {call.started_at ? formatDateTime(call.started_at, tz) : 'Unknown time'}
                </p>
                <div className="flex items-center gap-2 mt-0.5">
                  {call.outcome && (
                    <span className={`text-xs font-medium ${outcomeColors[call.outcome] || 'text-gray-500'}`}>
                      {outcomeLabel}
                    </span>
                  )}
                  {durationLabel && (
                    <span className="text-xs text-gray-400 dark:text-gray-500 flex items-center gap-1">
                      <Clock className="w-3 h-3" /> {durationLabel}
                    </span>
                  )}
                </div>
              </div>
              <ChevronRight className={`w-4 h-4 text-gray-400 flex-shrink-0 transition-transform ${isExpanded ? 'rotate-90' : ''}`} />
            </button>
            {isExpanded && (
              <div className="border-t border-gray-100 dark:border-gray-700 p-4 space-y-3">
                {call.summary && (
                  <div>
                    <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-1">Summary</p>
                    <p className="text-sm text-gray-700 dark:text-gray-300">{call.summary}</p>
                  </div>
                )}
                {call.transcript && call.transcript.length > 0 && (
                  <div>
                    <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">Transcript</p>
                    <div className="max-h-64 overflow-y-auto space-y-2">
                      {call.transcript.map((msg, idx) => {
                        const isAgent = msg.role === 'assistant' || msg.role === 'agent';
                        return (
                          <div key={idx} className={`flex ${isAgent ? 'justify-start' : 'justify-end'}`}>
                            <div className={`max-w-[80%] rounded-xl px-3 py-2 text-xs ${
                              isAgent
                                ? 'bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200'
                                : 'bg-indigo-50 dark:bg-indigo-900/30 text-indigo-800 dark:text-indigo-200'
                            }`}>
                              <p className="text-[10px] font-medium mb-0.5 opacity-60">
                                {isAgent ? 'Agent' : 'Caller'}
                              </p>
                              <p className="whitespace-pre-wrap break-words">{msg.content || msg.message || ''}</p>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
                {!call.summary && (!call.transcript || call.transcript.length === 0) && (
                  <p className="text-sm text-gray-400 dark:text-gray-500 italic">No details available for this call.</p>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function SMSTab({ messages, tz, callerPhone, onMessageSent }) {
  const [composeText, setComposeText] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState(null);

  async function handleSend() {
    const text = composeText.trim();
    if (!text || !callerPhone || sending) return;
    setSending(true);
    setSendError(null);
    try {
      await apiFetch('/api/sms/send', {
        method: 'POST',
        body: { to: callerPhone, message: text },
      });
      setComposeText('');
      onMessageSent?.();
    } catch (err) {
      setSendError(err.message || 'Failed to send');
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="space-y-3">
      {messages.length === 0 ? (
        <EmptyState icon={MessageSquare} message="No SMS messages." />
      ) : (
        <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 max-h-[400px] overflow-y-auto overscroll-y-contain p-4 space-y-2" style={{ WebkitOverflowScrolling: 'touch' }}>
          {[...messages].reverse().map((msg) => {
            const isOutbound = msg.direction === 'OUTBOUND';
            const isAdmin = msg.sender_type === 'admin';
            return (
              <div key={msg.id} className={`flex ${isOutbound ? 'justify-end' : 'justify-start'}`}>
                <div
                  className={`max-w-[70%] rounded-2xl px-3 py-2 ${
                    isOutbound
                      ? isAdmin
                        ? 'bg-emerald-500 text-white rounded-br-md'
                        : 'bg-indigo-500 text-white rounded-br-md'
                      : 'bg-gray-100 dark:bg-gray-700 text-gray-900 dark:text-white rounded-bl-md'
                  }`}
                >
                  <div className={`flex items-center gap-1 mb-0.5 text-[10px] ${
                    isOutbound ? 'text-white/60' : 'text-gray-400'
                  }`}>
                    {isOutbound ? (
                      <><ArrowUp className="w-2.5 h-2.5" /> {isAdmin ? 'You' : 'AI'}</>
                    ) : (
                      <><ArrowDown className="w-2.5 h-2.5" /> Caller</>
                    )}
                  </div>
                  <p className="text-sm whitespace-pre-wrap break-words">{msg.body}</p>
                  <p className={`text-[10px] mt-1 ${isOutbound ? 'text-white/50' : 'text-gray-400'}`}>
                    {msg.created_at ? formatDateTime(msg.created_at, tz) : ''}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Compose */}
      {callerPhone && (
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-3 space-y-2">
          <textarea
            value={composeText}
            onChange={(e) => setComposeText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSend();
            }}
            placeholder="Type a message…"
            rows={2}
            className="w-full resize-none text-sm bg-transparent text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none"
          />
          {sendError && <p className="text-xs text-red-500">{sendError}</p>}
          <div className="flex justify-end">
            <button
              onClick={handleSend}
              disabled={!composeText.trim() || sending}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-500 text-white rounded-lg text-xs font-medium hover:bg-indigo-600 disabled:opacity-50 transition-colors"
            >
              <Send className="w-3.5 h-3.5" />
              {sending ? 'Sending…' : 'Send'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function EmptyState({ icon: Icon, message }) {
  return (
    <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-8 text-center">
      <Icon className="w-10 h-10 text-gray-300 mx-auto mb-2" />
      <p className="text-sm text-gray-500 dark:text-gray-400">{message}</p>
    </div>
  );
}


// ── Helpers ─────────────────────────────────────────────────────────────────
// formatRelativeTime is now imported from lib/timezone.js as fmtRelative
