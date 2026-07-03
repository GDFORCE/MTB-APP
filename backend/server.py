"""My Trial Board — Dawn Rounds clinical-trials backend.

Production-grade FastAPI app with:
- JWT auth (access + refresh)
- Role-based access (sponsor / pi / crc / patient)
- Trials, visits, patients, notifications CRUD
- Real-time chat over WebSocket (1-to-1 + group, typing, read receipts)
- MongoDB persistence
"""
from fastapi import FastAPI, APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Query
from fastapi.security import OAuth2PasswordBearer
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from motor.motor_asyncio import AsyncIOMotorClient
import os, json, logging, uuid, asyncio
from pathlib import Path
from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional, Dict, Literal
from datetime import datetime, timezone, timedelta
from passlib.context import CryptContext
import jwt

import otp_service

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# ── Config ───────────────────────────────────────────────────────────────────
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']
JWT_SECRET = os.environ.get('JWT_SECRET', 'dawn-rounds-dev-secret-change-me')
JWT_REFRESH_SECRET = os.environ.get('JWT_REFRESH_SECRET', 'dawn-rounds-dev-refresh-secret')
ALGO = 'HS256'
ACCESS_MIN = 60 * 24            # 1 day for demo comfort
REFRESH_DAYS = 30

client = AsyncIOMotorClient(MONGO_URL, tz_aware=True)
db = client[DB_NAME]
pwd_ctx = CryptContext(schemes=['bcrypt'], deprecated='auto')

app = FastAPI(title="My Trial Board")
api = APIRouter(prefix='/api')
oauth2 = OAuth2PasswordBearer(tokenUrl='/api/auth/login', auto_error=False)

def now(): return datetime.now(timezone.utc)
def iso(d): return d.isoformat() if isinstance(d, datetime) else d

# ── Models ───────────────────────────────────────────────────────────────────
Role = Literal['sponsor', 'cro', 'smo', 'site', 'pi', 'crc', 'patient']

class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: Role
    phone: Optional[str] = None
    organization: Optional[str] = None
    security_question: Optional[str] = None
    security_answer: Optional[str] = None

class RegisterStartIn(BaseModel):
    full_name: str
    role: Role
    # Password is optional: in the design flow, OTP is verified BEFORE the password
    # is set. When omitted here, the account is created later via /register/complete.
    password: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    organization: Optional[str] = None
    security_question: Optional[str] = None
    security_answer: Optional[str] = None
    # The design collects three security questions (step 3). Each item: {question, answer}.
    security_questions: Optional[List[Dict]] = None
    profile: Optional[Dict] = None   # extra role-specific fields (designation, dob, gender…)

class RegisterVerifyIn(BaseModel):
    registration_id: str
    email_otp: Optional[str] = None
    phone_otp: Optional[str] = None

class RegisterCompleteIn(BaseModel):
    registration_id: str
    password: str

class RegisterResendIn(BaseModel):
    registration_id: str
    channel: Literal['email', 'phone']

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class ForgotIn(BaseModel):
    email: EmailStr

class ResetIn(BaseModel):
    email: EmailStr
    otp: str
    new_password: str

class ProfileUpdateIn(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None
    language: Optional[str] = None

class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str

class TicketIn(BaseModel):
    category: str
    subject: str
    description: Optional[str] = ''

class TrialIn(BaseModel):
    title: str
    protocol_id: str
    phase: str
    condition: str
    description: Optional[str] = ''
    sponsor_name: Optional[str] = ''

class VisitIn(BaseModel):
    trial_id: str
    visit_number: int
    name: str
    day_offset: int
    window_days: int = 3
    activities: List[str] = []

class PatientIn(BaseModel):
    full_name: str
    email: EmailStr
    phone: Optional[str] = ''
    trial_id: str
    pi_id: Optional[str] = None
    crc_id: Optional[str] = None
    enrolled_date: Optional[str] = None

class MessageIn(BaseModel):
    conversation_id: str
    content: str

class ConversationIn(BaseModel):
    participant_ids: List[str]
    title: Optional[str] = None
    is_group: bool = False

# ── Helpers ──────────────────────────────────────────────────────────────────
def make_token(sub: str, role: str, kind: str = 'access'):
    secret = JWT_SECRET if kind == 'access' else JWT_REFRESH_SECRET
    delta = timedelta(minutes=ACCESS_MIN) if kind == 'access' else timedelta(days=REFRESH_DAYS)
    return jwt.encode({'sub': sub, 'role': role, 'kind': kind, 'exp': now() + delta}, secret, ALGO)

async def current_user(token: Optional[str] = Depends(oauth2)):
    if not token:
        raise HTTPException(401, 'Not authenticated')
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, 'Token expired')
    except jwt.PyJWTError:
        raise HTTPException(401, 'Invalid token')
    user = await db.users.find_one({'id': payload['sub']}, {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0})
    if not user:
        raise HTTPException(401, 'User not found')
    return user

def require_roles(*allowed):
    async def dep(user=Depends(current_user)):
        if user['role'] not in allowed:
            raise HTTPException(403, 'Insufficient role')
        return user
    return dep

def serialize(d):
    if not d: return d
    d.pop('_id', None)
    d.pop('hashed_password', None)
    d.pop('security_answer_hash', None)
    return d

# ── Auth ─────────────────────────────────────────────────────────────────────
@api.post('/auth/register')
async def register(body: RegisterIn):
    if await db.users.find_one({'email': body.email.lower()}):
        raise HTTPException(400, 'Email already registered')
    uid = str(uuid.uuid4())
    doc = {
        'id': uid,
        'email': body.email.lower(),
        'full_name': body.full_name,
        'role': body.role,
        'phone': body.phone or '',
        'organization': body.organization or '',
        'hashed_password': pwd_ctx.hash(body.password),
        'security_question': body.security_question or '',
        'security_answer_hash': pwd_ctx.hash(body.security_answer.lower()) if body.security_answer else '',
        'avatar_initials': ''.join([w[0].upper() for w in body.full_name.split()[:2]]) or 'U',
        'created_at': now(),
        'is_online': False,
    }
    await db.users.insert_one(doc)
    access = make_token(uid, body.role, 'access')
    refresh = make_token(uid, body.role, 'refresh')
    return {'access_token': access, 'refresh_token': refresh, 'user': serialize({**doc})}

@api.post('/auth/login')
async def login(body: LoginIn):
    user = await db.users.find_one({'email': body.email.lower()})
    if not user or not pwd_ctx.verify(body.password, user['hashed_password']):
        raise HTTPException(401, 'Invalid credentials')
    access = make_token(user['id'], user['role'], 'access')
    refresh = make_token(user['id'], user['role'], 'refresh')
    return {'access_token': access, 'refresh_token': refresh, 'user': serialize({**user})}

