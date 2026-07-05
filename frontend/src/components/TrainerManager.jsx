import React, { useState, useEffect, useCallback } from 'react';
import {
  UserPlus,
  Users,
  Pencil,
  Trash2,
  Save,
  X,
  Calendar,
  Clock,
  CalendarCheck,
  CheckCircle,
  AlertCircle,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import { apiFetch } from '../lib/api';
import { useModal } from '../contexts/ModalContext';
import ThemedTimePicker from './ui/ThemedTimePicker';

const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];

// Styled time select — 30-min intervals, 12h display, stored as HH:MM 24h
function TimeSelect({ value, onChange, className = '' }) {
  const options = [];
  for (let h = 0; h < 24; h++) {
    for (const m of [0, 30]) {
      const hh = String(h).padStart(2, '0');
      const mm = String(m).padStart(2, '0');
      const val = `${hh}:${mm}`;
      const period = h < 12 ? 'AM' : 'PM';
      const displayH = h === 0 ? 12 : h > 12 ? h - 12 : h;
      options.push({ val, label: `${displayH}:${mm} ${period}` });
    }
  }
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`px-2 py-1 border border-gray-200 dark:border-gray-600 rounded-lg text-xs focus:ring-2 focus:ring-indigo-500 outline-none dark:bg-gray-700 dark:text-white cursor-pointer ${className}`}
    >
      {options.map(({ val, label }) => (
        <option key={val} value={val}>{label}</option>
      ))}
    </select>
  );
}

const EMPTY_TRAINER = {
  name: '',
  title: '',
  appointment_types: [],
  calendar_id: '',
  slot_capacity: '1',
  business_hours_override: null,
  specialty: '',
  trial_session_slots: {},
};

