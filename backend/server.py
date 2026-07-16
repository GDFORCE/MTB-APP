"""My Trial Board — Dawn Rounds clinical-trials backend.

Production-grade FastAPI app with:
- JWT auth (access + refresh)
- Role-based access (sponsor / pi / crc / patient)
- Trials, visits, patients, notifications CRUD
- Real-time chat over WebSocket (1-to-1 + group, typing, read receipts)
- MongoDB persistence
"""
from fastapi import FastAPI, APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Query, UploadFile, File, Form
from fastapi.security import OAuth2PasswordBearer
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument, UpdateOne
import os, re, json, logging, uuid, asyncio
from pathlib import Path
from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional, Dict, Literal
from datetime import datetime, date, timezone, timedelta
from passlib.context import CryptContext
import jwt

import otp_service
import protocol_extraction as pe
import storage as file_storage

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
Role = Literal['sponsor', 'cro', 'smo', 'site', 'pi', 'crc', 'patient', 'admin']

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
    avatar_file_id: Optional[str] = None   # uploaded avatar (file id from POST /api/files); '' clears it

class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str

class ChangeContactStartIn(BaseModel):
    field: Literal['email', 'phone']
    value: str

class ChangeContactVerifyIn(BaseModel):
    code: str

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
    checklist: List[str] = []   # "before you come in" patient-prep steps

class VisitUpdate(BaseModel):
    """Partial edit of an existing visit TEMPLATE (Task 4.1 edit mode). Only the
    fields present are applied; trial_id is immutable here. `visit_number` is
    editable so the editor can keep template order unique after a row is
    deleted/reordered (Finding 1); a change re-points the seq of eligible
    future instances."""
    name: Optional[str] = None
    visit_number: Optional[int] = None
    day_offset: Optional[int] = None
    window_days: Optional[int] = None
    activities: Optional[List[str]] = None
    checklist: Optional[List[str]] = None

class PatientIn(BaseModel):
    full_name: str
    email: EmailStr
    phone: Optional[str] = ''
    trial_id: str
    pi_id: Optional[str] = None
    crc_id: Optional[str] = None
    enrolled_date: Optional[str] = None
    subject_id: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None
    language: Optional[str] = None
    baseline_date: Optional[str] = None   # anchors visit-instance scheduling

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
    return jwt.encode({'sub': sub, 'role': role, 'kind': kind,
                       'iat': now(), 'exp': now() + delta}, secret, ALGO)

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
    # Admin-suspended accounts are dead sessions (Task 6.1).
    if user.get('status') == 'Suspended':
        raise HTTPException(403, 'Account suspended')
    # Admin force-logout: any token issued BEFORE force_logout_at is invalid.
    # Tokens without an iat claim (pre-6.1) are treated as old → fail-closed.
    flo = user.get('force_logout_at')
    if flo:
        iat = payload.get('iat')
        issued = datetime.fromtimestamp(iat, tz=timezone.utc) if iat else None
        if issued is None or issued < flo:
            raise HTTPException(401, 'Session terminated — please sign in again')
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

async def _read_upload_capped(file, max_bytes: int, too_large: str = 'File is too large') -> bytes:
    """Read an UploadFile in 1 MB chunks, aborting with 413 the moment the total
    exceeds max_bytes — so an oversized (or maliciously unbounded) request body
    never fully materializes in memory. Returns the bytes when within the cap."""
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, too_large)
        chunks.append(chunk)
    return b''.join(chunks)

# ── Audit trail ──────────────────────────────────────────────────────────────
async def write_audit(user, action, detail, status='success', **ctx):
    """Write a standard audit row for any mutation.

    `user` is the acting user document (or None for anonymous/public actions,
    e.g. a public invitation accept). `action` is dotted `category.verb`
    (e.g. 'visit.patch'); the category is derived from it unless overridden
    via ctx. Extra keyword context (target_id, changes, …) is stored verbatim.
    Returns the audit row id.
    """
    user = user or {}
    doc = {
        'id': str(uuid.uuid4()),
        'user_id': user.get('id'),
        'user_name': user.get('full_name', ''),
        'role': user.get('role', ''),
        'org': user.get('organization', ''),
        'action': action,
        'category': ctx.pop('category', action.split('.', 1)[0]),
        'detail': detail,
        'ip': ctx.pop('ip', ''),
        'device': ctx.pop('device', ''),
        'status': status,
        'created_at': now(),
        **ctx,
    }
    await db.audit_logs.insert_one(doc)
    return doc['id']

# ── Organizations ────────────────────────────────────────────────────────────
ORG_TYPES = ('sponsor', 'cro', 'smo', 'site')

def org_type_for_role(role: str) -> str:
    """sponsor/cro/smo/site users belong to that org type; pi/crc (and anyone
    else) work at a site."""
    return role if role in ORG_TYPES else 'site'

async def ensure_organization(name: Optional[str], org_type: str = 'site', actor=None):
    """Upsert an organization record the first time its name is seen
    (e.g. when a user registers with an organization we don't know yet)."""
    name = (name or '').strip()
    if not name:
        return
    oid = str(uuid.uuid4())
    res = await db.organizations.update_one(
        {'name': name},
        {'$setOnInsert': {
            'id': oid, 'name': name,
            'type': org_type if org_type in ORG_TYPES else 'site',
            'address': '', 'contact': '', 'email': '', 'website': '',
            'status': 'active', 'created_at': now(),
        }},
        upsert=True,
    )
    if res.upserted_id is not None:
        await write_audit(actor, 'organization.create',
                          f'Organization "{name}" auto-created at registration',
                          target_id=oid)

# ── Auth ─────────────────────────────────────────────────────────────────────
@api.post('/auth/register')
async def register(body: RegisterIn):
    if body.role == 'admin':
        raise HTTPException(403, 'This role cannot self-register')
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
    await ensure_organization(body.organization, org_type_for_role(body.role), actor=doc)
    access = make_token(uid, body.role, 'access')
    refresh = make_token(uid, body.role, 'refresh')
    return {'access_token': access, 'refresh_token': refresh, 'user': serialize({**doc})}

@api.post('/auth/login')
async def login(body: LoginIn):
    user = await db.users.find_one({'email': body.email.lower()})
    if not user or not pwd_ctx.verify(body.password, user['hashed_password']):
        raise HTTPException(401, 'Invalid credentials')
    if user.get('status') == 'Suspended':
        raise HTTPException(403, 'Your account has been suspended. Contact support.')
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
    if body.avatar_file_id is not None:
        updates['avatar_file_id'] = body.avatar_file_id.strip() or None
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
OTP_MAX_RESENDS = 3              # resend attempts allowed per channel
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
    await ensure_organization(doc['organization'], org_type_for_role(doc['role']), actor=doc)
    access = make_token(uid, doc['role'], 'access')
    refresh = make_token(uid, doc['role'], 'refresh')
    return {'access_token': access, 'refresh_token': refresh, 'user': serialize({**doc})}

@api.post('/auth/register/start')
async def register_start(body: RegisterStartIn):
    if body.role == 'admin':
        raise HTTPException(403, 'This role cannot self-register')
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
        'resend_counts': {ch: 0 for ch in channels},
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
    resend_counts = pending.get('resend_counts') or {}
    if int(resend_counts.get(body.channel, 0)) >= OTP_MAX_RESENDS:
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
         '$inc': {'send_count': 1, f'resend_counts.{body.channel}': 1}},
    )
    return {'ok': True, 'resend_cooldown': OTP_RESEND_COOLDOWN_SEC,
            'resend_count': int(resend_counts.get(body.channel, 0)) + 1,
            'resend_limit': OTP_MAX_RESENDS}

# ── Contact change (email / phone) with OTP verification ─────────────────────
# Reuses the registration OTP machinery (_deliver_otp / _otp_matches /
# _enforce_rate_limit / DEV_OTP_MODE). A single pending change per user lives in
# `pending_contact_changes`; a new /start replaces any earlier one, and rows
# auto-expire via the TTL index on `expires_at` (see _ensure_indexes).
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

async def _contact_value_taken(field: str, value: str, user_id: str) -> bool:
    return bool(await db.users.find_one({field: value, 'id': {'$ne': user_id}}))

@api.post('/auth/change-contact/start')
async def change_contact_start(body: ChangeContactStartIn, user=Depends(current_user)):
    field = body.field
    value = (body.value or '').strip()
    if field == 'email':
        value = value.lower()
        if not _EMAIL_RE.match(value):
            raise HTTPException(400, 'Please enter a valid email address.')
    elif not value:
        raise HTTPException(400, 'Please enter a valid phone number.')
    if await _contact_value_taken(field, value, user['id']):
        raise HTTPException(409, f'That {field} is already in use by another account.')

    await _enforce_rate_limit(value)
    # Single pending change per user — a new start supersedes the old one.
    await db.pending_contact_changes.delete_many({'user_id': user['id']})

    code = otp_service.generate_code()
    doc = {
        'id': str(uuid.uuid4()),
        'user_id': user['id'],
        'field': field,
        'value': value,
        'channel': field,   # email -> email channel, phone -> sms channel
        'otp_hash': pwd_ctx.hash(code),
        'attempts': 0,
        'created_at': now(),
        'expires_at': now() + timedelta(minutes=otp_service.OTP_TTL_MIN),
    }
    # Deliver first — if the send fails we raise and persist nothing.
    await _deliver_otp(field, value, code)
    await db.pending_contact_changes.insert_one(doc)
    return {'field': field, 'value': value, 'channel': field,
            'expires_in': otp_service.OTP_TTL_MIN * 60}

@api.post('/auth/change-contact/verify')
async def change_contact_verify(body: ChangeContactVerifyIn, user=Depends(current_user)):
    pending = await db.pending_contact_changes.find_one({'user_id': user['id']})
    if not pending:
        raise HTTPException(404, 'No pending contact change. Please start again.')
    if pending['expires_at'] < now():
        await db.pending_contact_changes.delete_one({'id': pending['id']})
        raise HTTPException(400, 'Your verification code expired. Please start again.')
    if pending.get('attempts', 0) >= OTP_MAX_VERIFY_ATTEMPTS:
        await db.pending_contact_changes.delete_one({'id': pending['id']})
        raise HTTPException(429, 'Too many incorrect attempts. Please start again.')
    if not _otp_matches(body.code, pending.get('otp_hash')):
        await db.pending_contact_changes.update_one({'id': pending['id']}, {'$inc': {'attempts': 1}})
        raise HTTPException(400, 'Incorrect verification code')

    field, value = pending['field'], pending['value']
    # Re-check uniqueness at commit time (another account may have taken it since).
    if await _contact_value_taken(field, value, user['id']):
        await db.pending_contact_changes.delete_one({'id': pending['id']})
        raise HTTPException(409, f'That {field} is already in use by another account.')

    await db.users.update_one(
        {'id': user['id']},
        {'$set': {field: value, f'{field}_verified': True}})
    await db.pending_contact_changes.delete_one({'id': pending['id']})
    await write_audit(user, 'contact.change', f'Changed {field} to {value}',
                      target_id=user['id'], field=field)
    fresh = await db.users.find_one(
        {'id': user['id']}, {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0})
    return {'ok': True, 'field': field, 'value': value, 'user': serialize(fresh)}

# ── Trials ───────────────────────────────────────────────────────────────────
@api.get('/trials')
async def list_trials(user=Depends(current_user)):
    trials = await db.trials.find({}, {'_id': 0}).to_list(500)
    # patient: filter to enrolled trials
    if user['role'] == 'patient':
        enrolled = await db.patients.find({'user_id': user['id']}, {'_id': 0, 'trial_id': 1}).to_list(100)
        ids = {p['trial_id'] for p in enrolled}
        trials = [t for t in trials if t['id'] in ids]

    # Batched enrolment counts — one grouped query over db.patients (no per-trial
    # N+1). enrolled_count is an aggregate (not patient PII), fine for sponsors.
    trial_ids = [t['id'] for t in trials]
    counts: Dict[str, int] = {}
    if trial_ids:
        async for row in db.patients.aggregate([
            {'$match': {'trial_id': {'$in': trial_ids}}},
            {'$group': {'_id': '$trial_id', 'n': {'$sum': 1}}},
        ]):
            counts[row['_id']] = row['n']

    for t in trials:
        t['enrolled_count'] = counts.get(t['id'], 0)
        # target_enrollment is NOT captured at trial creation (POST /trials is frozen
        # and TrialIn has no target field; the seed doesn't set one either). Surface
        # it only when a trial doc genuinely carries it, else null — never fabricate
        # a target. Keying it explicitly makes the null obvious and consistent.
        t['target_enrollment'] = t.get('target_enrollment')
        # schedule_status (approved/flagged/…) is already on `t` when stored — the
        # find() above returns the full doc, so we neither add nor fabricate it.
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

@api.post('/trials/{trial_id}/extract-schedule',
          dependencies=[Depends(require_roles('sponsor', 'cro', 'pi'))])
async def extract_schedule(trial_id: str, file: UploadFile = File(...),
                           user=Depends(current_user)):
    """AI-assisted: read an uploaded protocol PDF and return its Schedule of
    Assessments as visit templates for the caller to REVIEW and edit before
    saving. Never writes visits — the sponsor confirms via the normal save flow.
    Trial-ownership scoped (same rule as the schedule endpoints). The PDF is
    streamed to the extractor and discarded; nothing is persisted here."""
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if user['role'] in ('sponsor', 'cro'):
        owns = await _trial_in_caller_org(user, trial_id)
    else:  # pi
        owns = await _pi_owns_trial(user, trial)
    if not owns:
        raise HTTPException(403, 'You do not have access to this trial')

    ctype = (file.content_type or '').lower()
    if ctype not in ('application/pdf', 'application/octet-stream', ''):
        raise HTTPException(400, 'Upload a PDF protocol document')
    data = await _read_upload_capped(file, pe.MAX_PDF_BYTES, 'Protocol PDF is too large (max 25 MB)')
    if not data:
        raise HTTPException(400, 'The uploaded file is empty')
    if data[:5] != b'%PDF-':
        raise HTTPException(400, 'The uploaded file does not look like a PDF')

    try:
        schedule = await pe.get_extractor().extract(data)
    except pe.ExtractionNotConfigured:
        raise HTTPException(503, 'Protocol extraction is not configured on the '
                            'server. Set ANTHROPIC_API_KEY and restart.')
    except pe.ExtractionError as e:
        raise HTTPException(502, f'Could not extract the schedule: {e}')

    await write_audit(
        user, 'trial.extract_schedule',
        f'Extracted {len(schedule.visits)} visit(s) from protocol PDF for '
        f'{trial.get("protocol_id") or trial_id}', trial_id=trial_id)
    return {'visits': [v.dict() for v in schedule.visits]}