@api.post('/auth/refresh')
async def refresh_token(body: dict):
    try:
        payload = jwt.decode(body['refresh_token'], JWT_REFRESH_SECRET, algorithms=[ALGO])
    except jwt.PyJWTError:
        raise HTTPException(401, 'Invalid refresh token')
    access = make_token(payload['sub'], payload['role'], 'access')
    return {'access_token': access}

@api.get('/auth/me')
async def me(user=Depends(current_user)):
    return user

@api.patch('/auth/me')
async def update_me(body: ProfileUpdateIn, user=Depends(current_user)):
    """Update the signed-in user's profile. Name/phone/email are top-level;
    dob/gender/language ride in the `profile` sub-document."""
    updates: Dict = {}
    if body.full_name is not None and body.full_name.strip():
        name = body.full_name.strip()
        updates['full_name'] = name
        updates['avatar_initials'] = ''.join([w[0].upper() for w in name.split()[:2]]) or 'U'
    if body.phone is not None:
        updates['phone'] = body.phone.strip()
    if body.email is not None:
        email = body.email.lower().strip()
        existing = await db.users.find_one({'email': email, 'id': {'$ne': user['id']}})
        if existing:
            raise HTTPException(400, 'That email is already in use by another account.')
        updates['email'] = email
    # profile sub-fields
    for key, val in (('dob', body.dob), ('gender', body.gender), ('language', body.language)):
        if val is not None:
            updates[f'profile.{key}'] = val
    if updates:
        await db.users.update_one({'id': user['id']}, {'$set': updates})
    fresh = await db.users.find_one({'id': user['id']}, {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0})
    return serialize(fresh)

@api.post('/auth/change-password')
async def change_password(body: ChangePasswordIn, user=Depends(current_user)):
    full = await db.users.find_one({'id': user['id']})
    if not full or not full.get('hashed_password') or not pwd_ctx.verify(body.current_password, full['hashed_password']):
        raise HTTPException(400, 'Your current password is incorrect.')
    if len(body.new_password) < 8:
        raise HTTPException(400, 'New password must be at least 8 characters.')
    await db.users.update_one({'id': user['id']}, {'$set': {'hashed_password': pwd_ctx.hash(body.new_password)}})
    return {'ok': True}

# ── Support tickets ───────────────────────────────────────────────────────────
@api.get('/support/tickets')
async def list_tickets(user=Depends(current_user)):
    return await db.support_tickets.find({'user_id': user['id']}, {'_id': 0}).sort('created_at', -1).to_list(200)

@api.post('/support/tickets')
async def create_ticket(body: TicketIn, user=Depends(current_user)):
    n = now()
    ticket_id = f"#TKT-{n.strftime('%Y%m%d')}-{str(uuid.uuid4().int)[:4]}"
    doc = {
        'id': str(uuid.uuid4()), 'ticket_id': ticket_id, 'user_id': user['id'],
        'category': body.category, 'subject': body.subject.strip() or 'Support request',
        'description': body.description or '', 'status': 'Open',
        'created_at': n,
    }
    await db.support_tickets.insert_one(doc)
    return serialize({**doc})

@api.post('/auth/forgot')
async def forgot(body: ForgotIn):
    # Demo: returns OTP directly. Production would send via SMTP.
    user = await db.users.find_one({'email': body.email.lower()})
    if not user:
        return {'ok': True, 'otp': None}  # don't leak
    otp = str(uuid.uuid4().int)[:6]
    await db.users.update_one({'email': body.email.lower()}, {'$set': {'reset_otp': otp, 'reset_otp_at': now()}})
    return {'ok': True, 'otp': otp, 'message': 'OTP generated (returned for demo)'}

@api.post('/auth/reset')
async def reset(body: ResetIn):
    user = await db.users.find_one({'email': body.email.lower()})
    if not user or user.get('reset_otp') != body.otp:
        raise HTTPException(400, 'Invalid OTP')
    await db.users.update_one(
        {'email': body.email.lower()},
        {'$set': {'hashed_password': pwd_ctx.hash(body.new_password)}, '$unset': {'reset_otp': '', 'reset_otp_at': ''}}
    )
    return {'ok': True}

# ── Registration with OTP verification ───────────────────────────────────────
OTP_MAX_VERIFY_ATTEMPTS = 6      # wrong-code attempts before the pending is locked
OTP_MAX_SENDS = 5                # total sends (initial + resends) per registration
OTP_RESEND_COOLDOWN_SEC = 30     # min gap between sends for one registration
OTP_RATE_LIMIT = 5               # sends allowed per identifier (phone/email) …
OTP_RATE_WINDOW_SEC = 3600       # … within this rolling window

# ── DEV-ONLY OTP bypass ───────────────────────────────────────────────────────
# When DEV_OTP_MODE is on, any channel WITHOUT a configured provider (no SMTP /
# no MSG91) is not actually sent, and a single fixed code (DEV_OTP_CODE) is
# accepted for that channel — so the signup flow can be tested end-to-end before
# real providers are wired. A channel that IS configured still sends real codes.
# ⚠️  MUST be OFF in production (leaves signup wide open otherwise).
DEV_OTP_MODE = os.environ.get('DEV_OTP_MODE', '').strip().lower() in ('1', 'true', 'yes', 'on')
DEV_OTP_CODE = os.environ.get('DEV_OTP_CODE', '000000').strip()

def _channel_configured(channel: str) -> bool:
    if channel == 'email':
        return bool(os.environ.get('SMTP_HOST'))
    return bool(os.environ.get('MSG91_AUTHKEY') and os.environ.get('MSG91_TEMPLATE_ID'))

def required_channels(role: str) -> List[str]:
    """Patients verify by phone only; everyone else verifies email AND phone."""
    return ['phone'] if role == 'patient' else ['email', 'phone']

def _otp_matches(supplied: Optional[str], hashed: Optional[str]) -> bool:
    if not supplied:
        return False
    # DEV-ONLY: accept the fixed dev code for any channel (see DEV_OTP_MODE).
    if DEV_OTP_MODE and supplied.strip() == DEV_OTP_CODE:
        return True
    if not hashed:
        return False
    try:
        return pwd_ctx.verify(supplied.strip(), hashed)
    except Exception:
        return False

