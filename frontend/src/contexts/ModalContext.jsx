/**
 * ModalContext — themed replacements for window.alert, window.confirm, window.prompt.
 *
 * Provides:
 *   toast.error(msg)   / toast.success(msg) / toast.info(msg) — auto-dismissing toasts
 *   confirm({ title, message, ... })  → Promise<boolean>
 *   prompt({ title, message, ... })   → Promise<string|null>
 *
 * Wrap your app with <ModalProvider> and use the useModal() hook anywhere.
 */
import React, { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react';
import { AlertCircle, AlertTriangle, CheckCircle, Info, X } from 'lucide-react';

const ModalContext = createContext(null);

// ── Provider ───────────────────────────────────────────────────────────────────

export function ModalProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [modal, setModal] = useState(null);
  const resolveRef = useRef(null);

  // ── Toast API ──────────────────────────────────────────────────────────────
  const addToast = useCallback((type, message) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, type, message }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
  }, []);

  const toast = useRef({
    error: (msg) => addToast('error', msg),
    success: (msg) => addToast('success', msg),
    info: (msg) => addToast('info', msg),
    warning: (msg) => addToast('warning', msg),
  });
  // Keep callbacks fresh
  toast.current.error = (msg) => addToast('error', msg);
  toast.current.success = (msg) => addToast('success', msg);
  toast.current.info = (msg) => addToast('info', msg);
  toast.current.warning = (msg) => addToast('warning', msg);

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // ── Confirm API (returns Promise<boolean>) ─────────────────────────────────
  const confirm = useCallback(
    ({ title, message, confirmText = 'Confirm', cancelText = 'Cancel', variant = 'default', icon = null }) => {
      return new Promise((resolve) => {
        resolveRef.current = resolve;
        setModal({ type: 'confirm', title, message, confirmText, cancelText, variant, icon });
      });
    },
    [],
  );

  // ── Prompt API (returns Promise<string|null>) ──────────────────────────────
  const prompt = useCallback(
    ({ title, message, placeholder = '', confirmText = 'Confirm', cancelText = 'Cancel', variant = 'default', icon = null }) => {
      return new Promise((resolve) => {
        resolveRef.current = resolve;
        setModal({ type: 'prompt', title, message, placeholder, confirmText, cancelText, variant, icon });
      });
    },
    [],
  );

  function handleConfirm(value) {
    resolveRef.current?.(modal?.type === 'prompt' ? value : true);
    setModal(null);
    resolveRef.current = null;
  }

  function handleCancel() {
    resolveRef.current?.(modal?.type === 'prompt' ? null : false);
    setModal(null);
    resolveRef.current = null;
  }

  const value = { toast: toast.current, confirm, prompt };

  return (
    <ModalContext.Provider value={value}>
      {children}
      <ToastContainer toasts={toasts} onRemove={removeToast} />
      {modal && <ModalOverlay modal={modal} onConfirm={handleConfirm} onCancel={handleCancel} />}
    </ModalContext.Provider>
  );
}

export function useModal() {
  const ctx = useContext(ModalContext);
  if (!ctx) throw new Error('useModal must be used inside <ModalProvider>');
  return ctx;
}

// ── Toast container (fixed top-right) ──────────────────────────────────────────

const TOAST_STYLES = {
  error: {
    bg: 'bg-red-50 dark:bg-red-900/40 border-red-200 dark:border-red-800',
    icon: AlertCircle,
    iconColor: 'text-red-500',
    text: 'text-red-800 dark:text-red-300',
  },
  success: {
    bg: 'bg-green-50 dark:bg-green-900/40 border-green-200 dark:border-green-800',
    icon: CheckCircle,
    iconColor: 'text-green-500',
    text: 'text-green-800 dark:text-green-300',
  },
  warning: {
    bg: 'bg-amber-50 dark:bg-amber-900/40 border-amber-200 dark:border-amber-800',
    icon: AlertTriangle,
    iconColor: 'text-amber-500',
    text: 'text-amber-800 dark:text-amber-300',
  },
  info: {
    bg: 'bg-blue-50 dark:bg-blue-900/40 border-blue-200 dark:border-blue-800',
    icon: Info,
    iconColor: 'text-blue-500',
    text: 'text-blue-800 dark:text-blue-300',
  },
};