# ── Visit schedule ──────────────────────────────────────────────────────────
@api.post('/visits')
async def create_visit(body: VisitIn, user=Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))):
    vid = str(uuid.uuid4())
    doc = {'id': vid, **body.dict(), 'created_at': now()}
    await db.visits.insert_one(doc)
    # Finding 2: a visit ADDED to an in-flight schedule must appear for patients
    # already enrolled (materialize_visit_instances is a per-patient no-op once
    # they have instances, so it would never reach them otherwise).
    await _materialize_new_template_for_enrolled(doc)
    return serialize(doc)


async def _require_schedule_owner(user: dict, trial: dict):
    """Trial-ownership gate shared by the schedule CRUD endpoints. sponsor/cro
    own via their org (_trial_in_caller_org); pi owns via _pi_owns_trial. Raises
    403 for a foreign trial (fail-closed)."""
    if user['role'] in ('sponsor', 'cro'):
        owns = await _trial_in_caller_org(user, trial['id'])
    else:  # pi
        owns = await _pi_owns_trial(user, trial)
    if not owns:
        raise HTTPException(403, 'You do not have access to this trial')


@api.get('/trials/{trial_id}/visits')
async def list_trial_visits(trial_id: str,
                            user=Depends(require_roles('sponsor', 'cro', 'pi'))):
    """The trial's visit TEMPLATES, sorted by visit_number — the schedule the
    edit screen loads on entry. Trial-ownership scoped (403 for a foreign trial)."""
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    await _require_schedule_owner(user, trial)
    return await db.visits.find({'trial_id': trial_id}, {'_id': 0}) \
                          .sort('visit_number', 1).to_list(500)


@api.put('/visits/{visit_id}')
async def update_visit(visit_id: str, body: VisitUpdate,
                       user=Depends(require_roles('sponsor', 'cro', 'pi'))):
    """Update a visit TEMPLATE (name/day_offset/window_days/activities/checklist)
    and re-materialize the trial's future-pending instances. Trial-ownership
    scoped (403 for a foreign trial)."""
    tpl = await db.visits.find_one({'id': visit_id}, {'_id': 0})
    if not tpl:
        raise HTTPException(404, 'Visit template not found')
    trial = await db.trials.find_one({'id': tpl.get('trial_id')}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    await _require_schedule_owner(user, trial)
    fields = {k: v for k, v in body.dict().items() if v is not None}
    if not fields:
        raise HTTPException(400, 'Nothing to update')
    await db.visits.update_one({'id': visit_id}, {'$set': fields})
    fresh = await db.visits.find_one({'id': visit_id}, {'_id': 0})
    remat = await _rematerialize_template_change(fresh)
    await write_audit(user, 'visit.update',
                      f"Updated visit template {fresh.get('name', '')} "
                      f"({remat} future instance(s) re-materialized)",
                      target_id=visit_id, trial_id=tpl.get('trial_id'),
                      changes={k: iso(v) for k, v in fields.items()})
    return serialize(fresh)


@api.delete('/visits/{visit_id}')
async def delete_visit(visit_id: str,
                       user=Depends(require_roles('sponsor', 'cro', 'pi'))):
    """Delete a visit TEMPLATE and remove its future-pending instances (completed
    / missed / past ones are kept as history). Trial-ownership scoped (403)."""
    tpl = await db.visits.find_one({'id': visit_id}, {'_id': 0})
    if not tpl:
        raise HTTPException(404, 'Visit template not found')
    trial = await db.trials.find_one({'id': tpl.get('trial_id')}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    await _require_schedule_owner(user, trial)
    removed = await _rematerialize_template_delete(tpl)
    await db.visits.delete_one({'id': visit_id})
    await write_audit(user, 'visit.delete',
                      f"Deleted visit template {tpl.get('name', '')} "
                      f"({removed} future instance(s) removed)",
                      target_id=visit_id, trial_id=tpl.get('trial_id'))
    return {'deleted': True, 'instances_removed': removed}

async def _patient_care_context(patient) -> dict:
    """Site + PI contact for a patient, joined from their assigned PI user.

    Enriches GET /visits/mine so the mobile app renders the real site name and
    PI (name / phone / email for tel:+mailto: links) instead of hardcoding
    "AIIMS Delhi / Dr. Sharma". All keys are always present (empty string when
    the patient has no PI assigned) so the client can rely on the shape."""
    pi = None
    if patient.get('pi_id'):
        pi = await db.users.find_one({'id': patient['pi_id']}, {'_id': 0})
    pi = pi or {}
    return {
        'site': pi.get('organization') or '',
        'pi_name': pi.get('full_name') or '',
        'pi_phone': pi.get('phone') or '',
        'pi_email': pi.get('email') or '',
    }

async def _trial_checklist_map(trial_id) -> dict:
    """Map of visit-template id -> its `checklist` prep steps (empty list when
    the template carries none), used to enrich per-patient visit instances."""
    tpls = await db.visits.find({'trial_id': trial_id},
                                {'_id': 0, 'id': 1, 'checklist': 1}).to_list(500)
    return {t['id']: (t.get('checklist') or []) for t in tpls}

@api.get('/visits/mine')
async def my_visits(user=Depends(current_user)):
    """Return upcoming/completed visits for the logged-in patient.

    Served from the patient's own `visit_instances` (created at enrollment /
    startup migration). Falls back to on-the-fly template computation for
    legacy patient records that were never materialized, keeping the exact
    field names the mobile app already consumes. Each visit is additively
    enriched with site/pi_name/pi_phone/pi_email (joined from the patient's PI)
    and the visit template's `checklist`."""
    if user['role'] != 'patient':
        return []
    patient = await db.patients.find_one({'user_id': user['id']}, {'_id': 0})
    if not patient: return []
    care = await _patient_care_context(patient)
    checklists = await _trial_checklist_map(patient['trial_id'])
    instances = await db.visit_instances.find({'patient_id': patient['id']}, {'_id': 0}) \
                                        .sort('seq', 1).to_list(200)
    if instances:
        return [{**inst, **care,
                 'checklist': checklists.get(inst.get('visit_template_id'), [])}
                for inst in instances]
    visits = await db.visits.find({'trial_id': patient['trial_id']}, {'_id': 0}).sort('visit_number', 1).to_list(200)
    completed = set(patient.get('completed_visit_ids', []))
    result = []
    base_date = datetime.fromisoformat(patient.get('enrolled_date') or now().isoformat().replace('Z', '+00:00').replace('+00:00', ''))
    for v in visits:
        scheduled = base_date + timedelta(days=v['day_offset'])
        result.append({
            **v, **care,
            'patient_id': patient['id'],
            'scheduled_date': scheduled.isoformat(),
            'checklist': v.get('checklist') or [],
            'status': 'completed' if v['id'] in completed else ('upcoming' if scheduled >= now().replace(tzinfo=None) else 'missed'),
        })
    return result

# ── Visit instances (per-patient copies of the trial's visit templates) ─────
# The shared `visits` docs are TEMPLATES. Mutating them per patient would leak
# one patient's completion into every other patient's schedule, so on enrollment
# each patient gets their own `visit_instances` rows, and all per-patient
# updates go through PATCH /visit-instances/{id}.

def _patient_visit_anchor(patient) -> datetime:
    """The date a patient's visit schedule anchors on: their baseline date when
    present, else the enrolment date (legacy / seed patients), else now. Always
    returned tz-aware (UTC) so date math is stable."""
    base = None
    for cand in (patient.get('baseline_date'), patient.get('enrolled_date')):
        if cand:
            try:
                base = datetime.fromisoformat(cand)
                break
            except (TypeError, ValueError):
                continue
    if base is None:
        base = now()
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base


async def materialize_visit_instances(patient) -> int:
    """Create one visit_instance per trial visit template for `patient`.

    Idempotent per patient (no-op if any instances already exist). Honors the
    legacy `completed_visit_ids` list so migrated patients keep their history;
    otherwise status derives from the scheduled date vs. now (matching the old
    GET /visits/mine computation). Returns the number of instances created.
    """
    if not patient or not patient.get('id') or not patient.get('trial_id'):
        return 0
    if await db.visit_instances.count_documents({'patient_id': patient['id']}, limit=1):
        return 0
    templates = await db.visits.find({'trial_id': patient['trial_id']}, {'_id': 0}) \
                               .sort('visit_number', 1).to_list(500)
    if not templates:
        return 0
    base = _patient_visit_anchor(patient)
    n = now()
    completed = set(patient.get('completed_visit_ids') or [])
    docs = []
    for t in templates:
        sched = base + timedelta(days=t.get('day_offset', 0))
        wd = t.get('window_days', 3)
        docs.append({
            'id': str(uuid.uuid4()),
            'patient_id': patient['id'],
            'trial_id': patient['trial_id'],
            'visit_template_id': t['id'],
            'name': t.get('name', ''),
            'seq': t.get('visit_number'),
            # duplicated template fields the RN app reads from /visits/mine
            'visit_number': t.get('visit_number'),
            'activities': t.get('activities', []),
            'window_days': wd,
            'scheduled_date': sched,
            'window_start': sched - timedelta(days=wd),
            'window_end': sched + timedelta(days=wd),
            'status': 'completed' if t['id'] in completed
                      else ('upcoming' if sched >= n else 'missed'),
            'note': '',
            'updated_by': None,
            'updated_at': n,
            'created_at': n,
        })
    await db.visit_instances.insert_many(docs)
    return len(docs)


async def _materialize_new_template_for_enrolled(template) -> int:
    """Create the single instance of a JUST-ADDED template for every patient
    already enrolled in its trial (Finding 2).

    `materialize_visit_instances` is a per-patient no-op once a patient has any
    instances, so a template added mid-trial would otherwise never reach enrolled
    patients. Here we create only THIS template's instance, future-dated off each
    patient's own baseline/enrolment anchor, with the same status/shape as normal
    materialization. Patients not yet materialized are skipped (they'll pick it up
    at enrollment); an existing instance for this template is never duplicated.
    Returns the number of instances created."""
    if not template or not template.get('id') or not template.get('trial_id'):
        return 0
    n = now()
    wd = template.get('window_days', 3)
    created = 0
    async for patient in db.patients.find({'trial_id': template['trial_id']}, {'_id': 0}):
        # only patients who were already materialized need the retro-fit
        if not await db.visit_instances.count_documents({'patient_id': patient['id']}, limit=1):
            continue
        if await db.visit_instances.count_documents(
                {'patient_id': patient['id'], 'visit_template_id': template['id']}, limit=1):
            continue
        base = _patient_visit_anchor(patient)
        sched = base + timedelta(days=template.get('day_offset', 0))
        await db.visit_instances.insert_one({
            'id': str(uuid.uuid4()),
            'patient_id': patient['id'],
            'trial_id': template['trial_id'],
            'visit_template_id': template['id'],
            'name': template.get('name', ''),
            'seq': template.get('visit_number'),
            'visit_number': template.get('visit_number'),
            'activities': template.get('activities', []),
            'window_days': wd,
            'scheduled_date': sched,
            'window_start': sched - timedelta(days=wd),
            'window_end': sched + timedelta(days=wd),
            'status': 'upcoming' if sched >= n else 'missed',
            'note': '',
            'updated_by': None,
            'updated_at': n,
            'created_at': n,
        })
        created += 1
    return created


def _instance_is_repointable(inst, n) -> bool:
    """Whether a visit_instance may be safely re-materialized when its template
    changes. FAIL-CLOSED: only FUTURE, still-pending instances that no one has
    touched are eligible. Completed / missed / rescheduled / past instances and
    any instance carrying patient activity (a note, or an explicit
    updated_by from a PATCH) are treated as history and left untouched."""
    if inst.get('updated_by'):        # someone patched it (reschedule/complete/…)
        return False
    if inst.get('note'):              # carries patient/staff activity
        return False
    if inst.get('status') not in ('upcoming', 'scheduled'):
        return False
    sched = inst.get('scheduled_date')
    if sched is None:
        return False
    if isinstance(sched, str):
        try:
            sched = datetime.fromisoformat(sched)
        except ValueError:
            return False
    if sched.tzinfo is None:
        sched = sched.replace(tzinfo=timezone.utc)
    return sched >= n                 # future only


async def _rematerialize_template_change(template) -> int:
    """Propagate a TEMPLATE edit to the trial's future-pending visit_instances.

    Recomputes name/activities/window/scheduled_date for every eligible instance
    (see `_instance_is_repointable`) off each patient's own visit anchor, so a
    schedule edit flows through to patients who haven't yet had the visit —
    without ever clobbering completed/missed/past/touched history. Returns the
    number of instances updated."""
    n = now()
    updated = 0
    anchors: Dict[str, datetime] = {}
    async for inst in db.visit_instances.find(
            {'visit_template_id': template['id']}, {'_id': 0}):
        if not _instance_is_repointable(inst, n):
            continue
        pid = inst['patient_id']
        if pid not in anchors:
            patient = await db.patients.find_one({'id': pid}, {'_id': 0})
            anchors[pid] = _patient_visit_anchor(patient) if patient else n
        sched = anchors[pid] + timedelta(days=template.get('day_offset', 0))
        wd = template.get('window_days', 3)
        await db.visit_instances.update_one({'id': inst['id']}, {'$set': {
            'name': template.get('name', ''),
            # keep the instance's ordinal consistent with the template when the
            # editor re-numbers a row (Finding 1) — only ever for eligible
            # future/pending instances (completed/past keep their original seq).
            'seq': template.get('visit_number'),
            'visit_number': template.get('visit_number'),
            'activities': template.get('activities', []),
            'window_days': wd,
            'scheduled_date': sched,
            'window_start': sched - timedelta(days=wd),
            'window_end': sched + timedelta(days=wd),
            'status': 'upcoming' if sched >= n else 'missed',
            'updated_at': n,
        }})
        updated += 1
    return updated


async def _rematerialize_template_delete(template) -> int:
    """Remove the future-pending visit_instances of a DELETED template. Completed
    / missed / past / patient-touched instances are kept as history. Returns the
    number of instances removed."""
    n = now()
    removed = 0
    async for inst in db.visit_instances.find(
            {'visit_template_id': template['id']}, {'_id': 0}):
        if _instance_is_repointable(inst, n):
            await db.visit_instances.delete_one({'id': inst['id']})
            removed += 1
    return removed


async def _migrate_visit_instances():
    """Startup backfill: materialize instances for patients enrolled before the
    visit_instances collection existed. Idempotent + cheap (skips patients that
    already have instances); failures only log so the API still boots."""
    try:
        have = await db.visit_instances.distinct('patient_id')
        total = 0
        async for p in db.patients.find({'id': {'$nin': have}}, {'_id': 0}):
            total += await materialize_visit_instances(p)
        if total:
            logging.info('Visit-instance migration: materialized %d instance(s)', total)
    except Exception as e:
        logging.warning('Visit-instance migration deferred (DB unreachable?): %s', e)

# ── Patients ────────────────────────────────────────────────────────────────
# Statuses that still need action (as opposed to 'completed' / 'missed', which
# are terminal for a given visit instance).
_ACTIONABLE_VISIT_STATUSES = ('scheduled', 'upcoming', 'overdue')


def _derive_patient_status(instances, start_today):
    """Reduce a patient's visit instances to a single list-level status plus
    the soonest actionable visit (`next_visit`), both computed on read.

    - no instances                       → 'no_visits'
    - a pending visit already past-due    → 'overdue'
    - a pending visit still ahead         → 'active'
    - every instance completed            → 'completed'
    - otherwise (only missed remain)      → 'active'
    `next_visit` is the soonest actionable instance (past-due first, else the
    next upcoming), or None when nothing is actionable.
    """
    if not instances:
        return 'no_visits', None
    actionable = [i for i in instances
                  if i.get('status') in _ACTIONABLE_VISIT_STATUSES
                  and isinstance(i.get('scheduled_date'), datetime)]
    actionable.sort(key=lambda i: i['scheduled_date'])
    next_visit = None
    if actionable:
        nv = actionable[0]
        next_visit = {
            'id': nv['id'],
            'name': nv.get('name', ''),
            'seq': nv.get('seq'),
            'scheduled_date': iso(nv.get('scheduled_date')),
            'status': nv.get('status'),
        }
    overdue = any(i['scheduled_date'] < start_today or i.get('status') == 'overdue'
                  for i in actionable)
    if overdue:
        status = 'overdue'
    elif actionable:
        status = 'active'
    elif all(i.get('status') == 'completed' for i in instances):
        status = 'completed'
    else:
        status = 'active'
    return status, next_visit


# ── Ownership scoping (Task 3.75) ────────────────────────────────────────────
# Single source of truth for "may this caller reach this patient / trial?",
# shared by GET /patients/{id}, GET /patients/{id}/visits,
# PATCH /visit-instances/{id} and POST /schedules/{trial_id}/approve|flag.
# Mirrors the GET /patients list rule (site staff scoped to their own site;
# sponsors to their own org's trials) so a crafted id cannot leak a foreign
# patient. NOTE: the medications/calendar `_staff_scoped_patient` helper is a
# deliberately STRICTER pi_id/crc_id-only check and is intentionally left
# unchanged; here the brief calls for "patients whose site/org matches theirs",
# so same-site colleagues are allowed while cross-site is blocked.

async def _org_of(user_id: Optional[str]) -> str:
    """The organization string of a user id (empty when unknown/unset)."""
    if not user_id:
        return ''
    u = await db.users.find_one({'id': user_id}, {'_id': 0, 'organization': 1})
    return (u.get('organization') or '').strip() if u else ''

async def _patient_site_org(patient: dict) -> str:
    """The site a patient belongs to: the org of its assigned PI, else its CRC,
    else whoever enrolled it (created_by)."""
    for key in ('pi_id', 'crc_id', 'created_by'):
        org = await _org_of(patient.get(key))
        if org:
            return org
    return ''

async def _trial_in_caller_org(user: dict, trial_id: Optional[str]) -> bool:
    """True when a trial belongs to the sponsor/cro caller's organization — they
    created it, or its sponsor_name matches their org."""
    if not trial_id:
        return False
    trial = await db.trials.find_one(
        {'id': trial_id}, {'_id': 0, 'sponsor_name': 1, 'created_by': 1})
    if not trial:
        return False
    if trial.get('created_by') == user['id']:
        return True
    org = (user.get('organization') or '').strip()
    return bool(org) and (trial.get('sponsor_name') or '').strip() == org

async def _can_access_patient(user: dict, patient: dict) -> bool:
    """Ownership predicate shared by every single-patient staff endpoint.

    pi/crc: the patient must be assigned to them (pi_id / crc_id), enrolled by
    them (created_by), or sit at their own site (same organization).
    sponsor/cro: the patient must be enrolled in a trial belonging to their org.
    Any other role: no access.
    """
    role = user['role']
    if role in ('pi', 'crc'):
        key = 'pi_id' if role == 'pi' else 'crc_id'
        if patient.get(key) == user['id'] or patient.get('created_by') == user['id']:
            return True
        caller_org = (user.get('organization') or '').strip()
        return bool(caller_org) and (await _patient_site_org(patient)) == caller_org
    if role in ('sponsor', 'cro'):
        return await _trial_in_caller_org(user, patient.get('trial_id'))
    return False

async def _require_patient(user: dict, patient_id: Optional[str]) -> dict:
    """Load a patient and enforce the caller's ownership scope. 404 when it does
    not exist at all; 403 when it exists but lies outside the caller's scope."""
    p = await db.patients.find_one({'id': patient_id}, {'_id': 0}) if patient_id else None
    if not p:
        raise HTTPException(404, 'Patient not found')
    if not await _can_access_patient(user, p):
        raise HTTPException(403, 'You do not have access to this patient')
    return p

async def _pi_owns_trial(user: dict, trial: dict) -> bool:
    """Whether a PI may review a trial's visit schedule. FAIL-CLOSED: the PI must
    belong to the trial via one of three legitimate ties —
      1. they created it (`created_by`), or
      2. their org matches the trial's org (`sponsor_name`) — this is the
         pre-enrollment approval path (valid even for an unclaimed trial), or
      3. they are a listed PI on it (own a patient enrolled in it).
    A PI whose org differs from the trial's org, who is not the creator, and who
    has no enrolled patient gets no access — even on an 'unclaimed' trial (no
    prior 'unclaimed -> any PI' allow-path)."""
    if trial.get('created_by') == user['id']:
        return True
    org = (user.get('organization') or '').strip()
    if org and (trial.get('sponsor_name') or '').strip() == org:
        return True
    mine = await db.patients.find_one(
        {'trial_id': trial['id'], 'pi_id': user['id']}, {'_id': 0, 'id': 1})
    return mine is not None


@api.get('/patients')
async def list_patients(user=Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))):
    if user['role'] == 'pi':
        q = {'pi_id': user['id']}
    elif user['role'] == 'crc':
        q = {'crc_id': user['id']}
    else:
        # sponsor/cro: FAIL-CLOSED — only patients enrolled in a trial owned by
        # the caller's org (created by them, or sponsor_name == their org), the
        # same tie the detail endpoint enforces via _trial_in_caller_org. An
        # empty org / no org trials yields an empty list, never every patient.
        org = (user.get('organization') or '').strip()
        trial_or = [{'created_by': user['id']}]
        if org:
            trial_or.append({'sponsor_name': org})
        trials = await db.trials.find({'$or': trial_or}, {'_id': 0, 'id': 1}).to_list(2000)
        q = {'trial_id': {'$in': [t['id'] for t in trials]}}
    patients = await db.patients.find(q, {'_id': 0}).to_list(500)
    if patients:
        pids = [p['id'] for p in patients]
        insts = await db.visit_instances.find(
            {'patient_id': {'$in': pids}}, {'_id': 0}).sort('seq', 1).to_list(5000)
        by_patient: Dict[str, list] = {}
        for i in insts:
            by_patient.setdefault(i['patient_id'], []).append(i)
        start_today = now().replace(hour=0, minute=0, second=0, microsecond=0)
        for p in patients:
            status, next_visit = _derive_patient_status(by_patient.get(p['id'], []), start_today)
            p['status'] = status
            p['next_visit'] = next_visit
    return patients

