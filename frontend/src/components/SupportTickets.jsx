import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  HelpCircle,
  Plus,
  ChevronDown,
  ChevronUp,
  Clock,
  CheckCircle2,
  AlertCircle,
  Loader2,
  MessageSquare,
  X,
  Send,
  RotateCcw,
  User,
  Shield,
} from 'lucide-react';
import { apiFetch } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';
import ThemedSelect from './ui/ThemedSelect';

const STATUS_COLORS = {
  OPEN: 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400',
  IN_PROGRESS: 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400',
  RESOLVED: 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400',
  CLOSED: 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400',
  REOPENED: 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400',
};

const STATUS_LABELS = {
  OPEN: 'Open',
  IN_PROGRESS: 'In Progress',
  RESOLVED: 'Resolved',
  CLOSED: 'Closed',
  REOPENED: 'Reopened',
};

const PRIORITY_COLORS = {
  LOW: 'text-gray-500',
  MEDIUM: 'text-blue-500',
  HIGH: 'text-orange-500',
  URGENT: 'text-red-500',
};

const PRIORITY_LABELS = {
  LOW: 'Low',
  MEDIUM: 'Medium',
  HIGH: 'High',
  URGENT: 'Urgent',
};

const CATEGORY_LABELS = {
  GENERAL: 'General',
  BILLING: 'Billing',
  TECHNICAL: 'Technical',
  ACCOUNT: 'Account',
  FEATURE_REQUEST: 'Feature Request',
  VOICE_SETUP: 'Voice Setup',
  OTHER: 'Other',
};

const CATEGORIES = [
  { value: 'GENERAL', label: 'General' },
  { value: 'BILLING', label: 'Billing' },
  { value: 'TECHNICAL', label: 'Technical' },
  { value: 'FEATURE_REQUEST', label: 'Feature Request' },
  { value: 'VOICE_SETUP', label: 'Voice Setup' },
  { value: 'OTHER', label: 'Other' },
];

const PRIORITIES = [
  { value: 'LOW', label: 'Low' },
  { value: 'MEDIUM', label: 'Medium' },
  { value: 'HIGH', label: 'High' },
  { value: 'URGENT', label: 'Urgent' },
];

const STATUS_TABS = ['ALL', 'OPEN', 'IN_PROGRESS', 'RESOLVED', 'REOPENED', 'CLOSED'];