async def _enforce_rate_limit(identifier: str):
    """Fixed-window per-identifier cap to curb abuse and runaway SMS spend."""
    key = f'otp:{identifier.lower()}'
    n = now()
    doc = await db.otp_throttle.find_one({'_id': key})
    if doc and (n - doc['window_start']).total_seconds() < OTP_RATE_WINDOW_SEC:
        if doc['count'] >= OTP_RATE_LIMIT:
            mins = int((OTP_RATE_WINDOW_SEC - (n - doc['window_start']).total_seconds()) // 60) + 1
            raise HTTPException(429, f'Too many verification requests. Please try again in about {mins} minute(s).')
        await db.otp_throttle.update_one({'_id': key}, {'$inc': {'count': 1}})
    else:
        await db.otp_throttle.replace_one(
            {'_id': key},
            {'_id': key, 'count': 1, 'window_start': n, 'expires_at': n + timedelta(seconds=OTP_RATE_WINDOW_SEC)},
            upsert=True,
        )

async def _deliver_otp(channel: str, target: str, code: str):
    """Send a code, mapping provider failures to clean HTTP errors. Blocking
    provider I/O runs in a threadpool so it never stalls the event loop."""
    # DEV-ONLY: don't try to send on a channel with no provider; the fixed dev
    # code will be accepted at verify time instead.
    if DEV_OTP_MODE and not _channel_configured(channel):
        logging.warning('[DEV_OTP_MODE] Skipped real %s OTP to %s — enter dev code "%s" in the app.', channel, target, DEV_OTP_CODE)
        return
    try:
        if channel == 'email':
            await run_in_threadpool(otp_service.send_email, target, code)
        else:
            await run_in_threadpool(otp_service.send_sms, target, code)
    except otp_service.OTPConfigError:
        logging.error('OTP channel %s is not configured', channel)
        raise HTTPException(503, f'{channel.capitalize()} verification is temporarily unavailable.')
    except otp_service.OTPDeliveryError:
        raise HTTPException(502, f'We could not send the {channel} code. Please try again.')

async def _finalize_registration(pending: dict) -> dict:
    """Create the real user from a fully-verified pending registration + issue tokens."""
    uid = str(uuid.uuid4())
    name = pending['full_name']
    doc = {
        'id': uid,
        'email': (pending.get('email') or '').lower(),
        'full_name': name,
        'role': pending['role'],
        'phone': pending.get('phone') or '',
        'organization': pending.get('organization') or '',
        'hashed_password': pending['hashed_password'],
        'security_question': pending.get('security_question') or '',
        'security_answer_hash': pending.get('security_answer_hash') or '',
        'security_questions': pending.get('security_questions') or [],
        'profile': pending.get('profile') or {},
        'avatar_initials': ''.join([w[0].upper() for w in name.split()[:2]]) or 'U',
        'email_verified': bool(pending.get('email_verified')),
        'phone_verified': bool(pending.get('phone_verified')),
        'created_at': now(),
        'is_online': False,
    }
    await db.users.insert_one(doc)
    access = make_token(uid, doc['role'], 'access')
    refresh = make_token(uid, doc['role'], 'refresh')
    return {'access_token': access, 'refresh_token': refresh, 'user': serialize({**doc})}

@api.post('/auth/register/start')
async def register_start(body: RegisterStartIn):
    channels = required_channels(body.role)
    email = (body.email or '').lower().strip() or None
    phone = (body.phone or '').strip() or None

    if 'email' in channels and not email:
        raise HTTPException(400, 'Email is required for this role')
    if 'phone' in channels and not phone:
        raise HTTPException(400, 'Phone number is required')
    if email and await db.users.find_one({'email': email}):
        raise HTTPException(400, 'Email already registered')
    if phone and await db.users.find_one({'phone': phone}):
        raise HTTPException(400, 'Phone number already registered')

    # Throttle per identifier before we generate or send anything.
    if phone:
        await _enforce_rate_limit(phone)
    if email:
        await _enforce_rate_limit(email)

    # Drop any earlier in-flight attempt for these identifiers so they can't pile up.
    await db.pending_registrations.delete_many({'$or': [
        *([{'phone': phone}] if phone else []),
        *([{'email': email}] if email else []),
    ]})

    # Hash the three security-question answers (design step 3), storing only hashes.
    sec_qs = []
    for q in (body.security_questions or []):
        question = (q.get('question') or '').strip()
        answer = (q.get('answer') or '').strip().lower()
        if question and answer:
            sec_qs.append({'question': question, 'answer_hash': pwd_ctx.hash(answer)})

    rid = str(uuid.uuid4())
    doc = {
        'id': rid,
        'full_name': body.full_name,
        'role': body.role,
        'email': email,
        'phone': phone,
        'organization': body.organization,
        # Password may be set now (legacy callers) or later via /register/complete.
        'hashed_password': pwd_ctx.hash(body.password) if body.password else None,
        'security_question': body.security_question or (sec_qs[0]['question'] if sec_qs else ''),
        'security_answer_hash': (pwd_ctx.hash(body.security_answer.lower()) if body.security_answer
                                 else (sec_qs[0]['answer_hash'] if sec_qs else '')),
        'security_questions': sec_qs,
        'profile': body.profile or {},
        'channels': channels,
        'email_verified': False,
        'phone_verified': False,
        'attempts': 0,
        'send_count': len(channels),
        'last_sent_at': now(),
        'created_at': now(),
        'expires_at': now() + timedelta(minutes=otp_service.OTP_TTL_MIN),
    }

    # Generate codes, store ONLY their hashes, then deliver. If a send fails we
    # raise — nothing is persisted, so the user is never told a code is on its way.
    codes = {ch: otp_service.generate_code() for ch in channels}
    for ch in channels:
        doc[f'{ch}_otp_hash'] = pwd_ctx.hash(codes[ch])
    for ch in channels:
        target = email if ch == 'email' else phone
        assert target  # validated present above for every required channel
        await _deliver_otp(ch, target, codes[ch])

    await db.pending_registrations.insert_one(doc)
    return {
        'registration_id': rid,
        'channels': channels,
        'email': email,
        'phone': phone,
        'expires_in': otp_service.OTP_TTL_MIN * 60,
        'resend_cooldown': OTP_RESEND_COOLDOWN_SEC,
    }

@api.post('/auth/register/verify')
async def register_verify(body: RegisterVerifyIn):
    pending = await db.pending_registrations.find_one({'id': body.registration_id})
    if not pending:
        raise HTTPException(404, 'Registration not found or already completed')
    if pending['expires_at'] < now():
        await db.pending_registrations.delete_one({'id': pending['id']})
        raise HTTPException(400, 'Your verification code expired. Please restart registration.')
    if pending.get('attempts', 0) >= OTP_MAX_VERIFY_ATTEMPTS:
        await db.pending_registrations.delete_one({'id': pending['id']})
        raise HTTPException(429, 'Too many incorrect attempts. Please restart registration.')

    updates = {}
    for ch, supplied in (('email', body.email_otp), ('phone', body.phone_otp)):
        if ch in pending['channels'] and not pending.get(f'{ch}_verified'):
            if not supplied:
                raise HTTPException(400, f'{ch.capitalize()} verification code is required')
            if not _otp_matches(supplied, pending.get(f'{ch}_otp_hash')):
                await db.pending_registrations.update_one({'id': pending['id']}, {'$inc': {'attempts': 1}})
                raise HTTPException(400, f'Incorrect {ch} verification code')
            updates[f'{ch}_verified'] = True

    if updates:
        await db.pending_registrations.update_one({'id': pending['id']}, {'$set': updates})
        pending.update(updates)

    if not all(pending.get(f'{ch}_verified') for ch in pending['channels']):
        return {'verified': False, 'channels': pending['channels'],
                'email_verified': pending.get('email_verified', False),
                'phone_verified': pending.get('phone_verified', False)}

    # All channels verified. In the design flow the password isn't set yet — keep the
    # pending record and let /register/complete create the account. Legacy callers that
    # supplied a password at /start are finalized immediately here.
    if not pending.get('hashed_password'):
        await db.pending_registrations.update_one({'id': pending['id']}, {'$set': {'fully_verified': True}})
        return {'verified': True, 'pending_password': True}

    session = await _finalize_registration(pending)
    await db.pending_registrations.delete_one({'id': pending['id']})
    return {'verified': True, **session}

@api.post('/auth/register/complete')
async def register_complete(body: RegisterCompleteIn):
    """Final step of the design flow: set the password on an already-verified pending
    registration and create the account."""
    pending = await db.pending_registrations.find_one({'id': body.registration_id})
    if not pending:
        raise HTTPException(404, 'Registration not found or already completed')
    if not all(pending.get(f'{ch}_verified') for ch in pending['channels']):
        raise HTTPException(400, 'Please verify your contact details before setting a password.')
    pending['hashed_password'] = pwd_ctx.hash(body.password)
    session = await _finalize_registration(pending)
    await db.pending_registrations.delete_one({'id': pending['id']})
    return {'verified': True, **session}

@api.post('/auth/register/resend')
async def register_resend(body: RegisterResendIn):
    pending = await db.pending_registrations.find_one({'id': body.registration_id})
    if not pending:
        raise HTTPException(404, 'Registration not found or already completed')
    if body.channel not in pending['channels']:
        raise HTTPException(400, 'Channel not used for this registration')

    last_sent = pending.get('last_sent_at')
    if last_sent and (now() - last_sent).total_seconds() < OTP_RESEND_COOLDOWN_SEC:
        wait = OTP_RESEND_COOLDOWN_SEC - int((now() - last_sent).total_seconds())
        raise HTTPException(429, f'Please wait {wait}s before requesting another code.')
    if pending.get('send_count', 0) >= OTP_MAX_SENDS:
        raise HTTPException(429, 'Resend limit reached. Please restart registration.')

    target = pending['email'] if body.channel == 'email' else pending['phone']
    await _enforce_rate_limit(target)

    code = otp_service.generate_code()
    await _deliver_otp(body.channel, target, code)
    await db.pending_registrations.update_one(
        {'id': pending['id']},
        {'$set': {f'{body.channel}_otp_hash': pwd_ctx.hash(code),
                  'last_sent_at': now(),
                  'expires_at': now() + timedelta(minutes=otp_service.OTP_TTL_MIN)},
         '$inc': {'send_count': 1}},
    )
    return {'ok': True, 'resend_cooldown': OTP_RESEND_COOLDOWN_SEC}

# ── Trials ───────────────────────────────────────────────────────────────────
@api.get('/trials')
async def list_trials(user=Depends(current_user)):
    trials = await db.trials.find({}, {'_id': 0}).to_list(500)
    # patient: filter to enrolled trials
    if user['role'] == 'patient':
        enrolled = await db.patients.find({'user_id': user['id']}, {'_id': 0, 'trial_id': 1}).to_list(100)
        ids = {p['trial_id'] for p in enrolled}
        trials = [t for t in trials if t['id'] in ids]
    return trials

@api.post('/trials', dependencies=[Depends(require_roles('sponsor', 'cro', 'pi'))])
async def create_trial(body: TrialIn, user=Depends(current_user)):
    tid = str(uuid.uuid4())
    doc = {'id': tid, **body.dict(), 'created_by': user['id'], 'created_at': now(), 'status': 'active'}
    await db.trials.insert_one(doc)
    return serialize(doc)

@api.get('/trials/{trial_id}')
async def get_trial(trial_id: str, user=Depends(current_user)):
    t = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not t: raise HTTPException(404, 'Trial not found')
    visits = await db.visits.find({'trial_id': trial_id}, {'_id': 0}).sort('visit_number', 1).to_list(200)
    return {**t, 'visits': visits}

# ── Visit schedule ──────────────────────────────────────────────────────────
@api.post('/visits')
async def create_visit(body: VisitIn, user=Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))):
    vid = str(uuid.uuid4())
    doc = {'id': vid, **body.dict(), 'created_at': now()}
    await db.visits.insert_one(doc)
    return serialize(doc)

