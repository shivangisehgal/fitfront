"""
Seed script — populates the database with realistic demo data for a boutique gym.

Creates:
  - 1 demo tenant (Iron & Ivy Fitness Studio)
  - 3 trainers
  - 5 clients
  - 15 calls from the past 7 days with varied outcomes
  - 8 confirmed appointments across the next 2 weeks

Run:  python seed_data.py
"""

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from backend.database import async_session, init_db
from backend.models.tenant import Tenant, BusinessType, TenantStatus, PlanTier
from backend.models.provider import Trainer
from backend.models.call import Call, CallOutcome
from backend.models.appointment import Appointment, AppointmentStatus, BookedVia
from backend.models.caller import Caller


DEMO_TENANT_SLUG = "iron-and-ivy-fitness"

DEMO_TENANT = {
    "slug": DEMO_TENANT_SLUG,
    "business_name": "Iron & Ivy Fitness Studio",
    "business_type": BusinessType.FITNESS_STUDIO,
    "business_phone": "+15551234000",
    "business_address": "128 Market Street, Austin, TX 78701",
    "timezone": "America/Chicago",
    "owner_name": "Jordan Lee",
    "owner_email": "jordan@ironandivy.fit",
    "owner_phone": "+15551234001",
    "agent_name": "Aria",
    "greeting_message": "Thank you for calling Iron & Ivy Fitness Studio! This is Aria. How can I help you today?",
    "status": TenantStatus.ACTIVE,
    "plan": PlanTier.PROFESSIONAL,
    "demo_mode": True,
    "appointment_types": [
        {"code": "personal_training", "name": "Personal Training Session", "duration_minutes": 60, "slot_capacity": 1},
        {"code": "group_class", "name": "Group HIIT Class", "duration_minutes": 45, "slot_capacity": 12},
        {"code": "trial_session", "name": "Trial Session", "duration_minutes": 30, "slot_capacity": 1},
        {"code": "consultation", "name": "Membership Consultation", "duration_minutes": 30, "slot_capacity": 2},
    ],
    "business_hours": {
        "monday":    {"open": "06:00", "close": "21:00"},
        "tuesday":   {"open": "06:00", "close": "21:00"},
        "wednesday": {"open": "06:00", "close": "21:00"},
        "thursday":  {"open": "06:00", "close": "21:00"},
        "friday":    {"open": "06:00", "close": "21:00"},
        "saturday":  {"open": "08:00", "close": "14:00"},
        "sunday":    None,
    },
    "escalation_phone": "+15551234999",
    "test_caller_phone": "+15550100001",
}

TRAINERS = [
    {
        "name": "Alex Morgan",
        "title": "CSCS",
        "specialty": "Strength & Conditioning",
        "appointment_types": ["personal_training", "trial_session"],
        "trial_session_slots": {
            "monday":    [{"start": "07:00", "end": "08:00"}, {"start": "17:00", "end": "18:00"}],
            "wednesday": [{"start": "07:00", "end": "08:00"}, {"start": "17:00", "end": "18:00"}],
            "friday":    [{"start": "07:00", "end": "08:00"}],
        },
    },
    {
        "name": "Sam Rivera",
        "title": "RYT-200",
        "specialty": "Yoga",
        "appointment_types": ["group_class", "trial_session"],
        "trial_session_slots": {
            "tuesday":   [{"start": "09:00", "end": "10:00"}, {"start": "18:00", "end": "19:00"}],
            "thursday":  [{"start": "09:00", "end": "10:00"}],
            "saturday":  [{"start": "09:00", "end": "10:00"}],
        },
    },
    {
        "name": "Casey Brooks",
        "title": "NASM-CPT",
        "specialty": "HIIT",
        "appointment_types": ["group_class", "personal_training", "consultation"],
        "trial_session_slots": {
            "monday":    [{"start": "06:00", "end": "07:00"}],
            "wednesday": [{"start": "06:00", "end": "07:00"}],
            "friday":    [{"start": "06:00", "end": "07:00"}],
        },
    },
]

CLIENTS = [
    {"name": "Riley Chen",    "phone": "+15551234010", "email": "riley.chen@email.com",   "dob": "03/15/1990", "is_new": False},
    {"name": "Morgan Patel",  "phone": "+15551234011", "email": "morgan.p@email.com",     "dob": "11/22/1988", "is_new": False},
    {"name": "Taylor Kim",    "phone": "+15551234012", "email": "taylor.kim@email.com",   "dob": "07/04/1995", "is_new": True},
    {"name": "Jordan Walsh",  "phone": "+15551234013", "email": "jordan.w@email.com",     "dob": "01/30/1992", "is_new": False},
    {"name": "Quinn Adams",   "phone": "+15551234014", "email": "quinn.adams@email.com",  "dob": "09/12/1998", "is_new": True},
]