@api.post('/patients', dependencies=[Depends(require_roles('pi', 'crc'))])
async def add_patient(body: PatientIn, user=Depends(current_user)):
    # Server-side duplicate subject-ID guard (scoped to the trial) — the client
    # warns optimistically, but the DB is the source of truth.
    if body.subject_id:
        dup = await db.patients.find_one(
            {'trial_id': body.trial_id, 'subject_id': body.subject_id},
            {'_id': 0, 'id': 1})
        if dup:
            raise HTTPException(409, f'Subject ID {body.subject_id} already exists in this trial')
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
    created = await materialize_visit_instances(doc)
    await write_audit(user, 'patient.enroll',
                      f"Enrolled {doc['full_name']} in trial {doc['trial_id']} "
                      f"({created} visit instance(s) materialized)",
                      target_id=pid, trial_id=doc['trial_id'])
    return serialize(doc)

@api.get('/patients/{patient_id}')
async def get_patient(patient_id: str, user=Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))):
    """Patient detail: the patient record + its trial + its visit instances."""
    p = await _require_patient(user, patient_id)
    trial = await db.trials.find_one({'id': p.get('trial_id')}, {'_id': 0})
    instances = await db.visit_instances.find({'patient_id': patient_id}, {'_id': 0}) \
                                        .sort('seq', 1).to_list(500)
    return {**p, 'trial': trial, 'instances': instances}

@api.get('/patients/{patient_id}/visits')
async def get_patient_visits(patient_id: str, user=Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))):
    await _require_patient(user, patient_id)
    return await db.visit_instances.find({'patient_id': patient_id}, {'_id': 0}) \
                                   .sort('seq', 1).to_list(500)

# ── Organizations directory ─────────────────────────────────────────────────
@api.get('/organizations')
async def list_organizations(type: Optional[str] = None, search: Optional[str] = None,
                             include_platform_contact: bool = False):
    """Public directory of known organizations (used by the register screen).

    Platform-contact details are opt-in so live typeahead searches do not return
    user contact data. The registration Continue action requests them only after
    an exact organization match has been entered.
    """
    q: Dict = {}
    if type:
        q['type'] = type
    if search and search.strip():
        q['name'] = {'$regex': re.escape(search.strip()), '$options': 'i'}
    organizations = await db.organizations.find(q, {'_id': 0}).sort('name', 1).to_list(200)
    if not include_platform_contact or not organizations:
        return organizations

    names = [org['name'] for org in organizations]
    representatives = await db.users.find(
        {'organization': {'$in': names}, 'role': {'$ne': 'patient'}},
        {'_id': 0, 'organization': 1, 'full_name': 1, 'email': 1, 'phone': 1,
         'role': 1, 'org_admin': 1, 'profile.designation': 1},
    ).sort([('org_admin', -1), ('created_at', 1)]).to_list(1000)

    contacts: Dict[str, dict] = {}
    for user in representatives:
        organization = user.get('organization')
        if not organization or organization in contacts:
            continue
        profile = user.get('profile') or {}
        contacts[organization] = {
            'name': user.get('full_name') or 'Organization Admin',
            'designation': profile.get('designation') or (
                'Platform Contact Admin' if user.get('org_admin') else 'Organization Representative'
            ),
            'email': user.get('email') or '',
            'phone': user.get('phone') or '',
        }

    for org in organizations:
        contact = contacts.get(org['name'])
        if contact:
            org['platform_contact'] = contact
        elif org.get('email') or org.get('contact'):
            org['platform_contact'] = {
                'name': 'Organization Admin',
                'designation': 'Platform Contact Admin',
                'email': org.get('email') or '',
                'phone': org.get('contact') or '',
            }
    return organizations

# ── Notifications ───────────────────────────────────────────────────────────
@api.get('/notifications')
async def my_notifications(user=Depends(current_user)):
    items = await db.notifications.find({'user_id': user['id']}, {'_id': 0}).sort('created_at', -1).to_list(100)
    return items

@api.get('/notifications/unread-count')
async def unread_notification_count(user=Depends(current_user)):
    count = await db.notifications.count_documents({'user_id': user['id'], 'read': {'$ne': True}})
    return {'count': count}

@api.post('/notifications/read-all')
async def mark_all_notifications_read(user=Depends(current_user)):
    r = await db.notifications.update_many(
        {'user_id': user['id'], 'read': {'$ne': True}}, {'$set': {'read': True}})
    await write_audit(user, 'notifications.read_all',
                      f'Marked {r.modified_count} notification(s) as read')
    return {'ok': True, 'count': r.modified_count}

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

TEAM_ROLES = ['pi', 'crc', 'sponsor', 'cro', 'smo', 'site']