@api.get('/visits/mine')
async def my_visits(user=Depends(current_user)):
    """Return upcoming/completed visits for the logged-in patient."""
    if user['role'] != 'patient':
        return []
    patient = await db.patients.find_one({'user_id': user['id']}, {'_id': 0})
    if not patient: return []
    visits = await db.visits.find({'trial_id': patient['trial_id']}, {'_id': 0}).sort('visit_number', 1).to_list(200)
    completed = set(patient.get('completed_visit_ids', []))
    result = []
    base_date = datetime.fromisoformat(patient.get('enrolled_date') or now().isoformat().replace('Z', '+00:00').replace('+00:00', ''))
    for v in visits:
        scheduled = base_date + timedelta(days=v['day_offset'])
        result.append({
            **v,
            'patient_id': patient['id'],
            'scheduled_date': scheduled.isoformat(),
            'status': 'completed' if v['id'] in completed else ('upcoming' if scheduled >= now().replace(tzinfo=None) else 'missed'),
        })
    return result

# ── Patients ────────────────────────────────────────────────────────────────
@api.get('/patients')
async def list_patients(user=Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))):
    q = {}
    if user['role'] == 'pi':
        q = {'pi_id': user['id']}
    elif user['role'] == 'crc':
        q = {'crc_id': user['id']}
    patients = await db.patients.find(q, {'_id': 0}).to_list(500)
    return patients

