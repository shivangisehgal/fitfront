import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Power,
  Phone as PhoneIcon,
  MessageSquare,
  Mic,
  Save,
  CheckCircle,
  AlertCircle,
  Bot,
  CalendarCheck,
  Mail,
  Eye,
  EyeOff,
  Link as LinkIcon,
  Plus,
  Trash2,
  Bell,
  Star,
  ToggleLeft,
  ToggleRight,
  Play,
  Square,
  Volume2,
  Loader2,
  FlaskConical,
} from 'lucide-react';
import { apiFetch, API_BASE } from '../lib/api';
import { getToken } from '../lib/api';
import { useModal } from '../contexts/ModalContext';
import { useAuth } from '../contexts/AuthContext';
const VOICE_OPTIONS = [
  { id: '21m00Tcm4TlvDq8ikWAM', name: 'Rachel', description: 'Young female, warm and professional tone' },
  { id: 'AZnzlk1XvdvUeBnXmlld', name: 'Domi', description: 'Young female, confident and direct delivery' },
  { id: 'EXAVITQu4vr4xnSDxMaL', name: 'Bella', description: 'Young female, soft and friendly manner' },
  { id: 'MF3mGyEYCl7XYWbV9V6O', name: 'Emily', description: 'Young female, calm and gentle cadence' },
  { id: 'TxGEqnHWrfWFTfGW9XjX', name: 'Josh', description: 'Young male, deep and reassuring voice' },
];

