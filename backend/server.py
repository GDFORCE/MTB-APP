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
from motor.motor_asyncio import AsyncIOMotorClient
import os, json, logging, uuid
from pathlib import Path
from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional, Dict, Literal
from datetime import datetime, timezone, timedelta
from passlib.context import CryptContext
import jwt

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

client = AsyncIOMotorClient(MONGO_URL)
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

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class ForgotIn(BaseModel):
    email: EmailStr

class ResetIn(BaseModel):
    email: EmailStr
    otp: str
    new_password: str

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

@api.get('/')
async def root(): return {'app': 'My Trial Board', 'status': 'ok'}

app.include_router(api)
app.add_middleware(CORSMiddleware, allow_credentials=True, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

@app.on_event('shutdown')
async def shutdown(): client.close()