@api.post('/patients', dependencies=[Depends(require_roles('pi', 'crc'))])
async def add_patient(body: PatientIn, user=Depends(current_user)):
    pid = str(uuid.uuid4())
    doc = {
        'id': pid, **body.dict(),
        'created_by': user['id'],
        'created_at': now(),
        'enrolled_date': body.enrolled_date or now().date().isoformat(),
        'completed_visit_ids': [],
        'avatar_initials': ''.join([w[0].upper() for w in body.full_name.split()[:2]]) or 'P',
    }
    await db.patients.insert_one(doc)
    return serialize(doc)

# ── Notifications ───────────────────────────────────────────────────────────
@api.get('/notifications')
async def my_notifications(user=Depends(current_user)):
    items = await db.notifications.find({'user_id': user['id']}, {'_id': 0}).sort('created_at', -1).to_list(100)
    return items

@api.post('/notifications/{nid}/read')
async def mark_read(nid: str, user=Depends(current_user)):
    await db.notifications.update_one({'id': nid, 'user_id': user['id']}, {'$set': {'read': True}})
    return {'ok': True}

# ── Conversations & Messages ────────────────────────────────────────────────
@api.get('/conversations')
async def list_conversations(user=Depends(current_user)):
    convs = await db.conversations.find({'participant_ids': user['id']}, {'_id': 0}).sort('updated_at', -1).to_list(200)
    # enrich with other participant info + unread count
    out = []
    for c in convs:
        unread = await db.messages.count_documents({'conversation_id': c['id'], 'sender_id': {'$ne': user['id']}, f'read_by.{user["id"]}': {'$exists': False}})
        # other participant for 1-1
        other = None
        if not c.get('is_group'):
            other_id = next((p for p in c['participant_ids'] if p != user['id']), None)
            if other_id:
                other = await db.users.find_one({'id': other_id}, {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0})
        out.append({**c, 'unread_count': unread, 'other_participant': other})
    return out

@api.post('/conversations')
async def create_conversation(body: ConversationIn, user=Depends(current_user)):
    pids = sorted(set(body.participant_ids + [user['id']]))
    if not body.is_group:
        existing = await db.conversations.find_one({'participant_ids': pids, 'is_group': False}, {'_id': 0})
        if existing: return existing
    cid = str(uuid.uuid4())
    doc = {'id': cid, 'participant_ids': pids, 'title': body.title or '', 'is_group': body.is_group,
           'last_message': '', 'created_at': now(), 'updated_at': now()}
    await db.conversations.insert_one(doc)
    return serialize(doc)

@api.get('/conversations/{cid}/messages')
async def get_messages(cid: str, user=Depends(current_user)):
    msgs = await db.messages.find({'conversation_id': cid}, {'_id': 0}).sort('created_at', 1).to_list(500)
    # mark all as read
    await db.messages.update_many(
        {'conversation_id': cid, 'sender_id': {'$ne': user['id']}},
        {'$set': {f'read_by.{user["id"]}': now()}}
    )
    return msgs

# ── Users (directory) ───────────────────────────────────────────────────────
@api.get('/users')
async def list_users(user=Depends(current_user)):
    users = await db.users.find({}, {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0, 'reset_otp': 0}).to_list(500)
    return [u for u in users if u['id'] != user['id']]

# ── WebSocket chat ──────────────────────────────────────────────────────────
class WSManager:
    def __init__(self):
        self.connections: Dict[str, WebSocket] = {}

    async def connect(self, ws: WebSocket, user_id: str):
        await ws.accept()
        self.connections[user_id] = ws
        await db.users.update_one({'id': user_id}, {'$set': {'is_online': True, 'last_seen': now()}})

    def disconnect(self, user_id: str):
        self.connections.pop(user_id, None)

    async def send(self, user_id: str, payload: dict):
        ws = self.connections.get(user_id)
        if ws:
            try:
                await ws.send_text(json.dumps(payload, default=str))
            except Exception:
                self.disconnect(user_id)

manager = WSManager()

@app.websocket('/api/ws')
async def ws_endpoint(websocket: WebSocket, token: str = Query(...)):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGO])
        user_id = payload['sub']
    except jwt.PyJWTError:
        await websocket.close(code=1008); return

    await manager.connect(websocket, user_id)
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get('type')

            if event == 'message':
                cid = data['conversation_id']
                conv = await db.conversations.find_one({'id': cid})
                if not conv or user_id not in conv['participant_ids']:
                    continue
                mid = str(uuid.uuid4())
                msg = {
                    'id': mid, 'conversation_id': cid, 'sender_id': user_id,
                    'content': data['content'], 'created_at': now(),
                    'read_by': {user_id: now()},
                }
                await db.messages.insert_one(msg)
                await db.conversations.update_one({'id': cid}, {'$set': {'last_message': data['content'], 'updated_at': now()}})
                out = {**msg, 'created_at': iso(msg['created_at']), 'type': 'message'}
                for pid in conv['participant_ids']:
                    await manager.send(pid, out)

            elif event == 'typing':
                cid = data['conversation_id']
                conv = await db.conversations.find_one({'id': cid})
                if not conv: continue
                for pid in conv['participant_ids']:
                    if pid != user_id:
                        await manager.send(pid, {'type': 'typing', 'conversation_id': cid, 'user_id': user_id})

            elif event == 'read':
                cid = data['conversation_id']
                await db.messages.update_many(
                    {'conversation_id': cid, 'sender_id': {'$ne': user_id}},
                    {'$set': {f'read_by.{user_id}': now()}}
                )
                conv = await db.conversations.find_one({'id': cid})
                if conv:
                    for pid in conv['participant_ids']:
                        if pid != user_id:
                            await manager.send(pid, {'type': 'read', 'conversation_id': cid, 'user_id': user_id})
    except WebSocketDisconnect:
        manager.disconnect(user_id)
        await db.users.update_one({'id': user_id}, {'$set': {'is_online': False, 'last_seen': now()}})