@api.get('/team')
async def list_team(user=Depends(require_roles('pi', 'crc', 'sponsor', 'cro', 'smo', 'site'))):
    """Org- and trial-scoped clinical team for the caller — NOT the whole user
    directory. A member qualifies when they either share the caller's
    organization or collaborate on a trial the caller is connected to (its
    creator/sponsor, or the PI/CRC of any patient enrolled in it). Patients and
    unrelated accounts are never included."""
    org = (user.get('organization') or '').strip()

    # Trials the caller is connected to: ones they created / sponsor, plus ones
    # they staff as PI/CRC on a patient record.
    trial_ids: set = set()
    trial_or = [{'created_by': user['id']}] + ([{'sponsor_name': org}] if org else [])
    async for t in db.trials.find({'$or': trial_or}, {'_id': 0, 'id': 1}):
        trial_ids.add(t['id'])
    async for p in db.patients.find(
            {'$or': [{'pi_id': user['id']}, {'crc_id': user['id']}]},
            {'_id': 0, 'trial_id': 1}):
        if p.get('trial_id'):
            trial_ids.add(p['trial_id'])

    # Collaborator user-ids on those trials.
    collaborator_ids: set = set()
    if trial_ids:
        tid_list = list(trial_ids)
        async for t in db.trials.find({'id': {'$in': tid_list}}, {'_id': 0, 'created_by': 1}):
            if t.get('created_by'):
                collaborator_ids.add(t['created_by'])
        async for p in db.patients.find({'trial_id': {'$in': tid_list}},
                                        {'_id': 0, 'pi_id': 1, 'crc_id': 1}):
            for k in ('pi_id', 'crc_id'):
                if p.get(k):
                    collaborator_ids.add(p[k])

    ors = []
    if org:
        ors.append({'organization': org})
    if collaborator_ids:
        ors.append({'id': {'$in': list(collaborator_ids)}})
    if not ors:
        return []
    members = await db.users.find(
        {'role': {'$in': TEAM_ROLES}, '$or': ors},
        {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0, 'reset_otp': 0}
    ).to_list(500)
    return [m for m in members if m['id'] != user['id']]

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
# Every seeded document is keyed on a stable natural key (email, protocol_id,
# (trial_id, visit_number), (medication_id, date, time), …) and upserted, so
# POST /api/seed can run any number of times without duplicating rows and
# without wiping non-seed user data.
SEED_PASSWORD = 'Password1!'

async def _seed_upsert(coll, key: Dict, insert: Optional[Dict] = None,
                       update: Optional[Dict] = None) -> Dict:
    """Upsert one seed doc keyed on `key`. `insert` fields apply only on first
    creation ($setOnInsert — existing rows are never overwritten); `update`
    fields are refreshed on every run ($set). Returns the stored document."""
    ops: Dict = {'$setOnInsert': {'id': str(uuid.uuid4()), **(insert or {})}}
    if update:
        ops['$set'] = update
    return await coll.find_one_and_update(
        key, ops, upsert=True, return_document=ReturnDocument.AFTER,
        projection={'_id': 0})

@api.post('/seed')
async def seed_demo():
    """Idempotent demo seed (rich data for every role):

    - one account per role incl. `admin` (password Password1!), org_admin
      flags on the sponsor/site accounts
    - 4 organizations, 3 trials with visit templates
    - 8 patients across the trials, visit instances curated into a mix of
      completed / upcoming / missed plus one overdue and visits due today
      (so GET /api/tasks has items for pi@mtb.app / crc@mtb.app)
    - medications + 14 days of dose logs → ~93% adherence for patient@mtb.app
    - notifications of each kind, support tickets in each status, invitations
      in all four lifecycle statuses, sample audit rows
    - admin-module fixtures: master-data submissions, terms versions, system
      alerts, broadcast messages
    """
    n = now()
    today = n.date()
    start_today = n.replace(hour=0, minute=0, second=0, microsecond=0)
    pw = pwd_ctx.hash(SEED_PASSWORD)     # one hash — all demo users share the password
    pet = pwd_ctx.hash('bruno')

    # 1) Organizations — one per org type.
    for org_name, otype in [('Pfizer Global', 'sponsor'), ('IQVIA India', 'cro'),
                            ('MedPoint SMO Services', 'smo'), ('AIIMS Delhi', 'site')]:
        await _seed_upsert(db.organizations, {'name': org_name}, insert={
            'type': otype, 'address': 'Sector 12, New Delhi',
            'contact': '+91 11 2658 0000',
            'email': f"contact@{org_name.split()[0].lower()}.example",
            'website': '', 'status': 'active', 'created_at': n, 'seed': True})

    # 2) Users — one per role. org_admin marks the org-console owners.
    demo_users = [
        ('admin@mtb.app',   'admin',   'Meera Nair',        'MTB Health Technologies', {}),
        ('sponsor@mtb.app', 'sponsor', 'Sarah Chen',        'Pfizer Global',           {'org_admin': True}),
        ('cro@mtb.app',     'cro',     'David Okafor',      'IQVIA India',             {}),
        ('smo@mtb.app',     'smo',     'Kavita Rao',        'MedPoint SMO Services',   {}),
        ('site@mtb.app',    'site',    'Vikram Malhotra',   'AIIMS Delhi',             {'org_admin': True}),
        ('pi@mtb.app',      'pi',      'Dr. Rajesh Sharma', 'AIIMS Delhi',             {}),
        ('crc@mtb.app',     'crc',     'Anita Verma',       'AIIMS Delhi',             {}),
        ('patient@mtb.app', 'patient', 'Priya Kumar',       '',                        {}),
    ]
    users: Dict[str, dict] = {}
    for email, role, name, org, extra in demo_users:
        users[role] = await _seed_upsert(db.users, {'email': email}, insert={
            'role': role, 'full_name': name, 'organization': org,
            'phone': '+91 98765 43210', 'hashed_password': pw,
            'avatar_initials': ''.join(w[0].upper() for w in name.replace('Dr. ', '').split()[:2]) or 'U',
            'security_question': 'What is the name of your first pet?',
            'security_answer_hash': pet,
            'created_at': n, 'is_online': False,
        }, update=extra or None)

    # 3) Trials + their visit templates (keyed on trial_id + visit_number).
    #    Every seeded template carries the same "before you come in" checklist
    #    (refreshed via $set so existing seeded rows pick it up on re-seed).
    DEFAULT_VISIT_CHECKLIST = [
        'Fast for 8 hours before your visit',
        'Bring your patient ID card',
        'Wear comfortable clothing',
        'Take your regular medications unless told otherwise',
    ]
    trials_spec = [
        ('Protocol-001', 'A Phase II Trial of MTB-Diab-Rx in Type-2 Diabetes',
         'Phase II', 'Type-2 Diabetes',
         'A randomized, double-blind study of MTB-Diab-Rx vs placebo.',
         [(1, 'Screening', 0, ['Informed consent', 'Medical history', 'Vitals', 'Blood draw']),
          (2, 'Baseline', 7, ['Physical exam', 'ECG', 'Blood draw', 'Study drug dispense']),
          (3, 'Week 2', 14, ['Vitals', 'Adverse-event review']),
          (4, 'Week 4', 28, ['Vitals', 'Blood draw', 'Adverse-event review']),
          (5, 'Week 8', 56, ['Vitals', 'Blood draw', 'Drug accountability']),
          (6, 'Week 12', 84, ['Vitals', 'Blood draw', 'Drug accountability']),
          (7, 'Week 16 · Follow-Up', 112, ['Vitals', 'Blood draw', 'Adherence review']),
          (8, 'Week 20', 140, ['Vitals', 'Blood draw']),
          (9, 'Week 24', 168, ['Vitals', 'Blood draw', 'ECG']),
          (10, 'End of Study', 196, ['Final exam', 'Drug return', 'Final assessment'])]),
        ('Protocol-002', 'A Phase III Study of MTB-HTN-24 in Resistant Hypertension',
         'Phase III', 'Hypertension',
         'A multicentre, open-label study of MTB-HTN-24 in resistant hypertension.',
         [(1, 'Screening', 0, ['Informed consent', 'Vitals', 'ABPM setup']),
          (2, 'Baseline', 7, ['Physical exam', 'Blood draw', 'Study drug dispense']),
          (3, 'Week 4', 28, ['Vitals', 'Adverse-event review']),
          (4, 'Week 8', 56, ['Vitals', 'Blood draw']),
          (5, 'Week 12', 84, ['Vitals', 'Drug accountability']),
          (6, 'End of Study', 112, ['Final exam', 'Drug return'])]),
        ('Protocol-003', 'A Phase I Dose-Escalation Study of MTB-Onc-7',
         'Phase I', 'Solid Tumours',
         'First-in-human dose-escalation and safety study of MTB-Onc-7.',
         [(1, 'Screening', 0, ['Informed consent', 'Tumour imaging', 'Blood draw']),
          (2, 'Cycle 1 Day 1', 3, ['Dosing', 'PK sampling', 'Vitals']),
          (3, 'Cycle 1 Day 8', 10, ['PK sampling', 'Adverse-event review']),
          (4, 'Cycle 2 Day 1', 24, ['Dosing', 'Vitals', 'Blood draw']),
          (5, 'End of Cycle 2', 45, ['Tumour imaging', 'Final assessment'])]),
    ]
    trial_ids: Dict[str, str] = {}
    for protocol, title, phase, condition, desc, visits in trials_spec:
        t = await _seed_upsert(db.trials, {'protocol_id': protocol}, insert={
            'title': title, 'phase': phase, 'condition': condition,
            'description': desc, 'sponsor_name': 'Pfizer Global',
            'created_by': users['sponsor']['id'], 'created_at': n, 'status': 'active'})
        trial_ids[protocol] = t['id']
        for num, vname, off, acts in visits:
            await _seed_upsert(db.visits, {'trial_id': t['id'], 'visit_number': num}, insert={
                'name': vname, 'day_offset': off, 'window_days': 3,
                'activities': acts, 'created_at': n},
                update={'checklist': DEFAULT_VISIT_CHECKLIST})

    # 4) Patients — 8 across the 3 trials. pi/crc ids are re-pointed on every
    #    run so scoping and the tasks queue always resolve to the demo staff.
    staff = {'pi_id': users['pi']['id'], 'crc_id': users['crc']['id']}
    patients_spec = [
        ('Priya Kumar',   'patient@mtb.app',       'Protocol-001', 70, users['patient']['id']),
        ('Ravi Patel',    'ravi.patel@mtb.app',    'Protocol-001', 40, None),
        ('Sunita Iyer',   'sunita.iyer@mtb.app',   'Protocol-001', 40, None),
        ('Arjun Singh',   'arjun.singh@mtb.app',   'Protocol-001', 40, None),
        ('Meera Joshi',   'meera.joshi@mtb.app',   'Protocol-001', 40, None),
        ('Karan Mehta',   'karan.mehta@mtb.app',   'Protocol-002', 30, None),
        ('Fatima Sheikh', 'fatima.sheikh@mtb.app', 'Protocol-002', 10, None),
        ('Rohan Das',     'rohan.das@mtb.app',     'Protocol-003', 3,  None),
    ]
    pids: Dict[str, str] = {}
    for fname, email, protocol, days_ago, linked_user_id in patients_spec:
        p = await _seed_upsert(db.patients, {'email': email}, insert={
            'full_name': fname, 'phone': '+91 98765 00000',
            'trial_id': trial_ids[protocol],
            'enrolled_date': (n - timedelta(days=days_ago)).date().isoformat(),
            'completed_visit_ids': [],
            'avatar_initials': ''.join(w[0].upper() for w in fname.split()[:2]),
            'created_at': n,
        }, update={**staff, 'user_id': linked_user_id})
        pids[email] = p['id']
        await materialize_visit_instances(p)   # no-op if already materialized

    # 5) Curate visit instances into the demo status mix. Deterministic $set
    #    updates keyed on (patient_id, seq) — reruns re-align, never duplicate.
    def _sched(days_from_today: int) -> Dict:
        sd = start_today + timedelta(days=days_from_today, hours=10)
        return {'status': 'upcoming', 'scheduled_date': sd,
                'window_start': sd - timedelta(days=3),
                'window_end': sd + timedelta(days=3)}

    async def _curate(email: str, updates: Dict[int, Dict]):
        await db.visit_instances.bulk_write([
            UpdateOne({'patient_id': pids[email], 'seq': seq}, {'$set': fields})
            for seq, fields in updates.items()])

    done = {'status': 'completed'}
    await _curate('patient@mtb.app', {1: done, 2: done, 3: done,
                                      4: _sched(-2),    # overdue → tasks queue
                                      5: _sched(0)})    # due today
    await _curate('ravi.patel@mtb.app', {1: done, 2: done, 3: done, 4: done})
    await _curate('karan.mehta@mtb.app', {1: done, 2: done})
    await _curate('rohan.das@mtb.app', {1: done, 2: _sched(0)})
    # (sunita/arjun/meera/fatima keep their materialized missed/upcoming mix)

    # 6) Medications + 14 days of dose logs for patient@mtb.app.
    #    3 slots/day × 14 days = 42 expected; 3 non-taken → 39/42 ≈ 93%.
    #    The misses sit 10–11 days back so streak_days stays ≥ 10. start_date
    #    is re-pinned to today-13 on every run to keep the window aligned.
    priya = pids['patient@mtb.app']
    med_start = (today - timedelta(days=13)).isoformat()
    med_common = {'route': 'oral', 'end_date': None, 'active': True,
                  'created_by': users['crc']['id'], 'created_at': n}
    med1 = await _seed_upsert(db.medications, {'patient_id': priya, 'name': 'MTB-Diab-Rx'},
                              insert={'trial_id': trial_ids['Protocol-001'], 'dosage': '500 mg',
                                      'schedule': [{'time': '08:00', 'label': 'Morning'},
                                                   {'time': '20:00', 'label': 'Evening'}],
                                      **med_common},
                              update={'start_date': med_start})
    med2 = await _seed_upsert(db.medications, {'patient_id': priya, 'name': 'Metformin'},
                              insert={'trial_id': trial_ids['Protocol-001'], 'dosage': '850 mg',
                                      'schedule': [{'time': '08:00', 'label': 'Morning'}],
                                      **med_common},
                              update={'start_date': med_start})
    # Keep the demo adherence deterministic: stray meds added to the demo
    # patient outside the seed are deactivated (never deleted).
    await db.medications.update_many(
        {'patient_id': priya, 'name': {'$nin': ['MTB-Diab-Rx', 'Metformin']}, 'active': True},
        {'$set': {'active': False}})

    dose_ops = []
    def _dose(med, day_offset, slot, status_):
        dose_ops.append(UpdateOne(
            {'medication_id': med['id'],
             'date': (today - timedelta(days=day_offset)).isoformat(), 'time': slot},
            {'$set': {'status': status_, 'logged_at': n, 'seed': True},
             '$setOnInsert': {'id': str(uuid.uuid4()), 'patient_id': med['patient_id']}},
            upsert=True))
    # Re-running the seed on a later calendar day mints new dose rows (keyed on
    # today−k). Prune the demo patient's seed-marked rows that fell OUTSIDE the
    # current 14-day window so old dates can't accumulate and drift adherence.
    window_start = (today - timedelta(days=13)).isoformat()
    window_end = today.isoformat()
    await db.dose_logs.delete_many({
        'patient_id': priya, 'seed': True,
        '$or': [{'date': {'$lt': window_start}}, {'date': {'$gt': window_end}}],
    })
    for k in range(14):
        _dose(med1, k, '08:00', 'not_taken' if k == 11 else 'taken')
        _dose(med1, k, '20:00', 'skipped' if k == 10 else 'taken')
        _dose(med2, k, '08:00', 'skipped' if k == 11 else 'taken')
    # A second patient on medication so staff screens have variety.
    med3 = await _seed_upsert(db.medications, {'patient_id': pids['karan.mehta@mtb.app'], 'name': 'Amlodipine'},
                              insert={'trial_id': trial_ids['Protocol-002'], 'dosage': '5 mg',
                                      'schedule': [{'time': '09:00', 'label': 'Morning'}],
                                      **med_common},
                              update={'start_date': (today - timedelta(days=2)).isoformat()})
    for k in range(3):
        _dose(med3, k, '09:00', 'taken')
    await db.dose_logs.bulk_write(dose_ops)

    # 7) Notifications — one of each kind, spread across roles.
    for role, title, body_text, kind in [
        ('patient', 'Visit due today', 'Your Week 8 visit at AIIMS Delhi is scheduled today.', 'reminder'),
        ('patient', 'Message from Dr. Sharma', 'Please fast for 8 hours before your blood draw.', 'message'),
        ('patient', 'Lab results reviewed', 'Your Week 4 results have been reviewed by your care team.', 'result'),
        ('pi',      'Schedule review pending', 'Protocol-002 visit schedule is awaiting your review.', 'schedule'),
        ('crc',     'New patient enrolled', 'Rohan Das was enrolled in Protocol-003.', 'system'),
        ('sponsor', 'Schedule approved · Protocol-001', 'Dr. Rajesh Sharma approved the visit schedule.', 'schedule'),
        ('admin',   'OTP delivery failures', '3 OTP deliveries failed in the last 24 hours.', 'system'),
    ]:
        await _seed_upsert(db.notifications,
                           {'user_id': users[role]['id'], 'title': title, 'seed': True},
                           insert={'body': body_text, 'kind': kind, 'read': False,
                                   'created_at': n - timedelta(hours=2)})

    # 8) Support tickets — one per status (status refreshed each run).
    for role, cat, subject, ticket_status in [
        ('patient', 'Technical', 'App shows a blank screen after login', 'Open'),
        ('crc',     'Account',   'Unable to update phone number', 'In Progress'),
        ('pi',      'General',   'Question about visit-window rules', 'Resolved'),
    ]:
        await _seed_upsert(db.support_tickets,
                           {'user_id': users[role]['id'], 'subject': subject, 'seed': True},
                           insert={'ticket_id': f"#TKT-{n.strftime('%Y%m%d')}-{str(uuid.uuid4().int)[:4]}",
                                   'category': cat,
                                   'description': f'Seeded demo ticket ({ticket_status.lower()}).',
                                   'created_at': n - timedelta(days=1)},
                           update={'status': ticket_status})

    # 9) Invitations — one per lifecycle status. The pending one has its
    #    expiry pushed forward on every run so it stays genuinely pending.
    async def _seed_invite(email, role, inv_status, extra=None, refresh=None):
        await _seed_upsert(db.invitations, {'email': email}, insert={
            'token': uuid.uuid4().hex, 'phone': '', 'full_name': '',
            'role': role, 'trial_id': None, 'invited_by': users['pi']['id'],
            'org': 'AIIMS Delhi', 'site': 'AIIMS Delhi', 'status': inv_status,
            'created_at': n, 'resend_count': 0, 'seed': True, **(extra or {})},
            update=refresh)

    await _seed_invite('invitee.pending@mtb.app', 'crc', 'pending',
                       refresh={'expires_at': n + timedelta(days=INVITE_TTL_DAYS)})
    await _seed_invite('invitee.accepted@mtb.app', 'patient', 'accepted',
                       {'expires_at': n + timedelta(days=INVITE_TTL_DAYS),
                        'accepted_at': n - timedelta(days=1)})
    await _seed_invite('invitee.expired@mtb.app', 'patient', 'pending',
                       {'expires_at': n - timedelta(days=1)})   # reads as expired
    await _seed_invite('invitee.cancelled@mtb.app', 'crc', 'cancelled',
                       {'expires_at': n + timedelta(days=INVITE_TTL_DAYS),
                        'cancelled_at': n - timedelta(days=2)})

    # 10) Sample audit rows across categories (write_audit shape).
    for role, action, detail, audit_status in [
        ('patient', 'login.success', 'Signed in from the mobile app', 'success'),
        ('patient', 'login.failed', 'Wrong password (2 attempts)', 'failure'),
        ('crc',     'visit.patch', 'Marked Baseline visit completed for Ravi Patel', 'success'),
        ('crc',     'patient.enroll', 'Enrolled Rohan Das in Protocol-003', 'success'),
        ('sponsor', 'trial.create', 'Created trial Protocol-002', 'success'),
        ('patient', 'account.update', 'Updated phone number', 'success'),
        (None,      'system.backup', 'Nightly database backup completed', 'success'),
    ]:
        actor = users.get(role) or {}
        await _seed_upsert(db.audit_logs,
                           {'action': action, 'detail': detail, 'seed': True},
                           insert={'user_id': actor.get('id'),
                                   'user_name': actor.get('full_name', ''),
                                   'role': actor.get('role', ''),
                                   'org': actor.get('organization', ''),
                                   'category': action.split('.', 1)[0],
                                   'ip': '103.27.9.44', 'device': 'iPhone 15 · MTB app',
                                   'status': audit_status,
                                   'created_at': n - timedelta(hours=6)})

    # 11) Admin-module fixtures. Field names follow the admin API audit
    #     (docs/superpowers/audits/2026-07-07-admin-api-audit.md §4/5/7/14);
    #     the admin backend task will formalize these collections later.
    # Master-data "Others: specify" submissions (§4)
    for field_type, value, md_status, action_by, reject in [
        ('designation', 'Clinical Research Fellow', 'pending', None, ''),
        ('department', 'Endocrinology Research Wing', 'approved', 'Meera Nair', ''),
        ('designation', 'Wellness Consultant', 'rejected', 'Meera Nair', 'Not a recognised clinical designation'),
    ]:
        await _seed_upsert(db.master_data_submissions,
                           {'fieldType': field_type, 'value': value},
                           insert={'submittedBy': users['crc']['full_name'], 'org': 'AIIMS Delhi',
                                   'dateSubmitted': n - timedelta(days=2), 'status': md_status,
                                   'actionBy': action_by, 'rejectReason': reject, 'seed': True})

    # Terms & privacy versions (§5) — v1.0 of each, active.
    for doc_type in ('ToS', 'Privacy'):
        await _seed_upsert(db.terms_versions, {'type': doc_type, 'version': '1.0'},
                           insert={'status': 'active', 'createdAt': n - timedelta(days=30),
                                   'activatedAt': n - timedelta(days=30), 'acceptedBy': 6,
                                   'content': f'{doc_type} v1.0 — demo content; rendered document at /api/legal.',
                                   'seed': True})

    # System alerts (§7)
    for alert_type, desc, affected, severity, alert_status in [
        ('OTP failure', 'SMS OTP delivery failed 3 times in a row', 'patient@mtb.app', 'high', 'open'),
        ('Invite failure', 'Invitation email bounced', 'invitee.pending@mtb.app', 'medium', 'open'),
        ('Session anomaly', 'Login from a new device and location', 'crc@mtb.app', 'low', 'resolved'),
    ]:
        await _seed_upsert(db.system_alerts, {'type': alert_type, 'description': desc},
                           insert={'affected': affected, 'severity': severity,
                                   'status': alert_status, 'timestamp': n - timedelta(hours=4),
                                   'seed': True})

    # Broadcast messages (§14)
    for msg_type, subject, body_text, target in [
        ('general', 'Welcome to My Trial Board', 'The MTB platform is now live for all study teams.', 'all'),
        ('compliance', 'Annual GCP refresher due', 'Please complete your GCP refresher training by month end.', 'role:pi'),
        ('urgent', 'Planned maintenance tonight', 'The platform will be read-only 02:00–03:00 IST.', 'all'),
    ]:
        await _seed_upsert(db.broadcast_messages, {'subject': subject},
                           insert={'type': msg_type, 'body': body_text, 'target': target,
                                   'allowReplies': msg_type != 'urgent', 'scheduleAt': None,
                                   'status': 'sent', 'sent_at': n - timedelta(days=1),
                                   'created_by': users['admin']['id'], 'seed': True})

    await db.meta.update_one({'key': 'seeded_v2'}, {'$set': {'at': n}}, upsert=True)
    return {'ok': True,
            'users': [{'email': e, 'role': r} for e, r, _n, _o, _x in demo_users],
            'password': SEED_PASSWORD}

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
    await write_audit(user, 'visit.patch', f'Updated visit {visit_id}: {", ".join(sorted(set(upd) - {"updated_by", "updated_at"}))}',
                      target_id=visit_id, changes=upd)
    return v

