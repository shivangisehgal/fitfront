import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Routes, Route, NavLink, Navigate, useNavigate, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  CalendarDays,
  BookOpen,
  Settings,
  Sparkles,
  Shield,
  LogOut,
  Rocket,
  MessageSquare,
  Users,
  ClipboardList,
  MessagesSquare,
  Contact,
  Moon,
  Sun,
  UserCircle,
  HelpCircle,
  Menu,
  X,
} from 'lucide-react';

import Dashboard from './components/Dashboard';
import AppointmentManager from './components/AppointmentManager';
import KnowledgeBase from './components/KnowledgeBase';
import AgentConfig from './components/AgentConfig';
import TenantRegister from './components/TenantRegister';
import TenantAdmin from './components/TenantAdmin';
import TenantDetail from './components/TenantDetail';
import TicketDetail from './components/TicketDetail';
import Landing from './components/Landing';
import Login from './components/Login';
import PendingApproval from './components/PendingApproval';
import SetupGuide from './components/SetupGuide';
import LocalChat from './components/LocalChat';
import ProtectedRoute from './components/ProtectedRoute';
import TrainerManager from './components/TrainerManager';
import WaitlistView from './components/WaitlistView';
import SMSConversations from './components/SMSConversations';
import CallerCRM from './components/CallerCRM';
import Profile from './components/Profile';
import SupportTickets from './components/SupportTickets';
import { useAuth } from './contexts/AuthContext';
import { useTheme } from './contexts/ThemeContext';

// ── Nav groups ────────────────────────────────────────────────────────────────
const TENANT_NAV_GROUPS = [
  {
    label: null,
    items: [
      { to: '/dashboard', icon: LayoutDashboard, label: 'Overview' },
      { to: '/setup',     icon: Rocket,          label: 'Setup Guide' },
      { to: '/chat',      icon: MessageSquare,   label: 'Test Front Desk' },
    ],
  },
  {
    label: 'Manage',
    items: [
      { to: '/contacts',     icon: Contact,      label: 'Members' },
      { to: '/appointments', icon: CalendarDays, label: 'Sessions' },
      { to: '/trainers',     icon: Users,        label: 'Trainers' },
      { to: '/waitlist',     icon: ClipboardList,label: 'Waitlist' },
      { to: '/sms',          icon: MessagesSquare,label: 'SMS Messages', requireFeature: 'twilio' },
    ],
  },
  {
    label: 'Configure',
    items: [
      { to: '/knowledge', icon: BookOpen,  label: 'Studio Info' },
      { to: '/settings',  icon: Settings,  label: 'AI Agent' },
    ],
  },
  {
    label: 'Help',
    items: [
      { to: '/support', icon: HelpCircle, label: 'Support' },
    ],
  },
];

export default function App() {
  const { user, loading, isAuthenticated } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
        <div className="w-8 h-8 border-2 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <Routes>
      <Route
        path="/"
        element={isAuthenticated ? <Navigate to={user?.is_admin ? '/admin/tenants' : '/dashboard'} replace /> : <Landing />}
      />
      <Route
        path="/login"
        element={isAuthenticated ? <Navigate to={user?.is_admin ? '/admin/tenants' : '/dashboard'} replace /> : <Login />}
      />
      <Route
        path="/register"
        element={isAuthenticated ? <Navigate to="/dashboard" replace /> : <TenantRegister />}
      />
      <Route
        path="/pending"
        element={
          <ProtectedRoute requireActive={false}>
            <PendingApproval />
          </ProtectedRoute>
        }
      />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}

// ── Sidebar nav item ──────────────────────────────────────────────────────────
function SideNavItem({ to, icon: Icon, label, onClick, collapsed }) {
  return (
    <NavLink
      to={to}
      end={false}
      onClick={onClick}
      title={collapsed ? label : undefined}
      className={({ isActive }) =>
        `sidebar-item${isActive ? ' active' : ''}${collapsed ? ' justify-center !px-0 mx-1 !gap-0' : ''}`
      }
    >
      <Icon className="w-4 h-4 shrink-0" />
      {!collapsed && <span className="leading-tight min-w-0 break-words">{label}</span>}
    </NavLink>
  );
}

