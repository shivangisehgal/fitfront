import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Building2,
  Clock,
  CheckCircle,
  XCircle,
  RefreshCw,
  Search,
  ChevronDown,
  ChevronUp,
  ChevronRight,
  Wifi,
  AlertCircle,
  HelpCircle,
  Loader2,
  MessageSquare,
  Settings2,
  PhoneCall,
  Save,
  Info,
} from 'lucide-react';
import { apiFetch } from '../lib/api';
import { useModal } from '../contexts/ModalContext';
import { useAuth } from '../contexts/AuthContext';
import {
  STATUS_CONFIG,
  PLAN_LABELS,
  TICKET_STATUS_LABELS,
  TICKET_PRIORITY_LABELS,
  TICKET_CATEGORY_LABELS,
} from '../lib/tenantLabels';

// ── Constants ───────────────────────────────────────────────────────────────

const STATUS_FILTERS = ['ALL', 'PENDING', 'ACTIVE', 'APPROVED', 'SUSPENDED', 'DEACTIVATED'];

const TOP_TABS = [
  { key: 'tenants', label: 'Tenants', icon: Building2 },
  { key: 'tickets', label: 'Support Tickets', icon: HelpCircle },
  { key: 'platform', label: 'Platform Settings', icon: Settings2 },
];

// ── Main Component ──────────────────────────────────────────────────────────

