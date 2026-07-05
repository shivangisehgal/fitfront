/**
 * LocalChat — chat UI replacement for Vapi when LOCAL_CHAT_MODE is on.
 *
 * Talks to POST /api/chat/stream which returns OpenAI chat.completion.chunk SSE
 * events (the IDENTICAL wire format Vapi consumes). We parse each `data: {...}`
 * frame and append `delta.content` tokens to the in-flight assistant bubble as
 * they stream in — so the UX matches Vapi's transcript stream.
 */
import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { Send, RotateCcw, Bot, User as UserIcon, MessageSquare, Phone, Plus, ChevronDown, Download, AlertTriangle, Mic, MicOff, Volume2, VolumeX, Play, Check, Square, ArrowLeft, Search, Trash2, X } from 'lucide-react';
import { getToken, API_BASE } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';

// ── Per-user, per-caller storage keys ────────────────────────────────────────
// Keys are scoped to userId + callerPhone so each test caller has its own
// independent chat history, and switching accounts doesn't leak.
function storageKey(scope, suffix) {
  return `scheduler_ai_chat_${scope}_${suffix}`;
}

function makeScopeId(userId, callerPhone) {
  const phone = (callerPhone || 'default').replace(/[^a-zA-Z0-9]/g, '');
  return `${userId}_${phone}`;
}

