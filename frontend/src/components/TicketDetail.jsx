import React, { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import {
  ArrowLeft,
  Clock,
  CheckCircle,
  XCircle,
  RotateCcw,
  AlertTriangle,
  Send,
  Loader2,
  MessageSquare,
  Building2,
  Tag,
  Calendar,
  User,
  RefreshCw,
  Shield,
} from 'lucide-react';
import { apiFetch } from '../lib/api';
import { useModal } from '../contexts/ModalContext';
import {
  TICKET_STATUS_LABELS,
  TICKET_PRIORITY_LABELS,
  TICKET_CATEGORY_LABELS,
} from '../lib/tenantLabels';

// ── Status / priority config ─────────────────────────────────────────────────

const TICKET_STATUS_CONFIG = {
  OPEN:        { color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400',   dot: 'bg-blue-500' },
  IN_PROGRESS: { color: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400', dot: 'bg-amber-500' },
  RESOLVED:    { color: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400', dot: 'bg-green-500' },
  CLOSED:      { color: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',       dot: 'bg-gray-400' },
  REOPENED:    { color: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400',        dot: 'bg-red-500' },
};

const PRIORITY_CONFIG = {
  LOW:    'text-gray-500 dark:text-gray-400',
  MEDIUM: 'text-blue-600 dark:text-blue-400',
  HIGH:   'text-orange-500 dark:text-orange-400',
  URGENT: 'text-red-600 dark:text-red-400',
};

function fmt(iso, tz = 'America/Chicago') {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', {
    timeZone: tz, month: 'short', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit', hour12: true,
  });
}

function fmtShort(iso, tz = 'America/Chicago') {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', {
    timeZone: tz, month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', hour12: true,
  });
}

// ── Sub-components ───────────────────────────────────────────────────────────

function StatusBadge({ status }) {
  const cfg = TICKET_STATUS_CONFIG[status] || TICKET_STATUS_CONFIG.OPEN;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${cfg.color}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
      {TICKET_STATUS_LABELS[status] || status}
    </span>
  );
}

function MetaItem({ icon: Icon, label, value }) {
  return (
    <div className="flex items-start gap-2.5">
      <div className="w-7 h-7 rounded-lg bg-gray-100 dark:bg-gray-800 flex items-center justify-center shrink-0 mt-0.5">
        <Icon className="w-3.5 h-3.5 text-gray-500 dark:text-gray-400" />
      </div>
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">{label}</p>
        <p className="text-sm font-medium text-gray-800 dark:text-gray-200 mt-0.5">{value}</p>
      </div>
    </div>
  );
}

// SYSTEM event pill — renders as a timeline marker, not a chat bubble
function SystemEvent({ msg, tz }) {
  return (
    <div className="flex items-center gap-3 py-1">
      <div className="flex-1 h-px bg-gray-200 dark:bg-gray-700" />
      <div className="flex items-center gap-1.5 shrink-0 px-3 py-1 rounded-full bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700">
        <RefreshCw className="w-3 h-3 text-gray-400" />
        <span className="text-[11px] text-gray-500 dark:text-gray-400">{msg.body}</span>
        <span className="text-[10px] text-gray-400 dark:text-gray-500 ml-1">{fmtShort(msg.created_at, tz)}</span>
      </div>
      <div className="flex-1 h-px bg-gray-200 dark:bg-gray-700" />
    </div>
  );
}

// Regular chat bubble (TENANT or ADMIN)
function ChatBubble({ msg, tz }) {
  const isAdmin = msg.sender_type === 'ADMIN';
  return (
    <div className={`flex ${isAdmin ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[75%] rounded-2xl px-4 py-3 ${
        isAdmin
          ? 'bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-200 dark:border-indigo-800 rounded-br-md'
          : 'bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-bl-md'
      }`}>
        <div className="flex items-center gap-1.5 mb-1">
          {isAdmin && <Shield className="w-3 h-3 text-indigo-500 shrink-0" />}
          <p className={`text-[11px] font-semibold ${isAdmin ? 'text-indigo-600 dark:text-indigo-400' : 'text-gray-500 dark:text-gray-400'}`}>
            {msg.sender_name}
          </p>
        </div>
        <p className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap leading-relaxed">{msg.body}</p>
        <p className="text-[10px] text-gray-400 mt-1.5">{fmtShort(msg.created_at, tz)}</p>
      </div>
    </div>
  );
}

// Loading skeleton
function TicketSkeleton() {
  return (
    <div className="p-5 md:p-8 space-y-5 max-w-4xl mx-auto animate-pulse">
      <div className="h-4 w-32 bg-gray-200 dark:bg-gray-700 rounded" />
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-6 space-y-4">
        <div className="h-6 w-2/3 bg-gray-200 dark:bg-gray-700 rounded" />
        <div className="h-4 w-1/3 bg-gray-100 dark:bg-gray-800 rounded" />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-10 bg-gray-100 dark:bg-gray-800 rounded-lg" />
          ))}
        </div>
      </div>
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-6 space-y-3">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-4 bg-gray-100 dark:bg-gray-800 rounded" />
        ))}
      </div>
    </div>
  );
}