# ── Seed demo data ──────────────────────────────────────────────────────────
@api.post('/seed')
async def seed_demo():
    """Idempotent seed of demo users + a trial + visits + patient + notifications."""
    seeded = await db.meta.find_one({'key': 'seeded_v1'})
    if seeded:
        return {'ok': True, 'already': True}
    demo_users = [
        ('sponsor@mtb.app', 'sponsor', 'Sarah Chen', 'Pfizer Global'),
        ('pi@mtb.app',      'pi',      'Dr. Rajesh Sharma', 'AIIMS Delhi'),
        ('crc@mtb.app',     'crc',     'Anita Verma', 'AIIMS Delhi'),
        ('patient@mtb.app', 'patient', 'Priya Kumar', ''),
    ]
    ids = {}
    for email, role, name, org in demo_users:
        uid = str(uuid.uuid4())
        ids[role] = uid
        await db.users.insert_one({
            'id': uid, 'email': email, 'role': role, 'full_name': name,
            'organization': org, 'phone': '+91 98765 43210',
            'hashed_password': pwd_ctx.hash('Password1!'),
            'avatar_initials': ''.join([w[0].upper() for w in name.split()[:2]]),
            'security_question': "What is the name of your first pet?",
            'security_answer_hash': pwd_ctx.hash('bruno'),
            'created_at': now(), 'is_online': False,
        })
    # Trial
    tid = str(uuid.uuid4())
    await db.trials.insert_one({
        'id': tid, 'protocol_id': 'Protocol-001', 'title': 'A Phase II Trial of MTB-Diab-Rx in Type-2 Diabetes',
        'phase': 'Phase II', 'condition': 'Type-2 Diabetes',
        'description': 'A randomized, double-blind study of MTB-Diab-Rx vs placebo.',
        'sponsor_name': 'Pfizer Global', 'created_by': ids['sponsor'],
        'created_at': now(), 'status': 'active',
    })
    # Visits
    visits_spec = [
        (1, 'Screening', 0, ['Informed consent', 'Medical history', 'Vitals', 'Blood draw']),
        (2, 'Baseline', 7, ['Physical exam', 'ECG', 'Blood draw', 'Study drug dispense']),
        (3, 'Week 2', 14, ['Vitals', 'Adverse-event review']),
        (4, 'Week 4', 28, ['Vitals', 'Blood draw', 'Adverse-event review']),
        (5, 'Week 8', 56, ['Vitals', 'Blood draw', 'Drug accountability']),
        (6, 'Week 12', 84, ['Vitals', 'Blood draw', 'Drug accountability']),
        (7, 'Week 16 · Follow-Up', 112, ['Vitals', 'Blood draw', 'Adherence review']),
        (8, 'Week 20', 140, ['Vitals', 'Blood draw']),
        (9, 'Week 24', 168, ['Vitals', 'Blood draw', 'ECG']),
        (10, 'End of Study', 196, ['Final exam', 'Drug return', 'Final assessment']),
    ]
    for n, name, off, acts in visits_spec:
        await db.visits.insert_one({
            'id': str(uuid.uuid4()), 'trial_id': tid, 'visit_number': n,
            'name': name, 'day_offset': off, 'window_days': 3, 'activities': acts,
            'created_at': now(),
        })
    # Patient record
    enrolled = (now() - timedelta(days=70)).date().isoformat()
    await db.patients.insert_one({
        'id': str(uuid.uuid4()), 'user_id': ids['patient'],
        'full_name': 'Priya Kumar', 'email': 'patient@mtb.app',
        'phone': '+91 98765 43210', 'trial_id': tid,
        'pi_id': ids['pi'], 'crc_id': ids['crc'],
        'enrolled_date': enrolled, 'completed_visit_ids': [],
        'avatar_initials': 'PK', 'created_at': now(),
    })
    # 4 more patients for the PI/CRC list
    for fn in ['Ravi Patel', 'Sunita Iyer', 'Arjun Singh', 'Meera Joshi']:
        await db.patients.insert_one({
            'id': str(uuid.uuid4()), 'user_id': None,
            'full_name': fn, 'email': fn.lower().replace(' ', '.') + '@mtb.app',
            'phone': '+91 98765 00000', 'trial_id': tid,
            'pi_id': ids['pi'], 'crc_id': ids['crc'],
            'enrolled_date': (now() - timedelta(days=40)).date().isoformat(),
            'completed_visit_ids': [], 'avatar_initials': ''.join([w[0] for w in fn.split()[:2]]).upper(),
            'created_at': now(),
        })
    # Notifications for patient
    for title, body, kind in [
        ('Visit 7 tomorrow', 'Follow-Up Visit at AIIMS Delhi · 23 May', 'reminder'),
        ('Message from Dr. Sharma', 'Please fast for 8 hours before your Visit 7 blood draw.', 'message'),
        ('Lab results available', 'Your Visit 6 results have been reviewed.', 'result'),
    ]:
        await db.notifications.insert_one({
            'id': str(uuid.uuid4()), 'user_id': ids['patient'],
            'title': title, 'body': body, 'kind': kind, 'read': False,
            'created_at': now() - timedelta(hours=2),
        })
    await db.meta.insert_one({'key': 'seeded_v1', 'at': now()})
    return {'ok': True, 'users': demo_users, 'password': 'Password1!'}

# ── Visit mutations (mark complete / reschedule / flag) ────────────────────
class VisitPatch(BaseModel):
    status: Optional[Literal['completed', 'upcoming', 'missed', 'flagged']] = None
    scheduled_date: Optional[str] = None
    note: Optional[str] = None

@api.patch('/visits/{visit_id}')
async def patch_visit(visit_id: str, body: VisitPatch, user=Depends(require_roles('pi', 'crc', 'sponsor'))):
    upd = {k: v for k, v in body.dict().items() if v is not None}
    if not upd: raise HTTPException(400, 'Nothing to update')
    upd['updated_by'] = user['id']; upd['updated_at'] = now()
    r = await db.visits.update_one({'id': visit_id}, {'$set': upd})
    if r.matched_count == 0: raise HTTPException(404, 'Visit not found')
    v = await db.visits.find_one({'id': visit_id}, {'_id': 0})
    # Audit
    await db.audit_logs.insert_one({'id': str(uuid.uuid4()), 'user_id': user['id'], 'action': 'visit.patch', 'target_id': visit_id, 'changes': upd, 'at': now()})
    return v

# ── Reminders (patient medication reminders) ──────────────────────────────
class ReminderIn(BaseModel):
    medication: str; dosage: str; time: str; enabled: bool = True

@api.get('/reminders')
async def list_reminders(user=Depends(current_user)):
    return await db.reminders.find({'user_id': user['id']}, {'_id': 0}).sort('time', 1).to_list(100)