BOOKING_TRANSCRIPT = [
    {"role": "assistant", "content": "Thank you for calling Iron & Ivy Fitness Studio! This is Aria. How can I help you today?"},
    {"role": "user",      "content": "Hi, I'd like to book a trial personal training session."},
    {"role": "assistant", "content": "Absolutely! Alex Morgan handles our strength and conditioning trials. Can I get your name?"},
    {"role": "user",      "content": "Riley Chen."},
    {"role": "assistant", "content": "Great, Riley! I have a slot with Alex this Wednesday at 7 AM or Friday at 7 AM. Which works better?"},
    {"role": "user",      "content": "Wednesday at 7 sounds perfect."},
    {"role": "assistant", "content": "Wonderful! You're confirmed for a trial session this Wednesday at 7 AM with Alex. You'll get a text confirmation shortly."},
]

INQUIRY_TRANSCRIPT = [
    {"role": "assistant", "content": "Thank you for calling Iron & Ivy Fitness Studio! This is Aria. How can I help you today?"},
    {"role": "user",      "content": "Hi, I wanted to know about your membership options and class schedule."},
    {"role": "assistant", "content": "We offer personal training, group HIIT, and yoga classes with flexible membership tiers. Would you like to book a trial session to see the studio?"},
    {"role": "user",      "content": "Let me think about it and call back."},
    {"role": "assistant", "content": "Of course! We offer a free trial session so you can experience the studio before deciding. Just call whenever you're ready!"},
]

ESCALATED_TRANSCRIPT_1 = [
    {"role": "assistant", "content": "Thank you for calling Iron & Ivy Fitness Studio! This is Aria. How can I help you today?"},
    {"role": "user",      "content": "I twisted my ankle during yesterday's HIIT class and I'm still in pain."},
    {"role": "assistant", "content": "I'm sorry to hear that — your safety matters to us. Let me connect you with one of our trainers who can follow up with you right away."},
]

ESCALATED_TRANSCRIPT_2 = [
    {"role": "assistant", "content": "Thank you for calling Iron & Ivy Fitness Studio! This is Aria. How can I help you today?"},
    {"role": "user",      "content": "I need to speak to someone about a billing issue on my membership."},
    {"role": "assistant", "content": "I'd be happy to connect you with our membership team. Shall I transfer you now?"},
    {"role": "user",      "content": "Yes please."},
    {"role": "assistant", "content": "Connecting you now. Please hold for just a moment."},
]

CANCELLED_TRANSCRIPT = [
    {"role": "assistant", "content": "Thank you for calling Iron & Ivy Fitness Studio! This is Aria. How can I help you today?"},
    {"role": "user",      "content": "Hi, I need to cancel tomorrow's HIIT class."},
    {"role": "assistant", "content": "Of course. I've cancelled your class for tomorrow. Would you like to reschedule for another day?"},
    {"role": "user",      "content": "Not right now — I'll call back."},
    {"role": "assistant", "content": "No problem! Just give us a call whenever you're ready. Have a great day!"},
]

NEW_CLIENT_TRANSCRIPT = [
    {"role": "assistant", "content": "Thank you for calling Iron & Ivy Fitness Studio! This is Aria. How can I help you today?"},
    {"role": "user",      "content": "Hi, I just moved to Austin and I'm looking for a gym with personal training."},
    {"role": "assistant", "content": "Welcome! We'd love to have you at Iron & Ivy. Would you like to book a trial session with one of our trainers?"},
    {"role": "user",      "content": "Yes! What should I bring?"},
    {"role": "assistant", "content": "Comfortable workout clothes, a water bottle, and a towel — we provide all equipment. Can I get your name and phone number?"},
    {"role": "user",      "content": "Taylor Kim, 512-555-1012."},
    {"role": "assistant", "content": "Perfect! I have a trial slot Monday at 7 AM with Alex or Tuesday at 9 AM with Sam for yoga. Which sounds good?"},
    {"role": "user",      "content": "Monday with Alex works."},
    {"role": "assistant", "content": "Wonderful! Taylor, you're booked for a trial session Monday at 7 AM with Alex. You'll receive a confirmation text shortly. Welcome to Iron & Ivy!"},
]