// ── Main component ───────────────────────────────────────────────────────────

export default function TicketDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { toast } = useModal();
  const { user } = useAuth();
  const tz = user?.timezone || 'America/Chicago';
  const messagesEndRef = useRef(null);

  const [ticket, setTicket] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [replyText, setReplyText] = useState('');
  const [sendingReply, setSendingReply] = useState(false);

  useEffect(() => { loadTicket(true); }, [id]);

  async function loadTicket(initial = false) {
    if (initial) setLoading(true);
    setError(null);
    try {
      const data = await apiFetch(`/api/admin/support/tickets/${id}`);
      setTicket(data);
    } catch (err) {
      setError(err.message || 'Failed to load ticket.');
    } finally {
      setLoading(false);
    }
  }

  async function updateStatus(newStatus) {
    setActionLoading(true);
    try {
      const updated = await apiFetch(`/api/admin/support/tickets/${id}`, {
        method: 'PATCH',
        body: { status: newStatus },
      });
      setTicket(updated);
      toast.success(`Ticket marked as ${TICKET_STATUS_LABELS[newStatus] || newStatus}`);
    } catch {
      toast.error('Failed to update ticket status.');
    } finally {
      setActionLoading(false);
    }
  }

  async function sendReply() {
    if (!replyText.trim()) return;
    setSendingReply(true);
    try {
      const updated = await apiFetch(`/api/admin/support/tickets/${id}/messages`, {
        method: 'POST',
        body: { body: replyText.trim() },
      });
      setTicket(updated);
      setReplyText('');
      // Scroll to bottom after reply
      setTimeout(() => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
    } catch {
      toast.error('Failed to send reply.');
    } finally {
      setSendingReply(false);
    }
  }

  if (loading) return <TicketSkeleton />;

  if (error) {
    return (
      <div className="p-5 md:p-8 max-w-4xl mx-auto space-y-4">
        <button
          onClick={() => navigate('/admin/tenants')}
          className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" /> Back to Admin
        </button>
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-2xl p-8 text-center">
          <AlertTriangle className="w-10 h-10 text-red-400 mx-auto mb-3" />
          <p className="text-red-700 dark:text-red-400 font-medium">{error}</p>
          <button
            onClick={() => loadTicket(true)}
            className="mt-4 px-4 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!ticket) return null;

  const messages = ticket.messages || [];
  const canReply = ticket.status !== 'CLOSED';
  const statusCfg = TICKET_STATUS_CONFIG[ticket.status] || TICKET_STATUS_CONFIG.OPEN;

  return (
    <div className="p-5 md:p-8 space-y-5 max-w-4xl mx-auto">

      {/* Back navigation */}
      <button
        onClick={() => navigate('/admin/tenants')}
        className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
      >
        <ArrowLeft className="w-4 h-4" /> Back to Admin
      </button>

      {/* ── Header card ─────────────────────────────────────────────────── */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden">
        {/* Status-colored top stripe */}
        <div className={`h-1.5 bg-gradient-to-r ${
          ticket.status === 'OPEN' ? 'from-blue-400 to-indigo-500' :
          ticket.status === 'IN_PROGRESS' ? 'from-amber-400 to-orange-500' :
          ticket.status === 'RESOLVED' ? 'from-emerald-400 to-green-500' :
          ticket.status === 'REOPENED' ? 'from-red-400 to-rose-500' :
          'from-gray-400 to-gray-500'
        }`} />

        <div className="p-6">
          {/* Title + actions */}
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="flex-1 min-w-0">
              <h1 className="text-xl font-bold text-gray-900 dark:text-white leading-snug">
                {ticket.subject}
              </h1>
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                <StatusBadge status={ticket.status} />
                <span className={`text-xs font-semibold ${PRIORITY_CONFIG[ticket.priority] || 'text-gray-500'}`}>
                  {TICKET_PRIORITY_LABELS[ticket.priority] || ticket.priority} priority
                </span>
                <span className="text-xs text-gray-400 dark:text-gray-500">
                  {TICKET_CATEGORY_LABELS[ticket.category] || ticket.category}
                </span>
              </div>
            </div>

            {/* Action buttons */}
            <div className="flex items-center gap-2 flex-wrap shrink-0">
              {(ticket.status === 'OPEN' || ticket.status === 'REOPENED') && (
                <ActionBtn
                  icon={Clock}
                  label="Start Working"
                  color="amber"
                  loading={actionLoading}
                  onClick={() => updateStatus('IN_PROGRESS')}
                />
              )}
              {(ticket.status === 'OPEN' || ticket.status === 'IN_PROGRESS' || ticket.status === 'REOPENED') && (
                <ActionBtn
                  icon={CheckCircle}
                  label="Resolve"
                  color="green"
                  loading={actionLoading}
                  onClick={() => updateStatus('RESOLVED')}
                />
              )}
              {ticket.status === 'RESOLVED' && (
                <ActionBtn
                  icon={XCircle}
                  label="Close"
                  color="gray"
                  loading={actionLoading}
                  onClick={() => updateStatus('CLOSED')}
                />
              )}
              {(ticket.status === 'RESOLVED' || ticket.status === 'CLOSED') && (
                <ActionBtn
                  icon={RotateCcw}
                  label="Reopen"
                  color="red"
                  loading={actionLoading}
                  onClick={() => updateStatus('REOPENED')}
                />
              )}
            </div>
          </div>

          {/* Meta grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-5 mt-6 pt-5 border-t border-gray-100 dark:border-white/5">
            <MetaItem icon={Building2} label="Tenant" value={ticket.tenant_name || ticket.tenant_slug || '—'} />
            <MetaItem icon={Calendar} label="Submitted" value={fmt(ticket.created_at, tz)} />
            <MetaItem
              icon={CheckCircle}
              label="Resolved at"
              value={ticket.resolved_at ? fmt(ticket.resolved_at, tz) : '—'}
            />
            <MetaItem icon={User} label="Resolved by" value={ticket.resolved_by || '—'} />
          </div>

          {/* Admin notes (if set) */}
          {ticket.admin_notes && (
            <div className="mt-4 p-3 rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800/50">
              <p className="text-xs font-semibold text-amber-700 dark:text-amber-400 mb-1">Admin Notes</p>
              <p className="text-sm text-amber-800 dark:text-amber-300 whitespace-pre-wrap">{ticket.admin_notes}</p>
            </div>
          )}
        </div>
      </div>

      {/* ── Description ─────────────────────────────────────────────────── */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-6">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500 mb-3">
          Description
        </h2>
        <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap leading-relaxed">
          {ticket.body}
        </p>
      </div>

      {/* ── Conversation & History ───────────────────────────────────────── */}
      <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-6">
        <div className="flex items-center gap-2 mb-5">
          <MessageSquare className="w-4 h-4 text-gray-400" />
          <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
            Conversation & History
          </h2>
          {messages.length > 0 && (
            <span className="ml-auto text-xs text-gray-400">{messages.length} event{messages.length !== 1 ? 's' : ''}</span>
          )}
        </div>

        {messages.length === 0 ? (
          <div className="text-center py-8">
            <MessageSquare className="w-8 h-8 text-gray-200 dark:text-gray-700 mx-auto mb-2" />
            <p className="text-sm text-gray-400">No messages yet.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {messages.map((msg) =>
              msg.sender_type === 'SYSTEM'
                ? <SystemEvent key={msg.id} msg={msg} tz={tz} />
                : <ChatBubble key={msg.id} msg={msg} tz={tz} />
            )}
            <div ref={messagesEndRef} />
          </div>
        )}

        {/* Reply input */}
        {canReply && (
          <div className="mt-5 pt-4 border-t border-gray-100 dark:border-white/5">
            <div className="flex gap-2">
              <textarea
                value={replyText}
                onChange={(e) => setReplyText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendReply();
                  }
                }}
                placeholder="Type a reply… (Enter to send, Shift+Enter for new line)"
                rows={2}
                className="flex-1 px-3 py-2.5 border border-gray-200 dark:border-gray-600 rounded-xl bg-white dark:bg-gray-900 text-sm text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none resize-none"
              />
              <button
                onClick={sendReply}
                disabled={sendingReply || !replyText.trim()}
                className="px-4 py-2.5 bg-indigo-600 text-white rounded-xl text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 transition-colors self-end"
              >
                {sendingReply ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
              </button>
            </div>
            <p className="text-[10px] text-gray-400 mt-1.5">Replying as admin · tenant will see this message</p>
          </div>
        )}

        {ticket.status === 'CLOSED' && (
          <div className="mt-5 pt-4 border-t border-gray-100 dark:border-white/5 text-center">
            <p className="text-sm text-gray-400">This ticket is closed. Reopen it to send a reply.</p>
          </div>
        )}
      </div>
    </div>
  );
}


// ── ActionBtn ────────────────────────────────────────────────────────────────

function ActionBtn({ icon: Icon, label, color, loading, onClick }) {
  const colorMap = {
    green: 'bg-green-50 text-green-700 hover:bg-green-100 border-green-200 dark:bg-green-900/30 dark:text-green-400 dark:hover:bg-green-900/50 dark:border-green-800',
    amber: 'bg-amber-50 text-amber-700 hover:bg-amber-100 border-amber-200 dark:bg-amber-900/30 dark:text-amber-400 dark:hover:bg-amber-900/50 dark:border-amber-800',
    red:   'bg-red-50 text-red-700 hover:bg-red-100 border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:hover:bg-red-900/50 dark:border-red-800',
    gray:  'bg-gray-100 text-gray-600 hover:bg-gray-200 border-gray-200 dark:bg-gray-700 dark:text-gray-400 dark:hover:bg-gray-600 dark:border-gray-600',
  };
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors disabled:opacity-50 ${colorMap[color] || colorMap.gray}`}
    >
      {loading
        ? <div className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" />
        : <Icon className="w-3.5 h-3.5" />
      }
      {label}
    </button>
  );
}