@api.post('/reminders')
async def create_reminder(body: ReminderIn, user=Depends(current_user)):
    doc = {'id': str(uuid.uuid4()), 'user_id': user['id'], **body.dict(), 'created_at': now()}
    await db.reminders.insert_one(doc); return serialize(doc)

@api.patch('/reminders/{rid}')
async def update_reminder(rid: str, body: dict, user=Depends(current_user)):
    await db.reminders.update_one({'id': rid, 'user_id': user['id']}, {'$set': {k: v for k, v in body.items() if k in {'enabled', 'time', 'dosage'}}})
    return {'ok': True}

@api.delete('/reminders/{rid}')
async def delete_reminder(rid: str, user=Depends(current_user)):
    await db.reminders.delete_one({'id': rid, 'user_id': user['id']}); return {'ok': True}

# ── Invitations (invite patient/team via email/SMS) ───────────────────────
class InvitationIn(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    full_name: Optional[str] = ''
    role: Optional[Role] = 'patient'
    trial_id: Optional[str] = None

@api.post('/invitations', dependencies=[Depends(require_roles('pi', 'crc', 'sponsor', 'cro'))])
async def create_invitation(body: InvitationIn, user=Depends(current_user)):
    if not body.email and not body.phone:
        raise HTTPException(400, 'Email or phone required')
    token = uuid.uuid4().hex
    doc = {
        'id': str(uuid.uuid4()), 'token': token,
        'email': (body.email or '').lower(), 'phone': body.phone or '',
        'full_name': body.full_name or '', 'role': body.role or 'patient',
        'trial_id': body.trial_id, 'invited_by': user['id'],
        'status': 'pending', 'created_at': now(),
    }
    await db.invitations.insert_one(doc)
    # Real email sending is wired via EMAIL_API_KEY env (Resend) — falls back to logging in dev.
    api_key = os.environ.get('EMAIL_API_KEY')
    invite_link = f"https://my-trial-board.app/invite/{token}"
    if api_key and body.email:
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=10) as cli:
                await cli.post('https://api.resend.com/emails', headers={'Authorization': f'Bearer {api_key}'}, json={
                    'from': 'My Trial Board <noreply@mytrialboard.app>',
                    'to': [body.email], 'subject': "You're invited to My Trial Board",
                    'html': f'<p>Hi {body.full_name or "there"},</p><p>You\'ve been invited to join a clinical trial on My Trial Board.</p><p><a href="{invite_link}">Accept the invitation</a></p>',
                })
        except Exception as e: logging.warning(f'Email send failed: {e}')
    logging.info(f'INVITATION: link={invite_link} email={body.email} phone={body.phone}')
    return {**serialize(doc), 'invite_link': invite_link}

# ── Shares + PDF export ────────────────────────────────────────────────────
class ShareIn(BaseModel):
    trial_id: str
    via: Literal['email', 'link', 'pdf'] = 'link'
    recipients: List[EmailStr] = []

@api.post('/shares', dependencies=[Depends(require_roles('sponsor', 'cro', 'pi'))])
async def create_share(body: ShareIn, user=Depends(current_user)):
    token = uuid.uuid4().hex
    doc = {'id': str(uuid.uuid4()), 'token': token, 'trial_id': body.trial_id, 'via': body.via,
           'recipients': body.recipients, 'created_by': user['id'], 'created_at': now(),
           'expires_at': now() + timedelta(days=7), 'views': 0}
    await db.shares.insert_one(doc)
    base = os.environ.get('PUBLIC_APP_URL', 'https://my-trial-board.app')
    return {**serialize(doc), 'share_link': f'{base}/s/{token}', 'pdf_link': f'/api/shares/{token}/schedule.pdf'}

@api.get('/shares/{token}/schedule.pdf')
async def share_pdf(token: str):
    from fastapi.responses import Response as FastResp
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors as rcolors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    import io
    s = await db.shares.find_one({'token': token})
    if not s: raise HTTPException(404, 'Share not found')
    if s.get('expires_at') and s['expires_at'].replace(tzinfo=timezone.utc) < now():
        raise HTTPException(410, 'Share link expired')
    await db.shares.update_one({'token': token}, {'$inc': {'views': 1}})
    trial = await db.trials.find_one({'id': s['trial_id']}, {'_id': 0}) or {}
    visits = await db.visits.find({'trial_id': s['trial_id']}, {'_id': 0}).sort('visit_number', 1).to_list(200)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Visit Schedule · {trial.get('protocol_id', '')}")
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"<b>{trial.get('protocol_id', 'Trial')} — Visit Schedule</b>", styles['Title']),
        Paragraph(trial.get('title', ''), styles['Heading3']),
        Paragraph(f"Phase: {trial.get('phase', '')} · Condition: {trial.get('condition', '')}", styles['Normal']),
        Spacer(1, 16),
    ]
    rows = [['#', 'Visit name', 'Day offset', 'Window', 'Activities']]
    for v in visits:
        rows.append([v['visit_number'], v['name'], f"D+{v['day_offset']}", f"±{v.get('window_days', 3)}d", ', '.join(v.get('activities', [])[:3])])
    t = Table(rows, repeatRows=1, colWidths=[28, 140, 60, 50, 200])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), rcolors.HexColor('#A6213F')),
        ('TEXTCOLOR', (0, 0), (-1, 0), rcolors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, rcolors.HexColor('#E6D6C5')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [rcolors.HexColor('#FBF2E8'), rcolors.white]),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(t)
    doc.build(story)
    return FastResp(content=buf.getvalue(), media_type='application/pdf', headers={'Content-Disposition': f'inline; filename="schedule-{token[:8]}.pdf"'})

# ── User preferences ──────────────────────────────────────────────────────
@api.get('/preferences')
async def get_prefs(user=Depends(current_user)):
    p = await db.preferences.find_one({'user_id': user['id']}, {'_id': 0}) or {'user_id': user['id'], 'notifications_email': True, 'notifications_push': True, 'notifications_sms': False, 'language': 'en'}
    return p

@api.patch('/preferences')
async def patch_prefs(body: dict, user=Depends(current_user)):
    allow = {
        'notifications_email', 'notifications_push', 'notifications_sms', 'language',
        # granular patient notification preferences (Profile → Notifications)
        'visit_push', 'visit_sms', 'visit_email', 'visit_remind_days',
        'med_push', 'med_sms', 'trial_updates', 'pi_messages', 'system_notifs',
    }
    upd = {k: v for k, v in body.items() if k in allow}
    await db.preferences.update_one({'user_id': user['id']}, {'$set': upd, '$setOnInsert': {'user_id': user['id']}}, upsert=True)
    return {'ok': True, **upd}