async def seed():
    await init_db()

    async with async_session() as session:
        result = await session.execute(
            text("SELECT id FROM tenants WHERE slug = :slug"),
            {"slug": DEMO_TENANT_SLUG},
        )
        row = result.fetchone()

        if row:
            tenant_id = row[0]
            print(f"✓ Demo tenant already exists (id={tenant_id}). Checking data…")
            r2 = await session.execute(
                text("SELECT COUNT(*) FROM callers WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
            if r2.scalar() > 0:
                print("⚠  Clients already seeded for this tenant. Skipping.")
                return
        else:
            tenant = Tenant(**DEMO_TENANT)
            session.add(tenant)
            await session.flush()
            tenant_id = tenant.id
            print(f"✓ Created demo tenant '{DEMO_TENANT_SLUG}' (id={tenant_id})")

        trainer_records = []
        for t in TRAINERS:
            trainer = Trainer(
                tenant_id=tenant_id,
                name=t["name"],
                title=t["title"],
                specialty=t["specialty"],
                appointment_types=t["appointment_types"],
                trial_session_slots=t["trial_session_slots"],
                is_active=True,
            )
            session.add(trainer)
            trainer_records.append(trainer)
        await session.flush()
        print(f"✓ Created {len(trainer_records)} trainers")

        now = datetime.now(timezone.utc)
        caller_records = []
        for c in CLIENTS:
            caller_rec = Caller(
                tenant_id=tenant_id,
                name=c["name"],
                phone=c["phone"],
                email=c["email"],
                date_of_birth=c["dob"],
                is_new_caller=c["is_new"],
                first_seen_at=now - timedelta(days=random.randint(30, 365)),
                last_appointment_at=now - timedelta(days=random.randint(1, 60)),
            )
            session.add(caller_rec)
            caller_records.append(caller_rec)
        await session.flush()
        print(f"✓ Created {len(caller_records)} clients")

        transcripts = [
            (BOOKING_TRANSCRIPT,     CallOutcome.BOOKED,    "Client booked a trial PT session with Alex Morgan on Wednesday at 7 AM."),
            (INQUIRY_TRANSCRIPT,     CallOutcome.INQUIRY,   "Client inquired about membership options. Will call back."),
            (ESCALATED_TRANSCRIPT_1, CallOutcome.ESCALATED, "Client reported ankle injury after HIIT class. Escalated to trainer."),
            (ESCALATED_TRANSCRIPT_2, CallOutcome.ESCALATED, "Client billing issue. Transferred to membership team."),
            (CANCELLED_TRANSCRIPT,   CallOutcome.CANCELLED, "Client cancelled HIIT class. Will reschedule later."),
            (NEW_CLIENT_TRANSCRIPT,  CallOutcome.BOOKED,    "New client Taylor Kim booked trial with Alex on Monday at 7 AM."),
        ]

        call_records = []
        for i in range(15):
            days_ago = random.randint(0, 6)
            hour = random.randint(9, 18)
            started = now - timedelta(days=days_ago, hours=random.randint(0, 8))
            started = started.replace(hour=hour, minute=random.randint(0, 59), second=0, microsecond=0)
            duration = random.randint(60, 420)

            t_idx = i % len(transcripts)
            transcript, outcome, summary = transcripts[t_idx]

            stamped = [
                {**entry, "timestamp": (started + timedelta(seconds=j * 15)).isoformat()}
                for j, entry in enumerate(transcript)
            ]

            client = random.choice(CLIENTS)
            call = Call(
                tenant_id=tenant_id,
                vapi_call_id=f"demo-call-{uuid.uuid4().hex[:12]}",
                caller_number=client["phone"],
                started_at=started,
                ended_at=started + timedelta(seconds=duration),
                duration_seconds=duration,
                outcome=outcome,
                transcript=stamped,
                summary=summary,
            )
            session.add(call)
            call_records.append(call)

        await session.flush()
        print(f"✓ Created {len(call_records)} call records")

        apt_types = [
            ("Personal Training Session", 60, trainer_records[0]),
            ("Membership Consultation",     30, trainer_records[2]),
            ("Group HIIT Class",          45, trainer_records[2]),
            ("Trial Session",             30, trainer_records[1]),
            ("Personal Training Session", 60, trainer_records[0]),
            ("Group HIIT Class",          45, trainer_records[2]),
            ("Trial Session",             30, trainer_records[0]),
            ("Personal Training Session", 60, trainer_records[0]),
        ]

        for i, (apt_type, duration, trainer) in enumerate(apt_types):
            days_ahead = random.randint(1, 14)
            hour = random.choice([6, 7, 9, 10, 12, 17, 18])
            scheduled = (now + timedelta(days=days_ahead)).replace(
                hour=hour, minute=0, second=0, microsecond=0
            )

            client_data = CLIENTS[i % len(CLIENTS)]
            caller_rec = caller_records[i % len(caller_records)]
            linked_call = call_records[i % len(call_records)] if i < 5 else None

            appointment = Appointment(
                caller_id=caller_rec.id,
                cal_booking_id=f"demo-booking-{uuid.uuid4().hex[:8]}",
                cal_booking_uid=f"demo-uid-{uuid.uuid4().hex[:8]}",
                client_name=client_data["name"],
                client_phone=client_data["phone"],
                client_email=client_data["email"],
                date_of_birth=client_data["dob"],
                appointment_type=apt_type,
                scheduled_at=scheduled,
                duration_minutes=duration,
                status=AppointmentStatus.CONFIRMED,
                booked_via=BookedVia.AI if i < 6 else BookedVia.MANUAL,
                provider_id=trainer.id,
                call_id=linked_call.id if linked_call else None,
                notes=f"Demo seed — {apt_type.lower()}",
            )
            session.add(appointment)

        await session.commit()
        print(f"✓ Created {len(apt_types)} appointments")

        print("\n✅ Database seeded successfully!")
        print(f"   Tenant slug : {DEMO_TENANT_SLUG}")
        print(f"   Tenant id   : {tenant_id}")
        print("   Run the server: python -m backend.main")


if __name__ == "__main__":
    asyncio.run(seed())