function ensureConversationId(scope) {
  const key = storageKey(scope, 'conv');
  let id = sessionStorage.getItem(key);
  if (!id) {
    id = (crypto?.randomUUID?.() || `conv-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    sessionStorage.setItem(key, id);
  }
  return id;
}

// ── Background stream manager ─────────────────────────────────────────────
// Keeps the stream alive even when the component unmounts (user navigates away).
// Tokens are saved to sessionStorage so the response persists across route changes.
// `scope` is set when the stream starts (userId + callerPhone) to scope storage keys.
const backgroundStream = {
  active: false,
  abortController: null,
  onUpdate: null, // callback to notify mounted component of new tokens
  scope: null,

  _msgsKey() { return storageKey(this.scope, 'msgs'); },
  _streamKey() { return storageKey(this.scope, 'stream'); },

  start(fetchPromise, abortController, scope) {
    this.active = true;
    this.abortController = abortController;
    this.scope = scope;
    sessionStorage.setItem(this._streamKey(), 'active');
  },

  appendToken(token) {
    // Update sessionStorage directly (persists even if component unmounted)
    try {
      const msgs = JSON.parse(sessionStorage.getItem(this._msgsKey()) || '[]');
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant') {
        last.content = (last.content || '') + token;
        sessionStorage.setItem(this._msgsKey(), JSON.stringify(msgs));
      }
    } catch (_) { /* ignore */ }
    // Also notify mounted component if listening
    if (this.onUpdate) this.onUpdate(token);
  },

  finish(error = null) {
    this.active = false;
    this.abortController = null;
    sessionStorage.removeItem(this._streamKey());
    // Mark message as complete in sessionStorage
    try {
      const msgs = JSON.parse(sessionStorage.getItem(this._msgsKey()) || '[]');
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant') {
        delete last.pending;
        if (error && !last.content) {
          last.content = `_(error: ${error})_`;
        }
        sessionStorage.setItem(this._msgsKey(), JSON.stringify(msgs));
      }
    } catch (_) { /* ignore */ }
    // Notify mounted component
    if (this.onUpdate) this.onUpdate(null, true, error);
  },

  abort() {
    if (this.abortController) {
      this.abortController.abort();
    }
    this.active = false;
    if (this.scope) sessionStorage.removeItem(this._streamKey());
  },

  isActive(scope) {
    return this.active || sessionStorage.getItem(storageKey(scope, 'stream')) === 'active';
  },
};

const DEFAULT_WELCOME = {
  role: 'assistant',
  content:
    "Hi! I'm your AI agent — same brain that powers the voice call, just over chat. Ask me to book an appointment, look up open slots, or anything you'd say on a real call.",
};

function loadMessages(scope) {
  try {
    const raw = sessionStorage.getItem(storageKey(scope, 'msgs'));
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch (_) { /* corrupted — ignore */ }
  return [DEFAULT_WELCOME];
}

function saveMessages(scope, msgs) {
  try {
    // Strip pending flags before persisting
    const clean = msgs.map(({ pending, ...rest }) => rest);
    sessionStorage.setItem(storageKey(scope, 'msgs'), JSON.stringify(clean));
  } catch (_) { /* storage full — ignore */ }
}

export default function LocalChat() {
  const { user } = useAuth();
  const userId = user?.id || 'anonymous';
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState(null);
  // Unified test callers: [{phone, name}, ...]
  const [testCallers, setTestCallers] = useState([]);
  const [selectedCaller, setSelectedCaller] = useState(null); // {phone, name}
  const [agentActive, setAgentActive] = useState(true);
  const [addingCaller, setAddingCaller] = useState(false);
  const [newCallerName, setNewCallerName] = useState('');
  const [mobileShowChat, setMobileShowChat] = useState(false); // Mobile: show chat or contacts
  const [callerSearch, setCallerSearch] = useState(''); // Search filter for callers
  const [deleteConfirmCaller, setDeleteConfirmCaller] = useState(null); // Caller pending delete confirmation

  // ── Resizable contacts sidebar ──────────────────────────────────────────────
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = parseInt(localStorage.getItem('chat-sidebar-width'), 10);
    return isNaN(saved) ? 320 : Math.max(200, Math.min(400, saved));
  });
  const sidebarRef = useRef(null);
  const dragRef = useRef({ startX: 0, startW: 0 });
  const onDragStart = useCallback((e) => {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startW: sidebarWidth };
    document.body.style.cursor = 'col-resize';
    if (sidebarRef.current) sidebarRef.current.style.transition = 'none';
    function onMove(ev) {
      const next = Math.max(200, Math.min(400, dragRef.current.startW + ev.clientX - dragRef.current.startX));
      if (sidebarRef.current) sidebarRef.current.style.width = next + 'px';
    }
    function onUp(ev) {
      document.removeEventListener('mousemove', onMove);
      document.body.style.cursor = '';
      if (sidebarRef.current) sidebarRef.current.style.transition = '';
      const next = Math.max(200, Math.min(400, dragRef.current.startW + ev.clientX - dragRef.current.startX));
      setSidebarWidth(next);
      localStorage.setItem('chat-sidebar-width', String(next));
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp, { once: true });
  }, [sidebarWidth]);

  // ── Voice mode state (speech-to-text & text-to-speech) ─────────────────────
  const [voiceMode, setVoiceMode] = useState(false); // true = voice enabled
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [availableVoices, setAvailableVoices] = useState([]);
  const [selectedVoice, setSelectedVoice] = useState(''); // voice name
  const [voiceDropdownOpen, setVoiceDropdownOpen] = useState(false);
  const [previewingVoice, setPreviewingVoice] = useState(null); // voice being previewed
  const recognitionRef = useRef(null);
  const synthRef = useRef(window.speechSynthesis);
  const lastSpokenRef = useRef(''); // Prevent duplicate speech
  const voiceDropdownRef = useRef(null);

  // Scope = userId + callerPhone — each test caller gets independent chat history.
  const chatScope = useMemo(
    () => makeScopeId(userId, selectedCaller?.phone),
    [userId, selectedCaller?.phone],
  );
  const [messages, setMessages] = useState(() => loadMessages(chatScope));
  const conversationId = useRef(ensureConversationId(chatScope));
  // Ref tracks current scope so the save effect always writes to the right key
  // without needing chatScope as a dependency (which causes the overwrite race).
  const chatScopeRef = useRef(chatScope);
  chatScopeRef.current = chatScope;

  // When caller changes, swap to that caller's conversation
  useEffect(() => {
    setMessages(loadMessages(chatScope));
    conversationId.current = ensureConversationId(chatScope);
    setError(null);
  }, [chatScope]);

  // Fetch test callers from config
  const fetchConfig = useCallback(() => {
    const token = getToken();
    fetch(`${API_BASE}/api/config`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => (r.ok ? r.json() : {}))
      .then((cfg) => {
        const callers = cfg?.test_callers || [];
        setTestCallers(callers);
        setAgentActive(cfg?.agent_active !== false);
        // Set selected caller (prefer current selection if still exists)
        setSelectedCaller((prev) => {
          if (prev && callers.some((c) => c.phone === prev.phone)) {
            return callers.find((c) => c.phone === prev.phone);
          }
          return callers[0] || null;
        });
      })
      .catch(() => {});
  }, []);

  useEffect(() => { fetchConfig(); }, [fetchConfig]);

  // Close dropdowns on outside click
  useEffect(() => {
    function handleClick(e) {
      if (voiceDropdownRef.current && !voiceDropdownRef.current.contains(e.target)) {
        setVoiceDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // Add a new test caller (generates phone, user provides name)
  async function handleAddCaller(nameOverride) {
    // If no explicit name provided, generate a placeholder (will be updated by AI from chat)
    const name = (typeof nameOverride === 'string' ? nameOverride : newCallerName).trim()
      || `Caller ${testCallers.length + 1}`;
    if (name.length < 2) {
      setError('Name must be at least 2 characters.');
      return;
    }
    setAddingCaller(true);
    try {
      const token = getToken();
      const resp = await fetch(`${API_BASE}/api/config/test-callers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ name }),
      });
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        throw new Error(j.detail || `Failed (${resp.status})`);
      }
      const data = await resp.json();
      setTestCallers(data.test_callers || []);
      // Auto-select the newly created caller
      if (data.caller) setSelectedCaller(data.caller);
      setNewCallerName('');
      setCallerDropdownOpen(false);
    } catch (err) {
      setError(err.message || 'Failed to add test caller.');
    } finally {
      setAddingCaller(false);
    }
  }

  // Show delete confirmation modal
  function promptDeleteCaller(caller) {
    if (!caller?.phone) return;
    setDeleteConfirmCaller(caller);
  }

  // Actually delete a test caller (called from confirmation modal)
  async function confirmDeleteCaller() {
    const caller = deleteConfirmCaller;
    if (!caller?.phone) return;
    setDeleteConfirmCaller(null); // Close modal

    try {
      const token = getToken();
      const resp = await fetch(`${API_BASE}/api/config/test-callers/${encodeURIComponent(caller.phone)}`, {
        method: 'DELETE',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        throw new Error(j.detail || `Failed (${resp.status})`);
      }
      const data = await resp.json();
      setTestCallers(data.test_callers || []);

      // Clear sessionStorage for this caller
      const scope = makeScopeId(userId, caller.phone);
      sessionStorage.removeItem(storageKey(scope, 'msgs'));
      sessionStorage.removeItem(storageKey(scope, 'conv'));
      sessionStorage.removeItem(storageKey(scope, 'stream'));

      // If we deleted the selected caller, switch to another
      if (selectedCaller?.phone === caller.phone) {
        const remaining = data.test_callers || [];
        setSelectedCaller(remaining[0] || null);
        setMobileShowChat(false); // Go back to contacts on mobile
      }
    } catch (err) {
      setError(err.message || 'Failed to delete test caller.');
    }
  }

  // Persist messages to sessionStorage whenever they change.
  // We intentionally omit chatScope from deps and use the ref instead —
  // otherwise a scope transition fires the effect with the new scope
  // but stale (DEFAULT_WELCOME) messages, overwriting the real saved chat.
  useEffect(() => {
    saveMessages(chatScopeRef.current, messages);
  }, [messages]);
  const abortRef = useRef(null);
  const scrollRef = useRef(null);
  const inputRef = useRef(null);

  // Subscribe to background stream updates (for when component remounts mid-stream)
  useEffect(() => {
    // Check if there's an active background stream we need to listen to
    if (backgroundStream.isActive(chatScope)) {
      setStreaming(true);
    }

    // Register callback to receive token updates from background stream
    backgroundStream.onUpdate = (token, done, streamError) => {
      if (done) {
        // Stream finished while we were mounted or just remounted
        setStreaming(false);
        if (streamError) {
          setError(streamError);
        }
        // Reload messages from sessionStorage to get final state
        setMessages(loadMessages(chatScope));
      } else if (token) {
        // New token arrived — update the last message
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === 'assistant') {
            next[next.length - 1] = {
              ...last,
              content: (last.content || '') + token,
            };
          }
          return next;
        });
        // Real-time TTS: detect sentences and speak immediately (bypasses React batching)
        if (detectAndSpeakRef.current) {
          detectAndSpeakRef.current(token);
        }
      }
    };

    // On mount, sync with sessionStorage (in case stream completed while away)
    const streamKey = storageKey(chatScope, 'stream');
    if (!backgroundStream.active && sessionStorage.getItem(streamKey) !== 'active') {
      setMessages(loadMessages(chatScope));
      setStreaming(false);
    }

    return () => {
      // Don't abort on unmount — let background stream continue
      backgroundStream.onUpdate = null;
    };
  }, [chatScope]);

  // Auto-scroll to bottom on new content
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, streaming]);

  // Refocus the textarea when streaming ends so user can type immediately
  const prevStreaming = useRef(false);
  useEffect(() => {
    if (prevStreaming.current && !streaming) {
      // Streaming just finished — restore focus to input
      setTimeout(() => inputRef.current?.focus(), 50);
    }
    prevStreaming.current = streaming;
  }, [streaming]);

  // ── Load available TTS voices ───────────────────────────────────────────────
  useEffect(() => {
    const loadVoices = () => {
      const voices = synthRef.current.getVoices();
      // Filter to English voices only for cleaner list
      const englishVoices = voices.filter((v) => v.lang.startsWith('en'));
      setAvailableVoices(englishVoices.length > 0 ? englishVoices : voices);

      // Auto-select a good default voice if not already selected
      // Prefer "UK English Male" specifically
      if (!selectedVoice && voices.length > 0) {
        const defaultVoice =
          // Exact match for "UK English Male"
          voices.find((v) => v.name === 'UK English Male') ||
          voices.find((v) => v.name.toLowerCase() === 'uk english male') ||
          // Partial match
          voices.find((v) => v.name.toLowerCase().includes('uk english male')) ||
          // Any UK English voice
          englishVoices.find((v) => v.lang === 'en-GB') ||
          // Fallback to any English voice
          englishVoices.find((v) => v.lang === 'en-US') ||
          englishVoices[0] ||
          voices[0];
        if (defaultVoice) setSelectedVoice(defaultVoice.name);
      }
    };

    loadVoices();
    // Chrome loads voices async
    if (synthRef.current.onvoiceschanged !== undefined) {
      synthRef.current.onvoiceschanged = loadVoices;
    }

    return () => {
      if (synthRef.current.onvoiceschanged !== undefined) {
        synthRef.current.onvoiceschanged = null;
      }
    };
  }, [selectedVoice]);

  // ── Speech Recognition setup (speech-to-text) ──────────────────────────────
  useEffect(() => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      console.warn('[LocalChat] SpeechRecognition not supported in this browser');
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = false; // Stop after one phrase
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    recognition.onresult = (event) => {
      const transcript = Array.from(event.results)
        .map((result) => result[0].transcript)
        .join('');
      setInput(transcript);
    };

    recognition.onend = () => {
      setIsListening(false);
      // Auto-send if we have content and voice mode is on
      if (voiceMode && inputRef.current?.value?.trim()) {
        // Small delay to ensure state is updated
        setTimeout(() => {
          const form = inputRef.current?.closest('form');
          if (form) form.requestSubmit();
        }, 100);
      }
    };

    recognition.onerror = (event) => {
      console.error('[LocalChat] Speech recognition error:', event.error);
      setIsListening(false);
      if (event.error === 'not-allowed') {
        setError('Microphone access denied. Please allow microphone access in your browser settings.');
      }
    };

    recognitionRef.current = recognition;

    return () => {
      recognition.abort();
    };
  }, [voiceMode]);

  // Track message count when voice mode was enabled (to avoid replaying old messages)
  const voiceModeEnabledAtRef = useRef(messages.length);
  const spokenLengthRef = useRef(0); // How much of the current message we've already spoken
  const speakQueueRef = useRef([]); // Queue of sentences to speak
  const isSpeakingQueueRef = useRef(false);

  // When voice mode turns ON, mark current position so we don't replay old messages
  useEffect(() => {
    if (voiceMode) {
      voiceModeEnabledAtRef.current = messages.length;
      spokenLengthRef.current = 0;
      speakQueueRef.current = [];
      // Mark the last message as "spoken" so it won't replay
      const lastMsg = messages[messages.length - 1];
      if (lastMsg?.content) {
        lastSpokenRef.current = lastMsg.content;
        spokenLengthRef.current = lastMsg.content.length;
        // Sync streaming content ref to current content to avoid replay
        streamingContentRef.current = lastMsg.content;
      }
    }
  }, [voiceMode]); // Only run when voiceMode changes

  // Refs to track current state for callbacks (avoids stale closures)
  const voiceModeRef = useRef(voiceMode);
  const streamingRef = useRef(streaming);
  const streamingContentRef = useRef(''); // Accumulated content for real-time TTS
  const detectAndSpeakRef = useRef(null); // Ref to real-time TTS function
  useEffect(() => { voiceModeRef.current = voiceMode; }, [voiceMode]);
  useEffect(() => { streamingRef.current = streaming; }, [streaming]);

  // Real-time sentence detection for streaming TTS
  // Called directly from token handler to avoid React batching delays
  const detectAndSpeakSentences = useCallback((newToken) => {
    if (!voiceModeRef.current) return;

    streamingContentRef.current += newToken;
    const content = streamingContentRef.current;
    const alreadySpoken = spokenLengthRef.current;

    // Find complete sentences in new content (end with . ! ? or newline)
    const sentencePattern = /[^.!?\n]*[.!?\n]+/g;
    const searchText = content.slice(alreadySpoken);
    let match;
    const sentences = [];
    let lastEnd = alreadySpoken;

    while ((match = sentencePattern.exec(searchText)) !== null) {
      sentences.push(match[0].trim());
      lastEnd = alreadySpoken + match.index + match[0].length;
    }

    // Queue new sentences for speaking
    if (sentences.length > 0) {
      spokenLengthRef.current = lastEnd;
      sentences.forEach((s) => {
        if (s.trim()) {
          speakQueueRef.current.push(s);
        }
      });

      // Start speaking if not already
      if (!isSpeakingQueueRef.current && speakQueueRef.current.length > 0) {
        isSpeakingQueueRef.current = true;
        const first = speakQueueRef.current.shift();
        // Use setTimeout to avoid calling speakSentence before it's defined
        setTimeout(() => {
          const utterance = new SpeechSynthesisUtterance(first);
          utterance.rate = 1.0;
          utterance.pitch = 1.0;
          utterance.volume = 1.0;

          const voices = synthRef.current.getVoices();
          if (voices.length > 0 && selectedVoice) {
            const voice = voices.find((v) => v.name === selectedVoice);
            if (voice) utterance.voice = voice;
          }

          const processQueue = () => {
            if (speakQueueRef.current.length > 0) {
              const next = speakQueueRef.current.shift();
              const nextUtterance = new SpeechSynthesisUtterance(next);
              nextUtterance.rate = 1.0;
              nextUtterance.pitch = 1.0;
              nextUtterance.volume = 1.0;
              if (voices.length > 0 && selectedVoice) {
                const voice = voices.find((v) => v.name === selectedVoice);
                if (voice) nextUtterance.voice = voice;
              }
              nextUtterance.onend = processQueue;
              nextUtterance.onerror = () => {
                isSpeakingQueueRef.current = false;
                setIsSpeaking(false);
              };
              synthRef.current.speak(nextUtterance);
            } else {
              isSpeakingQueueRef.current = false;
              setIsSpeaking(false);
              // Auto-start listening after speaking (use refs for current state)
              if (voiceModeRef.current && recognitionRef.current && !streamingRef.current) {
                setTimeout(() => startListening(), 500);
              }
            }
          };

          utterance.onstart = () => setIsSpeaking(true);
          utterance.onend = processQueue;
          utterance.onerror = () => {
            isSpeakingQueueRef.current = false;
            setIsSpeaking(false);
          };

          synthRef.current.speak(utterance);
        }, 0);
      }
    }
  }, [selectedVoice]);

  // Keep ref updated so backgroundStream.onUpdate can call it
  useEffect(() => {
    detectAndSpeakRef.current = detectAndSpeakSentences;
  }, [detectAndSpeakSentences]);

  // Speak a single sentence and process queue
  const speakSentence = useCallback((sentence) => {
    if (!sentence.trim()) return;

    const utterance = new SpeechSynthesisUtterance(sentence);
    utterance.rate = 1.0;
    utterance.pitch = 1.0;
    utterance.volume = 1.0;

    const voices = synthRef.current.getVoices();
    if (voices.length > 0 && selectedVoice) {
      const voice = voices.find((v) => v.name === selectedVoice);
      if (voice) utterance.voice = voice;
    }

    utterance.onstart = () => setIsSpeaking(true);
    utterance.onend = () => {
      // Process next sentence in queue
      if (speakQueueRef.current.length > 0) {
        const next = speakQueueRef.current.shift();
        speakSentence(next);
      } else {
        isSpeakingQueueRef.current = false;
        setIsSpeaking(false);
        // Auto-start listening after all sentences spoken (use refs for current state)
        if (voiceModeRef.current && recognitionRef.current && !streamingRef.current) {
          setTimeout(() => startListening(), 500);
        }
      }
    };
    utterance.onerror = () => {
      isSpeakingQueueRef.current = false;
      setIsSpeaking(false);
    };

    synthRef.current.speak(utterance);
  }, [selectedVoice]);

  // Handle remaining text when message completes (real-time TTS is handled by token handler)
  useEffect(() => {
    if (!voiceMode) return;
    if (messages.length <= voiceModeEnabledAtRef.current) return;

    const lastMsg = messages[messages.length - 1];
    if (!lastMsg || lastMsg.role !== 'assistant' || !lastMsg.content) return;

    // Only handle completion case — streaming TTS is done in detectAndSpeakSentences
    if (!lastMsg.pending) {
      const content = lastMsg.content;
      // Speak any remaining text that didn't end with . ! ? \n
      if (spokenLengthRef.current < content.length) {
        const remaining = content.slice(spokenLengthRef.current).trim();
        if (remaining) {
          speakQueueRef.current.push(remaining);
          spokenLengthRef.current = content.length;

          if (!isSpeakingQueueRef.current && speakQueueRef.current.length > 0) {
            isSpeakingQueueRef.current = true;
            const first = speakQueueRef.current.shift();
            speakSentence(first);
          }
        }
      }
      lastSpokenRef.current = content;
    }
  }, [messages, voiceMode, speakSentence]);

  // ── Voice control functions ────────────────────────────────────────────────
  function startListening() {
    if (!recognitionRef.current || isListening || streaming || isSpeaking) return;

    // Stop any ongoing speech
    synthRef.current.cancel();
    setIsSpeaking(false);

    setInput('');
    setIsListening(true);
    try {
      recognitionRef.current.start();
    } catch (err) {
      console.error('[LocalChat] Failed to start recognition:', err);
      setIsListening(false);
    }
  }

  function stopListening() {
    if (recognitionRef.current && isListening) {
      recognitionRef.current.stop();
    }
  }

  function toggleVoiceMode() {
    const newMode = !voiceMode;
    setVoiceMode(newMode);

    if (!newMode) {
      // Turning off voice mode — stop everything
      stopListening();
      synthRef.current.cancel();
      setIsSpeaking(false);
    }
    // When turning ON, don't auto-speak old messages — only speak NEW messages
  }

  function stopSpeaking() {
    synthRef.current.cancel();
    speakQueueRef.current = []; // Clear sentence queue
    isSpeakingQueueRef.current = false;
    setIsSpeaking(false);
    setPreviewingVoice(null);
  }

  // Preview a voice with a sample phrase
  function previewVoice(voiceName) {
    synthRef.current.cancel();
    setPreviewingVoice(voiceName);

    const utterance = new SpeechSynthesisUtterance(
      "Hi! I'm your AI assistant. How can I help you today?"
    );
    utterance.rate = 1.0;
    utterance.pitch = 1.0;
    utterance.volume = 1.0;

    const voices = synthRef.current.getVoices();
    const voice = voices.find((v) => v.name === voiceName);
    if (voice) utterance.voice = voice;

    utterance.onend = () => setPreviewingVoice(null);
    utterance.onerror = () => setPreviewingVoice(null);

    synthRef.current.speak(utterance);
  }

  // Apply a voice (close dropdown and set as selected)
  function applyVoice(voiceName) {
    synthRef.current.cancel();
    setPreviewingVoice(null);
    setSelectedVoice(voiceName);
    setVoiceDropdownOpen(false);
  }

  async function sendMessage(e) {
    e?.preventDefault?.();
    const text = input.trim();
    if (!text || streaming) return;

    setError(null);
    setInput('');
    // Reset textarea height
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
    }

    // Reset voice streaming state for new message
    spokenLengthRef.current = 0;
    speakQueueRef.current = [];
    streamingContentRef.current = '';

    // Add user message and pending assistant bubble
    const newMessages = [
      ...messages,
      { role: 'user', content: text },
      { role: 'assistant', content: '', pending: true },
    ];
    setMessages(newMessages);
    saveMessages(chatScope, newMessages); // persist immediately for background stream
    setStreaming(true);

    const token = getToken();
    const controller = new AbortController();
    abortRef.current = controller;
    backgroundStream.start(null, controller, chatScope);

    try {
      const res = await fetch(`${API_BASE}/api/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          message: text,
          conversation_id: conversationId.current,
          test_phone: selectedCaller?.phone || undefined,
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        let detail = `Request failed (${res.status})`;
        try {
          const j = await res.json();
          detail = j.detail || detail;
        } catch (_) {
          /* not JSON */
        }
        throw new Error(detail);
      }
      if (!res.body) throw new Error('No response body for streaming.');

      // ── Parse SSE: each event is `data: <payload>\n\n` ────────────────
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Split on the SSE event terminator
        let sepIdx;
        while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, sepIdx);
          buffer = buffer.slice(sepIdx + 2);

          // An event may contain multiple lines; we only care about `data:` lines
          const dataLines = rawEvent
            .split('\n')
            .filter((l) => l.startsWith('data:'))
            .map((l) => l.slice(5).trimStart());
          if (dataLines.length === 0) continue;

          const payload = dataLines.join('\n');
          if (payload === '[DONE]') {
            // Terminator — finalize the stream
            backgroundStream.finish();
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === 'assistant') {
                next[next.length - 1] = { ...last, pending: false };
              }
              return next;
            });
            // Re-fetch callers — AI may have updated the caller's name during the turn
            fetchConfig();
            continue;
          }

          let chunk;
          try {
            chunk = JSON.parse(payload);
          } catch (err) {
            console.warn('[LocalChat] bad SSE payload', payload);
            continue;
          }

          // OpenAI chat.completion.chunk shape — same as Vapi consumes
          const delta = chunk?.choices?.[0]?.delta || {};
          if (typeof delta.content === 'string' && delta.content.length > 0) {
            // Update both sessionStorage (for background persistence) and React state
            backgroundStream.appendToken(delta.content);
            // React state update happens via backgroundStream.onUpdate callback
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        // Manual abort (e.g., reset button) — not navigation
        backgroundStream.finish('cancelled');
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === 'assistant') {
            const content = last.content || '';
            next[next.length - 1] = {
              ...last,
              content: content ? `${content}\n\n_(cancelled)_` : '_(cancelled)_',
              pending: false,
            };
          }
          return next;
        });
      } else {
        console.error('[LocalChat] stream error:', err);
        backgroundStream.finish(err.message);
        setError(err.message || 'Failed to reach the agent.');
        // Drop the empty pending bubble
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last && last.role === 'assistant' && last.pending && !last.content) {
            next.pop();
          }
          return next;
        });
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  async function resetConversation() {
    // Abort any background stream
    backgroundStream.abort();
    if (abortRef.current) {
      abortRef.current.abort();
    }
    const oldId = conversationId.current;
    // Tell the backend to drop the session
    const token = getToken();
    try {
      await fetch(`${API_BASE}/api/chat/reset`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          message: 'reset',
          conversation_id: oldId,
          test_phone: selectedCaller?.phone || undefined,
        }),
      });
    } catch (err) {
      console.warn('[LocalChat] reset request failed:', err);
    }

    // Generate a fresh conversation id and clear UI history
    const fresh =
      crypto?.randomUUID?.() || `conv-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    sessionStorage.setItem(storageKey(chatScope, 'conv'), fresh);
    sessionStorage.removeItem(storageKey(chatScope, 'msgs'));
    conversationId.current = fresh;
    setMessages([
      {
        role: 'assistant',
        content: "Fresh conversation started. How can I help?",
      },
    ]);
    setError(null);
  }

  // Get last message preview for a caller (for sidebar)
  function getLastMessagePreview(caller) {
    try {
      const scope = makeScopeId(userId, caller?.phone);
      const raw = sessionStorage.getItem(storageKey(scope, 'msgs'));
      if (raw) {
        const msgs = JSON.parse(raw);
        if (Array.isArray(msgs) && msgs.length > 0) {
          const last = msgs[msgs.length - 1];
          const content = last?.content || '';
          return content.length > 40 ? content.slice(0, 40) + '...' : content;
        }
      }
    } catch (_) { /* ignore */ }
    return 'No messages yet';
  }

  // Select a caller and show chat on mobile
  function selectCaller(caller) {
    setSelectedCaller(caller);
    setMobileShowChat(true);
  }

  // Filter callers by search
  const filteredCallers = testCallers.filter((c) =>
    c.name.toLowerCase().includes(callerSearch.toLowerCase()) ||
    c.phone.includes(callerSearch)
  );

  function exportChat() {
    const exportData = {
      exported_at: new Date().toISOString(),
      conversation_id: conversationId.current,
      test_caller: selectedCaller || null,
      messages: messages.map(({ pending, ...rest }) => rest), // strip pending flags
    };

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `chat-export-${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-')}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  return (
    <div className="flex h-full overflow-hidden bg-gray-100 dark:bg-[#0a0a0f]">
      {/* ═══════════════════════════════════════════════════════════════════════
          LEFT SIDEBAR — Contacts List (WhatsApp style)
          ═══════════════════════════════════════════════════════════════════════ */}
      <div
        ref={sidebarRef}
        className={`${mobileShowChat ? 'hidden md:flex' : 'flex'} flex-col bg-white dark:bg-gray-900/60 border-r border-gray-200 dark:border-white/5 shrink-0 w-full md:w-auto`}
        style={{ width: `${sidebarWidth}px` }}
      >
        {/* Sidebar Header */}
        <div className="px-4 py-3 bg-white dark:bg-zinc-900/80 border-b border-gray-100 dark:border-white/5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 bg-indigo-500 rounded-lg flex items-center justify-center shrink-0">
                <Bot className="w-4 h-4 text-white" />
              </div>
              <div>
                <p className="text-sm font-semibold text-gray-900 dark:text-white">Test Front Desk</p>
                <p className="text-xs text-gray-400 dark:text-white/40">Simulate member calls & bookings</p>
              </div>
            </div>
            <button
              onClick={() => handleAddCaller()}
              disabled={testCallers.length >= 10}
              className="p-1.5 hover:bg-indigo-50 dark:hover:bg-indigo-950/30 rounded-lg transition-colors btn-press text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 disabled:opacity-40 disabled:cursor-not-allowed"
              title="Add new test caller"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Search Bar */}
        <div className="px-3 py-2 bg-white dark:bg-zinc-900/80 border-b border-gray-100 dark:border-white/5">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              type="text"
              value={callerSearch}
              onChange={(e) => setCallerSearch(e.target.value)}
              placeholder="Search members"
              className="w-full pl-9 pr-4 py-2 bg-gray-100 dark:bg-zinc-800 rounded-lg text-sm text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-white/30 focus:outline-none"
            />
          </div>
        </div>

        {/* Add New Caller Form (inline) */}
        {addingCaller && (
          <div className="px-3 py-3 bg-indigo-50 dark:bg-indigo-950/30 border-b border-indigo-100 dark:border-indigo-900/50">
            <p className="text-xs font-medium text-indigo-700 dark:text-indigo-400 mb-2">Add Test Caller</p>
            <div className="flex gap-2">
              <input
                type="text"
                value={newCallerName}
                onChange={(e) => setNewCallerName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAddCaller(); } if (e.key === 'Escape') { setAddingCaller(false); setNewCallerName(''); } }}
                placeholder="Caller name..."
                className="flex-1 min-w-0 px-3 py-2 text-sm border border-indigo-200 dark:border-indigo-700/50 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500/40 bg-white dark:bg-zinc-800 dark:text-white"
                autoFocus
                maxLength={50}
              />
              <button
                onClick={handleAddCaller}
                disabled={!newCallerName.trim() || testCallers.length >= 10}
                className="px-3 py-2 bg-indigo-500 text-white rounded-lg hover:bg-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors text-sm font-medium shrink-0"
              >
                Add
              </button>
              <button
                onClick={() => { setAddingCaller(false); setNewCallerName(''); }}
                className="p-2 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-white/8 rounded-lg transition-colors shrink-0"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <p className="text-[10px] text-indigo-500 dark:text-indigo-400 mt-1.5">{testCallers.length}/10 callers</p>
          </div>
        )}

        {/* Contacts List */}
        <div className="flex-1 overflow-y-auto">
          {filteredCallers.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-6 py-12">
              <div className="w-16 h-16 bg-gray-100 dark:bg-gray-700 rounded-full flex items-center justify-center mb-4">
                <Phone className="w-8 h-8 text-gray-400" />
              </div>
              <p className="text-gray-500 dark:text-gray-400 text-sm mb-2">
                {testCallers.length === 0 ? 'No test callers yet' : 'No matches found'}
              </p>
              <button
                onClick={() => setAddingCaller(true)}
                className="text-indigo-600 dark:text-indigo-400 text-sm font-medium hover:underline"
              >
                + Add a test caller
              </button>
            </div>
          ) : (
            filteredCallers.map((caller) => {
              const isActive = caller.phone === selectedCaller?.phone;
              const preview = getLastMessagePreview(caller);
              return (
                <div
                  key={caller.phone}
                  className={`group relative flex items-center gap-3 px-4 py-3.5 hover:bg-gray-100 dark:hover:bg-white/5 active:bg-gray-200 dark:active:bg-white/10 transition-colors cursor-pointer ${
                    isActive ? 'bg-indigo-50 dark:bg-indigo-950/40' : ''
                  }`}
                  onClick={() => selectCaller(caller)}
                >
                  {/* Avatar */}
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${
                    isActive ? 'bg-indigo-500 text-white' : 'bg-gray-300 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
                  }`}>
                    <UserIcon className="w-4 h-4 md:w-5 md:h-5" />
                  </div>
                  {/* Name & Preview */}
                  <div className="flex-1 min-w-0 pr-8">
                    <div className="flex items-center gap-2">
                      <span className={`font-medium truncate text-sm ${isActive ? 'text-indigo-700 dark:text-indigo-300' : 'text-gray-900 dark:text-white'}`}>
                        {caller.name}
                      </span>
                    </div>
                    <p className="text-xs text-gray-500 dark:text-gray-400 truncate mt-0.5">
                      {preview}
                    </p>
                    <p className="text-[10px] text-gray-400 dark:text-gray-500 mt-0.5">
                      {caller.phone}
                    </p>
                  </div>
                  {/* Delete button — always visible on mobile, hover on desktop */}
                  {testCallers.length > 1 && (
                    <button
                      onClick={(e) => { e.stopPropagation(); promptDeleteCaller(caller); }}
                      className="absolute right-3 top-1/2 -translate-y-1/2 p-2.5 md:p-2 opacity-60 md:opacity-0 group-hover:opacity-100 hover:bg-red-100 dark:hover:bg-red-900/30 active:bg-red-200 rounded-full transition-all text-gray-400 hover:text-red-500"
                      title="Delete test caller"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Agent OFF Warning (in sidebar) */}
        {!agentActive && (
          <div className="px-4 py-3 bg-amber-50 dark:bg-amber-900/30 border-t border-amber-200 dark:border-amber-800">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0" />
              <p className="text-xs text-amber-700 dark:text-amber-400">
                <span className="font-semibold">Agent OFF</span> — Test mode still works
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Drag handle between sidebar and chat panel */}
      <div
        onMouseDown={onDragStart}
        className="hidden md:flex w-1 shrink-0 cursor-col-resize hover:bg-indigo-400/40 active:bg-indigo-400/60 transition-colors self-stretch"
      />

      {/* ═══════════════════════════════════════════════════════════════════════
          RIGHT SIDE — Chat Area
          ═══════════════════════════════════════════════════════════════════════ */}
      <div className={`${mobileShowChat ? 'flex' : 'hidden md:flex'} flex-col flex-1 min-w-0`}>
        {selectedCaller ? (
          <>
            {/* Chat Header */}
            <div className="px-3 md:px-4 py-3 bg-white dark:bg-zinc-900/80 border-b border-gray-100 dark:border-white/5 flex items-center gap-2 md:gap-3 shrink-0">
              {/* Back button (mobile only) */}
              <button
                onClick={() => setMobileShowChat(false)}
                className="md:hidden p-1.5 -ml-1 hover:bg-gray-100 dark:hover:bg-white/8 rounded-lg transition-colors text-gray-500 dark:text-white/50"
              >
                <ArrowLeft className="w-5 h-5" />
              </button>
              {/* Caller Avatar */}
              <div className="w-9 h-9 bg-indigo-500 rounded-xl flex items-center justify-center shrink-0">
                <UserIcon className="w-4 h-4 text-white" />
              </div>
              {/* Caller Info */}
              <div className="flex-1 min-w-0">
                <h2 className="text-gray-900 dark:text-white font-semibold truncate text-sm">{selectedCaller.name}</h2>
                <p className="text-gray-400 dark:text-white/40 text-xs truncate">{selectedCaller.phone}</p>
              </div>
              {/* Action Buttons */}
              <div className="flex items-center gap-1">
                {/* Voice mode toggle */}
                <div className="relative" ref={voiceDropdownRef}>
                  <button
                    onClick={() => voiceMode ? setVoiceDropdownOpen((v) => !v) : toggleVoiceMode()}
                    className={`p-2 rounded-full transition-colors ${
                      voiceMode ? 'bg-white/20 text-white' : 'hover:bg-white/10 text-white/80'
                    }`}
                    title={voiceMode ? 'Click to change voice' : 'Enable voice mode'}
                  >
                    {voiceMode ? <Volume2 className="w-5 h-5" /> : <VolumeX className="w-5 h-5" />}
                  </button>
                  {/* Voice selector dropdown */}
                  {voiceDropdownOpen && (
                    <div className="absolute top-full right-0 mt-1 w-64 md:w-72 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-lg z-50 py-1">
                      <div className="px-3 py-2 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
                        <div>
                          <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide">Select Voice</p>
                          <p className="text-[10px] text-gray-400 mt-0.5">Preview before applying</p>
                        </div>
                        <button onClick={toggleVoiceMode} className="text-xs text-red-500 hover:text-red-600 font-medium">Turn Off</button>
                      </div>
                      <div className="max-h-48 overflow-y-auto">
                        {availableVoices.map((voice) => {
                          const displayName = voice.name.replace(/Microsoft |Google |com\.apple\.speech\.synthesis\.voice\./, '').split('.')[0];
                          const isSelected = voice.name === selectedVoice;
                          const isPreviewing = voice.name === previewingVoice;
                          return (
                            <div key={voice.name} className={`flex items-center justify-between px-3 py-2 hover:bg-gray-50 dark:hover:bg-white/4 ${isSelected ? 'bg-indigo-50 dark:bg-indigo-950/30' : ''}`}>
                              <div className="flex items-center gap-2 flex-1 min-w-0">
                                <div className="min-w-0">
                                  <span className={`text-sm truncate block ${isSelected ? 'font-semibold text-indigo-700 dark:text-indigo-400' : 'text-gray-700 dark:text-gray-300'}`}>{displayName}</span>
                                  <span className="text-[10px] text-gray-400">{voice.lang}</span>
                                </div>
                              </div>
                              <div className="flex items-center gap-1.5 shrink-0">
                                <button type="button" onClick={(e) => { e.stopPropagation(); isPreviewing ? stopSpeaking() : previewVoice(voice.name); }} className={`p-1.5 rounded-lg transition-colors ${isPreviewing ? 'bg-amber-100 dark:bg-amber-900/50 text-amber-600' : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400'}`} title={isPreviewing ? 'Stop preview' : 'Preview voice'}>
                                  {isPreviewing ? <Square className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                                </button>
                                {!isSelected && (
                                  <button type="button" onClick={(e) => { e.stopPropagation(); applyVoice(voice.name); }} className="px-2 py-1 text-[10px] font-medium bg-indigo-500 text-white rounded-md hover:bg-indigo-600 transition-colors">Use</button>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
                {/* Export */}
                <button onClick={exportChat} className="hidden sm:block p-2 hover:bg-white/10 rounded-full transition-colors text-white/80" title="Export chat">
                  <Download className="w-5 h-5" />
                </button>
                {/* Reset */}
                <button onClick={resetConversation} className="p-2 hover:bg-white/10 rounded-full transition-colors text-white/80" title="New conversation">
                  <RotateCcw className="w-5 h-5" />
                </button>
              </div>
            </div>

            {/* Messages Area */}
            <div
              ref={scrollRef}
              className="flex-1 min-h-0 overflow-y-auto overscroll-y-contain px-3 md:px-4 py-3 md:py-4 space-y-2.5 md:space-y-3"
              style={{
                WebkitOverflowScrolling: 'touch',
                backgroundImage: 'url("data:image/svg+xml,%3Csvg width=\'60\' height=\'60\' viewBox=\'0 0 60 60\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cg fill=\'none\' fill-rule=\'evenodd\'%3E%3Cg fill=\'%239C92AC\' fill-opacity=\'0.05\'%3E%3Cpath d=\'M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z\'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E")',
              }}
            >
              {messages.map((m, i) => (
                <MessageBubble key={i} message={m} isSpeaking={isSpeaking && i === messages.length - 1 && m.role === 'assistant'} />
              ))}
              {error && (
                <div className="max-w-md mx-auto px-4 py-3 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-800 dark:text-red-400 text-sm rounded-lg">
                  {error}
                </div>
              )}
            </div>

            {/* Voice Status Bar */}
            {voiceMode && (isListening || isSpeaking) && (
              <div className="px-3 md:px-4 py-2.5 bg-indigo-50 dark:bg-indigo-950/30 border-t border-indigo-100 dark:border-indigo-900/40 flex items-center justify-center gap-2">
                {isListening && (
                  <span className="flex items-center gap-2 text-sm text-indigo-700 dark:text-indigo-400">
                    <span className="w-2.5 h-2.5 bg-indigo-500 rounded-full animate-pulse" />
                    <span className="hidden sm:inline">Listening... speak now</span>
                    <span className="sm:hidden">Listening...</span>
                  </span>
                )}
                {isSpeaking && (
                  <span className="flex items-center gap-2 text-sm text-amber-700 dark:text-amber-400">
                    <span className="w-2.5 h-2.5 bg-amber-500 rounded-full animate-pulse" />
                    Speaking...
                    <button onClick={stopSpeaking} className="ml-1 text-xs bg-amber-500 text-white px-2.5 py-1 rounded-full hover:bg-amber-600 active:bg-amber-700">Stop</button>
                  </span>
                )}
              </div>
            )}

            {/* Composer */}
            <form onSubmit={sendMessage} className="px-2 md:px-4 py-2 md:py-3 pb-safe bg-white dark:bg-zinc-900/80 border-t border-gray-100 dark:border-white/5 shrink-0">
              <div className="flex items-end gap-2">
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => {
                    setInput(e.target.value);
                    e.target.style.height = 'auto';
                    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
                  }}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
                  enterKeyHint="send"
                  placeholder={isListening ? 'Listening...' : 'Type a message'}
                  rows={1}
                  className={`flex-1 min-w-0 resize-none px-3 md:px-4 py-2.5 md:py-3 border rounded-2xl focus:outline-none focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-400 text-base bg-white dark:bg-zinc-800 dark:text-white transition-colors ${
                    isListening ? 'border-indigo-400 bg-indigo-50 dark:bg-indigo-950/20' : 'border-gray-200 dark:border-white/8'
                  }`}
                  style={{ minHeight: '44px', maxHeight: '120px' }}
                  disabled={streaming || isListening}
                />
                {/* Mic button (voice mode) */}
                {voiceMode && (
                  <button
                    type="button"
                    onClick={isListening ? stopListening : startListening}
                    disabled={streaming || isSpeaking}
                    className={`p-2.5 md:p-3 rounded-full transition-all shrink-0 ${
                      isListening ? 'bg-red-500 text-white animate-pulse' : 'bg-indigo-500 text-white disabled:bg-gray-300 dark:disabled:bg-gray-600'
                    }`}
                    title={isListening ? 'Stop listening' : 'Start speaking'}
                  >
                    {isListening ? <MicOff className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
                  </button>
                )}
                {/* Send button */}
                <button
                  type="submit"
                  disabled={streaming || !input.trim()}
                  className="p-2.5 md:p-3 bg-indigo-500 text-white rounded-full hover:bg-indigo-600 active:bg-indigo-700 disabled:bg-gray-200 dark:disabled:bg-white/8 disabled:cursor-not-allowed transition-colors shrink-0"
                >
                  <Send className="w-5 h-5" />
                </button>
              </div>
            </form>
          </>
        ) : (
          /* No caller selected — empty state */
          <div className="flex-1 flex flex-col items-center justify-center bg-gray-50 dark:bg-[#0a0a0f] text-center px-6">
            <div className="w-16 h-16 bg-indigo-50 dark:bg-indigo-950/30 rounded-2xl flex items-center justify-center mb-5">
              <MessageSquare className="w-8 h-8 text-indigo-400" />
            </div>
            <h2 className="text-base font-semibold text-gray-800 dark:text-white mb-1.5">Test Your Front Desk</h2>
            <p className="text-gray-500 dark:text-gray-400 max-w-sm mb-6">
              Select a test member from the left to practice trial bookings, class questions, and reschedules.
            </p>
            <p className="text-xs text-gray-400 dark:text-gray-500">
              Same AI, tools, and knowledge base as live phone calls
            </p>
          </div>
        )}
      </div>

      {/* ═══════════════════════════════════════════════════════════════════════
          DELETE CONFIRMATION MODAL (custom themed)
          ═══════════════════════════════════════════════════════════════════════ */}
      {deleteConfirmCaller && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            onClick={() => setDeleteConfirmCaller(null)}
          />
          {/* Modal */}
          <div className="relative bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-sm w-full overflow-hidden animate-in fade-in zoom-in-95 duration-200">
            {/* Header */}
            <div className="px-6 pt-6 pb-4">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 bg-red-100 dark:bg-red-900/30 rounded-full flex items-center justify-center shrink-0">
                  <Trash2 className="w-6 h-6 text-red-500" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-gray-900 dark:text-white">Delete Test Caller</h3>
                  <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">This action cannot be undone</p>
                </div>
              </div>
            </div>
            {/* Content */}
            <div className="px-6 pb-4">
              <p className="text-sm text-gray-600 dark:text-gray-300">
                Are you sure you want to delete <span className="font-semibold text-gray-900 dark:text-white">{deleteConfirmCaller.name}</span>?
              </p>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
                This will also clear all chat history for this test caller.
              </p>
            </div>
            {/* Actions */}
            <div className="px-6 py-4 bg-gray-50 dark:bg-gray-900/50 flex gap-3 justify-end">
              <button
                onClick={() => setDeleteConfirmCaller(null)}
                className="px-4 py-2.5 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-600 active:bg-gray-100 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={confirmDeleteCaller}
                className="px-4 py-2.5 text-sm font-medium text-white bg-red-500 rounded-xl hover:bg-red-600 active:bg-red-700 transition-colors"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function MessageBubble({ message, isSpeaking }) {
  const isUser = message.role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`relative px-3 py-2 rounded-lg text-sm leading-relaxed whitespace-pre-wrap break-words overflow-hidden shadow-sm ${
          isUser
            ? 'bg-indigo-500 text-white rounded-tr-none max-w-[80%]'
            : 'bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 rounded-tl-none max-w-[80%]'
        } ${isSpeaking && !isUser ? 'ring-2 ring-amber-400' : ''}`}
        style={{ wordBreak: 'break-word', overflowWrap: 'anywhere' }}
      >
        {/* WhatsApp-style tail */}
        <div className={`absolute top-0 w-3 h-3 ${
          isUser
            ? '-right-1.5 border-t-[12px] border-t-indigo-500 border-l-[12px] border-l-transparent'
            : '-left-1.5 border-t-[12px] border-t-white dark:border-t-gray-700 border-r-[12px] border-r-transparent'
        }`} />
        {/* Message content */}
        <div className={isSpeaking && !isUser ? 'animate-pulse' : ''}>
          {message.content || (message.pending ? <TypingDots /> : '')}
          {message.pending && message.content ? <span className="ml-1 animate-pulse">▍</span> : null}
        </div>
      </div>
    </div>
  );
}

function TypingDots() {
  return (
    <span className="inline-flex gap-1 items-center">
      <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
      <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '120ms' }} />
      <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '240ms' }} />
    </span>
  );
}