# ── Visit-instance mutations (per-patient — never touches the template) ─────
class VisitInstancePatch(BaseModel):
    status: Optional[Literal['scheduled', 'upcoming', 'completed', 'missed', 'overdue']] = None
    scheduled_date: Optional[str] = None
    note: Optional[str] = None

@api.patch('/visit-instances/{instance_id}')
async def patch_visit_instance(instance_id: str, body: VisitInstancePatch,
                               user=Depends(require_roles('pi', 'crc', 'sponsor'))):
    inst = await db.visit_instances.find_one({'id': instance_id}, {'_id': 0})
    if not inst:
        raise HTTPException(404, 'Visit instance not found')
    # Ownership: resolve the instance's patient and apply the patient-scoping
    # rule — a foreign instance is a 403, never a silent write.
    await _require_patient(user, inst.get('patient_id'))
    upd: Dict = {}
    if body.status is not None:
        upd['status'] = body.status
    if body.note is not None:
        upd['note'] = body.note
    if body.scheduled_date is not None:
        try:
            sched = datetime.fromisoformat(body.scheduled_date)
        except ValueError:
            raise HTTPException(400, 'scheduled_date must be an ISO 8601 date/datetime')
        if sched.tzinfo is None:
            sched = sched.replace(tzinfo=timezone.utc)
        wd = inst.get('window_days', 3)
        upd['scheduled_date'] = sched
        upd['window_start'] = sched - timedelta(days=wd)
        upd['window_end'] = sched + timedelta(days=wd)
    if not upd:
        raise HTTPException(400, 'Nothing to update')
    changed = sorted(upd)
    upd['updated_by'] = user['id']
    upd['updated_at'] = now()
    await db.visit_instances.update_one({'id': instance_id}, {'$set': upd})
    fresh = await db.visit_instances.find_one({'id': instance_id}, {'_id': 0})
    await write_audit(user, 'visit_instance.patch',
                      f"Updated visit instance {instance_id} ({inst.get('name', '')}): {', '.join(changed)}",
                      target_id=instance_id, patient_id=inst.get('patient_id'),
                      trial_id=inst.get('trial_id'),
                      changes={k: iso(v) for k, v in upd.items()})
    return fresh

# ── Visit-schedule review (PI approves or flags a trial's schedule) ─────────
class ScheduleFlagIn(BaseModel):
    reason: str

async def _notify_trial_sponsors(trial, title, body_text):
    """Notify the trial's sponsor users: its creator (if sponsor/cro) plus every
    sponsor-role user in the trial's sponsor organization."""
    ids = set()
    creator = await db.users.find_one({'id': trial.get('created_by')}, {'_id': 0, 'id': 1, 'role': 1})
    if creator and creator.get('role') in ('sponsor', 'cro'):
        ids.add(creator['id'])
    sponsor_name = (trial.get('sponsor_name') or '').strip()
    if sponsor_name:
        others = await db.users.find({'role': 'sponsor', 'organization': sponsor_name},
                                     {'_id': 0, 'id': 1}).to_list(200)
        ids.update(u['id'] for u in others)
    for uid in ids:
        await db.notifications.insert_one({
            'id': str(uuid.uuid4()), 'user_id': uid, 'title': title, 'body': body_text,
            'kind': 'schedule', 'trial_id': trial['id'], 'read': False, 'created_at': now(),
        })
    return len(ids)

async def _review_schedule(trial_id: str, user: dict, new_status: str, reason: str = ''):
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    # Ownership: PI-only is enforced by the route; on top of that the PI must
    # belong to this trial (creator, same org, or a listed PI) — fail-closed.
    if not await _pi_owns_trial(user, trial):
        raise HTTPException(403, 'You do not have access to this trial')
    upd = {'schedule_status': new_status,
           'schedule_reviewed_by': user['id'], 'schedule_reviewed_at': now()}
    if new_status == 'flagged':
        upd['schedule_flag_reason'] = reason
    await db.trials.update_one({'id': trial_id}, {'$set': upd})
    label = trial.get('protocol_id') or trial.get('title') or trial_id
    if new_status == 'approved':
        title = f'Schedule approved · {label}'
        body_text = f"{user['full_name']} approved the visit schedule for {label}."
    else:
        title = f'Schedule flagged · {label}'
        body_text = f"{user['full_name']} flagged the visit schedule for {label}: {reason}"
    notified = await _notify_trial_sponsors(trial, title, body_text)
    await write_audit(user, f'schedule.{"approve" if new_status == "approved" else "flag"}',
                      f'Visit schedule for {label} {new_status}' + (f' — {reason}' if reason else ''),
                      target_id=trial_id, notified=notified)
    return {'ok': True, 'trial_id': trial_id, 'schedule_status': new_status, 'notified': notified}

