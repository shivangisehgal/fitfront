"""
Async SQLAlchemy database engine, session factory, and table initialisation.
"""
from __future__ import annotations


import logging
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import settings

logger = logging.getLogger(__name__)

# ── Sanitise DATABASE_URL for asyncpg compatibility ─────────────────────────
# Cloud providers like Neon append query params (channel_binding, sslmode)
# that asyncpg doesn't understand.  Strip them so the engine can connect.

_ASYNCPG_UNSUPPORTED_PARAMS = {"channel_binding", "sslmode"}


def _sanitise_db_url(raw_url: str) -> str:
    """Remove query-string params that asyncpg cannot handle."""
    parsed = urlparse(raw_url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    # Convert sslmode=require → ssl=require (asyncpg uses 'ssl')
    if "sslmode" in params and "ssl" not in params:
        params["ssl"] = params["sslmode"]

    cleaned = {k: v for k, v in params.items() if k not in _ASYNCPG_UNSUPPORTED_PARAMS}
    new_query = urlencode(cleaned, doseq=True)
    sanitised = urlunparse(parsed._replace(query=new_query))
    if sanitised != raw_url:
        logger.info("Sanitised DATABASE_URL: removed unsupported asyncpg params %s",
                     [k for k in params if k in _ASYNCPG_UNSUPPORTED_PARAMS])
    return sanitised


_db_url = _sanitise_db_url(settings.DATABASE_URL)

# ── Engine & session factory ──────────────────────────────────────────────────

engine = create_async_engine(
    _db_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ── Base model ────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


# ── Schema migrations ───────────────────────────────────────────────────────
# Since we don't use Alembic, new columns on *existing* tables must be added
# via ALTER TABLE.  create_all() only creates new tables — it won't touch
# existing ones.  Each statement uses IF NOT EXISTS so it's safe to re-run.

_MIGRATIONS: list[str] = [
    # ── tenants table — reminder & review settings ────────────────────────
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS reminder_settings JSONB DEFAULT '{\"2h_enabled\": true, \"confirmation_reply_enabled\": true}'::jsonb",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS review_settings JSONB DEFAULT '{\"enabled\": false, \"google_review_link\": \"\", \"delay_hours\": 24, \"appointment_types\": []}'::jsonb",

    # ── tenants table — test caller phone for Test Agent chat
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS test_caller_phone VARCHAR(20)",
    # Backfill existing tenants with a random +155501XXX test phone
    "UPDATE tenants SET test_caller_phone = '+155501' || (100 + floor(random() * 100))::int::text WHERE test_caller_phone IS NULL",

    # ── tenants table — multiple test caller phones (JSONB array)
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS test_caller_phones JSONB DEFAULT '[]'::jsonb",
    # Backfill: seed array from existing single test_caller_phone if not yet populated
    """UPDATE tenants
       SET test_caller_phones = jsonb_build_array(test_caller_phone)
       WHERE test_caller_phone IS NOT NULL
         AND (test_caller_phones IS NULL OR test_caller_phones = '[]'::jsonb)""",

    # ── tenants table — unified test_callers [{phone, name}] (replaces parallel arrays)
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS test_callers JSONB DEFAULT '[]'::jsonb",

    # ── tenants table — Google Maps share-link for the office location
    #    Used at signup for admin approval review (iframe preview) and inside
    #    SMS / email templates as a clickable map link.
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS google_maps_url TEXT",

    # ── tenants table — list of one-off holiday closures
    #    Each entry: {"date": "YYYY-MM-DD", "name": "Christmas Day"}
    #    Used by the slot generator (refuses bookings on these dates) and by
    #    the AI agent (proactively tells callers when a day is a holiday).
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS holidays JSONB NOT NULL DEFAULT '[]'::jsonb",

    # ── appointments table — provider, extra reminders, caller confirmation
    "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS provider_id UUID REFERENCES faculty(id)",
    "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_2h_sent_at TIMESTAMPTZ",
    "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS review_requested_at TIMESTAMPTZ",
    "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS confirmed_by_student BOOLEAN",

    # ── tenants table — drop orphaned calcom_event_types column
    #    This column was added manually during a Cal.com integration experiment
    #    but never added to the SQLAlchemy model.  Its NOT NULL constraint causes
    #    INSERT failures on tenant registration.  Safe to drop — no code reads it.
    "ALTER TABLE tenants DROP COLUMN IF EXISTS calcom_event_types",

    # ── appointmentstatus enum — add NO_SHOW value (safe if already exists)
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'NO_SHOW' AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'appointmentstatus')) THEN
            ALTER TYPE appointmentstatus ADD VALUE 'NO_SHOW';
        END IF;
    END $$;""",

    # ── appointments — drop the old single-slot-capacity unique index.
    #    With slot_capacity > 1, multiple bookings at the same time/provider
    #    are allowed. Slot capacity is now enforced at the application level via
    #    pre-booking checks in create_native_booking and reschedule_native_booking.
    "DROP INDEX IF EXISTS uniq_appt_provider_time",

    # ── tenants table — usage metering columns (Option A billing) ────────
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS call_minutes_used DOUBLE PRECISION NOT NULL DEFAULT 0",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS sms_sent INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS current_period_start TIMESTAMPTZ",

    # ── tenants table — per-tenant feature flags for Vapi & Twilio ──────
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS feature_vapi_enabled BOOLEAN NOT NULL DEFAULT true",
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS feature_twilio_enabled BOOLEAN NOT NULL DEFAULT true",

    # ── support_tickets table — generic help / ticket system ────────────
    """CREATE TABLE IF NOT EXISTS support_tickets (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        subject VARCHAR(200) NOT NULL,
        body TEXT NOT NULL,
        category VARCHAR(20) NOT NULL DEFAULT 'GENERAL',
        status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
        priority VARCHAR(10) NOT NULL DEFAULT 'MEDIUM',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        resolved_at TIMESTAMPTZ,
        admin_notes TEXT,
        resolved_by VARCHAR(255)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_support_tickets_tenant_status ON support_tickets (tenant_id, status)",

    # ── support_ticket_messages table — conversation thread per ticket ───
    """CREATE TABLE IF NOT EXISTS support_ticket_messages (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        ticket_id UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
        sender_type VARCHAR(10) NOT NULL,
        sender_name VARCHAR(255) NOT NULL,
        body TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS ix_ticket_messages_ticket_created ON support_ticket_messages (ticket_id, created_at)",

    # ── appointments table — make provider_id NOT NULL for new rows ──────
    # Backfill existing NULL provider_ids with tenant's first active provider
    """DO $$ BEGIN
        UPDATE appointments a
        SET provider_id = (
            SELECT p.id FROM providers p
            WHERE p.tenant_id = a.tenant_id AND p.is_active = true
            ORDER BY p.created_at ASC LIMIT 1
        )
        WHERE a.provider_id IS NULL
          AND EXISTS (
            SELECT 1 FROM providers p
            WHERE p.tenant_id = a.tenant_id AND p.is_active = true
          );
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'Provider backfill skipped: %', SQLERRM;
    END $$;""",

    # ── is_test flag — separate test/demo data from real caller data ──
    "ALTER TABLE callers ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE waitlist_entries ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE sms_messages ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false",

    # ── phone normalisation — strip dashes, spaces, parentheses ────────
    #    Historic data may contain "635-241-8405" or "(512) 555-1234" which
    #    breaks suffix-match queries.  Strip all non-digit / non-plus chars
    #    so stored phones look like "+16352418405" or "6352418405".
    "UPDATE appointments SET student_phone = regexp_replace(student_phone, '[^0-9+]', '', 'g') WHERE student_phone ~ '[^0-9+]'",
    "UPDATE callers SET phone = regexp_replace(phone, '[^0-9+]', '', 'g') WHERE phone ~ '[^0-9+]'",
    "UPDATE waitlist_entries SET student_phone = regexp_replace(student_phone, '[^0-9+]', '', 'g') WHERE student_phone IS NOT NULL AND student_phone ~ '[^0-9+]'",
    "UPDATE sms_messages SET to_number = regexp_replace(to_number, '[^0-9+]', '', 'g') WHERE to_number IS NOT NULL AND to_number ~ '[^0-9+]'",

    # ── tenants table — owner-toggleable agent on/off (separate from status)
    "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS agent_active BOOLEAN NOT NULL DEFAULT true",

    # ── providers table — subject and demo time windows (coaching institutes) ─
    "ALTER TABLE providers ADD COLUMN IF NOT EXISTS subject VARCHAR(255)",
    "ALTER TABLE providers ADD COLUMN IF NOT EXISTS max_demo_slots_per_day INTEGER",
    # Replace integer daily cap with JSONB fixed-window time slots
    "ALTER TABLE providers DROP COLUMN IF EXISTS max_demo_slots_per_day",
    "ALTER TABLE providers ADD COLUMN IF NOT EXISTS demo_time_slots JSONB",

    # ── appointment_status_history — audit trail for status changes ──
    """CREATE TABLE IF NOT EXISTS appointment_status_history (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        appointment_id UUID NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
        old_status VARCHAR(20),
        new_status VARCHAR(20) NOT NULL,
        changed_by VARCHAR(100) NOT NULL DEFAULT 'system',
        note TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS ix_status_history_appt ON appointment_status_history (appointment_id, created_at)",

    # ── providers — null out legacy array-format demo_time_slots ─────────────
    # demo_time_slots changed from a flat list [{start,end}] to a day-keyed dict
    # {monday: [{start,end}], tuesday: null, ...}.  Old array data is incompatible;
    # nulling it out forces re-entry through the new UI.
    "UPDATE providers SET demo_time_slots = NULL WHERE demo_time_slots IS NOT NULL AND jsonb_typeof(demo_time_slots) = 'array'",
    # ── sms_messages — backfill is_test for test phone numbers ───────────────
    # is_test was not propagated through the send pipeline; all existing messages
    # from test phones (+1555XXXX) should be flagged as test data.
    "UPDATE sms_messages SET is_test = TRUE WHERE is_test = FALSE AND student_phone LIKE '+1555%'",

    # ── sms_messages — sender_type: distinguish AI agent vs admin manual send ─
    "ALTER TABLE sms_messages ADD COLUMN IF NOT EXISTS sender_type VARCHAR(20) NOT NULL DEFAULT 'agent'",

    # ── platform_config — admin-managed global key-value settings ────────────
    """CREATE TABLE IF NOT EXISTS platform_config (
        key VARCHAR(100) PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",

    # ── calls table — make vapi_call_id nullable (Vapi removed) ──────────────
    "ALTER TABLE calls ALTER COLUMN vapi_call_id DROP NOT NULL",

    # ── tenants — drop Vapi-specific columns (Vapi removed from platform) ─────
    "ALTER TABLE tenants DROP COLUMN IF EXISTS vapi_api_key",
    "ALTER TABLE tenants DROP COLUMN IF EXISTS vapi_assistant_id",
    "ALTER TABLE tenants DROP COLUMN IF EXISTS vapi_phone_number_id",
    "ALTER TABLE tenants DROP COLUMN IF EXISTS vapi_webhook_secret",
    "ALTER TABLE tenants DROP COLUMN IF EXISTS feature_vapi_enabled",

    # ── tenants — drop per-tenant Bolna columns (now global via platform_config)
    "ALTER TABLE tenants DROP COLUMN IF EXISTS bolna_api_key",
    "ALTER TABLE tenants DROP COLUMN IF EXISTS bolna_agent_id",

    # ── businesstype enum — migrate column to plain VARCHAR ──────────────────
    # SQLAlchemy 2.0 on Python 3.12+ builds _object_lookup from enum member
    # NAMES (uppercase) by default, but the DB may have stored lowercase values
    # (from enum .value) or old medical-domain values.  We fix this by:
    #   1. Converting the column to VARCHAR first (no value constraints).
    #   2. Dropping the stale PG enum type.
    #   3. LOWER()-normalising all values so they match the Python enum's .value.
    #   4. Remapping any remaining unknown values (old medical types) to 'custom'.
    # ORDER MATTERS: ALTER must precede UPDATE — you can't INSERT a lowercase
    # string into a native-enum column that only has uppercase labels.
    """DO $$ BEGIN
        ALTER TABLE tenants ALTER COLUMN business_type TYPE VARCHAR(50) USING business_type::text;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'business_type column type change skipped: %', SQLERRM;
    END $$;""",
    "DROP TYPE IF EXISTS businesstype",
    """DO $$ BEGIN
        UPDATE tenants SET business_type = LOWER(business_type)
        WHERE business_type IS DISTINCT FROM LOWER(business_type);
        UPDATE tenants SET business_type = 'custom'
        WHERE business_type NOT IN ('coaching_institute', 'custom');
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'business_type normalisation skipped: %', SQLERRM;
    END $$;""",

    # ── caller_id FK — consolidate is_test ownership onto callers table ────────
    # Step 1: Add caller_id FK columns (nullable — orphan rows keep NULL → is_test=False)
    "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS caller_id UUID REFERENCES callers(id)",
    "ALTER TABLE waitlist_entries ADD COLUMN IF NOT EXISTS caller_id UUID REFERENCES callers(id)",
    "ALTER TABLE sms_messages ADD COLUMN IF NOT EXISTS caller_id UUID REFERENCES callers(id)",
    "CREATE INDEX IF NOT EXISTS ix_appointments_caller_id ON appointments (caller_id)",
    "CREATE INDEX IF NOT EXISTS ix_waitlist_entries_caller_id ON waitlist_entries (caller_id)",
    "CREATE INDEX IF NOT EXISTS ix_sms_messages_caller_id ON sms_messages (caller_id)",

    # Step 2: Backfill caller_id via suffix-tail phone match (last 10 digits of digits-only phone)
    # Matches "+16352418405" with "6352418405" or "+6352418405" — same approach as _phone_digits_tail()
    """UPDATE appointments a SET caller_id = c.id
       FROM callers c
       WHERE c.tenant_id = a.tenant_id
         AND RIGHT(regexp_replace(c.phone, '[^0-9]', '', 'g'), 10)
             = RIGHT(regexp_replace(a.student_phone, '[^0-9]', '', 'g'), 10)
         AND a.caller_id IS NULL""",
    """UPDATE waitlist_entries w SET caller_id = c.id
       FROM callers c
       WHERE c.tenant_id = w.tenant_id
         AND RIGHT(regexp_replace(c.phone, '[^0-9]', '', 'g'), 10)
             = RIGHT(regexp_replace(w.student_phone, '[^0-9]', '', 'g'), 10)
         AND w.caller_id IS NULL""",
    """UPDATE sms_messages s SET caller_id = c.id
       FROM callers c
       WHERE c.tenant_id = s.tenant_id
         AND RIGHT(regexp_replace(c.phone, '[^0-9]', '', 'g'), 10)
             = RIGHT(regexp_replace(s.student_phone, '[^0-9]', '', 'g'), 10)
         AND s.caller_id IS NULL""",

    # Step 3: Reconcile callers.is_test — promote to TRUE if ANY linked record was test
    # Must run BEFORE dropping is_test from the 3 tables.
    """UPDATE callers c SET is_test = TRUE
       WHERE c.is_test = FALSE AND (
           EXISTS (SELECT 1 FROM appointments a WHERE a.caller_id = c.id AND a.is_test = TRUE)
           OR EXISTS (SELECT 1 FROM waitlist_entries w WHERE w.caller_id = c.id AND w.is_test = TRUE)
           OR EXISTS (SELECT 1 FROM sms_messages s WHERE s.caller_id = c.id AND s.is_test = TRUE)
       )""",

    # Step 4: Drop the now-redundant is_test columns from the 3 dependent tables
    "ALTER TABLE appointments DROP COLUMN IF EXISTS is_test",
    "ALTER TABLE waitlist_entries DROP COLUMN IF EXISTS is_test",
    "ALTER TABLE sms_messages DROP COLUMN IF EXISTS is_test",

    # ── Phase 2 — remove redundant tenant_id from child tables ─────────────────
    # tenant_id is fully derivable via caller_id → callers.tenant_id.
    # Callers ARE the single source of truth for tenant scope.

    # Step 1: Delete orphan rows (NULL caller_id) — user accepted data loss.
    # Must run BEFORE ALTER COLUMN SET NOT NULL.
    "DELETE FROM appointments WHERE caller_id IS NULL",
    "DELETE FROM waitlist_entries WHERE caller_id IS NULL",
    "DELETE FROM sms_messages WHERE caller_id IS NULL",

    # Step 2: Enforce NOT NULL on caller_id across all 3 tables.
    """DO $$ BEGIN
        ALTER TABLE appointments ALTER COLUMN caller_id SET NOT NULL;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'appointments caller_id NOT NULL already or skipped: %', SQLERRM;
    END $$;""",
    """DO $$ BEGIN
        ALTER TABLE waitlist_entries ALTER COLUMN caller_id SET NOT NULL;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'waitlist_entries caller_id NOT NULL already or skipped: %', SQLERRM;
    END $$;""",
    """DO $$ BEGIN
        ALTER TABLE sms_messages ALTER COLUMN caller_id SET NOT NULL;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'sms_messages caller_id NOT NULL already or skipped: %', SQLERRM;
    END $$;""",

    # Step 3: Drop the redundant tenant_id columns (derive from caller).
    "ALTER TABLE appointments DROP COLUMN IF EXISTS tenant_id",
    "ALTER TABLE waitlist_entries DROP COLUMN IF EXISTS tenant_id",
    "ALTER TABLE sms_messages DROP COLUMN IF EXISTS tenant_id",

    # Step 4: Drop student_phone from sms_messages (derive from caller.phone).
    "ALTER TABLE sms_messages DROP COLUMN IF EXISTS student_phone",

    # ── calls table — add caller_id FK for test-data filtering via Caller join ─
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS caller_id UUID REFERENCES callers(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS ix_calls_caller_id ON calls(caller_id)",
    # Backfill: match by last-10-digit phone tail within same tenant
    """UPDATE calls c SET caller_id = cal.id
       FROM callers cal
       WHERE cal.tenant_id = c.tenant_id
         AND RIGHT(regexp_replace(cal.phone, '[^0-9]', '', 'g'), 10)
             = RIGHT(regexp_replace(c.caller_number, '[^0-9]', '', 'g'), 10)
         AND c.caller_id IS NULL
         AND c.caller_number IS NOT NULL""",

    # ── providers — rename max_concurrent → slot_capacity ───────────────────────
    """DO $$ BEGIN
        ALTER TABLE faculty RENAME COLUMN max_concurrent TO slot_capacity;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'faculty.slot_capacity rename skipped (already done): %', SQLERRM;
    END $$;""",

    # ── tenants — migrate appointment_types JSON key max_concurrent → slot_capacity
    # Updates every element in the appointment_types JSONB array that still has
    # the old key, renaming it to slot_capacity and preserving the value.
    """UPDATE tenants
       SET appointment_types = (
           SELECT jsonb_agg(
               CASE
                   WHEN elem ? 'max_concurrent'
                   THEN (elem - 'max_concurrent') || jsonb_build_object('slot_capacity', elem->'max_concurrent')
                   ELSE elem
               END
           )
           FROM jsonb_array_elements(appointment_types) AS elem
       )
       WHERE appointment_types IS NOT NULL
         AND EXISTS (
             SELECT 1 FROM jsonb_array_elements(appointment_types) AS e
             WHERE e ? 'max_concurrent'
         )""",
    # ── waitlist_entries — store booked appointment time after promote ─────────
    "ALTER TABLE waitlist_entries ADD COLUMN IF NOT EXISTS appointment_scheduled_at TIMESTAMPTZ",

    # ── FitFront renames — table / column / enum value migrations ─────────────
    """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'faculty') THEN
            ALTER TABLE faculty RENAME TO trainers;
        ELSIF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'providers')
              AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'trainers') THEN
            ALTER TABLE providers RENAME TO trainers;
        END IF;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'faculty/providers→trainers rename skipped: %', SQLERRM;
    END $$;""",
    """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'appointments' AND column_name = 'student_name') THEN
            ALTER TABLE appointments RENAME COLUMN student_name TO client_name;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'appointments' AND column_name = 'student_phone') THEN
            ALTER TABLE appointments RENAME COLUMN student_phone TO client_phone;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'appointments' AND column_name = 'student_email') THEN
            ALTER TABLE appointments RENAME COLUMN student_email TO client_email;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'appointments' AND column_name = 'confirmed_by_student') THEN
            ALTER TABLE appointments RENAME COLUMN confirmed_by_student TO confirmed_by_client;
        END IF;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'appointments column renames skipped: %', SQLERRM;
    END $$;""",
    """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'waitlist_entries' AND column_name = 'student_name') THEN
            ALTER TABLE waitlist_entries RENAME COLUMN student_name TO client_name;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'waitlist_entries' AND column_name = 'student_phone') THEN
            ALTER TABLE waitlist_entries RENAME COLUMN student_phone TO client_phone;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'waitlist_entries' AND column_name = 'student_email') THEN
            ALTER TABLE waitlist_entries RENAME COLUMN student_email TO client_email;
        END IF;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'waitlist_entries column renames skipped: %', SQLERRM;
    END $$;""",
    """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'tenants' AND column_name = 'test_student_name') THEN
            ALTER TABLE tenants RENAME COLUMN test_student_name TO test_client_name;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'tenants' AND column_name = 'test_student_names') THEN
            ALTER TABLE tenants RENAME COLUMN test_student_names TO test_client_names;
        END IF;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'tenants column renames skipped: %', SQLERRM;
    END $$;""",
    "UPDATE tenants SET business_type = 'fitness_studio' WHERE business_type = 'coaching_institute'",
    """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'trainers' AND column_name = 'subject') THEN
            ALTER TABLE trainers RENAME COLUMN subject TO specialty;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'trainers' AND column_name = 'demo_time_slots') THEN
            ALTER TABLE trainers RENAME COLUMN demo_time_slots TO trial_session_slots;
        END IF;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'trainers column renames skipped: %', SQLERRM;
    END $$;""",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables that don't yet exist, then apply column migrations."""
    logger.info("Connecting to database: %s", settings.DATABASE_URL.split("@")[-1])  # Log host only, not creds
    async with engine.begin() as conn:
        # Import models so they register with Base.metadata
        from backend.models import tenant, call, appointment, caller, provider, waitlist, sms_message, profile_change_log, support_ticket, status_history, platform_config  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)

    # Apply column-level migrations — each in its own transaction so a failed
    # migration (e.g. column already exists from a prior run) doesn't abort the
    # rest via asyncpg's InFailedSQLTransactionError cascade.
    applied = 0
    for stmt in _MIGRATIONS:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
            applied += 1
        except Exception as exc:
            # Log but don't crash — column might already exist (non-PG DBs
            # may not support IF NOT EXISTS for ADD COLUMN).
            logger.warning("Migration skipped (%s): %s", exc.__class__.__name__, str(exc)[:120])
    if applied:
        logger.info("Applied %d column migrations on existing tables.", applied)

    logger.info("Database tables created / verified: tenants, calls, appointments, callers, trainers, waitlist_entries, sms_messages")


async def get_db() -> AsyncSession:
    """Dependency — yields an async session then closes it."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:
            logger.error("Database session error — rolling back: %s", exc)
            await session.rollback()
            raise
        finally:
            await session.close()