export default function AgentConfig() {
  const { isAdmin, user } = useAuth();
  const tz = user?.timezone || 'America/Chicago';
  const { confirm, prompt, toast } = useModal();
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState(null);
  const [showSecrets, setShowSecrets] = useState({});
  const [clearingTestData, setClearingTestData] = useState(false);
  const [testDataResult, setTestDataResult] = useState(null);

  // Voice preview state
  const [playingVoiceId, setPlayingVoiceId] = useState(null);
  const [loadingVoiceId, setLoadingVoiceId] = useState(null);
  const audioRef = useRef(null);

  // Cleanup audio on unmount
  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      window.speechSynthesis?.cancel();
    };
  }, []);

  const stopAudio = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current = null;
    }
    window.speechSynthesis?.cancel();
    setPlayingVoiceId(null);
    setLoadingVoiceId(null);
  }, []);

  const playVoicePreview = useCallback(async (voiceId) => {
    // If already playing this voice, stop it
    if (playingVoiceId === voiceId) {
      stopAudio();
      return;
    }

    // Stop any currently playing audio
    stopAudio();
    setLoadingVoiceId(voiceId);

    try {
      // Try the backend ElevenLabs endpoint first
      const token = getToken();
      const resp = await fetch(`${API_BASE}/api/voice-preview/${voiceId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (resp.ok) {
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audioRef.current = audio;

        audio.onplay = () => {
          setLoadingVoiceId(null);
          setPlayingVoiceId(voiceId);
        };
        audio.onended = () => {
          setPlayingVoiceId(null);
          URL.revokeObjectURL(url);
          audioRef.current = null;
        };
        audio.onerror = () => {
          setPlayingVoiceId(null);
          setLoadingVoiceId(null);
          URL.revokeObjectURL(url);
          audioRef.current = null;
        };

        await audio.play();
        return;
      }

      // Fallback: use browser SpeechSynthesis. We make each VOICE_OPTION sound
      // distinct by (a) deterministically picking a different OS voice from
      // the matching gender pool based on the voice index, and (b) varying
      // rate/pitch slightly per voice.
      if ('speechSynthesis' in window) {
        const utterance = new SpeechSynthesisUtterance(
          'Hi there! Thank you for calling. How can I help you today?'
        );

        const voices = window.speechSynthesis.getVoices();
        const voiceMeta = VOICE_OPTIONS.find((v) => v.id === voiceId);
        const voiceIdx = VOICE_OPTIONS.findIndex((v) => v.id === voiceId);

        // Vary pitch/rate so even when all OS voices are identical, the user
        // can still tell the previews apart. Range stays in a natural band.
        utterance.rate = 0.9 + (voiceIdx % 5) * 0.04;   // 0.90, 0.94, 0.98, 1.02, 1.06
        utterance.pitch = 0.85 + (voiceIdx % 5) * 0.10; // 0.85, 0.95, 1.05, 1.15, 1.25

        if (voiceMeta && voices.length > 0) {
          const desc = (voiceMeta.description || '').toLowerCase();
          const isMale = desc.includes('male') && !desc.includes('female');
          // Pool of en-* voices matching the expected gender (best effort —
          // browsers don't expose gender directly, so we match on name).
          const enVoices = voices.filter((v) => v.lang.startsWith('en'));
          const genderPool = enVoices.filter((v) => {
            const n = v.name.toLowerCase();
            return isMale
              ? n.includes('male') && !n.includes('female')
              : n.includes('female') || /alex|samantha|victoria|karen|tessa|moira|fiona|kate|allison|ava/.test(n);
          });
          const pool = genderPool.length > 0 ? genderPool : enVoices;
          if (pool.length > 0) {
            utterance.voice = pool[voiceIdx % pool.length];
          }
        }

        utterance.onstart = () => {
          setLoadingVoiceId(null);
          setPlayingVoiceId(voiceId);
        };
        utterance.onend = () => setPlayingVoiceId(null);
        utterance.onerror = () => {
          setPlayingVoiceId(null);
          setLoadingVoiceId(null);
        };

        window.speechSynthesis.speak(utterance);
      } else {
        setLoadingVoiceId(null);
      }
    } catch {
      setLoadingVoiceId(null);
      setPlayingVoiceId(null);
    }
  }, [playingVoiceId, stopAudio]);

  useEffect(() => {
    fetchConfig();
  }, []);

  async function fetchConfig() {
    try {
      const data = await apiFetch('/api/config');
      setConfig(data);
    } catch (err) {
      setError(err.message || 'Failed to load config');
    } finally {
      setLoading(false);
    }
  }

  async function saveConfig() {
    setSaving(true);
    setError(null);
    try {
      await apiFetch('/api/config', {
        method: 'PUT',
        body: config,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
      // Refresh from server so masked values come back
      await fetchConfig();
    } catch (err) {
      setError(err.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  function update(key, value) {
    setConfig((c) => ({ ...c, [key]: value }));
  }

  async function handleClearTestData() {
    const ok = await confirm({
      title: 'Clear All Test Data?',
      message: 'This will permanently delete all test members, sessions, waitlist entries, and SMS records created via Test Front Desk. This cannot be undone.',
      confirmText: 'Clear Test Data',
      variant: 'danger',
    });
    if (!ok) return;
    setClearingTestData(true);
    setTestDataResult(null);
    try {
      const result = await apiFetch('/api/callers/test-data', { method: 'DELETE' });
      setTestDataResult(result);
      setTimeout(() => setTestDataResult(null), 6000);
    } catch (err) {
      setError(err.message || 'Failed to clear test data');
    } finally {
      setClearingTestData(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="w-8 h-8 border-2 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin" />
      </div>
    );
  }

  if (!config) {
    return (
      <div className="p-8 text-center text-gray-500 dark:text-gray-400">
        Unable to load agent configuration.
      </div>
    );
  }

  return (
    <div className="p-5 md:p-8 space-y-5 max-w-4xl mx-auto animate-fade-in">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sticky top-0 bg-gray-50 dark:bg-[#0a0a0f] -mx-5 md:-mx-8 px-5 md:px-8 py-4 border-b border-gray-200/60 dark:border-white/5 z-10">
        <div>
          <h1 className="text-xl md:text-2xl font-bold text-gray-900 dark:text-white">AI Agent</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">Voice, SMS, session reminders, and integrations</p>
        </div>
        <button
          onClick={saveConfig}
          disabled={saving}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold text-white bg-indigo-500 hover:bg-indigo-600 disabled:opacity-50 transition-all btn-press"
        >
          {saved ? (
            <>
              <CheckCircle className="w-4 h-4" /> Saved!
            </>
          ) : (
            <>
              <Save className="w-4 h-4" /> {saving ? 'Saving…' : 'Save Settings'}
            </>
          )}
        </button>
      </div>

      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 mt-0.5 shrink-0" />
          <p className="text-sm text-red-700 dark:text-red-300">{error}</p>
        </div>
      )}

      {/* Agent status — toggleable with double confirm */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div
              className={`p-3 rounded-lg ${config.agent_active ? 'bg-indigo-50 dark:bg-indigo-900/30' : 'bg-red-50 dark:bg-red-900/30'}`}
            >
              <Power
                className={`w-6 h-6 ${config.agent_active ? 'text-indigo-500' : 'text-red-600'}`}
              />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Agent Status</h3>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                {config.agent_active
                  ? 'Your agent is ACTIVE and answering calls.'
                  : `Your agent is OFF — calls are redirected to ${config.business_phone || 'your business phone'}.`}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={async () => {
              if (config.agent_active) {
                // Turning OFF — double confirm
                const step1 = await confirm({
                  title: 'Turn off AI Agent?',
                  message: `All incoming calls will be redirected to ${config.business_phone || 'your business phone number'}. The AI agent will stop answering calls until you turn it back on.`,
                  confirmText: 'Continue',
                  variant: 'danger',
                });
                if (!step1) return;

                const typed = await prompt({
                  title: 'Confirm deactivation',
                  message: 'Type DISABLE to turn off your AI agent.',
                  placeholder: 'Type DISABLE',
                  confirmText: 'Turn Off Agent',
                  variant: 'danger',
                });
                if (typed !== 'DISABLE') {
                  if (typed !== null) toast.warning('Cancelled — you must type DISABLE exactly.');
                  return;
                }

                update('agent_active', false);
                // Auto-save immediately for agent status changes
                try {
                  await apiFetch('/api/config', {
                    method: 'PUT',
                    body: { ...config, agent_active: false },
                  });
                  await fetchConfig();
                  toast.success('Agent turned OFF — calls redirected to your business phone.');
                } catch (err) {
                  toast.error(err.message || 'Failed to update agent status');
                  update('agent_active', true); // revert
                }
              } else {
                // Turning ON — single confirm
                const ok = await confirm({
                  title: 'Activate AI Agent?',
                  message: 'Your AI agent will start answering incoming calls immediately.',
                  confirmText: 'Activate Agent',
                  variant: 'default',
                });
                if (!ok) return;

                update('agent_active', true);
                try {
                  await apiFetch('/api/config', {
                    method: 'PUT',
                    body: { ...config, agent_active: true },
                  });
                  await fetchConfig();
                  toast.success('Agent activated — now answering calls!');
                } catch (err) {
                  toast.error(err.message || 'Failed to update agent status');
                  update('agent_active', false); // revert
                }
              }
            }}
            className={`relative inline-flex h-8 w-14 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 ${
              config.agent_active
                ? 'bg-green-500 focus:ring-green-500'
                : 'bg-gray-300 dark:bg-gray-600 focus:ring-gray-400'
            }`}
            aria-label={config.agent_active ? 'Turn off agent' : 'Turn on agent'}
          >
            <span
              className={`inline-block h-6 w-6 rounded-full bg-white shadow-md transform transition-transform ${
                config.agent_active ? 'translate-x-7' : 'translate-x-1'
              }`}
            />
          </button>
        </div>
      </div>

      {/* Agent persona */}
      <Section icon={Bot} title="Agent Persona">
        <div className="grid grid-cols-1 gap-4">
          <Field label="Agent Name" help="What your AI agent introduces itself as on calls.">
            <input
              type="text"
              value={config.agent_name || ''}
              onChange={(e) => update('agent_name', e.target.value)}
              placeholder="e.g. Aria, Nova, Echo"
              className="input"
            />
          </Field>
          <Field label="Greeting Message" help="The first thing the AI agent says when answering a call.">
            <textarea
              value={config.greeting_message || ''}
              onChange={(e) => update('greeting_message', e.target.value)}
              rows={3}
              placeholder="Thanks for calling Iron & Ivy! How can I help you today?"
              className="input resize-none"
            />
          </Field>
        </div>
      </Section>

      {/* ── Connections ── high-level connected/not-connected status. The
          Platform-managed integrations — no API keys needed from the user. */}
      <GoogleCalendarSection config={config} onUpdate={fetchConfig} />

      {/* ── Usage & Plan (includes platform-managed connection statuses) ── */}
      <UsagePlanSection />

      {/* Session Reminders */}
      <Section icon={Bell} title="Session Reminders">
        <p className="text-sm text-gray-500 dark:text-gray-400 -mt-2 mb-3">
          Automated SMS reminders sent before sessions and classes. Members can reply <strong>C</strong> to confirm,
          <strong> R</strong> to reschedule, or <strong>X</strong> to cancel — all handled by the AI agent.
        </p>
        <div className="space-y-4">
          <Toggle
            label="Session Reminder (2 hours before)"
            help="Send an SMS reminder 2 hours before the session."
            checked={config.reminder_settings?.['2h_enabled'] !== false}
            onChange={(v) =>
              update('reminder_settings', {
                ...(config.reminder_settings || {}),
                '2h_enabled': v,
              })
            }
          />
          <Toggle
            label="Confirmation Tracking"
            help="Track member reply confirmations and show status on sessions."
            checked={config.reminder_settings?.confirmation_reply_enabled !== false}
            onChange={(v) =>
              update('reminder_settings', {
                ...(config.reminder_settings || {}),
                confirmation_reply_enabled: v,
              })
            }
          />
        </div>
      </Section>

      {/* Google Review Solicitation */}
      <Section icon={Star} title="Google Review Solicitation">
        <p className="text-sm text-gray-500 dark:text-gray-400 -mt-2 mb-3">
          Automatically send a friendly SMS asking members to leave a Google review after their session.
          Only sent after the follow-up message, with a configurable delay.
        </p>
        <div className="space-y-4">
          <Toggle
            label="Enable Review Requests"
            help="When enabled, members will receive a review request SMS after their session."
            checked={config.review_settings?.enabled === true}
            onChange={(v) =>
              update('review_settings', {
                ...(config.review_settings || {}),
                enabled: v,
              })
            }
          />
          {config.review_settings?.enabled && (
            <>
              <Field label="Google Review Link" help="Your Google Business profile review URL. Callers tap this link to leave a review.">
                <input
                  type="url"
                  value={config.review_settings?.google_review_link || ''}
                  onChange={(e) =>
                    update('review_settings', {
                      ...(config.review_settings || {}),
                      google_review_link: e.target.value,
                    })
                  }
                  placeholder="https://g.page/r/your-business/review"
                  className="input"
                />
              </Field>
              <Field label="Delay After Session (hours)" help="How many hours after the session to send the review request.">
                <input
                  type="text"
                  inputMode="numeric"
                  value={config.review_settings?.delay_hours ?? ''}
                  onChange={(e) => {
                    const raw = e.target.value.replace(/[^0-9]/g, '');
                    update('review_settings', {
                      ...(config.review_settings || {}),
                      delay_hours: raw === '' ? '' : parseInt(raw, 10),
                    });
                  }}
                  className="input"
                  style={{ maxWidth: '120px' }}
                />
              </Field>
              <Field
                label="Session Types for Reviews"
                help="Comma-separated list of session type keys. Leave empty to send for all types."
              >
                <input
                  type="text"
                  value={(config.review_settings?.appointment_types || []).join(', ')}
                  onChange={(e) =>
                    update('review_settings', {
                      ...(config.review_settings || {}),
                      appointment_types: e.target.value
                        .split(',')
                        .map((s) => s.trim())
                        .filter(Boolean),
                    })
                  }
                  placeholder="consultation, demo (leave empty for all)"
                  className="input"
                />
              </Field>
            </>
          )}
        </div>
      </Section>

      {/* Clear Test Data */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 px-4 py-3 md:px-6 md:py-4">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <FlaskConical className="w-4 h-4 text-amber-500" />
            Test Data
          </h3>
          <button
            onClick={handleClearTestData}
            disabled={clearingTestData}
            className="flex items-center gap-2 px-4 py-2.5 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 rounded-lg text-sm font-medium hover:bg-red-100 dark:hover:bg-red-900/40 disabled:opacity-50 transition-colors shrink-0"
          >
            <Trash2 className="w-4 h-4" />
            {clearingTestData ? 'Clearing...' : 'Clear Test Data'}
          </button>
        </div>
        {testDataResult && (
          <div className="mt-3 p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg">
            <p className="text-sm text-green-700 dark:text-green-400">
              <CheckCircle className="w-4 h-4 inline mr-1" />
              Cleared {testDataResult.total} test records: {testDataResult.deleted?.callers || 0} members, {testDataResult.deleted?.appointments || 0} sessions, {testDataResult.deleted?.waitlist_entries || 0} waitlist, {testDataResult.deleted?.sms_messages || 0} SMS
            </p>
          </div>
        )}
      </div>

      <style>{`
        .input {
          width: 100%;
          padding: 0.625rem 1rem;
          border: 1px solid #e5e7eb;
          border-radius: 0.5rem;
          font-size: 0.875rem;
          outline: none;
          background: white;
        }
        .input:focus {
          border-color: #14b8a6;
          box-shadow: 0 0 0 2px rgba(20,184,166,0.2);
        }
        .dark .input {
          background: #374151;
          border-color: #4b5563;
          color: #f3f4f6;
        }
        .dark .input:focus {
          border-color: #14b8a6;
          box-shadow: 0 0 0 2px rgba(20,184,166,0.3);
        }
      `}</style>
    </div>
  );
}

// ── Usage & Plan section ─────────────────────────────────────────────────────

function UsageBar({ label, used, limit, unit, color = 'primary' }) {
  const percent = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const remaining = Math.max(0, limit - used);
  const isWarning = percent >= 80;
  const isDanger = percent >= 95;
  const barColor = isDanger ? 'bg-red-500' : isWarning ? 'bg-amber-500' : `bg-${color}-500`;

  return (
    <div>
      <div className="flex justify-between items-baseline mb-1">
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">{label}</span>
        <span className="text-xs text-gray-500 dark:text-gray-400">
          {typeof used === 'number' && used % 1 !== 0 ? used.toFixed(1) : used} / {limit >= 99999 ? 'Unlimited' : limit} {unit}
        </span>
      </div>
      <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2.5">
        <div
          className={`h-2.5 rounded-full transition-all duration-500 ${isDanger ? 'bg-red-500' : isWarning ? 'bg-amber-500' : 'bg-indigo-500'}`}
          style={{ width: `${Math.min(100, percent)}%` }}
        />
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
        {limit >= 99999 ? 'Unlimited' : `${typeof remaining === 'number' && remaining % 1 !== 0 ? remaining.toFixed(1) : remaining} ${unit} remaining`}
        {isWarning && !isDanger && ' — approaching limit'}
        {isDanger && ' — limit reached, overage may apply'}
      </p>
    </div>
  );
}

function UsagePlanSection() {
  const { user } = useAuth();
  const tz = user?.timezone || 'America/Chicago';
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await apiFetch('/api/tenants/usage');
        if (!cancelled) setUsage(data);
      } catch {
        // Silently fail — endpoint may not exist on older backends
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  const planLabels = {
    starter: 'Starter',
    professional: 'Professional',
    enterprise: 'Enterprise',
  };

  return (
    <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
          <Bot className="w-5 h-5 text-indigo-500" />
          Usage & Plan
        </h3>
        {usage && (
          <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300 uppercase tracking-wide">
            {planLabels[usage.plan] || usage.plan}
          </span>
        )}
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading usage...
        </div>
      ) : usage ? (
        <div className="space-y-4">
          <UsageBar
            label="Call Minutes"
            used={usage.calls.used}
            limit={usage.calls.limit}
            unit="min"
          />
          <UsageBar
            label="SMS Messages"
            used={usage.sms.used}
            limit={usage.sms.limit}
            unit="SMS"
          />
          <p className="text-xs text-gray-400 dark:text-gray-500">
            Billing period started {new Date(usage.period_start).toLocaleDateString('en-US', { timeZone: tz, month: 'short', day: 'numeric', year: 'numeric' })}.
            Usage resets monthly. Contact support to upgrade your plan.
          </p>
        </div>
      ) : (
        <p className="text-sm text-gray-400">Usage data not available.</p>
      )}

      <div className="pt-3 border-t border-gray-100 dark:border-gray-700">
        <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Connections</h4>
        <div className="space-y-1.5">
          <div className="flex items-center gap-2 text-sm">
            <Mail className="w-4 h-4 text-gray-400" />
            <span className="text-gray-600 dark:text-gray-300">SMS (Twilio)</span>
            <CheckCircle className="w-4 h-4 text-green-500 ml-auto" />
            <span className="text-xs text-green-600 dark:text-green-400">Managed by platform</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Helper components ────────────────────────────────────────────────────────

function Section({ icon: Icon, title, children }) {
  return (
    <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-4 md:p-6 space-y-4">
      <h3 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
        <Icon className="w-5 h-5 text-indigo-500" />
        {title}
      </h3>
      {children}
    </div>
  );
}

function Field({ label, help, children, className = '' }) {
  return (
    <div className={className}>
      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">{label}</label>
      {children}
      {help && <p className="text-xs text-gray-400 mt-1">{help}</p>}
    </div>
  );
}

function ConnectionStatusRow({ icon: Icon, label, connected, connectedText, notConnectedText }) {
  return (
    <div
      className={`flex items-center gap-3 p-3 rounded-lg border ${
        connected
          ? 'bg-green-50/50 dark:bg-green-900/10 border-green-200 dark:border-green-800'
          : 'bg-gray-50 dark:bg-gray-700/50 border-gray-200 dark:border-gray-700'
      }`}
    >
      <div
        className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${
          connected
            ? 'bg-green-100 dark:bg-green-900/30 text-green-600'
            : 'bg-gray-200 dark:bg-gray-600 text-gray-500'
        }`}
      >
        <Icon className="w-4 h-4" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900 dark:text-white">{label}</p>
        <p className="text-xs text-gray-500 dark:text-gray-400">
          {connected ? connectedText : notConnectedText}
        </p>
      </div>
      {connected ? (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded-full text-xs font-medium shrink-0">
          <CheckCircle className="w-3 h-3" />
          Connected
        </span>
      ) : (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-200 dark:bg-gray-600 text-gray-600 dark:text-gray-300 rounded-full text-xs font-medium shrink-0">
          Not connected
        </span>
      )}
    </div>
  );
}

function IntegrationSection({
  icon: Icon,
  title,
  description,
  configured,
  accent,
  learnMore,
  children,
}) {
  const accentMap = {
    indigo: { bg: 'bg-indigo-50', border: 'border-indigo-200 dark:border-indigo-800', text: 'text-indigo-600', iconBg: 'bg-indigo-100 dark:bg-indigo-900/30' },
    emerald: { bg: 'bg-emerald-50', border: 'border-emerald-200 dark:border-emerald-800', text: 'text-emerald-600', iconBg: 'bg-emerald-100 dark:bg-emerald-900/30' },
    pink: { bg: 'bg-pink-50', border: 'border-pink-200 dark:border-pink-800', text: 'text-pink-600', iconBg: 'bg-pink-100 dark:bg-pink-900/30' },
  };
  const a = accentMap[accent] || accentMap.indigo;

  return (
    <div className={`bg-white dark:bg-gray-800 rounded-xl border ${a.border} p-6 space-y-4`}>
      <div className="flex items-start gap-3">
        <div className={`w-10 h-10 rounded-lg ${a.iconBg} flex items-center justify-center shrink-0`}>
          <Icon className={`w-5 h-5 ${a.text}`} />
        </div>
        <div className="flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-white">{title}</h3>
            {configured ? (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded-full text-xs font-medium">
                <CheckCircle className="w-3 h-3" />
                Connected
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 rounded-full text-xs font-medium">
                Not connected
              </span>
            )}
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">{description}</p>
          {learnMore && (
            <a
              href={learnMore}
              target="_blank"
              rel="noopener noreferrer"
              className={`inline-flex items-center gap-1 text-xs font-medium ${a.text} hover:underline mt-1`}
            >
              <LinkIcon className="w-3 h-3" />
              {learnMore.replace(/^https?:\/\//, '')}
            </a>
          )}
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">{children}</div>
    </div>
  );
}

function GoogleCalendarSection({ config, onUpdate }) {
  const { toast, confirm } = useModal();
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const connected = config?.google_calendar_connected;
  const email = config?.google_calendar_email;

  async function handleDisconnect() {
    const ok = await confirm({
      title: 'Disconnect Google Calendar?',
      message: 'Your agent will fall back to the built-in scheduler until reconnected.',
      confirmText: 'Disconnect',
      variant: 'danger',
    });
    if (!ok) return;
    setDisconnecting(true);
    try {
      await apiFetch('/api/integrations/google/disconnect', { method: 'POST' });
      onUpdate();
    } catch (err) {
      toast.error('Failed to disconnect: ' + (err.message || err));
    } finally {
      setDisconnecting(false);
    }
  }

  async function handleConnect() {
    setConnecting(true);
    try {
      // Pass current origin so callback redirects back here (important for tunnel URLs)
      const currentOrigin = window.location.origin;
      const data = await apiFetch(`/api/integrations/google/connect?redirect_uri=${encodeURIComponent(currentOrigin)}`);
      // Redirect browser to Google consent screen
      window.location.href = data.auth_url;
    } catch (err) {
      toast.error('Failed to start Google connection: ' + (err.message || err));
      setConnecting(false);
    }
  }

  return (
    <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-6 space-y-4">
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 rounded-lg bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center shrink-0">
          <CalendarCheck className="w-5 h-5 text-blue-600" />
        </div>
        <div className="flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Google Calendar</h3>
            {connected ? (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded-full text-xs font-medium">
                <CheckCircle className="w-3 h-3" />
                Connected
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 rounded-full text-xs font-medium">
                Not connected
              </span>
            )}
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            Connect your Google Calendar and the AI agent will
            check your real availability and book directly into your calendar.
          </p>
        </div>
      </div>

      {connected ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Mail className="w-4 h-4 text-gray-400" />
            <span className="text-sm font-medium text-gray-900 dark:text-white">{email}</span>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Your AI agent is using this Google Calendar for availability checks and bookings.
          </p>
          <button
            onClick={handleDisconnect}
            disabled={disconnecting}
            className="flex items-center gap-2 px-4 py-2.5 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 rounded-lg text-sm font-medium hover:bg-red-100 dark:hover:bg-red-900/40 disabled:opacity-50 transition-colors shrink-0"
          >
            <Trash2 className="w-4 h-4" />
            {disconnecting ? 'Disconnecting...' : 'Disconnect Google Calendar'}
          </button>
        </div>
      ) : (
        <div className="bg-gray-50 dark:bg-gray-700/50 border border-gray-100 dark:border-gray-700 rounded-lg p-4 space-y-3">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Click the button below to sign in with Google and grant calendar access.
            This is a one-time setup — no API keys needed.
          </p>
          <button
            onClick={handleConnect}
            disabled={connecting}
            className="inline-flex items-center gap-2 px-4 py-2.5 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-600 disabled:opacity-50 transition-colors shadow-sm"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/>
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
            </svg>
            {connecting ? 'Redirecting...' : 'Connect Google Calendar'}
          </button>
          <p className="text-xs text-gray-400 dark:text-gray-500">
            Requires the platform admin to have configured Google OAuth credentials.
          </p>
        </div>
      )}
    </div>
  );
}

function Toggle({ label, help, checked, onChange }) {
  return (
    <div className="flex items-start gap-3">
      <button
        type="button"
        onClick={() => onChange(!checked)}
        className="mt-0.5 shrink-0"
        aria-label={label}
      >
        {checked ? (
          <ToggleRight className="w-8 h-8 text-indigo-500" />
        ) : (
          <ToggleLeft className="w-8 h-8 text-gray-300 dark:text-gray-500" />
        )}
      </button>
      <div>
        <p className="text-sm font-medium text-gray-700 dark:text-gray-300">{label}</p>
        {help && <p className="text-xs text-gray-400 mt-0.5">{help}</p>}
      </div>
    </div>
  );
}

function SecretField({
  label,
  fieldKey,
  showSecrets,
  setShowSecrets,
  masked,
  value,
  onChange,
  placeholder,
}) {
  const visible = showSecrets[fieldKey];
  // If user has typed a new value, show what they typed; otherwise show masked from server
  const displayValue = value !== undefined && value !== null ? value : masked || '';

  return (
    <Field label={label}>
      <div className="relative">
        <input
          type={visible ? 'text' : 'password'}
          value={displayValue}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="input pr-10"
          autoComplete="off"
        />
        <button
          type="button"
          onClick={() =>
            setShowSecrets((prev) => ({ ...prev, [fieldKey]: !prev[fieldKey] }))
          }
          className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          aria-label={visible ? 'Hide' : 'Show'}
        >
          {visible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>
      <p className="text-xs text-gray-400 mt-1">
        {value
          ? 'New value will be saved.'
          : masked
          ? `Currently set (${masked}). Type to replace.`
          : 'Not set.'}
      </p>
    </Field>
  );
}