@api.post('/schedules/{trial_id}/approve')
async def approve_schedule(trial_id: str, user=Depends(require_roles('pi'))):
    return await _review_schedule(trial_id, user, 'approved')

@api.post('/schedules/{trial_id}/flag')
async def flag_schedule(trial_id: str, body: ScheduleFlagIn, user=Depends(require_roles('pi'))):
    return await _review_schedule(trial_id, user, 'flagged', reason=body.reason)

# ── Tasks queue (pi/crc action items, computed on read) ─────────────────────
@api.get('/tasks')
async def my_tasks(user=Depends(require_roles('pi', 'crc'))):
    """Action queue for site staff: overdue visit instances, visits due today,
    trials awaiting schedule review, and an unread-messages rollup. Computed
    from existing collections on every read — nothing is stored."""
    q = {'pi_id': user['id']} if user['role'] == 'pi' else {'crc_id': user['id']}
    patients = await db.patients.find(q, {'_id': 0}).to_list(500)
    pmap = {p['id']: p for p in patients}
    tasks = []
    start_today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    end_today = start_today + timedelta(days=1)

    if pmap:
        insts = await db.visit_instances.find({
            'patient_id': {'$in': list(pmap)},
            'status': {'$nin': ['completed', 'missed']},
            'scheduled_date': {'$lt': end_today},
        }, {'_id': 0}).sort('scheduled_date', 1).to_list(1000)
        for i in insts:
            sd = i.get('scheduled_date')
            if not isinstance(sd, datetime):
                continue
            overdue = sd < start_today
            ttype = 'overdue_visit' if overdue else 'visit_today'
            p = pmap.get(i['patient_id'], {})
            tasks.append({
                'id': f"{ttype}:{i['id']}",
                'type': ttype,
                'title': f"{'Overdue' if overdue else 'Today'}: {i.get('name', 'Visit')}",
                'subtitle': p.get('full_name', ''),
                'due': iso(sd),
                'patient_id': i['patient_id'],
                'trial_id': i.get('trial_id'),
                'priority': 'high' if overdue else 'medium',
            })

    trial_ids = sorted({p['trial_id'] for p in patients if p.get('trial_id')})
    if trial_ids:
        pending = await db.trials.find(
            {'id': {'$in': trial_ids}, 'schedule_status': {'$nin': ['approved', 'flagged']}},
            {'_id': 0}).to_list(200)
        for t in pending:
            tasks.append({
                'id': f"schedule_review:{t['id']}",
                'type': 'schedule_review',
                'title': f"Review visit schedule · {t.get('protocol_id') or t.get('title', '')}",
                'subtitle': t.get('title', ''),
                'due': None,
                'trial_id': t['id'],
                'priority': 'medium',
            })

    conv_ids = [c['id'] for c in await db.conversations.find(
        {'participant_ids': user['id']}, {'_id': 0, 'id': 1}).to_list(500)]
    unread = 0
    if conv_ids:
        unread = await db.messages.count_documents({
            'conversation_id': {'$in': conv_ids},
            'sender_id': {'$ne': user['id']},
            f'read_by.{user["id"]}': {'$exists': False},
        })
    if unread:
        tasks.append({
            'id': f"unread_messages:{user['id']}",
            'type': 'unread_messages',
            'title': f'{unread} unread message{"s" if unread != 1 else ""}',
            'subtitle': 'Open chat to reply',
            'due': None,
            'count': unread,
            'priority': 'low',
        })

    rank = {'high': 0, 'medium': 1, 'low': 2}
    tasks.sort(key=lambda t: (rank.get(t['priority'], 3), t['due'] or '~'))
    return tasks

# ── Team calendar (site-wide visit schedule for pi/crc) ─────────────────────
TEAM_CALENDAR_MAX_DAYS = 100

