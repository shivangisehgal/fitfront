import React, { useState, useEffect } from 'react';
import {
  BookOpen,
  Save,
  Plus,
  Trash2,
  CheckCircle,
  Building2,
  DollarSign,
  HelpCircle,
  Clock,
  StickyNote,
  CalendarX,
  CalendarCheck,
  AlertCircle,
} from 'lucide-react';
import { apiFetch } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';
import ThemedDatePicker from './ui/ThemedDatePicker';
import ThemedTimePicker from './ui/ThemedTimePicker';
import PhoneInput, { countryFromTimezone } from './ui/PhoneInput';

const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];

function slugifyTypeName(name) {
  return (name || '')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

function isoToLocalDate(iso) {
  if (!iso || typeof iso !== 'string') return null;
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

function localDateToIso(d) {
  if (!d) return '';
  const y = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${mo}-${day}`;
}

export default function KnowledgeBase() {
  const { user } = useAuth();
  const isFitnessStudio = user?.business_type === 'fitness_studio';

  const [kb, setKb] = useState(null);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    fetchAll();
  }, []);

  async function fetchAll() {
    try {
      const [kbData, cfgData] = await Promise.all([
        apiFetch('/api/knowledge'),
        apiFetch('/api/config'),
      ]);
      setKb(kbData || {});
      setConfig(cfgData || {});
    } catch (err) {
      console.error('Failed to load:', err);
    } finally {
      setLoading(false);
    }
  }

  async function saveAll() {
    setSaving(true);
    try {
      await Promise.all([
        apiFetch('/api/knowledge', { method: 'PUT', body: kb }),
        apiFetch('/api/config', { method: 'PUT', body: config }),
      ]);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      console.error('Save failed:', err);
    } finally {
      setSaving(false);
    }
  }

  // KB updaters
  function updateService(index, field, value) {
    const updated = [...(kb.services || [])];
    updated[index] = { ...updated[index], [field]: field.includes('price') || field === 'duration_minutes' ? Number(value) : value };
    setKb({ ...kb, services: updated });
  }

  function addService() {
    setKb({
      ...kb,
      services: [
        ...(kb.services || []),
        { name: 'New Service', price_min: 0, price_max: 0, duration_minutes: 30, notes: '' },
      ],
    });
  }

  function removeService(index) {
    setKb({ ...kb, services: (kb.services || []).filter((_, i) => i !== index) });
  }

  function updateFaq(index, field, value) {
    const updated = [...(kb.faqs || [])];
    updated[index] = { ...updated[index], [field]: value };
    setKb({ ...kb, faqs: updated });
  }

  function addFaq() {
    setKb({
      ...kb,
      faqs: [...(kb.faqs || []), { question: 'New question?', answer: 'Answer here.' }],
    });
  }

  function removeFaq(index) {
    setKb({ ...kb, faqs: (kb.faqs || []).filter((_, i) => i !== index) });
  }

  // Config updaters
  function updateConfig(key, value) {
    setConfig((c) => ({ ...c, [key]: value }));
  }

  if (loading || !kb || !config) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="w-8 h-8 border-2 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin"></div>
      </div>
    );
  }

  return (
    <div className="p-5 md:p-8 max-w-5xl mx-auto animate-fade-in">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-xl md:text-2xl font-bold text-gray-900 dark:text-white">Studio Info</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            Programs, pricing, hours, and FAQs your AI uses to book sessions
          </p>
        </div>
        <button
          onClick={saveAll}
          disabled={saving}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold text-white bg-indigo-500 hover:bg-indigo-600 disabled:opacity-50 transition-all btn-press"
        >
          {saved ? (
            <>
              <CheckCircle className="w-4 h-4" /> Saved!
            </>
          ) : (
            <>
              <Save className="w-4 h-4" /> {saving ? 'Saving…' : 'Save Changes'}
            </>
          )}
        </button>
      </div>

      {/* ── Business Information ─────────────────────────────────── */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden mb-6">
        <div className="px-5 py-4 bg-gray-50 dark:bg-gray-700/50 border-b border-gray-200 dark:border-gray-700">
          <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <Building2 className="w-5 h-5 text-gray-500" />
            Studio Details
          </h3>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            Your studio name, phone, address and timezone
          </p>
        </div>
        <div className="p-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">Studio Name</label>
              <input
                type="text"
                value={config.business_name || ''}
                onChange={(e) => updateConfig('business_name', e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 dark:text-white focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">Business Phone</label>
              <PhoneInput
                value={config.business_phone || ''}
                onChange={(v) => updateConfig('business_phone', v)}
                defaultCountry={countryFromTimezone(config.timezone)}
                placeholder="(512) 555-0100"
              />
            </div>
            <div className="md:col-span-2">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">Business Address</label>
              <input
                type="text"
                value={config.business_address || ''}
                onChange={(e) => updateConfig('business_address', e.target.value)}
                placeholder="123 Main St, Austin, TX"
                className="w-full px-3 py-2 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 dark:text-white focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                Timezone
                <span className="ml-1 text-xs font-normal text-gray-400">(set at registration)</span>
              </label>
              <input
                type="text"
                value={config.timezone || ''}
                readOnly
                disabled
                tabIndex={-1}
                className="w-full px-3 py-2 border border-gray-200 dark:border-gray-600 rounded-lg text-sm bg-gray-100 dark:bg-gray-700/50 text-gray-500 dark:text-gray-400 cursor-not-allowed"
              />
            </div>
          </div>
        </div>
      </div>

      {/* ── Business Hours ────────────────────────────────────────── */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden mb-6">
        <div className="px-5 py-4 bg-gray-50 dark:bg-gray-800/40 border-b border-gray-100 dark:border-white/5">
          <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <Clock className="w-5 h-5 text-indigo-500" />
            Business Hours
          </h3>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            Set your studio's open hours — the agent uses this to check class and session availability
          </p>
        </div>
        <div className="p-5 space-y-3">
          {DAYS.map((day) => {
            const hours = config.business_hours?.[day];
            const isOpen = hours !== null && hours !== undefined;
            return (
              <div key={day} className="flex flex-wrap items-center gap-2 md:gap-4">
                <div className="w-20 md:w-24">
                  <span className="text-sm font-medium text-gray-700 dark:text-gray-300 capitalize">{day}</span>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    const bh = { ...(config.business_hours || {}) };
                    bh[day] = isOpen ? null : { open: '08:00', close: '18:00' };
                    updateConfig('business_hours', bh);
                  }}
                  className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                    isOpen
                      ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 hover:bg-green-200 dark:hover:bg-green-900/50'
                      : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
                  }`}
                >
                  {isOpen ? 'Open' : 'Closed'}
                </button>
                {isOpen && (
                  <>
                    <ThemedTimePicker
                      value={hours.open}
                      onChange={(val) => {
                        const bh = { ...config.business_hours };
                        bh[day] = { ...bh[day], open: val };
                        updateConfig('business_hours', bh);
                      }}
                      minuteStep={30}
                    />
                    <span className="text-gray-400 text-sm">to</span>
                    <ThemedTimePicker
                      value={hours.close}
                      onChange={(val) => {
                        const bh = { ...config.business_hours };
                        bh[day] = { ...bh[day], close: val };
                        updateConfig('business_hours', bh);
                      }}
                      minuteStep={30}
                    />
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Holidays & Closures ───────────────────────────────────── */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden mb-6">
        <div className="px-5 py-4 bg-red-50 dark:bg-red-900/20 border-b border-red-100 dark:border-red-900/40">
          <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <CalendarX className="w-5 h-5 text-red-500" />
            Holidays & Office Closures
          </h3>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            One-off days you're closed — the agent will refuse bookings and inform callers
          </p>
        </div>
        <div className="p-5">
          <HolidaysEditor
            holidays={config.holidays || []}
            onChange={(list) => updateConfig('holidays', list)}
          />
        </div>
      </div>

      {/* ── Session / Appointment Types ───────────────────────────── */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden mb-6">
        <div className="px-5 py-4 bg-indigo-50 dark:bg-indigo-900/20 border-b border-indigo-100 dark:border-indigo-900/40 flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
              <CalendarCheck className="w-5 h-5 text-indigo-500" />
              {isFitnessStudio ? 'Session Types' : 'Appointment Types'}
            </h3>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              {isFitnessStudio
                ? 'Trial sessions, PT, and classes the agent can book. Max Concurrent = spots per time slot.'
                : 'Types the agent can book. Max Concurrent = overlapping bookings per slot.'}
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              const types = [...(config.appointment_types || [])];
              types.push({ code: '', name: '', duration_minutes: 60, slot_capacity: 1 });
              updateConfig('appointment_types', types);
            }}
            className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-indigo-700 dark:text-indigo-400 bg-indigo-100 dark:bg-indigo-900/40 rounded-lg hover:bg-indigo-200 dark:hover:bg-indigo-900/60 transition-colors"
          >
            <Plus className="w-3.5 h-3.5" /> Add
          </button>
        </div>
        <div className="p-5 space-y-3">
          {(config.appointment_types || []).length === 0 ? (
            <p className="text-sm text-gray-400 italic text-center py-4">No types added yet.</p>
          ) : (
            (config.appointment_types || []).map((at, idx) => (
              <div key={idx} className="flex items-start gap-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg p-3 border border-gray-100 dark:border-gray-700">
                <div className="flex-1 grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Name</label>
                    <input
                      type="text"
                      value={at.name || ''}
                      onChange={(e) => {
                        const types = [...(config.appointment_types || [])];
                        const newName = e.target.value;
                        const prev = types[idx] || {};
                        const prevSlug = slugifyTypeName(prev.name || '');
                        const shouldAutoCode = !prev.code || prev.code === prevSlug;
                        const nextCode = shouldAutoCode ? slugifyTypeName(newName) : prev.code;
                        types[idx] = { ...prev, name: newName, code: nextCode };
                        updateConfig('appointment_types', types);
                      }}
                      placeholder="Trial Session"
                      className="w-full px-3 py-2 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 dark:text-white focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Duration (min)</label>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={at.duration_minutes ?? ''}
                      onChange={(e) => {
                        const raw = e.target.value.replace(/[^0-9]/g, '');
                        const types = [...(config.appointment_types || [])];
                        types[idx] = { ...types[idx], duration_minutes: raw === '' ? '' : parseInt(raw, 10) };
                        updateConfig('appointment_types', types);
                      }}
                      className="w-full px-3 py-2 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 dark:text-white focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Slot Capacity</label>
                    <input
                      type="text"
                      inputMode="numeric"
                      value={at.slot_capacity ?? ''}
                      onChange={(e) => {
                        const raw = e.target.value.replace(/[^0-9]/g, '');
                        const types = [...(config.appointment_types || [])];
                        types[idx] = { ...types[idx], slot_capacity: raw === '' ? '' : parseInt(raw, 10) };
                        updateConfig('appointment_types', types);
                      }}
                      className="w-full px-3 py-2 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 dark:text-white focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all"
                    />
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    const types = (config.appointment_types || []).filter((_, i) => i !== idx);
                    updateConfig('appointment_types', types);
                  }}
                  className="mt-6 p-1.5 text-gray-400 dark:text-gray-500 hover:text-red-500 transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* ── Programs & Classes (fitness studio) ───────────────────── */}
      {isFitnessStudio && (
        <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden mb-6">
          <div className="px-5 py-4 bg-blue-50 dark:bg-blue-900/20 border-b border-blue-100 dark:border-blue-900/40 flex items-center justify-between">
            <div>
              <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                <BookOpen className="w-5 h-5 text-blue-500" />
                Programs & Classes
              </h3>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                Programs and classes you offer — the agent will use this when answering enquiries
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                const courses = [...(config.courses || [])];
                courses.push({ name: '', code: '', description: '' });
                updateConfig('courses', courses);
              }}
              className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-blue-700 dark:text-blue-400 bg-blue-100 dark:bg-blue-900/40 rounded-lg hover:bg-blue-200 dark:hover:bg-blue-900/60 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" /> Add Program
            </button>
          </div>
          <div className="p-5 space-y-3">
            {(config.courses || []).length === 0 ? (
              <p className="text-sm text-gray-400 italic text-center py-4">No programs added yet.</p>
            ) : (
              (config.courses || []).map((course, idx) => (
                <div key={idx} className="flex items-start gap-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg p-3 border border-gray-100 dark:border-gray-700">
                  <div className="flex-1 grid grid-cols-1 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Program Name</label>
                      <input
                        type="text"
                        value={course.name || ''}
                        onChange={(e) => {
                          const newName = e.target.value;
                          const base = slugifyTypeName(newName);
                          const others = (config.courses || []).filter((_, i) => i !== idx).map((c) => c.code || '');
                          let code = base;
                          if (code) {
                            let n = 2;
                            while (others.includes(code)) { code = `${base}_${n}`; n++; }
                          }
                          const courses = [...(config.courses || [])];
                          courses[idx] = { ...courses[idx], name: newName, code };
                          updateConfig('courses', courses);
                        }}
                        placeholder="Strength & Conditioning"
                        className="w-full px-3 py-2 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 dark:text-white focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all"
                      />
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Description</label>
                        <input
                          type="text"
                          value={course.description || ''}
                          onChange={(e) => {
                            const courses = [...(config.courses || [])];
                            courses[idx] = { ...courses[idx], description: e.target.value };
                            updateConfig('courses', courses);
                          }}
                          placeholder="Small-group strength training for all fitness levels"
                          className="w-full px-3 py-2 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 dark:text-white focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Fee (optional)</label>
                        <input
                          type="text"
                          value={course.fee || ''}
                          onChange={(e) => {
                            const courses = [...(config.courses || [])];
                            courses[idx] = { ...courses[idx], fee: e.target.value };
                            updateConfig('courses', courses);
                          }}
                          placeholder="$149/month or $25 drop-in"
                          className="w-full px-3 py-2 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 dark:text-white focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all"
                        />
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      const courses = (config.courses || []).filter((_, i) => i !== idx);
                      updateConfig('courses', courses);
                    }}
                    className="mt-6 p-1.5 text-gray-400 dark:text-gray-500 hover:text-red-500 transition-colors"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))
            )}
          </div>

          {/* Trial session limit */}
          <div className="px-5 py-4 border-t border-blue-100 dark:border-blue-900/40 bg-blue-50/50 dark:bg-blue-900/10">
            <div className="flex flex-col sm:flex-row sm:items-center gap-3">
              <div className="flex-1">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-0.5">
                  Max trial sessions per member
                </label>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  The AI will block booking and escalate to staff once a member reaches this limit. Leave blank for no limit.
                </p>
              </div>
              <input
                type="number"
                min="1"
                max="99"
                value={config.max_demo_classes_per_student ?? ''}
                onChange={(e) =>
                  updateConfig(
                    'max_demo_classes_per_student',
                    e.target.value === '' ? null : Math.max(1, parseInt(e.target.value, 10) || 1),
                  )
                }
                placeholder="e.g. 2"
                className="w-24 px-3 py-2 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all text-center [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
              />
            </div>
          </div>
        </div>
      )}

      {/* ── Services & Pricing (non-fitness-studio) ───────────────── */}
      {!isFitnessStudio && <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden mb-6">
        <div className="px-5 py-4 bg-emerald-50 dark:bg-emerald-900/20 border-b border-emerald-100 dark:border-emerald-900/40 flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
              <DollarSign className="w-5 h-5 text-emerald-500" />
              Services & Pricing
            </h3>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              Procedures you offer and their price ranges — e.g. Personal Training, Group Class
            </p>
          </div>
          <button
            onClick={addService}
            className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-emerald-700 dark:text-emerald-400 bg-emerald-100 dark:bg-emerald-900/40 rounded-lg hover:bg-emerald-200 dark:hover:bg-emerald-900/60 transition-colors"
          >
            <Plus className="w-3.5 h-3.5" /> Add Service
          </button>
        </div>
        <div className="p-5">
          {(kb.services || []).length === 0 ? (
            <div className="text-center py-6">
              <DollarSign className="w-8 h-8 text-gray-300 dark:text-gray-600 mx-auto mb-2" />
              <p className="text-sm text-gray-400 dark:text-gray-500">No services added yet. Click "Add Service" to get started.</p>
            </div>
          ) : (
            <div className="space-y-3">
              {(kb.services || []).map((svc, i) => (
                <div
                  key={i}
                  className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg border border-gray-100 dark:border-gray-600/50"
                >
                  <input
                    type="text"
                    value={svc.name}
                    onChange={(e) => updateService(i, 'name', e.target.value)}
                    className="flex-1 px-2 py-1.5 border border-gray-200 dark:border-gray-600 rounded text-sm bg-white dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-indigo-500 outline-none"
                    placeholder="Service name"
                  />
                  <div className="flex items-center gap-1.5">
                    <div className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400">
                      $
                      <input
                        type="text"
                        inputMode="numeric"
                        value={svc.price_min ?? ''}
                        onChange={(e) => updateService(i, 'price_min', e.target.value.replace(/[^0-9.]/g, ''))}
                        className="w-20 px-2 py-1.5 border border-gray-200 dark:border-gray-600 rounded text-sm bg-white dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-indigo-500 outline-none"
                      />
                      –$
                      <input
                        type="text"
                        inputMode="numeric"
                        value={svc.price_max ?? ''}
                        onChange={(e) => updateService(i, 'price_max', e.target.value.replace(/[^0-9.]/g, ''))}
                        className="w-20 px-2 py-1.5 border border-gray-200 dark:border-gray-600 rounded text-sm bg-white dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-indigo-500 outline-none"
                      />
                    </div>
                    {svc.duration_minutes !== undefined && (
                      <span className="flex items-center gap-0.5 text-xs text-gray-400 dark:text-gray-500 whitespace-nowrap" title="Duration">
                        <Clock className="w-3 h-3" /> {svc.duration_minutes}m
                      </span>
                    )}
                    <button
                      onClick={() => removeService(i)}
                      className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/30 rounded ml-1"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>}

      {/* ── Frequently Asked Questions ────────────────────────────── */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden">
        <div className="px-5 py-4 bg-amber-50 dark:bg-amber-900/20 border-b border-amber-100 dark:border-amber-900/40 flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
              <HelpCircle className="w-5 h-5 text-amber-500" />
              Frequently Asked Questions
            </h3>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              Common questions the AI can answer for callers
            </p>
          </div>
          <button
            onClick={addFaq}
            className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-amber-700 dark:text-amber-400 bg-amber-100 dark:bg-amber-900/40 rounded-lg hover:bg-amber-200 dark:hover:bg-amber-900/60 transition-colors"
          >
            <Plus className="w-3.5 h-3.5" /> Add FAQ
          </button>
        </div>
        <div className="p-5">
          {(kb.faqs || []).length === 0 ? (
            <div className="text-center py-6">
              <HelpCircle className="w-8 h-8 text-gray-300 dark:text-gray-600 mx-auto mb-2" />
              <p className="text-sm text-gray-400 dark:text-gray-500">No FAQs added yet. Click "Add FAQ" to teach your AI common answers.</p>
            </div>
          ) : (
            <div className="space-y-4">
              {(kb.faqs || []).map((faq, i) => (
                <div key={i} className="p-4 bg-gray-50 dark:bg-gray-700/50 rounded-lg border border-gray-100 dark:border-gray-600/50 space-y-2">
                  <div className="flex items-start gap-2">
                    <span className="mt-2 text-amber-400 dark:text-amber-500 text-sm font-bold">Q</span>
                    <input
                      type="text"
                      value={faq.question}
                      onChange={(e) => updateFaq(i, 'question', e.target.value)}
                      className="flex-1 px-3 py-2 border border-gray-200 dark:border-gray-600 rounded-lg text-sm font-medium bg-white dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-indigo-500 outline-none"
                      placeholder="Question"
                    />
                    <button
                      onClick={() => removeFaq(i)}
                      className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/30 rounded"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="mt-2 text-blue-400 dark:text-blue-500 text-sm font-bold">A</span>
                    <textarea
                      value={faq.answer}
                      onChange={(e) => updateFaq(i, 'answer', e.target.value)}
                      rows={2}
                      className="flex-1 px-3 py-2 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 dark:text-white focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all resize-none"
                      placeholder="Answer"
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

    </div>
  );
}

// ── HolidaysEditor (moved from AgentConfig) ──────────────────────────────────

function HolidaysEditor({ holidays, onChange }) {
  const [newDate, setNewDate] = useState('');
  const [newName, setNewName] = useState('');
  const [err, setErr] = useState(null);

  const sorted = React.useMemo(() => {
    return [...(holidays || [])]
      .filter((h) => h && h.date)
      .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  }, [holidays]);

  function add() {
    setErr(null);
    const date = (newDate || '').trim();
    const name = (newName || '').trim();
    if (!date) { setErr('Pick a date.'); return; }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) { setErr('Date must be YYYY-MM-DD.'); return; }
    const next = [
      ...sorted.filter((h) => h.date !== date),
      { date, name: name || '' },
    ].sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    onChange(next);
    setNewDate('');
    setNewName('');
  }

  function remove(date) {
    onChange(sorted.filter((h) => h.date !== date));
  }

  function updateName(date, name) {
    onChange(sorted.map((h) => (h.date === date ? { ...h, name } : h)));
  }

  function fmtDate(iso) {
    try {
      const [y, m, d] = iso.split('-').map((v) => parseInt(v, 10));
      const dt = new Date(y, m - 1, d);
      return dt.toLocaleDateString(undefined, { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
    } catch { return iso; }
  }

  const todayIso = (() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  })();

  return (
    <div className="space-y-3">
      {sorted.length === 0 ? (
        <p className="text-sm text-gray-400 italic">No holidays configured yet.</p>
      ) : (
        <div className="space-y-2">
          {sorted.map((h) => {
            const isPast = h.date < todayIso;
            return (
              <div
                key={h.date}
                className={`flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg p-3 border border-gray-100 dark:border-gray-700 ${isPast ? 'opacity-60' : ''}`}
              >
                <div className="sm:w-44 shrink-0">
                  <div className="text-sm font-medium text-gray-900 dark:text-white">{fmtDate(h.date)}</div>
                  <div className="text-xs text-gray-400 font-mono">{h.date}</div>
                </div>
                <input
                  type="text"
                  value={h.name || ''}
                  onChange={(e) => updateName(h.date, e.target.value)}
                  className="flex-1 px-3 py-1.5 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 dark:text-white focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all"
                  placeholder="Holiday name"
                />
                <button
                  type="button"
                  onClick={() => remove(h.date)}
                  className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            );
          })}
        </div>
      )}
      <div className="flex flex-col sm:flex-row sm:items-end gap-3 pt-2 border-t border-gray-100 dark:border-gray-700">
        <div className="sm:min-w-[12rem]">
          <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Date</label>
          <ThemedDatePicker
            value={isoToLocalDate(newDate)}
            onChange={(d) => setNewDate(localDateToIso(d))}
            onClear={() => setNewDate('')}
            placeholder="Pick a date"
            min={isoToLocalDate(todayIso)}
            accent="primary"
          />
        </div>
        <div className="flex-1">
          <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Holiday name</label>
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
            placeholder='"Christmas Day", "Thanksgiving"'
            className="w-full px-3 py-1.5 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 dark:text-white focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all"
          />
        </div>
        <button
          type="button"
          onClick={add}
          className="px-4 py-1.5 bg-indigo-500 hover:bg-indigo-600 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5"
        >
          <Plus className="w-4 h-4" />
          Add
        </button>
      </div>
      {err && (
        <p className="text-xs text-red-600 dark:text-red-400 flex items-center gap-1">
          <AlertCircle className="w-3 h-3" />
          {err}
        </p>
      )}
      <p className="text-xs text-gray-400">
        Don't forget to click <span className="font-medium">Save Changes</span> at the top to apply.
      </p>
    </div>
  );
}