export default function SupportTickets() {
  const { user } = useAuth();
  const tz = user?.timezone || 'America/Chicago';
  const [tickets, setTickets] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [expandedId, setExpandedId] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({ subject: '', body: '', category: 'GENERAL', priority: 'MEDIUM' });

  const fetchTickets = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (statusFilter !== 'ALL') params.set('status', statusFilter);
      const data = await apiFetch(`/api/support/tickets?${params}`);
      setTickets(data.tickets || []);
      setTotal(data.total || 0);
    } catch (err) {
      console.error('Failed to fetch tickets:', err);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => { fetchTickets(); }, [fetchTickets]);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!form.subject.trim() || !form.body.trim()) return;
    setSubmitting(true);
    try {
      await apiFetch('/api/support/tickets', { method: 'POST', body: form });
      setForm({ subject: '', body: '', category: 'GENERAL', priority: 'MEDIUM' });
      setShowForm(false);
      fetchTickets();
    } catch (err) {
      console.error('Failed to create ticket:', err);
    } finally {
      setSubmitting(false);
    }
  }

  // When a ticket is expanded, fetch its full conversation thread
  async function handleExpand(ticketId) {
    if (expandedId === ticketId) {
      setExpandedId(null);
      return;
    }
    setExpandedId(ticketId);
    // Fetch the ticket detail with messages
    try {
      const detail = await apiFetch(`/api/support/tickets/${ticketId}`);
      // Merge the messages into the ticket list
      setTickets((prev) =>
        prev.map((t) => (t.id === ticketId ? { ...t, ...detail } : t))
      );
    } catch (err) {
      console.error('Failed to fetch ticket detail:', err);
    }
  }

  return (
    <div className="p-4 md:p-8 max-w-4xl mx-auto space-y-4 md:space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-indigo-50 dark:bg-indigo-900/30 rounded-xl flex items-center justify-center">
            <HelpCircle className="w-5 h-5 text-indigo-500" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Support</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Submit and track help requests
            </p>
          </div>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-500 text-white rounded-lg text-sm font-medium hover:bg-indigo-600 transition-colors"
        >
          {showForm ? <X className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
          {showForm ? 'Cancel' : 'New Ticket'}
        </button>
      </div>

      {/* Create ticket form */}
      {showForm && (
        <form onSubmit={handleSubmit} className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-6 space-y-4">
          <h3 className="font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            <Send className="w-4 h-4 text-indigo-500" />
            Submit a Support Ticket
          </h3>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Subject</label>
            <input
              type="text"
              value={form.subject}
              onChange={(e) => setForm({ ...form, subject: e.target.value })}
              placeholder="Brief description of your issue"
              maxLength={200}
              className="w-full px-3 py-2 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-white/30 focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all"
              required
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Category</label>
              <ThemedSelect
                value={form.category}
                onChange={(val) => setForm({ ...form, category: val })}
                options={CATEGORIES}
                className="w-full"
                menuClassName="w-full"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Priority</label>
              <ThemedSelect
                value={form.priority}
                onChange={(val) => setForm({ ...form, priority: val })}
                options={PRIORITIES}
                className="w-full"
                menuClassName="w-full"
              />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Description</label>
            <textarea
              value={form.body}
              onChange={(e) => setForm({ ...form, body: e.target.value })}
              placeholder="Describe your issue in detail..."
              rows={4}
              maxLength={5000}
              className="w-full px-3 py-2 border border-gray-200 dark:border-white/10 rounded-xl text-sm bg-gray-50 dark:bg-white/5 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-white/30 resize-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-500 outline-none transition-all"
              required
            />
          </div>
          <div className="flex justify-end">
            <button
              type="submit"
              disabled={submitting || !form.subject.trim() || !form.body.trim()}
              className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-500 text-white rounded-lg text-sm font-medium hover:bg-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
              Submit Ticket
            </button>
          </div>
        </form>
      )}

      {/* Status filter tabs */}
      <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1 overflow-x-auto">
        {STATUS_TABS.map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors whitespace-nowrap ${
              statusFilter === s
                ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-white shadow-sm'
                : 'text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
            }`}
          >
            {STATUS_LABELS[s] || 'All'}
          </button>
        ))}
      </div>

      {/* Ticket list */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-indigo-500" />
        </div>
      ) : tickets.length === 0 ? (
        <div className="text-center py-12">
          <MessageSquare className="w-12 h-12 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
          <p className="text-gray-500 dark:text-gray-400">No tickets yet</p>
          <p className="text-sm text-gray-400 dark:text-gray-500 mt-1">
            Click "New Ticket" to submit a support request
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {tickets.map((ticket) => {
            const isExpanded = expandedId === ticket.id;
            return (
              <div
                key={ticket.id}
                className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden"
              >
                {/* Ticket header */}
                <button
                  onClick={() => handleExpand(ticket.id)}
                  className="w-full p-4 flex items-center gap-3 text-left hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-gray-900 dark:text-white text-sm truncate">
                        {ticket.subject}
                      </span>
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium ${STATUS_COLORS[ticket.status] || STATUS_COLORS.OPEN}`}>
                        {STATUS_LABELS[ticket.status] || ticket.status}
                      </span>
                      <span className={`text-[10px] font-medium ${PRIORITY_COLORS[ticket.priority] || ''}`}>
                        {PRIORITY_LABELS[ticket.priority] || ticket.priority}
                      </span>
                      {(ticket.message_count > 0) && (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-[10px] text-gray-500 dark:text-gray-400">
                          <MessageSquare className="w-2.5 h-2.5" />
                          {ticket.message_count}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-1">
                      <span className="text-xs text-gray-400">
                        {CATEGORY_LABELS[ticket.category] || ticket.category?.replace(/_/g, ' ')}
                      </span>
                      <span className="text-xs text-gray-400 flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {new Date(ticket.created_at).toLocaleDateString('en-US', { timeZone: tz, month: 'short', day: 'numeric', year: 'numeric' })}
                      </span>
                    </div>
                  </div>
                  {isExpanded ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
                </button>

                {/* Expanded: conversation thread */}
                {isExpanded && (
                  <TicketThread
                    ticket={ticket}
                    tz={tz}
                    onUpdate={() => {
                      fetchTickets();
                      // Re-fetch this ticket's detail
                      handleExpand(ticket.id);
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}


// ── Ticket conversation thread ────────────────────────────────────────────

function TicketThread({ ticket, tz, onUpdate }) {
  const [msgText, setMsgText] = useState('');
  const [sending, setSending] = useState(false);
  const [reopenReason, setReopenReason] = useState('');
  const [showReopen, setShowReopen] = useState(false);
  const [reopening, setReopening] = useState(false);
  const threadRef = useRef(null);

  const messages = ticket.messages || [];
  const canReply = !['CLOSED'].includes(ticket.status);
  const canReopen = ['RESOLVED', 'CLOSED'].includes(ticket.status);

  // Scroll to bottom when messages update
  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
    }
  }, [messages.length]);

  async function handleSendMessage(e) {
    e.preventDefault();
    if (!msgText.trim()) return;
    setSending(true);
    try {
      await apiFetch(`/api/support/tickets/${ticket.id}/messages`, {
        method: 'POST',
        body: { body: msgText.trim() },
      });
      setMsgText('');
      onUpdate();
    } catch (err) {
      console.error('Failed to send message:', err);
    } finally {
      setSending(false);
    }
  }

  async function handleReopen(e) {
    e.preventDefault();
    if (!reopenReason.trim()) return;
    setReopening(true);
    try {
      await apiFetch(`/api/support/tickets/${ticket.id}/reopen`, {
        method: 'POST',
        body: { reason: reopenReason.trim() },
      });
      setReopenReason('');
      setShowReopen(false);
      onUpdate();
    } catch (err) {
      console.error('Failed to reopen ticket:', err);
    } finally {
      setReopening(false);
    }
  }

  return (
    <div className="border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
      {/* Original ticket body */}
      <div className="px-4 pt-4 pb-2">
        <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-1">Description</h4>
        <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{ticket.body}</p>
      </div>

      {/* Conversation thread */}
      {messages.length > 0 && (
        <div className="px-4 pt-2">
          <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-2 flex items-center gap-1.5">
            <MessageSquare className="w-3 h-3" />
            Conversation ({messages.length})
          </h4>
          <div
            ref={threadRef}
            className="space-y-2 max-h-80 overflow-y-auto pr-1"
          >
            {messages.map((msg) => {
              const isAdmin = msg.sender_type === 'ADMIN';
              return (
                <div key={msg.id} className={`flex ${isAdmin ? 'justify-start' : 'justify-end'}`}>
                  <div className={`max-w-[80%] rounded-2xl px-3.5 py-2.5 ${
                    isAdmin
                      ? 'bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-bl-md'
                      : 'bg-indigo-500 text-white rounded-br-md'
                  }`}>
                    <div className={`flex items-center gap-1.5 mb-1 text-[10px] font-medium ${
                      isAdmin ? 'text-blue-600 dark:text-blue-400' : 'text-indigo-100'
                    }`}>
                      {isAdmin ? <Shield className="w-2.5 h-2.5" /> : <User className="w-2.5 h-2.5" />}
                      {msg.sender_name}
                    </div>
                    <p className={`text-sm whitespace-pre-wrap break-words ${
                      isAdmin ? 'text-blue-800 dark:text-blue-200' : ''
                    }`}>
                      {msg.body}
                    </p>
                    <p className={`text-[10px] mt-1.5 ${
                      isAdmin ? 'text-blue-400 dark:text-blue-500' : 'text-indigo-200'
                    }`}>
                      {formatTicketDate(msg.created_at, tz)}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Reply input */}
      {canReply && (
        <form onSubmit={handleSendMessage} className="p-4 flex gap-2">
          <input
            type="text"
            value={msgText}
            onChange={(e) => setMsgText(e.target.value)}
            placeholder="Type a message..."
            maxLength={5000}
            className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-white text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
          />
          <button
            type="submit"
            disabled={sending || !msgText.trim()}
            className="px-3 py-2 bg-indigo-500 text-white rounded-lg text-sm font-medium hover:bg-indigo-600 disabled:opacity-50 transition-colors"
          >
            {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </form>
      )}

      {/* Reopen button (for resolved/closed tickets) */}
      {canReopen && (
        <div className="px-4 pb-4">
          {!showReopen ? (
            <button
              onClick={() => setShowReopen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-2 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-lg text-xs font-medium hover:bg-red-100 dark:hover:bg-red-900/40 transition-colors"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Not satisfied? Reopen this ticket
            </button>
          ) : (
            <form onSubmit={handleReopen} className="space-y-2">
              <textarea
                value={reopenReason}
                onChange={(e) => setReopenReason(e.target.value)}
                placeholder="Tell us why this wasn't resolved to your satisfaction..."
                rows={2}
                maxLength={2000}
                className="w-full px-3 py-2 border border-red-300 dark:border-red-700 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-white text-sm resize-none focus:ring-2 focus:ring-red-500 focus:border-red-500"
                required
              />
              <div className="flex items-center gap-2">
                <button
                  type="submit"
                  disabled={reopening || !reopenReason.trim()}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-red-500 text-white rounded-lg text-xs font-medium hover:bg-red-600 disabled:opacity-50 transition-colors"
                >
                  {reopening ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
                  Reopen Ticket
                </button>
                <button
                  type="button"
                  onClick={() => { setShowReopen(false); setReopenReason(''); }}
                  className="px-3 py-1.5 text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {/* Resolved info */}
      {ticket.resolved_at && (
        <div className="px-4 pb-3">
          <p className="text-xs text-gray-400 flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3" />
            Resolved {formatTicketDate(ticket.resolved_at, tz)}
            {ticket.resolved_by && <span>by {ticket.resolved_by}</span>}
          </p>
        </div>
      )}
    </div>
  );
}


// ── Helpers ────────────────────────────────────────────────────────────────

function formatTicketDate(isoStr, tz) {
  if (!isoStr) return '';
  try {
    return new Date(isoStr).toLocaleString('en-US', {
      timeZone: tz,
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    });
  } catch {
    return new Date(isoStr).toLocaleDateString();
  }
}