export default function TrainerManager() {
  const { confirm } = useModal();
  const [providers, setProviders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editingId, setEditingId] = useState(null); // provider id or '__new__'
  const [form, setForm] = useState({ ...EMPTY_TRAINER });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [tenantAppointmentTypes, setTenantAppointmentTypes] = useState([]);

  const fetchProviders = useCallback(async () => {
    try {
      const data = await apiFetch('/api/trainers');
      setProviders(data || []);
    } catch (err) {
      setError(err.message || 'Failed to load providers');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchAppointmentTypes = useCallback(async () => {
    try {
      const config = await apiFetch('/api/config');
      setTenantAppointmentTypes(config.appointment_types || []);
    } catch {
      // Non-critical — appointment type picker just won't be populated
    }
  }, []);

  useEffect(() => {
    fetchProviders();
    fetchAppointmentTypes();
  }, [fetchProviders, fetchAppointmentTypes]);

  function startCreate() {
    setEditingId('__new__');
    setForm({ ...EMPTY_TRAINER });
    setError(null);
  }

  function startEdit(provider) {
    setEditingId(provider.id);
    setForm({
      name: provider.name || '',
      title: provider.title || '',
      appointment_types: provider.appointment_types || [],
      calendar_id: provider.calendar_id || '',
      slot_capacity: String(provider.slot_capacity || 1),
      business_hours_override: provider.business_hours_override || null,
      specialty: provider.specialty || '',
      // Handle legacy array format gracefully — reset to empty dict
      trial_session_slots: Array.isArray(provider.trial_session_slots) ? {} : (provider.trial_session_slots || {}),
    });
    setError(null);
  }

  function cancelEdit() {
    setEditingId(null);
    setForm({ ...EMPTY_TRAINER });
  }

  async function handleSave() {
    if (!form.name.trim()) {
      setError('Staff name is required.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = {
        name: form.name.trim(),
        title: form.title.trim() || null,
        appointment_types: form.appointment_types.filter((t) => t.toLowerCase() !== 'consultation').length > 0 ? form.appointment_types.filter((t) => t.toLowerCase() !== 'consultation') : null,
        calendar_id: form.calendar_id.trim() || null,
        slot_capacity: parseInt(form.slot_capacity, 10) || 1,
        business_hours_override: form.business_hours_override,
        specialty: form.specialty.trim() || null,
        trial_session_slots: Object.values(form.trial_session_slots || {}).some((v) => v && v.length > 0) ? form.trial_session_slots : null,
      };

      if (editingId === '__new__') {
        await apiFetch('/api/trainers', { method: 'POST', body: payload });
      } else {
        await apiFetch(`/api/trainers/${editingId}`, { method: 'PUT', body: payload });
      }
      setEditingId(null);
      setForm({ ...EMPTY_TRAINER });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      await fetchProviders();
    } catch (err) {
      setError(err.message || 'Failed to save provider.');
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(providerId) {
    const ok = await confirm({
      title: 'Remove Trainer?',
      message: 'This member will no longer appear in scheduling. You can re-add them later.',
      confirmText: 'Deactivate',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await apiFetch(`/api/trainers/${providerId}`, { method: 'DELETE' });
      await fetchProviders();
    } catch (err) {
      setError(err.message || 'Failed to deactivate provider.');
    }
  }

  function toggleAppointmentType(key) {
    setForm((f) => {
      const types = [...(f.appointment_types || [])];
      const idx = types.indexOf(key);
      if (idx >= 0) {
        types.splice(idx, 1);
      } else {
        types.push(key);
      }
      return { ...f, appointment_types: types };
    });
  }

  function toggleHoursOverride() {
    setForm((f) => {
      if (f.business_hours_override) {
        return { ...f, business_hours_override: null };
      }
      // Initialize with standard hours
      return {
        ...f,
        business_hours_override: {
          monday: { open: '08:00', close: '18:00' },
          tuesday: { open: '08:00', close: '18:00' },
          wednesday: { open: '08:00', close: '18:00' },
          thursday: { open: '08:00', close: '18:00' },
          friday: { open: '08:00', close: '18:00' },
          saturday: null,
          sunday: null,
        },
      };
    });
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin"></div>
      </div>
    );
  }

  return (
    <div className="p-5 md:p-8 space-y-5 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white flex items-center gap-2">
            <Users className="w-7 h-7 text-indigo-500" />
            Trainers
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">
            Coaches and staff who lead sessions. Each trainer can have their own
            specialties, session types, and availability.
          </p>
        </div>
        {editingId === null && (
          <button
            onClick={startCreate}
            className="flex items-center gap-2 px-4 py-2.5 bg-indigo-500 text-white rounded-lg text-sm font-medium hover:bg-indigo-600 transition-colors shadow-sm"
          >
            <UserPlus className="w-4 h-4" />
            Add Trainer
          </button>
        )}
      </div>

      {saved && (
        <div className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded-xl p-4 flex items-center gap-3">
          <CheckCircle className="w-5 h-5 text-green-500 shrink-0" />
          <p className="text-sm text-green-700 dark:text-green-400">Saved successfully.</p>
        </div>
      )}

      {error && (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-xl p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 mt-0.5 shrink-0" />
          <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
        </div>
      )}

      {/* Create / Edit form */}
      {editingId !== null && (
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-indigo-200 dark:border-indigo-800 p-6 space-y-4 shadow-sm">
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
            {editingId === '__new__' ? 'New Trainer' : 'Edit Trainer'}
          </h3>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
              Name <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="Full name"
              className="w-full px-4 py-2.5 border border-gray-200 dark:border-gray-600 rounded-lg text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 dark:bg-gray-700 dark:text-white"
            />
          </div>

          {/* Appointment types */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5 flex items-center gap-1.5">
              <CalendarCheck className="w-4 h-4 text-gray-400" />
              Appointment Types
            </label>
            <p className="text-xs text-gray-400 mb-2">
              Select which appointment types this member handles. Leave empty for all types.
            </p>
            {tenantAppointmentTypes.length === 0 ? (
              <p className="text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/30 px-3 py-2 rounded-lg">
                No appointment types configured yet. Go to Agent Config → Appointment Types to add some.
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {tenantAppointmentTypes.filter((at) => (at.code || '').toLowerCase() !== 'consultation').map((at) => {
                  const selected = (form.appointment_types || []).includes(at.code);
                  return (
                    <button
                      key={at.code}
                      type="button"
                      onClick={() => toggleAppointmentType(at.code)}
                      className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors border ${
                        selected
                          ? 'bg-indigo-50 dark:bg-indigo-900/30 border-indigo-300 dark:border-indigo-800 text-indigo-700 dark:text-indigo-400'
                          : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:border-gray-300'
                      }`}
                    >
                      {at.name || at.code}
                      {selected && <span className="ml-1">✓</span>}
                    </button>
                  );
                })}
              </div>
            )}
          </div>


          {/* Slot Capacity */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5 flex items-center gap-1.5">
              <Users className="w-4 h-4 text-gray-400" />
              Slot Capacity
            </label>
            <input
              type="text"
              inputMode="numeric"
              value={form.slot_capacity}
              onChange={(e) => setForm((f) => ({ ...f, slot_capacity: e.target.value.replace(/[^0-9]/g, '') }))}
              className="w-24 px-4 py-2.5 border border-gray-200 dark:border-gray-600 rounded-lg text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 dark:bg-gray-700 dark:text-white"
            />
            <p className="text-xs text-gray-400 mt-1">
              How many clients this trainer can see at overlapping times. Set to 1 for single-booking (one at a time).
            </p>
          </div>

          {/* Specialty */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
              Specialty
            </label>
            <input
              type="text"
              placeholder="e.g. Strength & Conditioning, Yoga, HIIT"
              value={form.specialty}
              onChange={(e) => setForm((f) => ({ ...f, specialty: e.target.value }))}
              className="w-full px-4 py-2.5 border border-gray-200 dark:border-gray-600 rounded-lg text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 dark:bg-gray-700 dark:text-white"
            />
            <p className="text-xs text-gray-400 mt-1">
              The specialty this trainer focuses on. Used to filter trial session slots by specialty.
            </p>
          </div>

          {/* Unified Weekly Schedule — business hours + trial session windows per day */}
          <div>
            <div className="flex items-center gap-2 mb-2">
              <Clock className="w-4 h-4 text-gray-400" />
              <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Weekly Schedule</label>
              <button
                type="button"
                onClick={toggleHoursOverride}
                className={`ml-2 px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                  form.business_hours_override
                    ? 'bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-800'
                    : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 border border-gray-200 dark:border-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600'
                }`}
              >
                {form.business_hours_override ? 'Custom Hours On' : 'Use Business Default'}
              </button>
            </div>
            <p className="text-xs text-gray-400 mb-2">
              Set per-day hours and optional trial session windows (for studios that offer intro classes).
            </p>
            <div className="space-y-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg p-3 border border-gray-100 dark:border-gray-700">
              {DAYS.map((day) => {
                const hours = form.business_hours_override?.[day];
                const isOpen = form.business_hours_override != null && hours !== null && hours !== undefined;
                const daySlots = (form.trial_session_slots || {})[day] || [];
                return (
                  <div key={day} className="space-y-1.5">
                    {/* Business hours row */}
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="w-20 text-xs font-medium text-gray-600 dark:text-gray-400 capitalize shrink-0">
                        {day}
                      </span>
                      {form.business_hours_override != null ? (
                        <>
                          <button
                            type="button"
                            onClick={() => {
                              const bh = { ...(form.business_hours_override || {}) };
                              bh[day] = isOpen ? null : { open: '08:00', close: '18:00' };
                              setForm((f) => ({ ...f, business_hours_override: bh }));
                            }}
                            className={`px-2 py-0.5 rounded-full text-xs font-medium transition-colors shrink-0 ${
                              isOpen
                                ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 hover:bg-green-200 dark:hover:bg-green-900/50'
                                : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
                            }`}
                          >
                            {isOpen ? 'Open' : 'Off'}
                          </button>
                          {isOpen && (
                            <>
                              <ThemedTimePicker
                                value={hours.open}
                                onChange={(val) => {
                                  const bh = { ...form.business_hours_override };
                                  bh[day] = { ...bh[day], open: val };
                                  setForm((f) => ({ ...f, business_hours_override: bh }));
                                }}
                              />
                              <span className="text-gray-400 text-xs">to</span>
                              <ThemedTimePicker
                                value={hours.close}
                                onChange={(val) => {
                                  const bh = { ...form.business_hours_override };
                                  bh[day] = { ...bh[day], close: val };
                                  setForm((f) => ({ ...f, business_hours_override: bh }));
                                }}
                              />
                            </>
                          )}
                        </>
                      ) : (
                        <span className="text-xs text-gray-400 italic">default hours</span>
                      )}
                    </div>
                    {/* Demo windows row */}
                    <div className="ml-20 flex flex-wrap items-center gap-2">
                      {daySlots.map((window, idx) => (
                        <div key={idx} className="flex items-center gap-1">
                          <ThemedTimePicker
                            value={window.start}
                            onChange={(val) => {
                              const slots = [...daySlots];
                              slots[idx] = { ...slots[idx], start: val };
                              setForm((f) => ({ ...f, trial_session_slots: { ...(f.trial_session_slots || {}), [day]: slots } }));
                            }}
                          />
                          <span className="text-gray-400 text-xs">→</span>
                          <ThemedTimePicker
                            value={window.end}
                            onChange={(val) => {
                              const slots = [...daySlots];
                              slots[idx] = { ...slots[idx], end: val };
                              setForm((f) => ({ ...f, trial_session_slots: { ...(f.trial_session_slots || {}), [day]: slots } }));
                            }}
                          />
                          <button
                            type="button"
                            onClick={() => {
                              const updated = daySlots.filter((_, i) => i !== idx);
                              setForm((f) => ({
                                ...f,
                                trial_session_slots: { ...(f.trial_session_slots || {}), [day]: updated.length > 0 ? updated : null },
                              }));
                            }}
                            className="text-gray-400 hover:text-red-500 transition-colors"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ))}
                      <button
                        type="button"
                        onClick={() => {
                          const slots = [...daySlots, { start: '08:00', end: '10:00' }];
                          setForm((f) => ({ ...f, trial_session_slots: { ...(f.trial_session_slots || {}), [day]: slots } }));
                        }}
                        className="text-xs text-orange-600 dark:text-orange-400 hover:text-orange-700 dark:hover:text-orange-300 transition-colors"
                      >
                        + Trial Window
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3 pt-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-2 px-5 py-2.5 bg-indigo-500 text-white rounded-lg text-sm font-medium hover:bg-indigo-600 disabled:opacity-50 transition-colors"
            >
              <Save className="w-4 h-4" />
              {saving ? 'Saving...' : 'Save Trainer'}
            </button>
            <button
              onClick={cancelEdit}
              className="flex items-center gap-2 px-4 py-2.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
            >
              <X className="w-4 h-4" />
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Provider list */}
      {providers.length === 0 && editingId === null ? (
        <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-12 text-center">
          <Users className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">No trainers yet</h3>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
            Add your trainers so the AI agent can schedule sessions with specific staff members.
          </p>
          <button
            onClick={startCreate}
            className="inline-flex items-center gap-2 px-4 py-2.5 bg-indigo-500 text-white rounded-lg text-sm font-medium hover:bg-indigo-600 transition-colors"
          >
            <UserPlus className="w-4 h-4" />
            Add Your First Trainer
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {providers.map((p) => {
            return (
              <div
                key={p.id}
                className={`bg-white dark:bg-gray-800 rounded-xl border ${
                  p.is_active ? 'border-gray-200 dark:border-gray-700' : 'border-red-100 dark:border-red-800 bg-red-50/30 dark:bg-red-900/20'
                } overflow-hidden transition-all`}
              >
                {/* Summary row — click anywhere to edit */}
                <div
                  className="flex items-center gap-4 p-4 cursor-pointer hover:bg-gray-50/50 dark:hover:bg-gray-700/50 transition-colors"
                  onClick={() => startEdit(p)}
                >
                  <div
                    className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-semibold shrink-0 ${
                      p.is_active
                        ? 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400'
                        : 'bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
                    }`}
                  >
                    {(p.name || '?').charAt(0).toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-gray-900 dark:text-white truncate">{p.name}</span>
                      {p.title && (
                        <span className="text-xs text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded-full">
                          {p.title}
                        </span>
                      )}
                      {!p.is_active && (
                        <span className="text-xs text-red-600 dark:text-red-400 bg-red-100 dark:bg-red-900/30 px-2 py-0.5 rounded-full">
                          Inactive
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-0.5">
                      {(p.appointment_types || []).length > 0 ? (
                        <span className="text-xs text-gray-500 dark:text-gray-400">
                          {p.appointment_types.map((code) => {
                            const match = tenantAppointmentTypes.find((at) => at.code === code);
                            return match ? match.name : code;
                          }).join(', ')}
                        </span>
                      ) : (
                        <span className="text-xs text-gray-400 italic">All appointment types</span>
                      )}
                      {(p.slot_capacity || 1) > 1 && (
                        <span className="text-xs text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30 px-2 py-0.5 rounded-full">
                          {p.slot_capacity} slots
                        </span>
                      )}
                      {p.specialty && (
                        <span className="text-xs text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-900/30 px-2 py-0.5 rounded-full">
                          {p.specialty}
                        </span>
                      )}
                      {(() => {
                        const dts = p.trial_session_slots;
                        if (!dts || Array.isArray(dts)) return null;
                        const count = Object.values(dts).reduce((a, v) => a + (v ? v.length : 0), 0);
                        if (!count) return null;
                        return (
                          <span className="text-xs text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-900/30 px-2 py-0.5 rounded-full">
                            {count} trial window{count !== 1 ? 's' : ''}
                          </span>
                        );
                      })()}
                      {p.calendar_id && (
                        <span className="text-xs text-blue-500 flex items-center gap-1">
                          <Calendar className="w-3 h-3" />
                          Own calendar
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    {p.is_active && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(p.id);
                        }}
                        className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/30 rounded-lg transition-colors"
                        title="Deactivate"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                    <Pencil className="w-3.5 h-3.5 text-gray-300 dark:text-gray-600" />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
