import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { AlertCircle, LogOut } from 'lucide-react';

/**
 * ProtectedRoute — guards routes based on auth state.
 *
 * Usage:
 *   <ProtectedRoute>...</ProtectedRoute>             — requires login
 *   <ProtectedRoute requireAdmin>...</ProtectedRoute> — requires admin
 *   <ProtectedRoute requireActive>...</ProtectedRoute> — requires ACTIVE status
 */
export default function ProtectedRoute({ children, requireAdmin = false, requireActive = true }) {
  const { user, loading, isAuthenticated, isAdmin, isPending, accountError, logout } = useAuth();
  const location = useLocation();

  // Wait for initial /auth/me load
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
        <div className="w-8 h-8 border-2 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin"></div>
      </div>
    );
  }

  // Account disabled (403 from /me) — show a friendly message instead of
  // silently kicking to /login with no explanation.
  if (accountError && !isAuthenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900 px-4">
        <div className="bg-white dark:bg-gray-800 rounded-2xl border border-gray-200 dark:border-gray-700 shadow-lg max-w-md w-full p-8 text-center space-y-4">
          <div className="mx-auto w-14 h-14 bg-red-100 dark:bg-red-900/30 rounded-full flex items-center justify-center">
            <AlertCircle className="w-7 h-7 text-red-500" />
          </div>
          <h2 className="text-xl font-bold text-gray-900 dark:text-white">
            Account Unavailable
          </h2>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            {accountError}
          </p>
          <button
            onClick={logout}
            className="inline-flex items-center gap-2 px-4 py-2.5 bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
          >
            <LogOut className="w-4 h-4" />
            Back to Login
          </button>
        </div>
      </div>
    );
  }

  // Not logged in → /login (preserve where they came from)
  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Pending users → /pending (admins skip this since they're auto-active)
  if (requireActive && isPending && !isAdmin) {
    return <Navigate to="/pending" replace />;
  }

  // Admin gate
  if (requireAdmin && !isAdmin) {
    return <Navigate to="/" replace />;
  }

  // Suspended/deactivated → kick to login
  if (user && (user.status === 'SUSPENDED' || user.status === 'DEACTIVATED') && !isAdmin) {
    return <Navigate to="/login" replace />;
  }

  return children;
}
