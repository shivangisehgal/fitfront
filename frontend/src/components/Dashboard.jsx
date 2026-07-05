import React, { useState, useEffect } from 'react';
import {
  Phone, CalendarCheck, AlertTriangle, Clock, Users, Activity, Zap,
} from 'lucide-react';
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import { apiFetch } from '../lib/api';
import TestDataToggle from './ui/TestDataToggle';

// ── Tiny sparkline for KPI cards ──────────────────────────────────────────────

function Sparkline({ data, dataKey = 'count', color = '#6366f1', type = 'area', uid }) {
  const safe = data && data.length >= 2 ? data : [{ [dataKey]: 0 }, { [dataKey]: 0 }];
  const gradId = `spark-${uid}`;

  if (type === 'bar') {
    return (
      <BarChart width={80} height={38} data={safe} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
        <Bar dataKey={dataKey} fill={color} radius={[2, 2, 0, 0]} isAnimationActive={false} />
      </BarChart>
    );
  }

  return (
    <AreaChart width={80} height={38} data={safe} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="5%" stopColor={color} stopOpacity={0.35} />
          <stop offset="95%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <Area
        type="monotone"
        dataKey={dataKey}
        stroke={color}
        strokeWidth={1.5}
        fill={`url(#${gradId})`}
        dot={false}
        isAnimationActive={false}
      />
    </AreaChart>
  );
}

// ── KPI card ─────────────────────────────────────────────────────────────────

function KpiCard({ icon: Icon, label, value, subtitle, sparkData, sparkColor = '#6366f1', sparkType = 'area', uid, badge, badgeClass }) {
  return (
    <div className="bg-white dark:bg-zinc-900/70 rounded-xl border border-gray-100 dark:border-zinc-800 p-5 card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 mb-2.5">
            <Icon className="w-3.5 h-3.5 text-gray-400 dark:text-gray-500 shrink-0" />
            <span className="text-xs font-medium text-gray-500 dark:text-gray-400 truncate">{label}</span>
          </div>
          <p className="text-2xl font-bold text-gray-900 dark:text-white tracking-tight leading-none">{value}</p>
          {subtitle && (
            <p className="text-xs text-gray-400 dark:text-white/30 mt-1.5">{subtitle}</p>
          )}
          {badge && (
            <span className={`inline-flex items-center text-xs font-medium px-2 py-0.5 rounded-md mt-2 ${badgeClass}`}>
              {badge}
            </span>
          )}
        </div>
        {sparkData && (
          <div className="shrink-0 opacity-70 mt-0.5">
            <Sparkline data={sparkData} color={sparkColor} type={sparkType} uid={uid} />
          </div>
        )}
      </div>
    </div>
  );
}

// ── Chart wrapper card ────────────────────────────────────────────────────────

function ChartCard({ title, subtitle, children }) {
  return (
    <div className="bg-white dark:bg-zinc-900/70 rounded-xl border border-gray-100 dark:border-zinc-800 p-5">
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-white">{title}</h3>
        {subtitle && (
          <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{subtitle}</p>
        )}
      </div>
      {children}
    </div>
  );
}

// ── Shared custom tooltip ─────────────────────────────────────────────────────

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white dark:bg-zinc-800 border border-gray-200 dark:border-zinc-700 rounded-lg px-3 py-2 shadow-lg text-xs">
      <p className="font-medium text-gray-600 dark:text-gray-300 mb-1">{label}</p>
      {payload.map((p, i) => (
        <p key={i} style={{ color: p.color ?? p.fill }} className="font-semibold">
          {p.name}: {p.value}
        </p>
      ))}
    </div>
  );
}

