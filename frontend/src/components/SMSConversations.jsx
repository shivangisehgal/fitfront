import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  MessageSquare,
  ArrowLeft,
  RefreshCw,
  AlertCircle,
  Phone,
  Send,
  ArrowDown,
  ArrowUp,
  Clock,
  Inbox,
  Search,
  User,
  Plus,
  X,
} from 'lucide-react';
import { apiFetch } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';
import { formatDateTime, formatRelativeTime as fmtRelative } from '../lib/timezone';
import TestDataToggle from './ui/TestDataToggle';

export default function SMSConversations() {
  const navigate = useNavigate();
  const { callerId: urlCallerId } = useParams();
  const { user } = useAuth();
  const tz = user?.timezone || 'America/Chicago';
  const [conversations, setConversations] = useState([]);
  const [messages, setMessages] = useState([]);
  const [selectedPhone, setSelectedPhone] = useState(null);
  const [selectedName, setSelectedName] = useState(null);
  const [selectedCallerId, setSelectedCallerId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [showTestData, setShowTestData] = useState(false);
  const [search, setSearch] = useState('');

  // Compose state
  const [composeText, setComposeText] = useState('');
  const [sending, setSending] = useState(false);

  // Outbound calling state
  const [calling, setCalling] = useState(false);
  const [callSuccess, setCallSuccess] = useState(false);

  // New conversation modal
  const [showNewConv, setShowNewConv] = useState(false);
  const [newPhone, setNewPhone] = useState('');
  const [newMessage, setNewMessage] = useState('');
  const [newSending, setNewSending] = useState(false);

  const messagesEndRef = useRef(null);
  const composeRef = useRef(null);
  const searchTimeout = useRef(null);
  const initialLoadDone = useRef(false);

  const fetchConversations = useCallback(async (showRefresh = false) => {
    if (showRefresh) setRefreshing(true);
    try {
      const params = new URLSearchParams();
      if (showTestData) params.set('include_test', 'true');
      if (search.trim()) params.set('search', search.trim());
      const url = `/api/sms/conversations${params.toString() ? '?' + params.toString() : ''}`;
      const data = await apiFetch(url);
      setConversations(data || []);
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to load conversations');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [showTestData, search]);

  const fetchMessages = useCallback(async (phone) => {
    setLoadingMessages(true);
    try {
      const params = new URLSearchParams({ client_phone: phone });
      if (showTestData) params.set('include_test', 'true');
      const data = await apiFetch(`/api/sms/messages?${params.toString()}`);
      setMessages(data || []);
      setError(null);
      setTimeout(() => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 100);
    } catch (err) {
      setError(err.message || 'Failed to load messages');
    } finally {
      setLoadingMessages(false);
    }
  }, [showTestData]);

  useEffect(() => {
    if (!initialLoadDone.current) setLoading(true);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    searchTimeout.current = setTimeout(() => {
      fetchConversations().then(() => { initialLoadDone.current = true; });
    }, 300);
    return () => clearTimeout(searchTimeout.current);
  }, [fetchConversations]);

  // Re-fetch open conversation messages when showTestData toggle changes
  useEffect(() => {
    if (selectedPhone) fetchMessages(selectedPhone);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchMessages]);

  function selectConversation(phone, name, callerId) {
    setSelectedPhone(phone);
    setSelectedName(name || null);
    setSelectedCallerId(callerId || null);
    setComposeText('');
    fetchMessages(phone);
    if (callerId) {
      navigate(`/sms/${callerId}`, { replace: true });
    }
  }

  function goBack() {
    setSelectedPhone(null);
    setSelectedName(null);
    setSelectedCallerId(null);
    setMessages([]);
    setComposeText('');
    navigate('/sms', { replace: true });
  }

  // Sync conversation open/close with URL
  useEffect(() => {
    if (!urlCallerId) {
      // Browser navigated back to /sms — close any open thread
      if (selectedPhone) {
        setSelectedPhone(null);
        setSelectedName(null);
        setSelectedCallerId(null);
        setMessages([]);
        setComposeText('');
      }
      return;
    }
    if (!conversations.length || selectedPhone) return;
    const id = parseInt(urlCallerId, 10);
    const conv = conversations.find((c) => c.caller_id === id);
    if (conv) {
      setSelectedPhone(conv.client_phone);
      setSelectedName(conv.caller_name || null);
      setSelectedCallerId(conv.caller_id || null);
      setComposeText('');
      fetchMessages(conv.client_phone);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlCallerId, conversations]);

  async function handleSend() {
    const text = composeText.trim();
    if (!text || !selectedPhone || sending) return;
    setSending(true);
    setError(null);
    try {
      await apiFetch('/api/sms/send', {
        method: 'POST',
        body: JSON.stringify({ to: selectedPhone, message: text }),
      });
      setComposeText('');
      // Refresh both thread and conversation list
      await fetchMessages(selectedPhone);
      fetchConversations();
    } catch (err) {
      setError(err.message || 'Failed to send message');
    } finally {
      setSending(false);
    }
  }

  async function handleCall() {
    if (!selectedPhone || calling) return;
    setCalling(true);
    setError(null);
    try {
      await apiFetch('/api/calls/outbound', {
        method: 'POST',
        body: JSON.stringify({
          phone: selectedPhone,
          name: selectedName || 'Member',
        }),
      });
      setCallSuccess(true);
      setTimeout(() => setCallSuccess(false), 4000);
    } catch (err) {
      // If no calling platform configured, fall back to tel: link
      if (err.message?.includes('not configured')) {
        window.location.href = `tel:${selectedPhone}`;
      } else {
        setError(err.message || 'Call failed');
      }
    } finally {
      setCalling(false);
    }
  }

  async function handleNewConversation() {
    const phone = newPhone.trim();
    const msg = newMessage.trim();
    if (!phone || !msg || newSending) return;
    setNewSending(true);
    setError(null);
    try {
      await apiFetch('/api/sms/send', {
        method: 'POST',
        body: JSON.stringify({ to: phone, message: msg }),
      });
      setShowNewConv(false);
      setNewPhone('');
      setNewMessage('');
      await fetchConversations();
      selectConversation(phone, null);
    } catch (err) {
      setError(err.message || 'Failed to send message');
    } finally {
      setNewSending(false);
    }
  }

  if (loading && !conversations.length) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin"></div>
      </div>
    );
  }

  return (
    <div className="p-4 md:p-8 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4 md:mb-6">
        <div className="flex items-center gap-3 min-w-0">
          {selectedPhone && (
            <button
              onClick={goBack}
              className="p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors shrink-0"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
          )}
          <div className="min-w-0">
            <h1 className="text-xl md:text-2xl font-bold text-gray-900 dark:text-white flex items-center gap-2">
              <MessageSquare className="w-6 md:w-7 h-6 md:h-7 text-indigo-500 shrink-0" />
              {selectedPhone ? (
                <span className="truncate text-base md:text-2xl">
                  {selectedCallerId ? (
                    <button
                      onClick={() => navigate(`/contacts/${selectedCallerId}`)}
                      className="text-indigo-600 dark:text-indigo-400 hover:underline font-bold"
                    >
                      {selectedName || selectedPhone}
                    </button>
                  ) : (
                    selectedName || selectedPhone
                  )}
                </span>
              ) : (
                'SMS'
              )}
            </h1>
            {selectedPhone && selectedName && (
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5 ml-8 md:ml-9">
                {selectedPhone}
              </p>
            )}
            {!selectedPhone && (
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                Two-way SMS between contacts and the AI agent.
              </p>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {!selectedPhone && (
            <>
              <TestDataToggle enabled={showTestData} onChange={setShowTestData} />
              <button
                onClick={() => setShowNewConv(true)}
                className="flex items-center gap-2 px-4 py-2.5 bg-indigo-500 text-white rounded-lg text-sm font-medium hover:bg-indigo-600 transition-colors"
              >
                <Plus className="w-4 h-4" />
                New Message
              </button>
            </>
          )}

          {/* Call button when in a conversation */}
          {selectedPhone && (
            <button
              onClick={handleCall}
              disabled={calling}
              className={`flex items-center gap-2 px-4 py-2.5 border rounded-lg text-sm font-medium transition-colors disabled:opacity-50 ${
                callSuccess
                  ? 'bg-green-50 dark:bg-green-900/30 border-green-200 dark:border-green-700 text-green-700 dark:text-green-400'
                  : 'bg-emerald-50 dark:bg-emerald-900/30 border-emerald-200 dark:border-emerald-700 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-100 dark:hover:bg-emerald-900/50'
              }`}
            >
              <Phone className={`w-4 h-4 ${calling ? 'animate-pulse' : ''}`} />
              {calling ? 'Calling…' : callSuccess ? 'Call Placed ✓' : 'Call'}
            </button>
          )}

          <button
            onClick={() =>
              selectedPhone ? fetchMessages(selectedPhone) : fetchConversations(true)
            }
            disabled={refreshing || loadingMessages}
            className="flex items-center gap-2 px-4 py-2.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50 transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${refreshing || loadingMessages ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Search bar — only on conversation list */}
      {!selectedPhone && (
        <div className="relative mb-4">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name or phone..."
            className="w-full pl-10 pr-10 py-2.5 border border-gray-200 dark:border-gray-600 rounded-lg text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 dark:bg-gray-700 dark:text-white"
          />
          {search && refreshing && (
            <div className="absolute right-3 top-1/2 -translate-y-1/2">
              <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-indigo-500"></div>
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 rounded-xl p-4 flex items-start gap-3 mb-6">
          <AlertCircle className="w-5 h-5 text-red-500 mt-0.5 shrink-0" />
          <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
        </div>
      )}

      {/* New Conversation Modal */}
      {showNewConv && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
          <div className="w-full max-w-md bg-white dark:bg-gray-900 rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-700 p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">New Message</h2>
              <button
                onClick={() => { setShowNewConv(false); setNewPhone(''); setNewMessage(''); }}
                className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
                  Phone Number
                </label>
                <input
                  type="tel"
                  value={newPhone}
                  onChange={(e) => setNewPhone(e.target.value)}
                  placeholder="+1 (555) 000-0000"
                  className="w-full px-3 py-2.5 border border-gray-200 dark:border-gray-600 rounded-lg text-sm outline-none focus:ring-2 focus:ring-indigo-500/30 focus:border-indigo-500 dark:bg-gray-800 dark:text-white"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
                  Message
                </label>
                <textarea
                  value={newMessage}
                  onChange={(e) => setNewMessage(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleNewConversation(); }
                  }}
                  placeholder="Type your message..."
                  rows={4}
                  className="w-full px-3 py-2.5 border border-gray-200 dark:border-gray-600 rounded-lg text-sm outline-none focus:ring-2 focus:ring-indigo-500/30 focus:border-indigo-500 dark:bg-gray-800 dark:text-white resize-none"
                />
              </div>
            </div>

            <div className="flex gap-2 mt-4">
              <button
                onClick={() => { setShowNewConv(false); setNewPhone(''); setNewMessage(''); }}
                className="flex-1 px-4 py-2.5 bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleNewConversation}
                disabled={!newPhone.trim() || !newMessage.trim() || newSending}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-indigo-500 text-white rounded-lg text-sm font-medium hover:bg-indigo-600 disabled:opacity-50 transition-colors"
              >
                <Send className="w-4 h-4" />
                {newSending ? 'Sending...' : 'Send'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Conversation list or message thread */}
      {!selectedPhone ? (
        conversations.length === 0 ? (
          <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 p-12 text-center">
            <Inbox className="w-12 h-12 text-gray-300 mx-auto mb-3" />
            <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">
              {search ? 'No conversations match your search' : 'No conversations yet'}
            </h3>
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
              {search
                ? 'Try a different name or phone number.'
                : 'SMS conversations will appear here when contacts text your number or receive reminders and reply back.'}
            </p>
            {!search && (
              <button
                onClick={() => setShowNewConv(true)}
                className="inline-flex items-center gap-2 px-4 py-2.5 bg-indigo-500 text-white rounded-lg text-sm font-medium hover:bg-indigo-600 transition-colors"
              >
                <Plus className="w-4 h-4" />
                Send First Message
              </button>
            )}
          </div>
        ) : (
          <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden">
            {conversations.map((conv, idx) => (
              <button
                key={conv.client_phone}
                onClick={() => selectConversation(conv.client_phone, conv.caller_name, conv.caller_id)}
                className={`w-full flex items-center gap-4 p-4 text-left hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors ${
                  idx > 0 ? 'border-t border-gray-100 dark:border-gray-700' : ''
                }`}
              >
                {/* Avatar */}
                <div className="w-10 h-10 rounded-full bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-400 flex items-center justify-center text-sm font-semibold shrink-0">
                  {conv.caller_name ? (
                    conv.caller_name.charAt(0).toUpperCase()
                  ) : (
                    <Phone className="w-4 h-4" />
                  )}
                </div>

                {/* Content */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between">
                    <div className="min-w-0">
                      {conv.caller_name ? (
                        <>
                          <span className="font-semibold text-gray-900 dark:text-white text-sm truncate block">
                            {conv.caller_name}
                          </span>
                          <span className="text-xs text-gray-400 dark:text-gray-500">
                            {conv.client_phone}
                          </span>
                        </>
                      ) : (
                        <span className="font-semibold text-gray-900 dark:text-white text-sm">
                          {conv.client_phone}
                        </span>
                      )}
                    </div>
                    <span className="text-xs text-gray-400 shrink-0 ml-2">
                      {conv.last_message_at ? fmtRelative(conv.last_message_at, tz) : ''}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 mt-0.5">
                    {conv.last_direction === 'INBOUND' ? (
                      <ArrowDown className="w-3 h-3 text-blue-400 shrink-0" />
                    ) : (
                      <ArrowUp className="w-3 h-3 text-green-400 shrink-0" />
                    )}
                    <p className="text-sm text-gray-500 dark:text-gray-400 truncate">
                      {conv.last_message_body || 'No messages'}
                    </p>
                  </div>
                </div>

                {/* Message count badge */}
                <span className="px-2.5 py-1 bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 rounded-full text-xs font-medium shrink-0">
                  {conv.message_count}
                </span>
              </button>
            ))}
          </div>
        )
      ) : (
        /* Message thread */
        <div className="bg-white dark:bg-gray-900/60 rounded-2xl border border-gray-200/80 dark:border-white/5 overflow-hidden flex flex-col">
          {loadingMessages ? (
            <div className="flex items-center justify-center h-64">
              <div className="w-8 h-8 border-2 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin"></div>
            </div>
          ) : messages.length === 0 ? (
            <div className="p-12 text-center flex-1">
              <MessageSquare className="w-12 h-12 text-gray-300 mx-auto mb-3" />
              <p className="text-sm text-gray-500 dark:text-gray-400">No messages yet. Send one below.</p>
            </div>
          ) : (
            <div
              className="overflow-y-auto overscroll-y-contain p-4 space-y-3 bg-gray-50 dark:bg-gray-900 flex-1"
              style={{ maxHeight: '520px', WebkitOverflowScrolling: 'touch' }}
            >
              {messages.map((msg) => {
                const isOutbound = msg.direction === 'OUTBOUND';
                const isAdmin = msg.sender_type === 'admin';
                return (
                  <div
                    key={msg.id}
                    className={`flex ${isOutbound ? 'justify-end' : 'justify-start'}`}
                  >
                    <div
                      className={`max-w-[70%] rounded-2xl px-4 py-2.5 ${
                        isOutbound
                          ? isAdmin
                            ? 'bg-violet-500 text-white rounded-br-md'
                            : 'bg-indigo-500 text-white rounded-br-md'
                          : 'bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 text-gray-900 dark:text-white rounded-bl-md'
                      }`}
                    >
                      {/* Direction/sender label */}
                      <div
                        className={`flex items-center gap-1 mb-1 text-xs ${
                          isOutbound
                            ? isAdmin ? 'text-violet-100' : 'text-indigo-100'
                            : 'text-gray-400'
                        }`}
                      >
                        {isOutbound ? (
                          <>
                            <ArrowUp className="w-3 h-3" />
                            {isAdmin ? 'You' : 'AI Agent'}
                          </>
                        ) : (
                          <>
                            <ArrowDown className="w-3 h-3" />
                            Contact
                          </>
                        )}
                      </div>

                      {/* Message body */}
                      <p className="text-sm whitespace-pre-wrap break-words">{msg.body}</p>

                      {/* Timestamp */}
                      <div
                        className={`flex items-center gap-1 mt-1 text-xs ${
                          isOutbound
                            ? isAdmin ? 'text-violet-200' : 'text-indigo-200'
                            : 'text-gray-400'
                        }`}
                      >
                        <Clock className="w-3 h-3" />
                        {msg.created_at ? formatDateTime(msg.created_at, tz) : ''}
                      </div>
                    </div>
                  </div>
                );
              })}
              <div ref={messagesEndRef} />
            </div>
          )}

          {/* Compose bar */}
          <div className="border-t border-gray-100 dark:border-gray-700 p-3 flex items-end gap-2 bg-white dark:bg-gray-900">
            <textarea
              ref={composeRef}
              value={composeText}
              onChange={(e) => {
                setComposeText(e.target.value);
                // Auto-resize
                e.target.style.height = 'auto';
                e.target.style.height = Math.min(e.target.scrollHeight, 128) + 'px';
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); }
              }}
              placeholder="Type a message… (Enter to send, Shift+Enter for new line)"
              rows={1}
              className="flex-1 resize-none px-3 py-2.5 border border-gray-200 dark:border-gray-600 rounded-xl text-sm outline-none focus:ring-2 focus:ring-indigo-500/30 focus:border-indigo-500 dark:bg-gray-800 dark:text-white overflow-hidden"
              style={{ minHeight: '40px', maxHeight: '128px' }}
            />
            <button
              onClick={handleSend}
              disabled={!composeText.trim() || sending}
              className="flex items-center gap-1.5 px-4 py-2.5 bg-indigo-500 text-white rounded-xl text-sm font-medium hover:bg-indigo-600 disabled:opacity-50 transition-colors shrink-0"
            >
              <Send className="w-4 h-4" />
              {sending ? 'Sending…' : 'Send'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