@api.get('/calendar/team')
async def team_calendar(from_: Optional[str] = Query(None, alias='from'),
                        to: Optional[str] = Query(None, alias='to'),
                        user=Depends(require_roles('pi', 'crc'))):
    """Read-only site schedule: visit instances for the caller's OWN patients
    (pi_id / crc_id scoping — same rule as GET /patients) within a bounded
    date range, joined with privacy-safe patient identifiers (initials +
    subject label, never full names) and the trial's protocol / condition.

    ?from=&to= are inclusive YYYY-MM-DD bounds. Both omitted → the current
    UTC month; one omitted → a window extending from the other. The span is
    capped at 100 days so a single call can never sweep the whole collection.
    Read-only, so no audit row is written."""
    f = _parse_ymd(from_, 'from')
    t = _parse_ymd(to, 'to')
    if f is None and t is None:                    # default: current month
        f = now().date().replace(day=1)
        t = (f + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    elif f is None:                                # only `to` given → window back
        assert t is not None
        f = t - timedelta(days=TEAM_CALENDAR_MAX_DAYS - 1)
    elif t is None:                               # only `from` given → window forward
        t = f + timedelta(days=TEAM_CALENDAR_MAX_DAYS - 1)
    assert f is not None and t is not None        # both resolved above (type-narrowing)
    if t < f:
        raise HTTPException(400, 'to must be on or after from')
    if (t - f).days + 1 > TEAM_CALENDAR_MAX_DAYS:
        raise HTTPException(400, f'range cannot exceed {TEAM_CALENDAR_MAX_DAYS} days')

    key = 'pi_id' if user['role'] == 'pi' else 'crc_id'
    patients = await db.patients.find({key: user['id']}, {'_id': 0}).to_list(500)
    if not patients:
        return []
    pmap = {p['id']: p for p in patients}

    start_dt = datetime(f.year, f.month, f.day, tzinfo=timezone.utc)
    end_dt = datetime(t.year, t.month, t.day, tzinfo=timezone.utc) + timedelta(days=1)
    insts = await db.visit_instances.find({
        'patient_id': {'$in': list(pmap)},
        'scheduled_date': {'$gte': start_dt, '$lt': end_dt},
    }, {'_id': 0}).sort([('scheduled_date', 1), ('seq', 1)]).to_list(2000)

    # Joins: one query per collection, not per row.
    pi_ids = sorted({p['pi_id'] for p in patients if p.get('pi_id')})
    pi_map = {u['id']: u async for u in db.users.find(
        {'id': {'$in': pi_ids}}, {'_id': 0, 'id': 1, 'full_name': 1, 'organization': 1})}
    trial_ids = sorted({p['trial_id'] for p in patients if p.get('trial_id')}
                       | {i['trial_id'] for i in insts if i.get('trial_id')})
    trial_map = {tr['id']: tr async for tr in db.trials.find(
        {'id': {'$in': trial_ids}}, {'_id': 0, 'id': 1, 'protocol_id': 1, 'condition': 1})}

    out = []
    for i in insts:
        p = pmap.get(i['patient_id'], {})
        tr = trial_map.get(i.get('trial_id') or p.get('trial_id'), {})
        assigned_pi = pi_map.get(p.get('pi_id'), {})
        initials = p.get('avatar_initials') \
            or ''.join(w[0].upper() for w in (p.get('full_name') or '').split()[:2]) or 'P'
        out.append({
            'id': i.get('id'),
            'patient_id': i['patient_id'],
            'trial_id': i.get('trial_id') or p.get('trial_id'),
            'name': i.get('name', ''),
            'seq': i.get('seq'),
            'visit_number': i.get('visit_number'),
            'scheduled_date': iso(i.get('scheduled_date')),
            'window_start': iso(i.get('window_start')),
            'window_end': iso(i.get('window_end')),
            'status': i.get('status'),
            'activities': i.get('activities', []),
            # privacy-safe patient identifiers — initials + short subject code
            'patient_initials': initials,
            'subject_label': f"SUBJ-{(i['patient_id'] or '')[:4].upper()}",
            'protocol_id': tr.get('protocol_id', ''),
            'condition': tr.get('condition', ''),
            'pi_name': assigned_pi.get('full_name', ''),
            'site': assigned_pi.get('organization', ''),
        })
    return out

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

# ── Medications + dose logs + adherence ────────────────────────────────────
DOSE_STATUSES = ('taken', 'skipped', 'not_taken', 'remind_later')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_TIME_RE = re.compile(r'^\d{2}:\d{2}$')

class ScheduleSlot(BaseModel):
    time: str                      # "08:00"
    label: Optional[str] = ''      # "Morning" / "Evening"

class MedicationIn(BaseModel):
    patient_id: str
    name: str
    dosage: str
    route: Optional[str] = 'oral'
    schedule: List[ScheduleSlot] = []
    start_date: Optional[str] = None   # "YYYY-MM-DD"; defaults to today (UTC)
    end_date: Optional[str] = None     # "YYYY-MM-DD", inclusive
    active: bool = True

class DoseLogIn(BaseModel):
    date: str                          # "YYYY-MM-DD"
    time: str                          # "HH:MM" — a slot from the med's schedule
    status: Literal['taken', 'skipped', 'not_taken', 'remind_later']

def _parse_ymd(value: Optional[str], field: str) -> Optional[date]:
    if value is None:
        return None
    if not _DATE_RE.match(value):
        raise HTTPException(400, f'{field} must be formatted YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f'{field} is not a valid calendar date')

async def _own_patient_record(user) -> dict:
    """The `patients` row linked to the signed-in patient user."""
    p = await db.patients.find_one({'user_id': user['id']}, {'_id': 0})
    if not p:
        raise HTTPException(404, 'No patient record linked to this account')
    return p

async def _staff_scoped_patient(user, patient_id: Optional[str]) -> dict:
    """pi/crc access check: the patient must be one of THEIR patients
    (same scoping GET /patients applies — pi_id / crc_id match)."""
    if not patient_id:
        raise HTTPException(400, 'patient_id is required for staff')
    p = await db.patients.find_one({'id': patient_id}, {'_id': 0})
    if not p:
        raise HTTPException(404, 'Patient not found')
    key = 'pi_id' if user['role'] == 'pi' else 'crc_id'
    if p.get(key) != user['id']:
        raise HTTPException(403, 'You do not manage this patient')
    return p

async def _resolve_patient_scope(user, patient_id: Optional[str]) -> dict:
    """Patient → own record (ignores ?patient_id=); pi/crc → their patient."""
    if user['role'] == 'patient':
        return await _own_patient_record(user)
    return await _staff_scoped_patient(user, patient_id)

@api.get('/medications')
async def list_medications(patient_id: Optional[str] = None,
                           user=Depends(require_roles('patient', 'pi', 'crc'))):
    p = await _resolve_patient_scope(user, patient_id)
    return await db.medications.find({'patient_id': p['id']}, {'_id': 0}) \
                               .sort('created_at', 1).to_list(200)

@api.post('/medications')
async def create_medication(body: MedicationIn, user=Depends(require_roles('pi', 'crc'))):
    p = await _staff_scoped_patient(user, body.patient_id)
    start = _parse_ymd(body.start_date, 'start_date') or now().date()
    end = _parse_ymd(body.end_date, 'end_date')
    if end and end < start:
        raise HTTPException(400, 'end_date cannot be before start_date')
    for slot in body.schedule:
        if not _TIME_RE.match(slot.time):
            raise HTTPException(400, 'schedule times must be formatted HH:MM')
    mid = str(uuid.uuid4())
    doc = {
        'id': mid,
        'patient_id': p['id'],
        'trial_id': p.get('trial_id'),
        'name': body.name.strip(),
        'dosage': body.dosage.strip(),
        'route': (body.route or 'oral').strip(),
        'schedule': [{'time': s.time, 'label': s.label or ''} for s in body.schedule],
        'start_date': start.isoformat(),
        'end_date': end.isoformat() if end else None,
        'active': body.active,
        'created_by': user['id'],
        'created_at': now(),
    }
    await db.medications.insert_one(doc)
    await write_audit(user, 'medication.create',
                      f"Prescribed {doc['name']} {doc['dosage']} to {p.get('full_name', p['id'])}",
                      target_id=mid, patient_id=p['id'], trial_id=doc['trial_id'])
    return serialize(doc)

async def _med_for_access(medication_id: str, user) -> dict:
    """Load a medication and enforce access: the patient it belongs to, or
    pi/crc staff who manage that patient."""
    med = await db.medications.find_one({'id': medication_id}, {'_id': 0})
    if not med:
        raise HTTPException(404, 'Medication not found')
    if user['role'] == 'patient':
        p = await _own_patient_record(user)
        if med['patient_id'] != p['id']:
            raise HTTPException(403, 'This medication belongs to another patient')
    else:
        await _staff_scoped_patient(user, med['patient_id'])
    return med

@api.post('/medications/{medication_id}/doses')
async def log_dose(medication_id: str, body: DoseLogIn,
                   user=Depends(require_roles('patient'))):
    """Idempotent upsert keyed on (medication_id, date, time): re-logging the
    same slot replaces its status (same row id), never duplicates."""
    med = await _med_for_access(medication_id, user)
    _parse_ymd(body.date, 'date')
    if not _TIME_RE.match(body.time):
        raise HTTPException(400, 'time must be formatted HH:MM')
    n = now()
    key = {'medication_id': medication_id, 'date': body.date, 'time': body.time}
    await db.dose_logs.update_one(
        key,
        {'$set': {'status': body.status, 'logged_at': n},
         '$setOnInsert': {'id': str(uuid.uuid4()), 'patient_id': med['patient_id'], **key}},
        upsert=True,
    )
    log = await db.dose_logs.find_one(key, {'_id': 0})
    await write_audit(user, 'dose.log',
                      f"Logged {med['name']} {body.date} {body.time} as {body.status}",
                      target_id=log['id'], medication_id=medication_id,
                      patient_id=med['patient_id'])
    return log

@api.get('/medications/{medication_id}/doses')
async def list_doses(medication_id: str,
                     from_: Optional[str] = Query(None, alias='from'),
                     to: Optional[str] = Query(None, alias='to'),
                     user=Depends(require_roles('patient', 'pi', 'crc'))):
    """Dose history for one medication, optionally windowed by ?from=&to=
    (inclusive YYYY-MM-DD bounds — lexicographic compare is safe for ISO dates)."""
    await _med_for_access(medication_id, user)
    q: Dict = {'medication_id': medication_id}
    date_q: Dict = {}
    if _parse_ymd(from_, 'from'):
        date_q['$gte'] = from_
    if _parse_ymd(to, 'to'):
        date_q['$lte'] = to
    if date_q:
        q['date'] = date_q
    return await db.dose_logs.find(q, {'_id': 0}).sort([('date', -1), ('time', 1)]).to_list(1000)

async def compute_adherence(patient_id: str) -> dict:
    """Adherence summary for one patient. THE formula (frontend contract):

    - Expected doses ("total"): for every ACTIVE medication, each calendar day
      D with start_date <= D <= min(today, end_date) contributes
      len(schedule) expected doses (meds with an empty schedule contribute 0;
      a future start_date contributes 0 until it arrives). Days are UTC dates,
      inclusive on both ends — a med started today with 2 slots expects 2 today.
    - "taken": dose_logs with status == 'taken' whose (date, time) fall on an
      expected slot of that med (date within the active window AND time equal
      to one of the med's schedule times). Upsert semantics guarantee at most
      one log per (medication_id, date, time), so taken <= total always.
    - "rate": round(taken / total * 100) as an int; 0 when total == 0
      (e.g. 13/14 -> 93).
    - "streak_days": consecutive fully-adherent days (every expected dose that
      day logged 'taken') counting backwards from today; if today is not yet
      complete the streak ends at yesterday instead (an in-progress day never
      breaks the streak). Days with zero expected doses stop the streak.
      Capped at 365.
    - "last7": exactly 7 entries, oldest first, ending today:
      [{date: "YYYY-MM-DD", taken: int, total: int}].
    """
    today = now().date()
    meds = await db.medications.find({'patient_id': patient_id, 'active': True},
                                     {'_id': 0}).to_list(200)
    windows = []                      # (start, end, slot_times) per scorable med
    for m in meds:
        slots = {s['time'] for s in (m.get('schedule') or []) if s.get('time')}
        if not slots:
            continue
        try:
            start = _parse_ymd(m.get('start_date'), 'start_date')
            end = _parse_ymd(m.get('end_date'), 'end_date') or today
        except HTTPException:
            continue                  # malformed stored dates never break the summary
        if not start or start > today:
            continue
        end = min(end, today)
        if end < start:
            continue
        windows.append((m['id'], start, end, slots))

    def expected_on(d: date) -> int:
        return sum(len(slots) for _, s, e, slots in windows if s <= d <= e)

    total = sum(((e - s).days + 1) * len(slots) for _, s, e, slots in windows)

    logs = await db.dose_logs.find({'patient_id': patient_id, 'status': 'taken'},
                                   {'_id': 0}).to_list(5000)
    slot_map = {mid: (s, e, slots) for mid, s, e, slots in windows}
    taken_by_day: Dict[str, int] = {}
    taken = 0
    for log in logs:
        w = slot_map.get(log.get('medication_id'))
        if not w:
            continue
        s, e, slots = w
        try:
            d = date.fromisoformat(log.get('date') or '')
        except ValueError:
            continue
        if s <= d <= e and log.get('time') in slots:
            taken += 1
            taken_by_day[log['date']] = taken_by_day.get(log['date'], 0) + 1

    def day_stats(d: date):
        return taken_by_day.get(d.isoformat(), 0), expected_on(d)

    rate = round(taken / total * 100) if total else 0

    streak = 0
    t_taken, t_total = day_stats(today)
    cursor = today if (t_total and t_taken >= t_total) else today - timedelta(days=1)
    while streak < 365:
        d_taken, d_total = day_stats(cursor)
        if not d_total or d_taken < d_total:
            break
        streak += 1
        cursor -= timedelta(days=1)

    last7 = []
    for k in range(6, -1, -1):
        d = today - timedelta(days=k)
        d_taken, d_total = day_stats(d)
        last7.append({'date': d.isoformat(), 'taken': d_taken, 'total': d_total})

    return {'rate': rate, 'taken': taken, 'total': total,
            'streak_days': streak, 'last7': last7}

@api.get('/adherence')
async def get_adherence(patient_id: Optional[str] = None,
                        user=Depends(require_roles('patient', 'pi', 'crc'))):
    p = await _resolve_patient_scope(user, patient_id)
    return await compute_adherence(p['id'])

# ── Invitations (invite patient/team via email/SMS) ───────────────────────
INVITE_TTL_DAYS = 7

class InvitationIn(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    full_name: Optional[str] = ''
    designation: Optional[str] = ''
    role: Optional[Role] = 'patient'
    trial_id: Optional[str] = None
    organization: Optional[str] = None   # defaults to the inviter's org
    site: Optional[str] = None

def _invite_link(token: str) -> str:
    base = os.environ.get('PUBLIC_APP_URL', 'https://my-trial-board.app')
    return f"{base}/invite/{token}"

def _can_manage_invitation(inv: dict, user: dict) -> bool:
    """The inviter — or anyone in the same organization — may manage it."""
    return inv.get('invited_by') == user['id'] or \
        bool(inv.get('org')) and inv.get('org') == user.get('organization')

@api.post('/invitations', dependencies=[Depends(require_roles('pi', 'crc', 'sponsor', 'cro'))])
async def create_invitation(body: InvitationIn, user=Depends(current_user)):
    if not body.email and not body.phone:
        raise HTTPException(400, 'Email or phone required')
    token = uuid.uuid4().hex
    doc = {
        'id': str(uuid.uuid4()), 'token': token,
        'email': (body.email or '').lower(), 'phone': body.phone or '',
        'full_name': body.full_name or '', 'designation': body.designation or '',
        'role': body.role or 'patient',
        'trial_id': body.trial_id, 'invited_by': user['id'],
        'org': (body.organization or user.get('organization') or '').strip(),
        'site': (body.site or '').strip(),
        'status': 'pending', 'created_at': now(),
        'expires_at': now() + timedelta(days=INVITE_TTL_DAYS),
        'resend_count': 0,
    }
    await db.invitations.insert_one(doc)
    await write_audit(user, 'invitation.create',
                      f"Invited {doc['email'] or doc['phone']} as {doc['role']}",
                      target_id=doc['id'])
    # Real email sending is wired via EMAIL_API_KEY env (Resend) — falls back to logging in dev.
    api_key = os.environ.get('EMAIL_API_KEY')
    invite_link = _invite_link(token)
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

@api.get('/invitations')
async def list_invitations(user=Depends(require_roles('pi', 'crc', 'sponsor', 'cro'))):
    """Invitations sent by the caller or anyone in their organization."""
    org = (user.get('organization') or '').strip()
    ors = [{'invited_by': user['id']}] + ([{'org': org}] if org else [])
    return await db.invitations.find({'$or': ors}, {'_id': 0}).sort('created_at', -1).to_list(200)

def _invitation_status(inv: dict) -> str:
    """Effective status: a pending invitation past its expiry reads as expired."""
    st = inv.get('status', 'pending')
    exp = inv.get('expires_at')
    if st == 'pending' and exp and exp < now():
        return 'expired'
    return st

@api.get('/invitations/{token}')
async def resolve_invitation(token: str):
    """Public: resolve an invite token for the accept screen."""
    inv = await db.invitations.find_one({'token': token}, {'_id': 0})
    if not inv:
        raise HTTPException(404, 'Invitation not found')
    inviter = await db.users.find_one(
        {'id': inv.get('invited_by')},
        {'_id': 0, 'full_name': 1, 'organization': 1},
    )
    return {
        'org': inv.get('org', ''), 'site': inv.get('site', ''),
        'role': inv.get('role'), 'inviter': (inviter or {}).get('full_name', ''),
        'admin_name': (inviter or {}).get('full_name', ''),
        'org_name': inv.get('org') or (inviter or {}).get('organization', ''),
        'full_name': inv.get('full_name', ''),
        'designation': inv.get('designation', ''),
        'phone': inv.get('phone', ''),
        'email': inv.get('email', ''), 'status': _invitation_status(inv),
        'expires_at': iso(inv.get('expires_at')),
    }

class InvitationAcceptIn(BaseModel):
    full_name: Optional[str] = ''
    designation: Optional[str] = ''
    phone: Optional[str] = ''
    role: Optional[Role] = None

@api.post('/invitations/{token}/accept')
async def accept_invitation(token: str, body: Optional[InvitationAcceptIn] = None):
    """Public: mark an invitation accepted (the invitee then registers/signs in)."""
    inv = await db.invitations.find_one({'token': token})
    if not inv:
        raise HTTPException(404, 'Invitation not found')
    st = _invitation_status(inv)
    if st != 'pending':
        raise HTTPException(400, f'This invitation is {st} and can no longer be accepted')
    accepted_details = {
        'full_name': (body.full_name if body else inv.get('full_name')) or '',
        'designation': (body.designation if body else inv.get('designation')) or '',
        'phone': (body.phone if body else inv.get('phone')) or '',
        'role': (body.role if body and body.role else inv.get('role')) or 'patient',
    }
    await db.invitations.update_one(
        {'id': inv['id']},
        {'$set': {'status': 'accepted', 'accepted_at': now(), **accepted_details}},
    )
    await write_audit(None, 'invitation.accept',
                      f"Invitation for {inv.get('email') or inv.get('phone')} accepted",
                      target_id=inv['id'])
    return {
        'ok': True, 'status': 'accepted', 'email': inv.get('email', ''),
        'org': inv.get('org', ''), **accepted_details,
    }

@api.post('/invitations/{invitation_id}/resend')
async def resend_invitation(invitation_id: str, user=Depends(require_roles('pi', 'crc', 'sponsor', 'cro'))):
    inv = await db.invitations.find_one({'id': invitation_id})
    if not inv:
        raise HTTPException(404, 'Invitation not found')
    if not _can_manage_invitation(inv, user):
        raise HTTPException(403, 'You can only manage invitations from your organization')
    if _invitation_status(inv) not in ('pending', 'expired'):
        raise HTTPException(400, 'Only pending invitations can be resent')
    new_exp = now() + timedelta(days=INVITE_TTL_DAYS)
    await db.invitations.update_one(
        {'id': invitation_id},
        {'$set': {'status': 'pending', 'expires_at': new_exp, 'last_sent_at': now()},
         '$inc': {'resend_count': 1}})
    invite_link = _invite_link(inv['token'])
    logging.info(f"INVITATION RESEND: link={invite_link} email={inv.get('email')} phone={inv.get('phone')}")
    await write_audit(user, 'invitation.resend',
                      f"Invitation for {inv.get('email') or inv.get('phone')} resent",
                      target_id=invitation_id)
    return {'ok': True, 'invite_link': invite_link, 'expires_at': iso(new_exp)}

@api.post('/invitations/{invitation_id}/cancel')
async def cancel_invitation(invitation_id: str, user=Depends(require_roles('pi', 'crc', 'sponsor', 'cro'))):
    inv = await db.invitations.find_one({'id': invitation_id})
    if not inv:
        raise HTTPException(404, 'Invitation not found')
    if not _can_manage_invitation(inv, user):
        raise HTTPException(403, 'You can only manage invitations from your organization')
    if inv.get('status') == 'accepted':
        raise HTTPException(400, 'An accepted invitation cannot be cancelled')
    await db.invitations.update_one({'id': invitation_id}, {'$set': {'status': 'cancelled', 'cancelled_at': now()}})
    await write_audit(user, 'invitation.cancel',
                      f"Invitation for {inv.get('email') or inv.get('phone')} cancelled",
                      target_id=invitation_id)
    return {'ok': True, 'status': 'cancelled'}

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
        # calendar settings (Clinical → Calendar → Settings)
        'calendar_default_view', 'week_start', 'reminders_visits', 'reminders_meds',
        'reminder_hours_before',
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

def _deidentify_audit_row(row: dict, patient: Optional[dict]) -> dict:
    """Return a sponsor-safe copy of an audit row.

    A sponsor/CRO may audit its own trials but must NEVER see patient PII. We
    (1) drop any direct PII columns, (2) scrub the patient's identifiers out of
    the free-text `detail` and `user_name` (a patient-actor row carries the
    patient's own name), replacing them with a trial-level subject label, and
    (3) relabel a patient actor's name outright. Fail-safe: even with no patient
    row resolved, direct PII columns and a patient-actor name are still stripped.
    """
    row = dict(row)
    for k in ('full_name', 'email', 'phone', 'dob', 'patient_name'):
        row.pop(k, None)
    label = 'Subject'
    pii = []
    if patient:
        label = (patient.get('subject_id') or patient.get('avatar_initials')
                 or 'Subject')
        row['subject_label'] = label
        pii = [patient.get('full_name'), patient.get('email'),
               patient.get('phone'), patient.get('dob')]
    for field in ('detail', 'user_name'):
        val = row.get(field)
        if isinstance(val, str):
            for pv in pii:
                if pv:
                    val = val.replace(str(pv), label)
            row[field] = val
    if row.get('role') == 'patient':           # actor's own name must not leak
        row['user_name'] = label
    row['deidentified'] = True
    return row


async def _scope_audit_logs(user: dict, rows: list) -> list:
    """Fail-closed row-level scoping of audit entries for the calling role.

    patient  → own actions, or rows whose subject is their own patient record.
    pi/crc   → own action rows + rows referencing a patient/trial they own
               (reuses _can_access_patient / _pi_owns_trial — never cross-site).
    sponsor/cro → rows for a trial in their org, DE-IDENTIFIED.
    admin    → unrestricted.
    any other role → nothing.
    """
    role = user['role']
    if role == 'admin':
        return rows
    if role == 'patient':
        own = await db.patients.find_one({'user_id': user['id']}, {'_id': 0, 'id': 1})
        own_pid = own['id'] if own else None
        return [r for r in rows
                if r.get('user_id') == user['id']
                or (own_pid and r.get('patient_id') == own_pid)]
    if role not in ('pi', 'crc', 'sponsor', 'cro'):
        return []

    pcache: Dict[str, Optional[dict]] = {}
    tcache: Dict[str, bool] = {}

    async def _patient(pid):
        if pid not in pcache:
            pcache[pid] = await db.patients.find_one({'id': pid}, {'_id': 0})
        return pcache[pid]

    async def _trial_ok(tid):
        if tid not in tcache:
            if role in ('sponsor', 'cro'):
                tcache[tid] = await _trial_in_caller_org(user, tid)
            else:                              # pi/crc: PI-ownership tie to trial
                trial = await db.trials.find_one({'id': tid}, {'_id': 0})
                tcache[tid] = bool(trial) and await _pi_owns_trial(user, trial)
        return tcache[tid]

    out = []
    for r in rows:
        pid, tid = r.get('patient_id'), r.get('trial_id')
        patient = await _patient(pid) if pid else None
        allowed = r.get('user_id') == user['id']          # own action
        if not allowed and patient is not None:
            allowed = await _can_access_patient(user, patient)
        if not allowed and tid:
            allowed = await _trial_ok(tid)
        if not allowed:
            continue
        if role in ('sponsor', 'cro'):
            # Resolve the subject for scrubbing even when the writer linked the
            # patient only via target_id (e.g. patient.enroll) — a sponsor's
            # free-text detail must never carry a patient name.
            scrub = patient
            if scrub is None and r.get('target_id'):
                scrub = await _patient(r['target_id'])
            out.append(_deidentify_audit_row(r, scrub))
        else:
            out.append(r)
    return out


@api.get('/audit-logs')
async def list_audit(category: Optional[str] = None,
                     from_: Optional[str] = Query(None, alias='from'),
                     to: Optional[str] = Query(None, alias='to'),
                     user=Depends(current_user)):
    """Role-scoped audit trail. Open to every authenticated role; the returned
    rows are scoped fail-closed per role (see _scope_audit_logs) so no caller
    can read another tenant's / site's activity, and a sponsor's view is
    de-identified. ?category= is an exact match; ?from=&to= are inclusive
    YYYY-MM-DD timestamp bounds (same parse + range guard as the calendar)."""
    f = _parse_ymd(from_, 'from')
    t = _parse_ymd(to, 'to')
    if f is not None and t is not None and t < f:
        raise HTTPException(400, 'to must be on or after from')

    q: Dict = {}
    if category:
        q['category'] = category
    if f is not None or t is not None:
        rng: Dict = {}
        if f is not None:
            rng['$gte'] = datetime(f.year, f.month, f.day, tzinfo=timezone.utc)
        if t is not None:
            rng['$lt'] = datetime(t.year, t.month, t.day,
                                  tzinfo=timezone.utc) + timedelta(days=1)
        q['created_at'] = rng

    rows = await db.audit_logs.find(q, {'_id': 0}).sort('created_at', -1).to_list(500)
    scoped = await _scope_audit_logs(user, rows)
    return scoped[:200]

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

# ── File uploads (storage abstraction — Task 5.1) ────────────────────────────
# Uploaded files may carry PHI, so download is scope-checked (never a public
# link on the local backend) and delete is owner/admin-only. Storage backend is
# pluggable (local disk now, S3-ready) via storage.get_storage().
FILE_MAX_BYTES = 10 * 1024 * 1024   # 10 MB
# extension -> (allowed content-types, magic-byte prefixes). Both the extension
# AND the declared content-type must be allowed, and the bytes must match the
# type's magic (defence-in-depth against a spoofed content-type / extension).
_ALLOWED_UPLOADS = {
    'pdf':  ({'application/pdf', 'application/octet-stream'}, (b'%PDF-',)),
    'png':  ({'image/png', 'application/octet-stream'}, (b'\x89PNG\r\n\x1a\n',)),
    'jpg':  ({'image/jpeg', 'application/octet-stream'}, (b'\xff\xd8\xff',)),
    'jpeg': ({'image/jpeg', 'application/octet-stream'}, (b'\xff\xd8\xff',)),
    'docx': ({'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              'application/zip', 'application/octet-stream'}, (b'PK\x03\x04',)),
}
_FILE_SCOPE_TYPES = ('user', 'trial', 'ticket')


async def _caller_in_trial(user: dict, trial_id: Optional[str]) -> bool:
    """Whether the caller legitimately belongs to a trial (for trial-scoped file
    access). sponsor/cro: their org owns it. pi: _pi_owns_trial. crc: they are a
    listed CRC on an enrolled patient, created it, or share the trial's org.
    Fail-closed for everyone else."""
    if not trial_id:
        return False
    role = user['role']
    if role in ('sponsor', 'cro'):
        return await _trial_in_caller_org(user, trial_id)
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        return False
    if role == 'pi':
        return await _pi_owns_trial(user, trial)
    if role == 'crc':
        if trial.get('created_by') == user['id']:
            return True
        org = (user.get('organization') or '').strip()
        if org and (trial.get('sponsor_name') or '').strip() == org:
            return True
        mine = await db.patients.find_one(
            {'trial_id': trial_id, 'crc_id': user['id']}, {'_id': 0, 'id': 1})
        return mine is not None
    return False


async def _file_access_allowed(user: dict, doc: dict) -> bool:
    """Scope gate for GET /api/files/{id}. Owner and admin always pass; otherwise
    the caller must satisfy the file's scope. Fail-closed (unknown scope → deny)."""
    if user['role'] == 'admin' or doc.get('owner_id') == user['id']:
        return True
    scope = doc.get('scope') or {}
    stype, sid = scope.get('type'), scope.get('id')
    if stype == 'user':
        return sid == user['id']
    if stype == 'trial':
        return await _caller_in_trial(user, sid)
    if stype == 'ticket':
        return False   # only owner/admin (handled above); no broad ticket access
    return False


@api.post('/files')
async def upload_file(file: UploadFile = File(...),
                      scope_type: str = Form('user'),
                      scope_id: Optional[str] = Form(None),
                      user=Depends(current_user)):
    """Upload a file (any authenticated role). 10 MB cap; pdf/png/jpg/docx only
    (validated by extension AND content-type AND magic bytes). The blob is stored
    under a uuid key via the configured storage backend and indexed in `files`
    with a scope (default {type:'user', id: caller}). Returns
    {id, name, size, content_type, url} — url is the presigned link (S3) or the
    authenticated API GET path (local)."""
    name = (file.filename or '').strip() or 'file'
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    spec = _ALLOWED_UPLOADS.get(ext)
    if not spec:
        raise HTTPException(400, 'Unsupported file type (allowed: pdf, png, jpg, docx)')
    allowed_cts, magics = spec
    ctype = (file.content_type or '').lower().split(';')[0].strip()
    if ctype and ctype not in allowed_cts:
        raise HTTPException(400, 'Content-type does not match the file extension')

    data = await _read_upload_capped(file, FILE_MAX_BYTES, 'File is too large (max 10 MB)')
    if not data:
        raise HTTPException(400, 'The uploaded file is empty')
    if not any(data.startswith(m) for m in magics):
        raise HTTPException(400, 'File contents do not match the declared type')

    stype = (scope_type or 'user').strip().lower()
    if stype not in _FILE_SCOPE_TYPES:
        raise HTTPException(400, 'Invalid scope type')
    # Default scope is {type:'user', id: caller}; a scope id is required for
    # trial/ticket scopes and defaults to the caller for a user scope.
    sid = (scope_id or '').strip() or user['id']
    scope = {'type': stype, 'id': sid}

    # Prefer the declared content-type; fall back to a canonical one per ext.
    stored_ct = ctype or next(iter(allowed_cts - {'application/octet-stream'}), 'application/octet-stream')
    key = str(uuid.uuid4())
    st = file_storage.get_storage()
    await st.save(key, data, stored_ct)
    doc = {
        'id': str(uuid.uuid4()), 'key': key, 'owner_id': user['id'],
        'scope': scope, 'name': name, 'content_type': stored_ct,
        'size': len(data), 'created_at': now(),
    }
    await db.files.insert_one(doc)
    await write_audit(user, 'file.upload',
                      f'Uploaded {name} ({len(data)} bytes, scope {stype})',
                      target_id=doc['id'])
    url = st.url(key) or f"/api/files/{doc['id']}"
    return {'id': doc['id'], 'name': name, 'size': len(data),
            'content_type': stored_ct, 'url': url}


@api.get('/files/{file_id}')
async def download_file(file_id: str, user=Depends(current_user)):
    """Scope-checked download. Missing → 404; foreign scope → 403. Streams the
    bytes (local) or redirects to the presigned URL (S3)."""
    from fastapi.responses import Response as FastResp, RedirectResponse
    doc = await db.files.find_one({'id': file_id}, {'_id': 0})
    if not doc:
        raise HTTPException(404, 'File not found')
    if not await _file_access_allowed(user, doc):
        raise HTTPException(403, 'You do not have access to this file')
    st = file_storage.get_storage()
    presigned = st.url(doc['key'])
    if presigned:
        return RedirectResponse(presigned, status_code=307)
    try:
        data, _ct = await st.open(doc['key'])
    except FileNotFoundError:
        raise HTTPException(404, 'File blob is missing')
    name = doc.get('name', 'file').replace('"', '')
    return FastResp(
        content=data, media_type=doc.get('content_type', 'application/octet-stream'),
        headers={'Content-Disposition': f'inline; filename="{name}"'})


@api.delete('/files/{file_id}')
async def delete_file(file_id: str, user=Depends(current_user)):
    """Delete a file blob + its db doc. Owner or admin only (else 403)."""
    doc = await db.files.find_one({'id': file_id}, {'_id': 0})
    if not doc:
        raise HTTPException(404, 'File not found')
    if user['role'] != 'admin' and doc.get('owner_id') != user['id']:
        raise HTTPException(403, 'Only the owner or an admin can delete this file')
    try:
        await file_storage.get_storage().delete(doc['key'])
    except Exception as e:
        logging.warning('File blob delete failed for %s: %s', doc['key'], e)
    await db.files.delete_one({'id': file_id})
    await write_audit(user, 'file.delete', f"Deleted {doc.get('name', file_id)}",
                      target_id=file_id)
    return {'ok': True, 'id': file_id}


@api.get('/')
async def root(): return {'app': 'My Trial Board', 'status': 'ok'}

app.include_router(api)

# ── Admin + org-admin routers (Task 6.1) ─────────────────────────────────────
# Imported at the bottom on purpose: admin_routes/org_routes import helpers
# (db, write_audit, require_roles, …) back from this module, so they can only
# be imported once those names exist. Both routers carry their own /api/…
# prefixes and their own role gates (admin-only / org-admin-only).
import admin_routes                              # noqa: E402
import org_routes                                # noqa: E402
app.include_router(admin_routes.router)
app.include_router(org_routes.router)
app.include_router(org_routes.trial_access_router)
app.add_middleware(CORSMiddleware, allow_credentials=True, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

async def _ensure_indexes():
    """Create indexes in the background so a slow/unreachable DB never blocks startup."""
    try:
        # Auto-expire abandoned/unverified registrations + throttle windows via TTL.
        await db.pending_registrations.create_index('expires_at', expireAfterSeconds=0)
        await db.pending_contact_changes.create_index('expires_at', expireAfterSeconds=0)
        await db.otp_throttle.create_index('expires_at', expireAfterSeconds=0)
        # Enforce unique emails at the DB layer (defence-in-depth vs. concurrent signups).
        await db.users.create_index(
            'email', unique=True,
            partialFilterExpression={'email': {'$type': 'string', '$gt': ''}},
        )
        # Per-patient visit instances are always fetched by patient.
        await db.visit_instances.create_index('patient_id')
        # Medications are fetched per patient; the dose upsert key is also
        # unique at the DB layer (defence-in-depth vs. concurrent logging).
        await db.medications.create_index('patient_id')
        await db.dose_logs.create_index(
            [('medication_id', 1), ('date', 1), ('time', 1)], unique=True)
        # Adherence (GET /api/adherence) scans dose_logs by patient.
        await db.dose_logs.create_index('patient_id')
    except Exception as e:
        logging.warning('Index setup deferred (DB unreachable or existing duplicates?): %s', e)

async def _ensure_admin_seed():
    """Guarantee the platform-admin account exists (admins cannot self-register,
    so a fresh database would otherwise have no way into the admin portal)."""
    try:
        await db.users.update_one(
            {'email': 'admin@mtb.app'},
            {'$setOnInsert': {
                'id': str(uuid.uuid4()), 'role': 'admin', 'full_name': 'Meera Nair',
                'organization': 'MTB Health Technologies', 'phone': '+91 98765 43210',
                'hashed_password': pwd_ctx.hash(SEED_PASSWORD),
                'security_question': '', 'security_answer_hash': '',
                'avatar_initials': 'MN', 'created_at': now(), 'is_online': False,
            }},
            upsert=True)
    except Exception as e:
        logging.warning('Admin seed deferred (DB unreachable?): %s', e)

@app.on_event('startup')
async def startup():
    if DEV_OTP_MODE:
        logging.warning('⚠️  DEV_OTP_MODE is ON — fixed OTP "%s" accepted for unconfigured channels. NEVER enable in production.', DEV_OTP_CODE)
    # Fire-and-forget: don't await, so the API serves immediately even if Atlas is down.
    asyncio.create_task(_ensure_indexes())
    # Backfill visit_instances for pre-existing patients (idempotent; logs on failure).
    asyncio.create_task(_migrate_visit_instances())
    # Make sure the platform-admin login exists (idempotent).
    asyncio.create_task(_ensure_admin_seed())

@app.on_event('shutdown')
async def shutdown(): client.close()