export default function TenantAdmin() {
  const [activeTopTab, setActiveTopTab] = useState('tenants');
  const [tenants, setTenants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [error, setError] = useState(null);
  const { toast } = useModal();
  const { user } = useAuth();
  const tz = user?.timezone || 'America/Chicago';

  // Support tickets state
  const [ticketStats, setTicketStats] = useState(null);
  const [adminTickets, setAdminTickets] = useState([]);
  const [ticketsLoading, setTicketsLoading] = useState(false);
  const [ticketStatusFilter, setTicketStatusFilter] = useState('ALL');

  useEffect(() => {
    fetchTenants();
    fetchTicketStats();
  }, [statusFilter]);

  // ── Data fetching ──

  async function fetchTenants() {
    setLoading(true);
    setError(null);
    try {
      const params = statusFilter !== 'ALL' ? `?status=${statusFilter}` : '';
      const data = await apiFetch(`/api/tenants${params}`);
      setTenants(data);
    } catch (err) {
      console.error('Failed to fetch tenants:', err);
      setError(err.message || 'Failed to load tenants.');
    } finally {
      setLoading(false);
    }
  }

  async function fetchTicketStats() {
    try {
      const stats = await apiFetch('/api/admin/support/tickets/stats');
      setTicketStats(stats);
    } catch (err) {
      console.error('Failed to fetch ticket stats:', err);
    }
  }

  async function fetchAdminTickets(status = 'OPEN') {
    setTicketsLoading(true);
    try {
      const params = status !== 'ALL' ? `?status=${status}` : '';
      const data = await apiFetch(`/api/admin/support/tickets${params}`);
      setAdminTickets(data.tickets || []);
    } catch (err) {
      console.error('Failed to fetch admin tickets:', err);
    } finally {
      setTicketsLoading(false);
    }
  }

  // Client-side search filter
  const filtered = tenants.filter((t) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      t.business_name.toLowerCase().includes(q) ||
      t.slug.toLowerCase().includes(q) ||
      t.owner_name.toLowerCase().includes(q) ||
      t.owner_email.toLowerCase().includes(q)
    );
  });

  const pendingCount = tenants.filter((t) => t.status === 'PENDING').length;
  const activeCount = tenants.filter((t) => t.status === 'ACTIVE').length;
  const totalCount = tenants.length;
  const openTicketCount = (ticketStats?.OPEN || 0) + (ticketStats?.REOPENED || 0);

  if (loading && tenants.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="w-8 h-8 border-2 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="p-5 md:p-8 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Platform Admin</h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">
            Manage tenants, support tickets, and platform settings
          </p>
        </div>
        <button
          onClick={() => {
            fetchTenants();
            fetchTicketStats();
          }}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-600 dark:text-gray-400 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* ── Top-level tab bar ─────────────────────────────────────────── */}
      <div className="flex items-center gap-1 border-b border-gray-200 dark:border-gray-700">
        {TOP_TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => {
              setActiveTopTab(key);
              if (key === 'tickets' && adminTickets.length === 0) fetchAdminTickets(ticketStatusFilter);
            }}
            className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              activeTopTab === key
                ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
                : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:border-gray-300'
            }`}
          >
            <Icon className="w-4 h-4" />
            {label}
            {key === 'tenants' && pendingCount > 0 && (
              <span className="ml-1 px-1.5 py-0.5 bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-400 rounded-full text-[10px] font-bold">
                {pendingCount}
              </span>
            )}
            {key === 'tickets' && openTicketCount > 0 && (
              <span className="ml-1 px-1.5 py-0.5 bg-red-100 dark:bg-red-900/50 text-red-600 dark:text-red-400 rounded-full text-[10px] font-bold">
                {openTicketCount}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ── Tab Content ───────────────────────────────────────────────── */}
      {activeTopTab === 'tenants' && (
        <TenantsTab
          tenants={filtered}
          totalCount={totalCount}
          pendingCount={pendingCount}
          activeCount={activeCount}
          statusFilter={statusFilter}
          setStatusFilter={setStatusFilter}
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          error={error}
          onRefresh={fetchTenants}
        />
      )}

      {activeTopTab === 'tickets' && (
        <TicketsTab
          tickets={adminTickets}
          ticketsLoading={ticketsLoading}
          ticketStatusFilter={ticketStatusFilter}
          setTicketStatusFilter={(s) => {
            setTicketStatusFilter(s);
            fetchAdminTickets(s);
          }}
          ticketStats={ticketStats}
        />
      )}

      {activeTopTab === 'platform' && <PlatformSettingsTab />}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════════
// TENANTS TAB
// ═══════════════════════════════════════════════════════════════════════════

function TenantsTab({
  tenants, totalCount, pendingCount, activeCount,
  statusFilter, setStatusFilter, searchQuery, setSearchQuery,
  error, onRefresh,
}) {
  return (
    <div className="space-y-5">
      {/* Stat cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard
          icon={Building2}
          iconBg="bg-blue-50 dark:bg-blue-900/50"
          iconColor="text-blue-600"
          value={totalCount}
          label="Total Tenants"
        />
        <StatCard
          icon={Clock}
          iconBg="bg-amber-50 dark:bg-amber-900/50"
          iconColor="text-amber-600"
          value={pendingCount}
          label="Pending Approval"
        />
        <StatCard
          icon={Wifi}
          iconBg="bg-green-50 dark:bg-green-900/50"
          iconColor="text-green-600"
          value={activeCount}
          label="Active Tenants"
        />
      </div>

      {/* Search + status filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search by name, slug, owner…"
            className="w-full pl-10 pr-4 py-2.5 border border-gray-200 dark:border-gray-600 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none dark:bg-gray-700 dark:text-white"
          />
        </div>
        <div className="flex items-center gap-1 flex-wrap">
          {STATUS_FILTERS.map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                statusFilter === s
                  ? 'bg-indigo-500 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-400 dark:hover:bg-gray-600'
              }`}
            >
              {s === 'ALL' ? 'All' : STATUS_CONFIG[s]?.label || s}
              {s === 'PENDING' && pendingCount > 0 && (
                <span className="ml-1 bg-white/30 px-1.5 py-0.5 rounded-full text-[10px]">{pendingCount}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-xl p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 mt-0.5 shrink-0" />
          <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
        </div>
      )}

      {/* Tenant list */}
      {tenants.length === 0 ? (
        <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-12 text-center">
          <Building2 className="w-10 h-10 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
          <p className="text-gray-500 dark:text-gray-400 text-sm">
            {searchQuery
              ? 'No tenants match your search.'
              : statusFilter !== 'ALL'
              ? `No ${STATUS_CONFIG[statusFilter]?.label?.toLowerCase()} tenants.`
              : 'No tenants registered yet.'}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {tenants.map((tenant) => (
            <TenantRow key={tenant.id} tenant={tenant} />
          ))}
        </div>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════════
// TENANT ROW — navigates to detail page on click
// ═══════════════════════════════════════════════════════════════════════════

function TenantRow({ tenant }) {
  const navigate = useNavigate();
  const cfg = STATUS_CONFIG[tenant.status] || STATUS_CONFIG.PENDING;

  return (
    <button
      onClick={() => navigate(`/admin/tenants/${tenant.id}`)}
      className="w-full text-left bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden hover:border-indigo-300 dark:hover:border-indigo-700/60 hover:shadow-sm transition-all group"
    >
      <div className="flex items-center gap-4 p-4">
        {/* Status dot */}
        <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${cfg.dot}`} />

        {/* Primary info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-gray-900 dark:text-white truncate">
              {tenant.business_name}
            </span>
            <span className={`px-2 py-0.5 rounded-full text-[11px] font-medium ${cfg.color}`}>
              {cfg.label}
            </span>
            {tenant.demo_mode && (
              <span className="px-2 py-0.5 rounded-full text-[11px] font-medium bg-purple-100 text-purple-700 dark:bg-purple-900/50 dark:text-purple-400">
                Demo
              </span>
            )}
            {tenant.plan && (
              <span className="px-2 py-0.5 rounded-full text-[11px] font-medium bg-indigo-100 text-indigo-700 dark:bg-indigo-900/50 dark:text-indigo-400">
                {PLAN_LABELS[tenant.plan] || tenant.plan}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            <span className="font-mono">{tenant.slug}</span>
            <span>·</span>
            <span>{tenant.owner_name}</span>
            <span>·</span>
            <span>{tenant.owner_email}</span>
          </div>
        </div>

        {/* Integration badges */}
        <div className="hidden md:flex items-center gap-1.5">
          <span
            className={`px-2 py-0.5 rounded text-[10px] font-medium ${
              tenant.agent_active !== false
                ? 'bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-400'
                : 'bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-400'
            }`}
          >
            {tenant.agent_active !== false ? 'Agent ON' : 'Agent OFF'}
          </span>
          <IntegrationBadge label="GCal" active={tenant.google_calendar_connected} />
          <IntegrationBadge
            label="Twilio"
            active={tenant.twilio_configured}
            enabled={tenant.feature_twilio_enabled !== false}
          />
        </div>

        <ChevronRight className="w-4 h-4 text-gray-400 group-hover:text-indigo-500 shrink-0 transition-colors" />
      </div>
    </button>
  );
}


// ═══════════════════════════════════════════════════════════════════════════
// SUPPORT TICKETS TAB — grouped by tenant, each ticket navigates to detail
// ═══════════════════════════════════════════════════════════════════════════

const TICKET_STATUS_DOT = {
  OPEN:        'bg-blue-500',
  IN_PROGRESS: 'bg-amber-500',
  RESOLVED:    'bg-green-500',
  CLOSED:      'bg-gray-400',
  REOPENED:    'bg-red-500',
};

const TICKET_STATUS_COLOR = {
  OPEN:        'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  IN_PROGRESS: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  RESOLVED:    'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  CLOSED:      'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',
  REOPENED:    'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
};

function TicketsTab({ tickets, ticketsLoading, ticketStatusFilter, setTicketStatusFilter, ticketStats }) {
  const [tenantSearch, setTenantSearch] = useState('');

  // Filter by status + tenant search
  const filtered = tickets.filter((t) => {
    if (tenantSearch) {
      const q = tenantSearch.toLowerCase();
      const matchTenant =
        (t.tenant_name || '').toLowerCase().includes(q) ||
        (t.tenant_slug || '').toLowerCase().includes(q);
      if (!matchTenant) return false;
    }
    return true;
  });

  // Group by tenant_id
  const groupMap = {};
  filtered.forEach((t) => {
    const key = t.tenant_id || 'unknown';
    if (!groupMap[key]) {
      groupMap[key] = {
        tenantId: key,
        name: t.tenant_name || t.tenant_slug || 'Unknown Tenant',
        slug: t.tenant_slug || '',
        tickets: [],
      };
    }
    groupMap[key].tickets.push(t);
  });
  const tenantGroups = Object.values(groupMap);

  return (
    <div className="space-y-4">
      {/* Search + status filter row */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative sm:w-64">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
          <input
            type="text"
            value={tenantSearch}
            onChange={(e) => setTenantSearch(e.target.value)}
            placeholder="Search by tenant…"
            className="w-full pl-10 pr-4 py-2.5 border border-gray-200 dark:border-gray-600 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none dark:bg-gray-700 dark:text-white"
          />
        </div>
        <div className="flex items-center gap-1 flex-wrap">
          {['ALL', 'OPEN', 'REOPENED', 'IN_PROGRESS', 'RESOLVED', 'CLOSED'].map((s) => (
            <button
              key={s}
              onClick={() => setTicketStatusFilter(s)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                ticketStatusFilter === s
                  ? 'bg-indigo-500 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-400 dark:hover:bg-gray-600'
              }`}
            >
              {s === 'ALL' ? 'All' : TICKET_STATUS_LABELS[s] || s}
              {s !== 'ALL' && ticketStats?.[s] > 0 && (
                <span className="ml-1 opacity-70">({ticketStats[s]})</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Stats summary */}
      {ticketStats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { key: 'OPEN', label: 'Open', icon: HelpCircle, iconColor: 'text-blue-500', iconBg: 'bg-blue-50 dark:bg-blue-900/30' },
            { key: 'REOPENED', label: 'Reopened', icon: RefreshCw, iconColor: 'text-red-500', iconBg: 'bg-red-50 dark:bg-red-900/30' },
            { key: 'IN_PROGRESS', label: 'In Progress', icon: Clock, iconColor: 'text-amber-500', iconBg: 'bg-amber-50 dark:bg-amber-900/30' },
            { key: 'RESOLVED', label: 'Resolved', icon: CheckCircle, iconColor: 'text-green-500', iconBg: 'bg-green-50 dark:bg-green-900/30' },
          ].map(({ key, label, icon: Icon, iconColor, iconBg }) => (
            <StatCard key={key} icon={Icon} iconBg={iconBg} iconColor={iconColor} value={ticketStats[key] || 0} label={label} />
          ))}
        </div>
      )}

      {/* Content */}
      {ticketsLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="w-5 h-5 animate-spin text-indigo-500" />
        </div>
      ) : tenantGroups.length === 0 ? (
        <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-12 text-center">
          <HelpCircle className="w-10 h-10 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
          <p className="text-gray-400 text-sm">
            {tenantSearch ? 'No tenants match your search.' : 'No tickets found.'}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {tenantGroups.map((group) => (
            <TenantTicketGroup key={group.tenantId} group={group} />
          ))}
        </div>
      )}
    </div>
  );
}