// ── Loading skeleton ──────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div className="p-5 md:p-8 space-y-6">
      <div className="h-8 w-48 rounded-xl animate-shimmer" />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {[0, 1, 2, 3, 4, 5].map(i => (
          <div key={i} className="h-28 rounded-xl animate-shimmer" />
        ))}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {[0, 1, 2, 3].map(i => (
          <div key={i} className="h-72 rounded-xl animate-shimmer" />
        ))}
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showTestData, setShowTestData] = useState(false);

  useEffect(() => {
    fetchStats(showTestData);
    const interval = setInterval(() => fetchStats(showTestData), 30000);
    return () => clearInterval(interval);
  }, [showTestData]);

  async function fetchStats(includeTest = false) {
    try {
      const url = includeTest
        ? '/api/dashboard/stats?include_test=true'
        : '/api/dashboard/stats';
      const data = await apiFetch(url);
      setStats(data);
    } catch (err) {
      console.error('Failed to fetch stats:', err);
    } finally {
      setLoading(false);
    }
  }

  if (loading) return <LoadingSkeleton />;

  if (!stats) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-center p-8">
        <div className="w-12 h-12 rounded-2xl bg-red-100 dark:bg-red-900/30 flex items-center justify-center">
          <AlertTriangle className="w-6 h-6 text-red-500" />
        </div>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Unable to load dashboard data.<br />Is the backend running?
        </p>
      </div>
    );
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  const formatDuration = (secs) => {
    if (!secs) return '0s';
    const m = Math.floor(secs / 60);
    const s = Math.round(secs % 60);
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  };

  // Detect dark mode for Recharts colors
  const isDark = document.documentElement.classList.contains('dark');
  const gridStroke = isDark ? '#3f3f46' : '#f3f4f6';
  const axisColor = isDark ? '#71717a' : '#9ca3af';

  // Escalation badge
  const escalationBadge =
    stats.escalation_rate <= 10
      ? { text: '✓ Low', cls: 'bg-green-50 dark:bg-green-950/40 text-green-700 dark:text-green-400' }
      : stats.escalation_rate <= 20
        ? { text: 'Moderate', cls: 'bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-400' }
        : { text: 'High', cls: 'bg-red-50 dark:bg-red-950/40 text-red-700 dark:text-red-400' };

  // Outcomes donut data
  const outcomeColors = {
    BOOKED: '#6366f1',
    ESCALATED: '#f59e0b',
    INQUIRY: '#94a3b8',
    CANCELLED: '#ef4444',
    ABANDONED: '#f97316',
  };

  const outcomesData = Object.entries(stats.outcomes_breakdown || {})
    .filter(([, v]) => v > 0)
    .map(([key, value]) => ({
      name: key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
      value,
      color: outcomeColors[key] || '#94a3b8',
    }));

  const totalOutcomes = outcomesData.reduce((sum, d) => sum + d.value, 0);

  // Safe guard: ensure at least 2 points for area/bar charts
  const safeCpd = stats.calls_per_day?.length ? stats.calls_per_day : [{ day: '–', count: 0 }, { day: '–', count: 0 }];
  const safeApd = stats.appointments_per_day?.length ? stats.appointments_per_day : [{ day: '–', count: 0 }, { day: '–', count: 0 }];
  const safeNcd = stats.new_callers_per_day?.length ? stats.new_callers_per_day : [{ day: '–', count: 0 }, { day: '–', count: 0 }];

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="p-5 md:p-8 space-y-6 animate-fade-in">

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl md:text-2xl font-bold text-gray-900 dark:text-white">Overview</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">Real-time snapshot of your AI front desk</p>
        </div>
        <div className="flex items-center gap-3">
          <TestDataToggle enabled={showTestData} onChange={setShowTestData} />
          <div className={`inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm font-medium border ${
            stats.agent_active
              ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800 text-green-700 dark:text-green-400'
              : 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 text-red-700 dark:text-red-400'
          }`}>
            <span className={`w-2 h-2 rounded-full ${stats.agent_active ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`} />
            {stats.agent_active ? 'Agent Active' : 'Agent Offline'}
          </div>
        </div>
      </div>

      {/* ── KPI Grid ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <KpiCard
          icon={Phone}
          label="Today's Calls"
          value={stats.today_calls}
          subtitle="Handled by AI agent"
          sparkData={safeCpd}
          sparkColor="#6366f1"
          sparkType="bar"
          uid="calls-today"
        />
        <KpiCard
          icon={CalendarCheck}
          label="Sessions This Week"
          value={stats.week_appointments_booked}
          subtitle="AI-booked sessions & classes"
          sparkData={safeApd}
          sparkColor="#8b5cf6"
          sparkType="area"
          uid="appts-week"
        />
        <KpiCard
          icon={Users}
          label="New Members This Week"
          value={stats.new_contacts_week ?? 0}
          subtitle="New prospects & members"
          sparkData={safeNcd.slice(-7)}
          sparkColor="#14b8a6"
          sparkType="area"
          uid="new-contacts"
        />
        <KpiCard
          icon={AlertTriangle}
          label="Escalation Rate"
          value={`${stats.escalation_rate}%`}
          subtitle="Last 30 days"
          badge={escalationBadge.text}
          badgeClass={escalationBadge.cls}
          uid="escalation"
        />
        <KpiCard
          icon={Clock}
          label="Avg Call Duration"
          value={formatDuration(stats.avg_call_duration)}
          subtitle="Last 30 days"
          uid="avg-duration"
        />
        <KpiCard
          icon={Activity}
          label="Total Calls (30 days)"
          value={stats.total_calls_30d ?? 0}
          subtitle="All handled calls"
          sparkData={safeCpd}
          sparkColor="#0ea5e9"
          sparkType="area"
          uid="total-calls-30d"
        />
      </div>

      {/* ── Charts Grid ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

        {/* Calls per Day */}
        <ChartCard title="Calls per Day" subtitle="This week">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={safeCpd} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} vertical={false} />
              <XAxis
                dataKey="day"
                tick={{ fontSize: 11, fill: axisColor }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                allowDecimals={false}
                tick={{ fontSize: 11, fill: axisColor }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(99,102,241,0.06)' }} />
              <Bar dataKey="count" fill="#6366f1" radius={[4, 4, 0, 0]} name="Calls" />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* New Unique Interactions */}
        <ChartCard title="New Member Inquiries" subtitle="Last 14 days — new callers per day">
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={safeNcd} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
              <defs>
                <linearGradient id="tealGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#14b8a6" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="#14b8a6" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} vertical={false} />
              <XAxis
                dataKey="day"
                tick={{ fontSize: 10, fill: axisColor }}
                axisLine={false}
                tickLine={false}
                interval={1}
              />
              <YAxis
                allowDecimals={false}
                tick={{ fontSize: 11, fill: axisColor }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip content={<ChartTooltip />} />
              <Area
                type="monotone"
                dataKey="count"
                stroke="#14b8a6"
                strokeWidth={2}
                fill="url(#tealGrad)"
                dot={{ r: 3, fill: '#14b8a6', strokeWidth: 0 }}
                activeDot={{ r: 5 }}
                name="New Callers"
              />
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* AI-Booked Appointments per Day */}
        <ChartCard title="AI-Booked Sessions" subtitle="This week — by session date">
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={safeApd} margin={{ top: 4, right: 4, left: -22, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} vertical={false} />
              <XAxis
                dataKey="day"
                tick={{ fontSize: 11, fill: axisColor }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                allowDecimals={false}
                tick={{ fontSize: 11, fill: axisColor }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(139,92,246,0.06)' }} />
              <Bar dataKey="count" fill="#8b5cf6" radius={[4, 4, 0, 0]} name="Sessions" />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* Call Outcomes Donut */}
        <ChartCard title="Call Outcomes" subtitle="All-time breakdown">
          {outcomesData.length > 0 ? (
            <div className="flex flex-col items-center gap-3">
              <div className="relative" style={{ width: 160, height: 160 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={outcomesData}
                      cx="50%"
                      cy="50%"
                      innerRadius={48}
                      outerRadius={72}
                      paddingAngle={2}
                      dataKey="value"
                      isAnimationActive={false}
                    >
                      {outcomesData.map((entry, index) => (
                        <Cell key={index} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip
                      wrapperStyle={{ zIndex: 100 }}
                      content={({ active, payload }) => {
                        if (!active || !payload?.length) return null;
                        const d = payload[0].payload;
                        const pct = totalOutcomes > 0 ? ((d.value / totalOutcomes) * 100).toFixed(1) : 0;
                        return (
                          <div className="bg-white dark:bg-zinc-800 border border-gray-200 dark:border-zinc-700 rounded-lg px-3 py-2 shadow-lg text-xs">
                            <p className="font-semibold" style={{ color: d.color }}>{d.name}</p>
                            <p className="text-gray-700 dark:text-gray-300">{d.value} calls · {pct}%</p>
                          </div>
                        );
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
                {/* Center label */}
                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                  <span className="text-xl font-bold text-gray-900 dark:text-white leading-none">{totalOutcomes}</span>
                  <span className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">total</span>
                </div>
              </div>
              {/* Legend below chart */}
              <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs w-full">
                {outcomesData.map((d) => (
                  <div key={d.name} className="flex items-center gap-1.5 min-w-0">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: d.color }} />
                    <span className="text-gray-600 dark:text-gray-400 truncate">{d.name}</span>
                    <span className="font-semibold text-gray-900 dark:text-white tabular-nums ml-auto">{d.value}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center h-[220px] text-sm text-gray-400 dark:text-gray-500">
              No call data yet
            </div>
          )}
        </ChartCard>

      </div>

      {/* Tip */}
      <div className="rounded-xl border border-indigo-100 dark:border-indigo-900/40 bg-indigo-50/60 dark:bg-indigo-950/20 p-4">
        <div className="flex items-start gap-3">
          <Zap className="w-4 h-4 text-indigo-500 mt-0.5 shrink-0" />
          <div>
            <p className="text-sm font-semibold text-gray-900 dark:text-white">Your front desk is learning</p>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
              Add programs, pricing, and FAQs in Studio Info to improve booking accuracy and reduce escalations.
            </p>
          </div>
        </div>
      </div>

    </div>
  );
}