function ToastContainer({ toasts, onRemove }) {
  if (toasts.length === 0) return null;
  return (
    <div className="fixed top-4 right-4 z-[9999] space-y-2 max-w-sm w-full pointer-events-none">
      {toasts.map((t) => {
        const style = TOAST_STYLES[t.type] || TOAST_STYLES.info;
        const Icon = style.icon;
        return (
          <div
            key={t.id}
            className={`pointer-events-auto flex items-start gap-3 px-4 py-3 rounded-xl border shadow-lg ${style.bg} animate-slide-in`}
          >
            <Icon className={`w-5 h-5 mt-0.5 shrink-0 ${style.iconColor}`} />
            <p className={`text-sm flex-1 ${style.text}`}>{t.message}</p>
            <button
              onClick={() => onRemove(t.id)}
              className="shrink-0 p-0.5 rounded hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
            >
              <X className="w-3.5 h-3.5 text-gray-400" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ── Modal overlay (confirm / prompt) ───────────────────────────────────────────

function ModalOverlay({ modal, onConfirm, onCancel }) {
  const [inputValue, setInputValue] = useState('');
  const inputRef = useRef(null);
  const isDanger = modal.variant === 'danger';

  // Focus input when prompt modal opens
  useEffect(() => {
    if (modal.type === 'prompt' && inputRef.current) {
      inputRef.current.focus();
    }
  }, [modal.type]);

  // Close on Escape
  useEffect(() => {
    function handleKey(e) {
      if (e.key === 'Escape') onCancel();
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onCancel]);

  function handleSubmit(e) {
    e?.preventDefault?.();
    if (modal.type === 'prompt') {
      onConfirm(inputValue);
    } else {
      onConfirm(true);
    }
  }

  return (
    <div className="fixed inset-0 z-[9998] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40 dark:bg-black/60" onClick={onCancel} />

      {/* Modal card */}
      <div className="relative bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-2xl max-w-md w-full p-6 animate-modal-in">
        {/* Icon */}
        {modal.icon ? (
          <div className="mb-4">{modal.icon}</div>
        ) : isDanger ? (
          <div className="mx-auto w-12 h-12 bg-red-100 dark:bg-red-900/30 rounded-full flex items-center justify-center mb-4">
            <AlertTriangle className="w-6 h-6 text-red-500" />
          </div>
        ) : null}

        {/* Title */}
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white text-center">
          {modal.title}
        </h3>

        {/* Message */}
        {modal.message && (
          <p className="mt-2 text-sm text-gray-600 dark:text-gray-400 text-center whitespace-pre-line">
            {modal.message}
          </p>
        )}

        {/* Prompt input */}
        {modal.type === 'prompt' && (
          <form onSubmit={handleSubmit} className="mt-4">
            <input
              ref={inputRef}
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder={modal.placeholder}
              className="w-full px-4 py-2.5 border border-gray-300 dark:border-gray-600 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none dark:bg-gray-700 dark:text-white"
              autoComplete="off"
            />
          </form>
        )}

        {/* Buttons */}
        <div className="flex items-center gap-3 mt-6">
          <button
            onClick={onCancel}
            className="flex-1 px-4 py-2.5 text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg transition-colors"
          >
            {modal.cancelText}
          </button>
          <button
            onClick={handleSubmit}
            className={`flex-1 px-4 py-2.5 text-sm font-medium text-white rounded-lg transition-colors ${
              isDanger
                ? 'bg-red-500 hover:bg-red-600'
                : 'bg-indigo-500 hover:bg-indigo-600'
            }`}
          >
            {modal.confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