function TenantTicketGroup({ group }) {
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);

  // Count by status for summary badges
  const statusCounts = group.tickets.reduce((acc, t) => {
    acc[t.status] = (acc[t.status] || 0) + 1;
    return acc;
  }, {});

  const initials = (group.name || '?').charAt(0).toUpperCase();

  return (
    <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden">
      {/* Group header */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center justify-between gap-4 p-4 hover:bg-gray-50 dark:hover:bg-gray-800/40 transition-colors text-left"
      >
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-400 to-purple-500 flex items-center justify-center text-white text-sm font-bold shrink-0">
            {initials}
          </div>
          <div>
            <p className="font-semibold text-sm text-gray-900 dark:text-white">{group.name}</p>
            <p className="text-xs text-gray-500 dark:text-gray-400 font-mono">{group.slug}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Status summary pills */}
          <div className="hidden sm:flex items-center gap-1.5">
            {Object.entries(statusCounts).map(([status, count]) => (
              <span key={status} className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${TICKET_STATUS_COLOR[status] || 'bg-gray-100 text-gray-500'}`}>
                {count} {TICKET_STATUS_LABELS[status] || status}
              </span>
            ))}
          </div>
          <span className="text-xs text-gray-400 tabular-nums shrink-0">
            {group.tickets.length} ticket{group.tickets.length !== 1 ? 's' : ''}
          </span>
          {collapsed
            ? <ChevronDown className="w-4 h-4 text-gray-400 shrink-0" />
            : <ChevronUp className="w-4 h-4 text-gray-400 shrink-0" />
          }
        </div>
      </button>

      {/* Ticket rows */}
      {!collapsed && (
        <div className="border-t border-gray-100 dark:border-gray-700/50 divide-y divide-gray-100 dark:divide-gray-700/30">
          {group.tickets.map((ticket) => (
            <button
              key={ticket.id}
              onClick={() => navigate(`/admin/tickets/${ticket.id}`)}
              className="w-full text-left flex items-center gap-3 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-800/40 group transition-colors"
            >
              {/* Status dot */}
              <span className={`w-2 h-2 rounded-full shrink-0 ${TICKET_STATUS_DOT[ticket.status] || 'bg-gray-400'}`} />

              {/* Subject + meta */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">
                    {ticket.subject}
                  </span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold shrink-0 ${TICKET_STATUS_COLOR[ticket.status] || 'bg-gray-100 text-gray-500'}`}>
                    {TICKET_STATUS_LABELS[ticket.status] || ticket.status}
                  </span>
                  {(ticket.priority === 'HIGH' || ticket.priority === 'URGENT') && (
                    <span className={`text-[10px] font-semibold shrink-0 ${ticket.priority === 'URGENT' ? 'text-red-500' : 'text-orange-500'}`}>
                      {TICKET_PRIORITY_LABELS[ticket.priority]}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 mt-0.5 text-xs text-gray-400 dark:text-gray-500">
                  <span>{TICKET_CATEGORY_LABELS[ticket.category] || ticket.category}</span>
                  <span>·</span>
                  <span>{new Date(ticket.created_at).toLocaleDateString('en-US', { timeZone: tz, month: 'short', day: 'numeric', year: 'numeric' })}</span>
                  {ticket.message_count > 0 && (
                    <>
                      <span>·</span>
                      <span className="inline-flex items-center gap-0.5">
                        <MessageSquare className="w-3 h-3" /> {ticket.message_count}
                      </span>
                    </>
                  )}
                </div>
              </div>

              <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-indigo-500 shrink-0 transition-colors" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════════
// PLATFORM SETTINGS TAB
// ═══════════════════════════════════════════════════════════════════════════

function PlatformSettingsTab() {
  const { toast } = useModal();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [config, setConfig] = useState({
    bolna_api_key_masked: '',
    bolna_agent_id: '',
    bolna_configured: false,
  });
  const [editKey, setEditKey] = useState('');
  const [editAgentId, setEditAgentId] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null); // {ok, message, agent_name?}

  useEffect(() => {
    loadConfig();
  }, []);

  async function loadConfig() {
    setLoading(true);
    try {
      const data = await apiFetch('/api/admin/platform/config');
      setConfig(data);
      setEditAgentId(data.bolna_agent_id || '');
    } catch {
      toast.error('Failed to load platform config.');
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setTestResult(null);
    try {
      const body = { bolna_agent_id: editAgentId.trim() };
      if (editKey.trim()) body.bolna_api_key = editKey.trim();
      const data = await apiFetch('/api/admin/platform/config', {
        method: 'PUT',
        body: JSON.stringify(body),
      });
      setConfig(data);
      setEditKey('');
      toast.success('Platform settings saved.');
    } catch (err) {
      toast.error(err.message || 'Failed to save platform settings.');
    } finally {
      setSaving(false);
    }
  }

  async function handleTestConnection() {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await apiFetch('/api/admin/platform/bolna/test');
      setTestResult(result);
    } catch (err) {
      setTestResult({ ok: false, message: err.message || 'Request failed.' });
    } finally {
      setTesting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Header */}
      <div className="bg-gradient-to-r from-indigo-50 to-purple-50 dark:from-indigo-900/20 dark:to-purple-900/20 rounded-xl p-5 border border-indigo-100 dark:border-indigo-800/50">
        <div className="flex items-start gap-3">
          <div className="p-2 bg-indigo-100 dark:bg-indigo-900/50 rounded-lg">
            <Settings2 className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
          </div>
          <div>
            <h3 className="text-base font-semibold text-gray-900 dark:text-white">
              Global Platform Settings
            </h3>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
              Admin-only configuration that applies across all tenants. These credentials are managed
              centrally — tenants cannot see or change them.
            </p>
          </div>
        </div>
      </div>

      {/* Bolna AI Section */}
      <div className="bg-white dark:bg-gray-800/60 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <PhoneCall className="w-4 h-4 text-indigo-500" />
            <h4 className="text-sm font-semibold text-gray-900 dark:text-white">
              Bolna AI — Outbound Calling
            </h4>
          </div>
          <span
            className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${
              config.bolna_configured
                ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${config.bolna_configured ? 'bg-green-500' : 'bg-gray-400'}`}
            />
            {config.bolna_configured ? 'Configured' : 'Not configured'}
          </span>
        </div>

        <div className="p-5 space-y-5">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Bolna AI provides AI-powered outbound calling with Indian phone number support (+91). A single
            global API key and agent are used for all tenant outbound calls initiated via the platform.
          </p>

          {/* API Key */}
          <div>
            <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-2">
              Bolna API Key
            </label>
            <div className="relative">
              <input
                type="text"
                value={editKey}
                onChange={(e) => setEditKey(e.target.value)}
                placeholder={config.bolna_api_key_masked || 'Paste your Bolna API key…'}
                className="w-full px-3 py-2.5 border border-gray-200 dark:border-gray-600 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none dark:bg-gray-700 dark:text-white font-mono pr-28"
              />
              {config.bolna_api_key_masked && !editKey && (
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400 dark:text-gray-500 font-mono pointer-events-none">
                  {config.bolna_api_key_masked}
                </span>
              )}
            </div>
            <p className="text-[11px] text-gray-400 mt-1">
              Leave blank to keep the existing key. Shown masked — paste a new key to update.
            </p>
          </div>

          {/* Agent ID */}
          <div>
            <label className="block text-xs font-semibold text-gray-700 dark:text-gray-300 mb-2">
              Bolna Agent ID
            </label>
            <input
              type="text"
              value={editAgentId}
              onChange={(e) => setEditAgentId(e.target.value)}
              placeholder="e.g. 123e4567-e89b-12d3-a456-426655440000"
              className="w-full px-3 py-2.5 border border-gray-200 dark:border-gray-600 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none dark:bg-gray-700 dark:text-white font-mono"
            />
            <p className="text-[11px] text-gray-400 mt-1">
              UUID of the Bolna voice agent (found in the Bolna dashboard under Agents).
            </p>
          </div>

          <div className="pt-2 space-y-3">
            <div className="flex items-center gap-3 flex-wrap">
              <button
                onClick={handleSave}
                disabled={saving || testing}
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors disabled:opacity-50"
              >
                {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                Save Platform Settings
              </button>
              <button
                onClick={handleTestConnection}
                disabled={saving || testing || !config.bolna_configured}
                title={!config.bolna_configured ? 'Save credentials first' : 'Ping Bolna API — no call is placed'}
                className="inline-flex items-center gap-1.5 px-4 py-2 border border-gray-200 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors disabled:opacity-40"
              >
                {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <PhoneCall className="w-3.5 h-3.5" />}
                Test Connection
              </button>
              <button
                onClick={loadConfig}
                disabled={loading || saving}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition-colors disabled:opacity-50"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                Refresh
              </button>
            </div>

            {/* Test result banner */}
            {testResult && (
              <div className={`flex items-start gap-2.5 p-3 rounded-lg border text-sm ${
                testResult.ok
                  ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800 text-green-800 dark:text-green-300'
                  : 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 text-red-800 dark:text-red-300'
              }`}>
                {testResult.ok
                  ? <CheckCircle className="w-4 h-4 shrink-0 mt-0.5 text-green-600 dark:text-green-400" />
                  : <XCircle className="w-4 h-4 shrink-0 mt-0.5 text-red-500" />
                }
                <div>
                  <p className="font-medium">{testResult.ok ? 'Connection successful' : 'Connection failed'}</p>
                  <p className="text-xs mt-0.5 opacity-80">{testResult.message}</p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Info callout */}
      <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-100 dark:border-blue-800/50 rounded-xl p-4 flex items-start gap-3">
        <Info className="w-4 h-4 text-blue-500 shrink-0 mt-0.5" />
        <div className="text-xs text-blue-700 dark:text-blue-300 space-y-1">
          <p className="font-medium">How it works</p>
          <p>
            When a tenant triggers an outbound call, the platform uses these global Bolna credentials —
            not any per-tenant key. Tenants cannot see or modify these credentials.
          </p>
          <p>
            The Bolna agent ID identifies which pre-configured voice agent handles the call. Create and
            configure agents at <span className="font-mono">app.bolna.dev</span>.
          </p>
        </div>
      </div>
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════════
// SHARED SUB-COMPONENTS
// ═══════════════════════════════════════════════════════════════════════════

function StatCard({ icon: Icon, iconBg, iconColor, value, label }) {
  return (
    <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-5">
      <div className="flex items-center gap-3">
        <div className={`p-2.5 rounded-lg ${iconBg}`}>
          <Icon className={`w-5 h-5 ${iconColor}`} />
        </div>
        <div>
          <p className="text-2xl font-bold text-gray-900 dark:text-white">{value}</p>
          <p className="text-sm text-gray-500 dark:text-gray-400">{label}</p>
        </div>
      </div>
    </div>
  );
}

function IntegrationBadge({ label, active, enabled = true }) {
  if (!enabled) {
    return (
      <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-gray-100 text-gray-400 dark:bg-gray-700 dark:text-gray-500 line-through">
        {label}
      </span>
    );
  }
  return (
    <span
      className={`px-2 py-0.5 rounded text-[10px] font-medium ${
        active
          ? 'bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-400'
          : 'bg-gray-100 text-gray-400 dark:bg-gray-700 dark:text-gray-500'
      }`}
    >
      {label}
    </span>
  );
}

