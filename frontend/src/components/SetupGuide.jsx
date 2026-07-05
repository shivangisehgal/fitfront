import React, { useState, useEffect } from 'react';
import {
  Phone,
  CalendarCheck,
  MessageSquare,
  CheckCircle2,
  Circle,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Settings,
  Sparkles,
  ArrowRight,
  ShieldCheck,
  Zap,
} from 'lucide-react';
import { apiFetch } from '../lib/api';
import { useAuth } from '../contexts/AuthContext';
import { Link } from 'react-router-dom';

/**
 * SetupGuide — guided onboarding for new tenants.
 */
export default function SetupGuide() {
  const { user } = useAuth();
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState({ business: true, calendar: false, integrations: false });

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await apiFetch('/api/config');
        if (!cancelled) setConfig(data);
      } catch (err) {
        console.error('Failed to load config:', err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  const calendarConnected = config?.google_calendar_connected ?? false;
  const twilioConfigured = config?.twilio_configured ?? user?.twilio_configured ?? false;
  const twilioEnabled = config?.twilio_enabled ?? true;
  const isFitnessStudio = user?.business_type === 'fitness_studio';

  const steps = [
    {
      key: 'business',
      icon: Settings,
      iconColor: 'text-blue-500',
      iconBg: 'bg-blue-50 dark:bg-blue-900/30',
      title: isFitnessStudio ? 'Configure Your Studio' : 'Configure Your Business',
      done: true,
      shortDesc: isFitnessStudio
        ? 'Studio hours, session types, programs, and your front-desk agent persona.'
        : 'Business hours, appointment types, and agent personality.',
      details: isFitnessStudio
        ? [
            'Set your studio hours so the AI knows when trial session slots are available',
            'Define session types (Trial Session, Personal Training, Group Class) with durations',
            'Add the programs you offer — Strength, Yoga, HIIT, etc.',
            'Customise your front-desk agent\'s name, greeting, and voice',
            'Add FAQs — membership fees, class schedule, trainer bios, cancellation policy',
          ]
        : [
            'Set your business hours so the AI knows when you\'re open',
            'Define appointment types (consultation, follow-up, etc.) with durations',
            'Customise your AI agent\'s name, greeting, and voice',
            'Add your knowledge base — FAQs, policies, directions',
          ],
      setupSteps: isFitnessStudio
        ? [
            { step: 'Open Agent Config', detail: 'Go to the Settings page → configure your studio hours, session types, and agent details.' },
            { step: 'Add Programs & Classes', detail: 'In Studio Info → "Programs & Classes" — add every program you offer (Strength, Yoga, HIIT, etc.) with a short description.' },
            { step: 'Add your trainers', detail: 'Go to Trainers → add each trainer with their specialty and trial session availability.' },
            { step: 'Add Knowledge Base entries', detail: 'Add membership fees, trainer bios, class schedule, and frequently asked questions.' },
          ]
        : [
            { step: 'Open Agent Config', detail: 'Go to the Settings page and configure your business hours, appointment types, and agent details.' },
            { step: 'Customise your AI agent', detail: 'Set the agent name, greeting message, and voice.' },
            { step: 'Add knowledge base entries', detail: 'Add FAQs, pricing info, directions, and policies so the AI can answer caller questions accurately.' },
          ],
    },
    {
      key: 'calendar',
      icon: CalendarCheck,
      iconColor: 'text-emerald-500',
      iconBg: 'bg-emerald-50 dark:bg-emerald-900/30',
      title: 'Connect Google Calendar',
      done: calendarConnected,
      shortDesc: 'One-click OAuth — lets the AI check availability and create bookings.',
      details: [
        'Lets the agent check your real-time availability before booking',
        'Creates actual calendar events (no double-bookings)',
        'Free, connects in one click — no API keys needed',
      ],
      setupSteps: [
        { step: 'Connect Google Calendar (Recommended)', detail: 'Go to Agent Config → Google Calendar section → click "Connect Google Calendar". One-click OAuth — no API keys needed.' },
        { step: 'Or use the built-in scheduler', detail: 'Without Google Calendar, the system uses your configured business hours to manage availability automatically.' },
      ],
    },
    ...(twilioEnabled ? [{
      key: 'integrations',
      icon: Zap,
      iconColor: 'text-indigo-500',
      iconBg: 'bg-indigo-50 dark:bg-indigo-900/30',
      title: 'SMS (Managed by Platform)',
      done: !twilioEnabled || twilioConfigured,
      shortDesc: 'SMS infrastructure is handled centrally — no setup needed from you.',
      details: [
        'Twilio (SMS) is managed centrally by FitFront',
        'Your dedicated phone number is assigned by the platform admin after approval',
        'No API keys, no third-party accounts to create',
      ],
      setupSteps: [
        { step: 'Nothing to do here!', detail: 'Once your account is approved, the platform admin will assign your dedicated phone number.' },
      ],
    }] : []),
  ];

  const allReady = !twilioEnabled || twilioConfigured;

  if (loading) {
    return (
      <div className="p-5 md:p-8 space-y-5 max-w-3xl mx-auto">
        <div className="h-8 w-56 rounded-lg animate-shimmer" />
        <div className="h-24 rounded-xl animate-shimmer" />
        {[0, 1, 2].map(i => (
          <div key={i} className="h-20 rounded-xl animate-shimmer" />
        ))}
      </div>
    );
  }

  return (
    <div className="p-5 md:p-8 max-w-3xl mx-auto space-y-5 animate-fade-in">

      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-9 h-9 rounded-xl bg-indigo-500 flex items-center justify-center shadow-lg shadow-indigo-500/20 shrink-0">
          <Sparkles className="w-4.5 h-4.5 text-white" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-gray-900 dark:text-white">
            Welcome, {user?.business_name}!
          </h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {isFitnessStudio
              ? "Let's get your AI front desk set up."
              : "Let's get your AI voice agent set up. Most of the heavy lifting is done for you."}
          </p>
        </div>
      </div>

      {/* Status banner */}
      <div className={`rounded-xl border p-4 ${
        allReady
          ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'
          : 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800'
      }`}>
        <div className="flex items-start gap-3">
          {allReady
            ? <CheckCircle2 className="w-5 h-5 text-green-600 dark:text-green-400 mt-0.5 shrink-0" />
            : <AlertCircle className="w-5 h-5 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
          }
          <div className="flex-1 min-w-0">
            <p className={`text-sm font-semibold ${allReady ? 'text-green-900 dark:text-green-300' : 'text-amber-900 dark:text-amber-300'}`}>
              {allReady ? "You're ready to go live!" : 'Almost there — configure your studio and wait for admin provisioning'}
            </p>
            <p className={`text-xs mt-0.5 ${allReady ? 'text-green-700 dark:text-green-400' : 'text-amber-700 dark:text-amber-400'}`}>
              {allReady
                ? 'All services are connected. Your AI agent can now answer calls, book sessions, and send SMS reminders.'
                : 'Set up your studio info and calendar. Phone and SMS will be activated by the platform admin after your account is approved.'}
            </p>
            <div className="mt-3 flex items-center gap-2 flex-wrap">
              <Link
                to="/settings"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300 rounded-lg text-xs font-medium hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
              >
                <Settings className="w-3.5 h-3.5" />
                Open Agent Config
              </Link>
              {allReady && (
                <Link
                  to="/"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-indigo-500 text-white rounded-lg text-xs font-medium hover:bg-indigo-600 transition-colors"
                >
                  Go to Dashboard
                  <ArrowRight className="w-3.5 h-3.5" />
                </Link>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Step cards */}
      <div className="space-y-3">
        {steps.map((step, stepIdx) => {
          const Icon = step.icon;
          const isOpen = expanded[step.key];
          return (
            <div
              key={step.key}
              className="bg-white dark:bg-zinc-900/70 rounded-xl border border-gray-100 dark:border-zinc-800 overflow-hidden"
            >
              {/* Accordion header */}
              <button
                onClick={() => setExpanded(prev => ({ ...prev, [step.key]: !prev[step.key] }))}
                className="w-full p-4 flex items-center gap-4 text-left hover:bg-gray-50/70 dark:hover:bg-white/[0.02] transition-colors"
              >
                {/* Icon */}
                <div className={`w-10 h-10 rounded-xl ${step.iconBg} flex items-center justify-center shrink-0`}>
                  <Icon className={`w-5 h-5 ${step.iconColor}`} />
                </div>

                {/* Title + status */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-semibold text-gray-900 dark:text-white">{step.title}</span>
                    {step.done ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400 border border-green-200 dark:border-green-800 rounded-full text-xs font-medium">
                        <CheckCircle2 className="w-3 h-3" />
                        {step.key === 'integrations' ? 'Provisioned' : 'Done'}
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-100 dark:bg-zinc-800 text-gray-500 dark:text-gray-400 border border-gray-200 dark:border-zinc-700 rounded-full text-xs font-medium">
                        <Circle className="w-3 h-3" />
                        {step.key === 'integrations' ? 'Pending admin setup' : 'To do'}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 truncate">{step.shortDesc}</p>
                </div>

                {isOpen
                  ? <ChevronUp className="w-4 h-4 text-gray-400 shrink-0" />
                  : <ChevronDown className="w-4 h-4 text-gray-400 shrink-0" />
                }
              </button>

              {/* Expanded content */}
              {isOpen && (
                <div className="border-t border-gray-100 dark:border-zinc-800 p-4 space-y-4">

                  {/* What it covers */}
                  <div>
                    <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">What this covers</p>
                    <ul className="space-y-1.5">
                      {step.details.map((item, idx) => (
                        <li key={idx} className="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300">
                          <CheckCircle2 className={`w-3.5 h-3.5 ${step.iconColor} mt-0.5 shrink-0`} />
                          {item}
                        </li>
                      ))}
                    </ul>
                  </div>

                  {/* How to set up */}
                  <div>
                    <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">
                      {step.key === 'integrations' ? 'How it works' : 'How to set it up'}
                    </p>
                    <ol className="space-y-3">
                      {step.setupSteps.map((s, idx) => (
                        <li key={idx} className="flex items-start gap-3">
                          <span className={`shrink-0 w-5 h-5 rounded-full ${step.iconBg} ${step.iconColor} flex items-center justify-center text-[10px] font-bold`}>
                            {step.key === 'integrations' ? <ShieldCheck className="w-3 h-3" /> : idx + 1}
                          </span>
                          <div>
                            <p className="text-sm font-medium text-gray-900 dark:text-white">{s.step}</p>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{s.detail}</p>
                          </div>
                        </li>
                      ))}
                    </ol>
                  </div>

                  {/* Integration status rows */}
                  {step.key === 'integrations' && (
                    <div className="space-y-2">
                      {twilioEnabled && (
                        <div className="flex items-center gap-3 p-3 bg-gray-50 dark:bg-zinc-800/60 rounded-lg border border-gray-100 dark:border-zinc-700">
                          <MessageSquare className="w-4 h-4 text-pink-400 shrink-0" />
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-gray-900 dark:text-white">Twilio SMS</p>
                            <p className="text-xs text-gray-500 dark:text-gray-400">Session reminders and post-workout follow-up texts</p>
                          </div>
                          {twilioConfigured ? (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400 border border-green-200 dark:border-green-800 rounded-full text-xs font-medium shrink-0">
                              <CheckCircle2 className="w-3 h-3" /> Active
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border border-amber-200 dark:border-amber-800 rounded-full text-xs font-medium shrink-0">
                              <Circle className="w-3 h-3" /> Pending
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* CTA */}
                  {step.key !== 'integrations' && (
                    <Link
                      to="/settings"
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-gray-50 dark:bg-zinc-800 border border-gray-200 dark:border-zinc-700 text-gray-700 dark:text-gray-300 rounded-lg text-xs font-medium hover:bg-gray-100 dark:hover:bg-zinc-700 transition-colors"
                    >
                      <Settings className="w-3.5 h-3.5" />
                      {step.key === 'business' ? 'Open Agent Config' : 'Connect Calendar in Agent Config'}
                    </Link>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

    </div>
  );
}