// ── Sidebar content ───────────────────────────────────────────────────────────
function SidebarContent({ user, isAdmin, dark, toggleTheme, navigate, location, handleLogout, onNavClick, collapsed, onToggleCollapse }) {
  const initials = (user?.name || user?.email || '?').charAt(0).toUpperCase();

  const filteredGroups = TENANT_NAV_GROUPS.map(group => ({
    ...group,
    items: group.items.filter(({ requireFeature }) => {
      if (!requireFeature) return true;
      if (requireFeature === 'twilio') return user?.twilio_enabled !== false;
      return true;
    }),
  })).filter(g => g.items.length > 0);

  const labelClass = collapsed
    ? 'hidden'
    : 'text-[10px] font-semibold uppercase tracking-widest text-gray-400 dark:text-white/20 px-3 mb-1';

  return (
    <div className="flex flex-col h-full sidebar-bg overflow-hidden">
      {/* Logo + collapse toggle */}
      <div className={`flex items-center border-b border-gray-100 dark:border-white/5 ${collapsed ? 'justify-center px-2 py-3' : 'justify-between px-3 py-3'}`}>
        {!collapsed && (
          <button
            onClick={() => { navigate(isAdmin ? '/admin/tenants' : '/dashboard'); onNavClick?.(); }}
            className="flex items-center gap-2.5 rounded-lg hover:bg-gray-100 dark:hover:bg-white/5 transition-colors btn-press px-1 py-1 flex-1 min-w-0"
          >
            <div className="w-7 h-7 rounded-lg bg-indigo-500 flex items-center justify-center shrink-0">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <p className="font-semibold text-gray-900 dark:text-white text-sm leading-tight break-words min-w-0">
              {user?.business_name || 'FitFront'}
            </p>
          </button>
        )}
        {onToggleCollapse && (
          <button
            onClick={onToggleCollapse}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="p-1.5 rounded-lg text-gray-400 dark:text-white/30 hover:text-gray-600 dark:hover:text-white/70 hover:bg-gray-100 dark:hover:bg-white/8 transition-colors shrink-0"
          >
            <Menu className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-3">
        {!isAdmin && filteredGroups.map((group, gi) => (
          <div key={gi}>
            {group.label && <p className={labelClass}>{group.label}</p>}
            <div className="space-y-0.5">
              {group.items.map(item => (
                <SideNavItem key={item.to} {...item} onClick={onNavClick} collapsed={collapsed} />
              ))}
            </div>
          </div>
        ))}

        {isAdmin && (
          <div className="space-y-0.5">
            <NavLink
              to="/admin/tenants"
              onClick={onNavClick}
              title={collapsed ? 'Manage Tenants' : undefined}
              className={({ isActive }) => `sidebar-item${isActive ? ' active' : ''}${collapsed ? ' justify-center !px-0 mx-1 !gap-0' : ''}`}
            >
              <Shield className="w-4 h-4 shrink-0" />
              {!collapsed && <span>Manage Tenants</span>}
            </NavLink>
          </div>
        )}
      </nav>

      {/* Bottom controls */}
      <div className={`pb-3 pt-2 space-y-0.5 border-t border-gray-100 dark:border-white/5 ${collapsed ? 'px-1' : 'px-2'}`}>
        {/* User card */}
        <button
          onClick={() => { navigate('/profile'); onNavClick?.(); }}
          title={collapsed ? (user?.name || 'Profile') : undefined}
          className={`w-full flex items-center rounded-lg transition-all duration-100 btn-press ${collapsed ? 'justify-center p-2' : 'gap-2.5 px-3 py-2'} ${
            location.pathname === '/profile'
              ? 'bg-indigo-100/60 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300'
              : 'text-gray-500 dark:text-white/50 hover:bg-gray-100 dark:hover:bg-white/5 hover:text-gray-800 dark:hover:text-white/85'
          }`}
        >
          <div className="w-6 h-6 rounded-md bg-indigo-500 flex items-center justify-center text-[10px] font-bold text-white shrink-0">
            {initials}
          </div>
          {!collapsed && (
            <p className="text-sm font-medium truncate flex-1 text-left">{user?.name || 'Account'}</p>
          )}
        </button>

        {/* Theme toggle */}
        <button
          onClick={toggleTheme}
          title={collapsed ? (dark ? 'Light mode' : 'Dark mode') : undefined}
          className={`sidebar-item w-full btn-press ${collapsed ? 'justify-center !px-0 mx-1 !gap-0' : ''}`}
        >
          {dark ? <Sun className="w-4 h-4 shrink-0" /> : <Moon className="w-4 h-4 shrink-0" />}
          {!collapsed && <span>{dark ? 'Light mode' : 'Dark mode'}</span>}
        </button>

        {/* Sign out */}
        <button
          onClick={() => { handleLogout(); onNavClick?.(); }}
          title={collapsed ? 'Sign out' : undefined}
          className={`sidebar-item w-full btn-press hover:text-red-500 hover:bg-red-50 dark:hover:text-red-400 dark:hover:bg-red-500/10 ${collapsed ? 'justify-center !px-0 mx-1 !gap-0' : ''}`}
        >
          <LogOut className="w-4 h-4 shrink-0" />
          {!collapsed && <span>Sign out</span>}
        </button>
      </div>
    </div>
  );
}

// ── App shell ─────────────────────────────────────────────────────────────────
function AppShell() {
  const { user, logout, isAdmin } = useAuth();
  const { dark, toggle: toggleTheme } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() =>
    localStorage.getItem('sidebar-collapsed') === 'true'
  );
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = parseInt(localStorage.getItem('sidebar-width'), 10);
    return isNaN(saved) ? 224 : Math.max(160, Math.min(360, saved));
  });
  const sidebarRef = useRef(null);
  const dragRef = useRef({ startX: 0, startW: 0 });

  useEffect(() => {
    setSidebarOpen(false);
  }, [location.pathname]);

  // Drag-to-resize: update DOM directly for zero-lag, commit to state on mouseup
  const onDragStart = useCallback((e) => {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startW: sidebarWidth };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    // Kill transition during drag so there's no animation lag
    if (sidebarRef.current) sidebarRef.current.style.transition = 'none';

    function onMove(ev) {
      const next = Math.max(160, Math.min(360, dragRef.current.startW + ev.clientX - dragRef.current.startX));
      // Direct DOM update — no React re-render each pixel
      if (sidebarRef.current) sidebarRef.current.style.width = next + 'px';
    }
    function onUp(ev) {
      const next = Math.max(160, Math.min(360, dragRef.current.startW + ev.clientX - dragRef.current.startX));
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      // Restore transition for collapse/expand animation
      if (sidebarRef.current) sidebarRef.current.style.transition = '';
      localStorage.setItem('sidebar-width', String(next));
      setSidebarWidth(next);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    }
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [sidebarWidth]);

  function handleLogout() {
    logout();
    navigate('/login', { replace: true });
  }

  function toggleCollapse() {
    setCollapsed(c => {
      const next = !c;
      localStorage.setItem('sidebar-collapsed', String(next));
      return next;
    });
  }

  const sidebarProps = {
    user, isAdmin, dark, toggleTheme, navigate, location, handleLogout,
    onNavClick: () => setSidebarOpen(false),
    collapsed,
    onToggleCollapse: toggleCollapse,
  };

  return (
    <div className="flex h-dvh bg-gray-50 dark:bg-[#0a0a0f]">
      {/* Mobile top bar */}
      <div className="fixed top-0 left-0 right-0 z-30 md:hidden bg-white dark:bg-gray-900/90 backdrop-blur-sm border-b border-gray-200 dark:border-white/5 px-4 py-3 flex items-center justify-between">
        <button
          onClick={() => setSidebarOpen(true)}
          className="p-2 -ml-2 rounded-lg text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-white/5 transition-colors"
          aria-label="Open menu"
        >
          <Menu className="w-5 h-5" />
        </button>
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-indigo-500 flex items-center justify-center">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <span className="font-semibold text-gray-900 dark:text-white text-sm truncate max-w-[160px]">
            {user?.business_name || 'FitFront'}
          </span>
        </div>
        <button
          onClick={() => navigate('/profile')}
          className="p-2 -mr-2 rounded-lg text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-white/5 transition-colors"
          aria-label="Profile"
        >
          <UserCircle className="w-5 h-5" />
        </button>
      </div>

      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 md:hidden animate-fade-in"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Mobile sidebar drawer */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-72 flex flex-col transform transition-transform duration-200 ease-in-out md:hidden ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="absolute top-3 right-3 z-10">
          <button
            onClick={() => setSidebarOpen(false)}
            className="p-1.5 rounded-lg text-gray-400 dark:text-white/40 hover:bg-gray-100 dark:hover:bg-white/10 hover:text-gray-600 dark:hover:text-white/80 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <SidebarContent {...sidebarProps} collapsed={false} onToggleCollapse={undefined} />
      </aside>

      {/* Desktop sidebar — collapsible + drag-resizable */}
      <aside
        ref={sidebarRef}
        className="hidden md:flex flex-col shrink-0 relative"
        style={{ width: collapsed ? 56 : sidebarWidth, transition: 'width 200ms ease-in-out' }}
      >
        <SidebarContent {...sidebarProps} />
        {/* Drag handle */}
        {!collapsed && (
          <div
            onMouseDown={onDragStart}
            className="absolute top-0 right-0 w-1 h-full cursor-col-resize hover:bg-indigo-400/30 active:bg-indigo-400/50 transition-colors z-20"
            title="Drag to resize"
          />
        )}
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto overscroll-y-contain bg-gray-50 dark:bg-[#0a0a0f] pt-[56px] md:pt-0">
        <Routes>
          <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
          <Route path="/setup"     element={<ProtectedRoute><SetupGuide /></ProtectedRoute>} />
          <Route path="/chat"      element={<ProtectedRoute><LocalChat /></ProtectedRoute>} />
          <Route path="/contacts"  element={<ProtectedRoute><CallerCRM /></ProtectedRoute>} />
          <Route path="/contacts/:id" element={<ProtectedRoute><CallerCRM /></ProtectedRoute>} />
          <Route path="/appointments" element={<ProtectedRoute><AppointmentManager /></ProtectedRoute>} />
          <Route path="/trainers"  element={<ProtectedRoute><TrainerManager /></ProtectedRoute>} />
          <Route path="/providers" element={<Navigate to="/trainers" replace />} />
          <Route path="/waitlist"  element={<ProtectedRoute><WaitlistView /></ProtectedRoute>} />
          <Route path="/sms"           element={<ProtectedRoute><SMSConversations /></ProtectedRoute>} />
          <Route path="/sms/:callerId" element={<ProtectedRoute><SMSConversations /></ProtectedRoute>} />
          <Route path="/knowledge" element={<ProtectedRoute><KnowledgeBase /></ProtectedRoute>} />
          <Route path="/settings"  element={<ProtectedRoute><AgentConfig /></ProtectedRoute>} />
          <Route path="/support"   element={<ProtectedRoute><SupportTickets /></ProtectedRoute>} />
          <Route path="/profile"   element={<ProtectedRoute><Profile /></ProtectedRoute>} />
          <Route
            path="/admin/tenants"
            element={<ProtectedRoute requireAdmin requireActive={false}><TenantAdmin /></ProtectedRoute>}
          />
          <Route
            path="/admin/tenants/:id"
            element={<ProtectedRoute requireAdmin requireActive={false}><TenantDetail /></ProtectedRoute>}
          />
          <Route
            path="/admin/tickets/:id"
            element={<ProtectedRoute requireAdmin requireActive={false}><TicketDetail /></ProtectedRoute>}
          />
          <Route path="*" element={<Navigate to={isAdmin ? '/admin/tenants' : '/dashboard'} replace />} />
        </Routes>
      </main>

      {/* Floating Support Button — hidden on /support and /chat pages */}
      {!['/support', '/chat'].includes(location.pathname) && (
        <NavLink
          to="/support"
          title="Get support"
          className="fixed bottom-6 right-6 z-50 w-12 h-12 rounded-full bg-indigo-600 hover:bg-indigo-700 text-white shadow-lg hover:shadow-xl flex items-center justify-center transition-all duration-200 hover:scale-110"
        >
          <HelpCircle className="w-5 h-5" />
        </NavLink>
      )}
    </div>
  );
}