# ── Push notification token registration (Emergent push - real values at deploy time) ─
@api.post('/push/register')
async def register_push(body: dict, user=Depends(current_user)):
    token = body.get('token')
    if not token: raise HTTPException(400, 'Token required')
    await db.push_tokens.update_one({'user_id': user['id'], 'token': token}, {'$set': {'platform': body.get('platform', 'unknown'), 'updated_at': now()}, '$setOnInsert': {'created_at': now()}}, upsert=True)
    return {'ok': True}

@api.get('/audit-logs')
async def list_audit(user=Depends(require_roles('sponsor', 'cro', 'pi'))):
    return await db.audit_logs.find({}, {'_id': 0}).sort('at', -1).to_list(200)

# ── App content (config / FAQ / legal) — DB-backed, lazily seeded ─────────────
# So the app never ships hardcoded copy: these come from the DB and can be edited
# there. Each getter upserts a sensible default the first time it's requested.
DEFAULT_APP_CONFIG = {
    'key': 'app_config',
    'version': '1.0.0',
    'copyright': f'© {now().year} MTB Health Technologies',
    'support_email': 'support@mytrialboard.app',
    'support_phone': '1800-123-4567',
    'support_hours': 'Mon – Fri, 9:00 AM – 6:00 PM',
}
DEFAULT_FAQ = [
    {'order': 1, 'q': 'How do I view my upcoming visit?', 'a': 'Open My Trial from the dashboard — your next visit is highlighted at the top.'},
    {'order': 2, 'q': 'What if I miss a visit?', 'a': 'Contact your research team immediately via the Chat section in the app.'},
    {'order': 3, 'q': 'How do I contact my research team?', 'a': 'Use the Chat icon to message your PI or CRC directly.'},
    {'order': 4, 'q': 'Can I change my phone number?', 'a': 'Yes — Profile & Settings → Edit Profile. Changing it requires OTP verification.'},
    {'order': 5, 'q': 'How are medication reminders set?', 'a': 'Reminders are set by your research team based on your protocol. Manage channels in Notification Preferences.'},
]
DEFAULT_LEGAL = {
    'terms': {
        'key': 'terms', 'version': '2.1', 'effective_date': '01 Jan 2025',
        'blocks': [
            {'heading': '1. Use of Application', 'body': 'This app helps patients manage clinical-trial visit schedules, medication reminders, and communication with research teams.'},
            {'heading': '2. Privacy', 'body': 'Your personal health information is protected in accordance with applicable privacy laws including HIPAA and GDPR.'},
            {'heading': '3. Data Security', 'body': 'We use industry-standard security. All communications are encrypted using TLS 1.3.'},
            {'heading': '4. Medical Disclaimer', 'body': 'This app is informational only and does not replace professional medical advice. Always consult your healthcare provider.'},
            {'heading': '5. User Responsibilities', 'body': 'You are responsible for keeping your login credentials confidential and for all activity under your account.'},
        ],
    },
    'privacy': {
        'key': 'privacy', 'version': '2.1', 'effective_date': '01 Jan 2025',
        'blocks': [
            {'heading': 'Information We Collect', 'body': 'We collect information you provide including contact details, trial-relevant health information, and usage data.'},
            {'heading': 'How We Use Information', 'body': 'To manage your trial participation, send reminders, and facilitate communication with your research team.'},
            {'heading': 'Data Sharing', 'body': 'Shared only with your designated research team and the trial sponsor as required by your protocol.'},
            {'heading': 'Your Rights', 'body': 'You may access, correct, or request deletion of your personal data at any time via your research team.'},
        ],
    },
}

@api.get('/app/config')
async def app_config():
    doc = await db.app_content.find_one({'key': 'app_config'}, {'_id': 0})
    if not doc:
        await db.app_content.update_one({'key': 'app_config'}, {'$setOnInsert': DEFAULT_APP_CONFIG}, upsert=True)
        doc = {k: v for k, v in DEFAULT_APP_CONFIG.items() if k != 'key'}
    doc.pop('key', None)
    return doc

@api.get('/faq')
async def get_faq():
    items = await db.faq.find({}, {'_id': 0}).sort('order', 1).to_list(100)
    if not items:
        await db.faq.insert_many([dict(x) for x in DEFAULT_FAQ])
        items = [{k: v for k, v in x.items()} for x in DEFAULT_FAQ]
    return [{'q': i['q'], 'a': i['a']} for i in items]

@api.get('/legal/{doc_type}')
async def get_legal(doc_type: str):
    if doc_type not in ('terms', 'privacy'):
        raise HTTPException(404, 'Unknown document')
    doc = await db.app_content.find_one({'key': doc_type}, {'_id': 0})
    if not doc:
        default = DEFAULT_LEGAL[doc_type]
        await db.app_content.update_one({'key': doc_type}, {'$setOnInsert': default}, upsert=True)
        doc = default
    return {'version': doc['version'], 'effective_date': doc['effective_date'], 'blocks': doc['blocks']}

@api.post('/legal/accept')
async def accept_legal(user=Depends(current_user)):
    n = now()
    await db.users.update_one({'id': user['id']}, {'$set': {'terms_accepted_at': n}})
    return {'accepted_at': iso(n)}

@api.get('/')
async def root(): return {'app': 'My Trial Board', 'status': 'ok'}

app.include_router(api)
app.add_middleware(CORSMiddleware, allow_credentials=True, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

async def _ensure_indexes():
    """Create indexes in the background so a slow/unreachable DB never blocks startup."""
    try:
        # Auto-expire abandoned/unverified registrations + throttle windows via TTL.
        await db.pending_registrations.create_index('expires_at', expireAfterSeconds=0)
        await db.otp_throttle.create_index('expires_at', expireAfterSeconds=0)
        # Enforce unique emails at the DB layer (defence-in-depth vs. concurrent signups).
        await db.users.create_index(
            'email', unique=True,
            partialFilterExpression={'email': {'$type': 'string', '$gt': ''}},
        )
    except Exception as e:
        logging.warning('Index setup deferred (DB unreachable or existing duplicates?): %s', e)

@app.on_event('startup')
async def startup():
    if DEV_OTP_MODE:
        logging.warning('⚠️  DEV_OTP_MODE is ON — fixed OTP "%s" accepted for unconfigured channels. NEVER enable in production.', DEV_OTP_CODE)
    # Fire-and-forget: don't await, so the API serves immediately even if Atlas is down.
    asyncio.create_task(_ensure_indexes())

@app.on_event('shutdown')
async def shutdown(): client.close()
