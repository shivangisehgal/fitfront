import React, { useState, useEffect } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import {
  ArrowLeft,
  RefreshCw,
  CheckCircle,
  PauseCircle,
  PlayCircle,
  Trash2,
  XCircle,
  MapPin,
  ExternalLink,
  Save,
  Loader2,
  MessageSquare,
  Settings2,
  History,
  BarChart3,
  Info,
  Zap,
  Wifi,
  WifiOff,
  AlertCircle,
  Clock,
  Calendar,
} from 'lucide-react';
import { apiFetch } from '../lib/api';
import { useModal } from '../contexts/ModalContext';
import { useAuth } from '../contexts/AuthContext';
import {
  STATUS_CONFIG,
  STATUS_ACCENT,
  BUSINESS_TYPE_LABELS,
  PLAN_LABELS,
} from '../lib/tenantLabels';

const TENANT_TABS = [
  { key: 'overview', label: 'Overview', icon: Info },
  { key: 'integrations', label: 'Integrations & Flags', icon: Zap },
  { key: 'usage', label: 'Usage & History', icon: BarChart3 },
];

// ═══════════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═══════════════════════════════════════════════════════════════════════════

export default function TenantDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { toast, confirm, prompt } = useModal();
  const { user } = useAuth();
  const tz = user?.timezone || 'America/Chicago';
  const [tenant, setTenant] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('overview');
  const [actionLoading, setActionLoading] = useState(false);

  useEffect(() => {
    loadTenant(true);
  }, [id]);

  async function loadTenant(initial = false) {
    if (initial) setLoading(true);
    else setRefreshing(true);
    setError(null);
    try {
      const data = await apiFetch(`/api/admin/tenants/${id}`);
      setTenant(data);
    } catch (err) {
      setError(err.message || 'Failed to load tenant.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  async function performAction(action, method = 'POST') {
    setActionLoading(true);
    try {
      const url =
        action === 'delete'
          ? `/api/tenants/${id}`
          : `/api/tenants/${id}/${action}`;
      await apiFetch(url, { method });
      if (action === 'delete') {
        toast.success('Tenant deactivated.');
        navigate('/admin/tenants');
      } else {
        toast.success('Action completed.');
        await loadTenant();
      }
    } catch (err) {
      toast.error(err.message || 'Action failed.');
    } finally {
      setActionLoading(false);
    }
  }

  async function purgeAccount() {
    const first = await confirm({
      title: 'Permanently Delete Account?',
      message: `This will permanently delete "${tenant.business_name}" (${tenant.owner_email}) and ALL associated data — appointments, contacts, calls, SMS messages.\n\nThis action is IRREVERSIBLE.`,
      confirmText: 'Continue',
      variant: 'danger',
    });
    if (!first) return;

    const typed = await prompt({
      title: 'Type to Confirm',
      message: 'Type the exact business name to confirm permanent deletion:',
      placeholder: tenant.business_name,
      confirmText: 'Delete Forever',
      variant: 'danger',
    });
    if (typed === null) return;
    if (typed !== tenant.business_name) {
      toast.error('Business name did not match. Deletion cancelled.');
      return;
    }

    setActionLoading(true);
    try {
      await apiFetch(`/api/tenants/${id}/purge`, { method: 'DELETE' });
      toast.success(`"${tenant.business_name}" permanently deleted.`);
      navigate('/admin/tenants');
    } catch (err) {
      toast.error(err.message || 'Permanent deletion failed.');
    } finally {
      setActionLoading(false);
    }
  }

  // ── Loading skeleton ──────────────────────────────────────────────────────
  if (loading) return <DetailSkeleton />;

  // ── Error state ───────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="p-8 flex flex-col items-center justify-center min-h-[60vh] gap-4">
        <div className="w-14 h-14 rounded-2xl bg-red-50 dark:bg-red-900/20 flex items-center justify-center">
          <AlertCircle className="w-7 h-7 text-red-500" />
        </div>
        <div className="text-center">
          <p className="text-base font-semibold text-gray-900 dark:text-white">Failed to load tenant</p>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">{error}</p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            to="/admin/tenants"
            className="inline-flex items-center gap-1.5 text-sm font-medium text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Tenants
          </Link>
          <button
            onClick={() => loadTenant(true)}
            className="px-4 py-2 bg-indigo-500 text-white text-sm font-medium rounded-lg hover:bg-indigo-600 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const cfg = STATUS_CONFIG[tenant.status] || STATUS_CONFIG.PENDING;
  const accent = STATUS_ACCENT[tenant.status] || 'from-indigo-400 to-purple-500';
  const initials = (tenant.business_name || '?')
    .split(' ')
    .map((w) => w[0])
    .join('')
    .substring(0, 2)
    .toUpperCase();

  return (
    <div className="p-5 md:p-8 space-y-5 max-w-5xl mx-auto">
      {/* ── Back nav ──────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <Link
          to="/admin/tenants"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          All Tenants
        </Link>
        <button
          onClick={() => loadTenant()}
          disabled={refreshing}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-500 dark:text-gray-400 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* ── Hero card ─────────────────────────────────────────────────── */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden shadow-sm">
        {/* Status gradient stripe */}
        <div className={`h-1.5 bg-gradient-to-r ${accent}`} />

        <div className="p-6 md:p-8">
          <div className="flex items-start gap-5">
            {/* Avatar */}
            <div
              className={`w-16 h-16 rounded-2xl bg-gradient-to-br ${accent} flex items-center justify-center text-white text-xl font-bold shrink-0 shadow-md`}
            >
              {initials}
            </div>

            {/* Main info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2.5 flex-wrap">
                <h1 className="text-xl md:text-2xl font-bold text-gray-900 dark:text-white">
                  {tenant.business_name}
                </h1>
                <span className={`px-2.5 py-1 rounded-full text-xs font-semibold ${cfg.color}`}>
                  {cfg.label}
                </span>
                {tenant.plan && (
                  <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-700 dark:bg-indigo-900/50 dark:text-indigo-400">
                    {PLAN_LABELS[tenant.plan] || tenant.plan}
                  </span>
                )}
                {tenant.demo_mode && (
                  <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-purple-100 text-purple-700 dark:bg-purple-900/50 dark:text-purple-400">
                    Demo
                  </span>
                )}
              </div>

              <p className="font-mono text-xs text-gray-400 dark:text-gray-500 mt-1.5 tracking-tight">
                {tenant.slug}
              </p>

              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2.5 text-sm">
                <span className="font-semibold text-gray-800 dark:text-gray-200">{tenant.owner_name}</span>
                <span className="text-gray-300 dark:text-gray-600">·</span>
                <span className="text-gray-500 dark:text-gray-400">{tenant.owner_email}</span>
                {tenant.owner_phone && (
                  <>
                    <span className="text-gray-300 dark:text-gray-600">·</span>
                    <span className="text-gray-500 dark:text-gray-400">{tenant.owner_phone}</span>
                  </>
                )}
                {tenant.business_type && (
                  <>
                    <span className="text-gray-300 dark:text-gray-600">·</span>
                    <span className="text-gray-400 dark:text-gray-500">
                      {BUSINESS_TYPE_LABELS[tenant.business_type] || tenant.business_type}
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Quick stats */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-6">
            <QuickStat
              icon={tenant.agent_active !== false ? Wifi : WifiOff}
              label="AI Agent"
              value={tenant.agent_active !== false ? 'Live' : 'Off'}
              active={tenant.agent_active !== false}
            />
            <QuickStat
              icon={Calendar}
              label="Google Calendar"
              value={tenant.google_calendar_connected ? 'Connected' : 'Not set up'}
              active={!!tenant.google_calendar_connected}
            />
            <QuickStat
              icon={MessageSquare}
              label="Twilio SMS"
              value={tenant.twilio_configured ? 'Configured' : 'Not set up'}
              active={!!tenant.twilio_configured}
            />
            <QuickStat
              icon={Clock}
              label="Member since"
              value={
                tenant.created_at
                  ? new Date(tenant.created_at).toLocaleDateString('en-US', { timeZone: tz, month: 'short', year: 'numeric' })
                  : '—'
              }
              active={null}
            />
          </div>
        </div>

        {/* Action strip */}
        <div className="px-6 md:px-8 py-4 bg-gray-50/80 dark:bg-gray-800/40 border-t border-gray-100 dark:border-gray-700/50 flex flex-wrap gap-2">
          {tenant.status === 'PENDING' && (
            <ActionBtn
              icon={CheckCircle}
              label="Approve"
              color="green"
              loading={actionLoading}
              onClick={() => performAction('approve')}
            />
          )}
          {(tenant.status === 'ACTIVE' || tenant.status === 'APPROVED') && (
            <ActionBtn
              icon={PauseCircle}
              label="Suspend"
              color="amber"
              loading={actionLoading}
              onClick={() => performAction('suspend')}
            />
          )}
          {(tenant.status === 'SUSPENDED' || tenant.status === 'DEACTIVATED') && (
            <ActionBtn
              icon={PlayCircle}
              label="Reactivate"
              color="blue"
              loading={actionLoading}
              onClick={() => performAction('reactivate')}
            />
          )}
          {tenant.status !== 'DEACTIVATED' && (
            <ActionBtn
              icon={Trash2}
              label="Deactivate"
              color="red"
              loading={actionLoading}
              onClick={async () => {
                const ok = await confirm({
                  title: 'Deactivate Account?',
                  message: `Deactivate "${tenant.business_name}"? The account will be disabled but data will be preserved.`,
                  confirmText: 'Deactivate',
                  variant: 'danger',
                });
                if (ok) performAction('delete', 'DELETE');
              }}
            />
          )}
          <ActionBtn
            icon={XCircle}
            label="Permanently Delete"
            color="red"
            loading={actionLoading}
            onClick={purgeAccount}
          />
        </div>
      </div>

      {/* ── Detail tabs ────────────────────────────────────────────────── */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden">
        <div className="flex items-center border-b border-gray-200 dark:border-gray-700 px-2">
          {TENANT_TABS.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-2 px-4 py-3.5 text-sm font-medium border-b-2 transition-colors ${
                activeTab === key
                  ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:border-gray-300'
              }`}
            >
              <Icon className="w-4 h-4" />
              {label}
            </button>
          ))}
        </div>

        <div className="p-5 md:p-6">
          {activeTab === 'overview' && <OverviewTab tenant={tenant} />}
          {activeTab === 'integrations' && (
            <IntegrationsTab tenantId={id} tenant={tenant} onRefresh={loadTenant} />
          )}
          {activeTab === 'usage' && <UsageHistoryTab tenantId={id} />}
        </div>
      </div>
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════════
// SMALL SHARED UI COMPONENTS
// ═══════════════════════════════════════════════════════════════════════════

function QuickStat({ icon: Icon, label, value, active }) {
  const iconColor =
    active === null ? 'text-gray-400' : active ? 'text-green-500' : 'text-gray-400';
  const valueColor =
    active === null
      ? 'text-gray-700 dark:text-gray-300'
      : active
      ? 'text-green-700 dark:text-green-400'
      : 'text-gray-500 dark:text-gray-400';

  return (
    <div className="p-3 bg-gray-50 dark:bg-gray-800/60 rounded-xl">
      <div className="flex items-center gap-1.5 mb-1.5">
        <Icon className={`w-3.5 h-3.5 ${iconColor}`} />
        <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500 truncate">
          {label}
        </span>
      </div>
      <p className={`text-sm font-semibold ${valueColor}`}>{value}</p>
    </div>
  );
}

function ActionBtn({ icon: Icon, label, color, loading, onClick }) {
  const colorMap = {
    green:
      'bg-green-50 text-green-700 hover:bg-green-100 border-green-200 dark:bg-green-900/30 dark:text-green-400 dark:hover:bg-green-900/50 dark:border-green-800',
    amber:
      'bg-amber-50 text-amber-700 hover:bg-amber-100 border-amber-200 dark:bg-amber-900/30 dark:text-amber-400 dark:hover:bg-amber-900/50 dark:border-amber-800',
    blue: 'bg-blue-50 text-blue-700 hover:bg-blue-100 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:hover:bg-blue-900/50 dark:border-blue-800',
    red: 'bg-red-50 text-red-700 hover:bg-red-100 border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:hover:bg-red-900/50 dark:border-red-800',
    gray: 'bg-gray-100 text-gray-600 hover:bg-gray-200 border-gray-200 dark:bg-gray-700 dark:text-gray-400 dark:hover:bg-gray-600 dark:border-gray-600',
  };
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className={`inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold border transition-colors disabled:opacity-50 ${
        colorMap[color] || colorMap.blue
      }`}
    >
      {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Icon className="w-3.5 h-3.5" />}
      {label}
    </button>
  );
}

function DetailRow({ label, value, mono }) {
  return (
    <div className="flex items-start gap-2">
      <span className="text-xs font-medium text-gray-500 dark:text-gray-400 w-24 shrink-0 pt-0.5">{label}</span>
      <span
        className={`text-sm text-gray-800 dark:text-gray-200 break-all ${
          mono ? 'font-mono text-xs bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded' : ''
        }`}
      >
        {value}
      </span>
    </div>
  );
}

function IntegrationStatus({ label, configured, enabled = true }) {
  return (
    <div className="flex items-center gap-2">
      <div
        className={`w-2 h-2 rounded-full ${
          !enabled ? 'bg-gray-300 dark:bg-gray-600' : configured ? 'bg-green-400' : 'bg-gray-300'
        }`}
      />
      <span className={`text-sm ${!enabled ? 'text-gray-400 line-through' : 'text-gray-700 dark:text-gray-300'}`}>
        {label}
      </span>
      <span
        className={`text-xs ${
          !enabled
            ? 'text-gray-400 dark:text-gray-500'
            : configured
            ? 'text-green-600 dark:text-green-400'
            : 'text-gray-400'
        }`}
      >
        {!enabled ? 'Disabled' : configured ? 'Connected' : 'Not configured'}
      </span>
    </div>
  );
}

function FeatureToggle({ label, description, enabled, onChange }) {
  return (
    <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg">
      <div>
        <p className="text-sm font-medium text-gray-900 dark:text-white">{label}</p>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{description}</p>
      </div>
      <button
        onClick={() => onChange(!enabled)}
        className={`relative shrink-0 ml-4 w-11 h-6 rounded-full transition-colors ${
          enabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${
            enabled ? 'translate-x-5' : 'translate-x-0'
          }`}
        />
      </button>
    </div>
  );
}

function ProvisioningField({ icon: Icon, label, value, onChange, placeholder, hint }) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
        <Icon className="w-3.5 h-3.5 inline mr-1" />
        {label}
      </label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-3 py-2 border border-gray-200 dark:border-gray-600 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none dark:bg-gray-700 dark:text-white font-mono"
      />
      {hint && <p className="text-[11px] text-gray-400 mt-1">{hint}</p>}
    </div>
  );
}

function UsageBar({ label, used, limit, raw, percent }) {
  const pct = percent ?? (limit ? Math.min((raw / limit) * 100, 100) : 0);
  const color = pct >= 95 ? 'bg-red-500' : pct >= 80 ? 'bg-amber-500' : 'bg-green-500';
  return (
    <div>
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="text-gray-600 dark:text-gray-400">{label}</span>
        <span className="font-medium text-gray-900 dark:text-white">
          {used} / {limit ?? '∞'}
        </span>
      </div>
      <div className="w-full h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="p-5 md:p-8 space-y-5 max-w-5xl mx-auto animate-pulse">
      <div className="h-5 w-28 bg-gray-200 dark:bg-gray-700 rounded" />
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden">
        <div className="h-1.5 bg-gray-200 dark:bg-gray-700" />
        <div className="p-8">
          <div className="flex items-start gap-5">
            <div className="w-16 h-16 bg-gray-200 dark:bg-gray-700 rounded-2xl shrink-0" />
            <div className="flex-1 space-y-2.5">
              <div className="h-7 w-56 bg-gray-200 dark:bg-gray-700 rounded" />
              <div className="h-3 w-32 bg-gray-200 dark:bg-gray-700 rounded" />
              <div className="h-4 w-80 bg-gray-200 dark:bg-gray-700 rounded" />
            </div>
          </div>
          <div className="grid grid-cols-4 gap-3 mt-6">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-16 bg-gray-200 dark:bg-gray-700 rounded-xl" />
            ))}
          </div>
        </div>
        <div className="h-14 bg-gray-100 dark:bg-gray-800" />
      </div>
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden">
        <div className="h-14 bg-gray-100 dark:bg-gray-800" />
        <div className="p-6 space-y-3">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-full" style={{ width: `${70 + (i % 3) * 10}%` }} />
          ))}
        </div>
      </div>
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════════
// OVERVIEW TAB
// ═══════════════════════════════════════════════════════════════════════════

function OverviewTab({ tenant }) {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Business details */}
        <div className="space-y-3">
          <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
            Business Details
          </h4>
          <DetailRow label="ID" value={tenant.id} mono />
          <DetailRow label="Slug" value={tenant.slug} mono />
          <DetailRow
            label="Business Type"
            value={BUSINESS_TYPE_LABELS[tenant.business_type] || tenant.business_type || '—'}
          />
          <DetailRow label="Timezone" value={tenant.timezone || '—'} />
          <DetailRow label="Plan" value={PLAN_LABELS[tenant.plan] || tenant.plan || '—'} />
          <DetailRow label="Agent Name" value={tenant.agent_name || '—'} />
          <DetailRow
            label="Created"
            value={tenant.created_at ? new Date(tenant.created_at).toLocaleString('en-US', { timeZone: tz, month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—'}
          />
          <DetailRow
            label="Updated"
            value={tenant.updated_at ? new Date(tenant.updated_at).toLocaleString('en-US', { timeZone: tz, month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—'}
          />
        </div>

        {/* Owner info */}
        <div className="space-y-3">
          <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
            Owner
          </h4>
          <DetailRow label="Name" value={tenant.owner_name} />
          <DetailRow label="Email" value={tenant.owner_email} />
          <DetailRow label="Phone" value={tenant.owner_phone || '—'} />
        </div>
      </div>

      {/* Greeting message */}
      {tenant.greeting_message && (
        <div className="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Greeting Message</p>
          <p className="text-sm text-gray-700 dark:text-gray-300 italic">"{tenant.greeting_message}"</p>
        </div>
      )}

      {/* Location */}
      {(tenant.business_address || tenant.google_maps_url) && (
        <div className="p-4 bg-gray-50 dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <h4 className="text-sm font-semibold text-gray-900 dark:text-white flex items-center gap-2 mb-2">
            <MapPin className="w-4 h-4 text-indigo-500" />
            Business Location
          </h4>
          {tenant.business_address && (
            <p className="text-sm text-gray-700 dark:text-gray-300 mb-2">{tenant.business_address}</p>
          )}
          {tenant.google_maps_url && (
            <a
              href={tenant.google_maps_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sm font-medium text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              Open in Google Maps
            </a>
          )}
        </div>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════════
// INTEGRATIONS TAB
// ═══════════════════════════════════════════════════════════════════════════

function IntegrationsTab({ tenantId, tenant, onRefresh }) {
  const { toast } = useModal();
  const [saving, setSaving] = useState(false);
  const [fields, setFields] = useState({
    twilio_phone_number: tenant.twilio_phone_number || '',
    agent_active: tenant.agent_active !== false,
    feature_twilio_enabled: tenant.feature_twilio_enabled !== false,
  });
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!loaded) loadCurrentValues();
  }, []);

  async function loadCurrentValues() {
    try {
      const data = await apiFetch(`/api/admin/tenants/${tenantId}`);
      setFields({
        twilio_phone_number: data.twilio_phone_number || '',
        agent_active: data.agent_active !== false,
        feature_twilio_enabled: data.feature_twilio_enabled !== false,
      });
    } catch {
      setFields((f) => ({
        ...f,
        agent_active: tenant.agent_active !== false,
        feature_twilio_enabled: tenant.feature_twilio_enabled !== false,
      }));
    }
    setLoaded(true);
  }

  async function handleSave() {
    setSaving(true);
    try {
      await apiFetch(`/api/admin/tenants/${tenantId}/integrations`, {
        method: 'POST',
        body: JSON.stringify({
          twilio_phone_number: fields.twilio_phone_number.trim() || null,
          feature_twilio_enabled: fields.feature_twilio_enabled,
          agent_active: fields.agent_active,
        }),
      });
      toast.success('Settings saved.');
      if (onRefresh) onRefresh();
    } catch (err) {
      toast.error(err.message || 'Failed to save settings.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Agent Status */}
      <div
        className={`p-4 rounded-lg border ${
          fields.agent_active
            ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'
            : 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800'
        }`}
      >
        <FeatureToggle
          label="Agent Status"
          description={
            fields.agent_active
              ? 'AI agent is LIVE — answering and processing requests'
              : 'Agent is OFF — inbound calls forwarded to the business phone'
          }
          enabled={fields.agent_active}
          onChange={(v) => setFields((f) => ({ ...f, agent_active: v }))}
        />
      </div>

      {/* Feature Flags */}
      <div className="p-4 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
        <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-4 flex items-center gap-2">
          <Settings2 className="w-3.5 h-3.5" />
          Feature Flags
        </h4>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
          Toggle features on/off for this tenant. Disabled features are hidden and non-functional
          platform-wide.
        </p>
        <div className="space-y-3">
          <FeatureToggle
            label="SMS (Twilio)"
            description="Appointment confirmations, reminders, and waitlist notifications via text"
            enabled={fields.feature_twilio_enabled}
            onChange={(v) => setFields((f) => ({ ...f, feature_twilio_enabled: v }))}
          />
        </div>
      </div>

      {/* Connection Status */}
      <div className="p-4 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
        <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">
          Connection Status
        </h4>
        <div className="space-y-2">
          <IntegrationStatus
            label={`Google Calendar${tenant.google_calendar_email ? ` (${tenant.google_calendar_email})` : ''}`}
            configured={tenant.google_calendar_connected}
          />
          <IntegrationStatus
            label="Twilio SMS"
            configured={tenant.twilio_configured}
            enabled={fields.feature_twilio_enabled}
          />
        </div>
      </div>

      {/* Twilio Assignment */}
      <div className="p-4 bg-white dark:bg-gray-800 rounded-lg border border-indigo-200 dark:border-indigo-800">
        <h4 className="text-xs font-semibold text-indigo-600 dark:text-indigo-400 uppercase tracking-wider mb-3 flex items-center gap-2">
          <MessageSquare className="w-3.5 h-3.5" />
          Twilio Assignment
        </h4>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
          Assign a Twilio phone number from the platform's global Twilio account to this tenant.
        </p>
        <ProvisioningField
          icon={MessageSquare}
          label="Twilio Phone Number"
          value={fields.twilio_phone_number}
          onChange={(v) => setFields((f) => ({ ...f, twilio_phone_number: v }))}
          placeholder="e.g. +14155551234"
          hint="Twilio SMS number from the platform account used for this tenant's reminders."
        />
        <div className="pt-4 flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors disabled:opacity-50"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            Save Settings
          </button>
        </div>
      </div>
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════════
// USAGE & HISTORY TAB
// ═══════════════════════════════════════════════════════════════════════════

function UsageHistoryTab({ tenantId }) {
  return (
    <div className="space-y-5">
      <TenantUsageStats tenantId={tenantId} />
      <TenantChangeHistory tenantId={tenantId} />
    </div>
  );
}

function TenantUsageStats({ tenantId }) {
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!loaded) loadUsage();
  }, []);

  async function loadUsage() {
    setLoading(true);
    try {
      const data = await apiFetch(`/api/admin/tenants/${tenantId}/usage`);
      setUsage(data);
    } catch (err) {
      console.error('Failed to load usage:', err);
    } finally {
      setLoading(false);
      setLoaded(true);
    }
  }

  return (
    <div className="p-4 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
      <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3 flex items-center gap-2">
        <BarChart3 className="w-3.5 h-3.5" />
        Usage Stats
      </h4>

      {loading ? (
        <div className="flex items-center justify-center py-6">
          <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
        </div>
      ) : !usage ? (
        <p className="text-xs text-gray-400 py-2">Could not load usage data.</p>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
            <span>
              Plan:{' '}
              <strong className="text-gray-900 dark:text-white">
                {PLAN_LABELS[usage.plan] || usage.plan || '—'}
              </strong>
            </span>
            {usage.period_start && (
              <span>Period started: {new Date(usage.period_start).toLocaleDateString('en-US', { timeZone: tz, month: 'short', day: 'numeric', year: 'numeric' })}</span>
            )}
          </div>
          <UsageBar
            label="Call Minutes"
            used={usage.calls?.used?.toFixed(1) ?? '0'}
            limit={usage.calls?.limit}
            raw={usage.calls?.used ?? 0}
            percent={usage.calls?.percent ?? 0}
          />
          <UsageBar
            label="SMS Sent"
            used={usage.sms?.used ?? 0}
            limit={usage.sms?.limit}
            raw={usage.sms?.used ?? 0}
            percent={usage.sms?.percent ?? 0}
          />
        </div>
      )}
    </div>
  );
}

function TenantChangeHistory({ tenantId }) {
  const [changes, setChanges] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!loaded) loadChanges();
  }, []);

  async function loadChanges() {
    setLoading(true);
    try {
      const data = await apiFetch(`/api/auth/profile-changes?tenant_id=${tenantId}`);
      setChanges(Array.isArray(data) ? data : []);
      setLoaded(true);
    } catch {
      setLoaded(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="p-4 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
      <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3 flex items-center gap-2">
        <History className="w-3.5 h-3.5" />
        Change History
        {loaded && changes.length > 0 && (
          <span className="px-1.5 py-0.5 bg-gray-100 dark:bg-gray-600 text-gray-500 dark:text-gray-400 rounded text-[10px]">
            {changes.length}
          </span>
        )}
      </h4>

      {loading ? (
        <div className="flex items-center justify-center py-6">
          <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
        </div>
      ) : changes.length === 0 ? (
        <p className="text-xs text-gray-400 dark:text-gray-500 py-2">No profile changes recorded.</p>
      ) : (
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {changes.map((log) => (
            <div
              key={log.id}
              className="flex items-start gap-2.5 p-2.5 bg-gray-50 dark:bg-gray-700/50 rounded-lg text-xs"
            >
              <div className="w-1.5 h-1.5 rounded-full bg-indigo-400 mt-1.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-gray-900 dark:text-white font-medium">
                  {log.field_name === 'password' ? (
                    'Password changed'
                  ) : (
                    <>
                      <span className="text-gray-500 dark:text-gray-400">{log.field_name}:</span>{' '}
                      <span className="line-through text-gray-400 dark:text-gray-500">{log.old_value}</span>{' '}
                      <span className="text-indigo-600 dark:text-indigo-400">{log.new_value}</span>
                    </>
                  )}
                </p>
                <p className="text-gray-400 dark:text-gray-500 mt-0.5">
                  by {log.changed_by} · {log.created_at ? new Date(log.created_at).toLocaleString('en-US', { timeZone: tz, month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—'}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
