import React, { useEffect } from 'react';
import { Clock, LogOut, RefreshCw, Mail, Sparkles } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';
import ThemeToggle from './ThemeToggle';

export default function PendingApproval() {
  const { user, logout, refreshUser } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    const tick = async () => {
      const updated = await refreshUser();
      if (updated && updated.status === 'ACTIVE') {
        navigate('/', { replace: true });
      }
    };
    const interval = setInterval(tick, 30000);
    return () => clearInterval(interval);
  }, [refreshUser, navigate]);

  function handleLogout() {
    logout();
    navigate('/login', { replace: true });
  }

  async function handleRefresh() {
    const updated = await refreshUser();
    if (updated && updated.status === 'ACTIVE') {
      navigate('/', { replace: true });
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0a0a0f] p-4 relative">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>

      <div className="w-full max-w-md animate-slide-up">
        <div className="text-center mb-6">
          <div className="mx-auto w-14 h-14 rounded-2xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center mb-4">
            <Clock className="w-7 h-7 text-amber-400" />
          </div>
          <h1 className="text-2xl font-bold text-white">Awaiting Approval</h1>
          <p className="text-white/40 mt-1 text-sm">Your studio registration is being reviewed</p>
        </div>

        <div className="bg-white/4 rounded-2xl border border-white/8 p-6 space-y-4">
          {user && (
            <>
              <div className="flex items-center gap-3 pb-4 border-b border-white/8">
                <div className="w-10 h-10 rounded-xl bg-indigo-500 flex items-center justify-center shrink-0">
                  <Sparkles className="w-5 h-5 text-white" />
                </div>
                <div className="min-w-0">
                  <p className="font-semibold text-white truncate">{user.business_name}</p>
                  <p className="text-xs text-white/40 truncate">{user.email}</p>
                </div>
              </div>

              <div className="bg-amber-500/8 border border-amber-500/20 rounded-xl p-4">
                <div className="flex items-start gap-3">
                  <Clock className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />
                  <div>
                    <p className="text-sm font-medium text-amber-300">Status: Pending</p>
                    <p className="text-sm text-amber-400/70 mt-0.5">
                      An admin will review your registration shortly. Once approved, you'll have full access.
                    </p>
                  </div>
                </div>
              </div>

              <div className="bg-blue-500/8 border border-blue-500/20 rounded-xl p-4">
                <div className="flex items-start gap-3">
                  <Mail className="w-4 h-4 text-blue-400 mt-0.5 shrink-0" />
                  <div>
                    <p className="text-sm font-medium text-blue-300">What happens next?</p>
                    <ul className="text-sm text-blue-400/70 mt-1 space-y-1 list-disc list-inside">
                      <li>An admin reviews your application</li>
                      <li>You'll be notified at <strong className="text-blue-300">{user.email}</strong></li>
                      <li>This page auto-refreshes every 30 seconds</li>
                    </ul>
                  </div>
                </div>
              </div>
            </>
          )}

          <div className="flex items-center gap-3 pt-1">
            <button
              onClick={handleRefresh}
              className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border border-white/10 bg-white/5 text-white/70 hover:bg-white/8 hover:text-white text-sm font-medium transition-all btn-press"
            >
              <RefreshCw className="w-4 h-4" />
              Check status
            </button>
            <button
              onClick={handleLogout}
              className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl border border-red-500/20 bg-red-500/8 text-red-400 hover:bg-red-500/15 text-sm font-medium transition-all btn-press"
            >
              <LogOut className="w-4 h-4" />
              Sign out
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
