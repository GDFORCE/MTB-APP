"""Per-patient visit instances, tasks queue, schedule approve/flag — Task 1.2.

Covers: enrollment materializes one visit_instance per trial visit template;
PATCH /visit-instances/{id} mutates only that patient's copy (never the shared
template); GET /patients/{id} + /patients/{id}/visits; GET /visits/mine reads
instances (with template fallback); schedule approve/flag notifies sponsors and
audits; GET /tasks computes the pi/crc action queue.

Same harness as test_foundation.py: in-process ASGITransport against the real
Atlas DB, RUN_ID-marked data, module teardown cleanup, single module-level
event loop (Motor pins its io_loop on first use — never asyncio.run here).
"""
import asyncio
import sys
import uuid
from datetime import timedelta, timezone
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
import server  # noqa: E402

RUN_ID = uuid.uuid4().hex[:8]
PASSWORD = 'Password1!'
ORG_SITE = f'TESTORG-{RUN_ID} Hospital'
ORG_SPONSOR = f'TESTORG-{RUN_ID} Pharma'

LOOP = asyncio.new_event_loop()

_trial_ids = []          # every trial we create, for teardown
_conversation_ids = []   # chat fixtures for the tasks test


def run(coro):
    return LOOP.run_until_complete(coro)


def make_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url='http://testserver'
    )


async def _register(role, org=None):
    email = f'test-{RUN_ID}-{role}-{uuid.uuid4().hex[:6]}@example.com'
    async with make_client() as cli:
        r = await cli.post('/api/auth/register', json={
            'email': email, 'password': PASSWORD,
            'full_name': f'Test {role.upper()} {RUN_ID}',
            'role': role, 'organization': org,
        })
    assert r.status_code == 200, r.text
    j = r.json()
    return j['user'], {'Authorization': f"Bearer {j['access_token']}"}


async def _make_trial(sponsor_headers, templates=((0, 'Screening'), (7, 'Baseline'), (14, 'Week 2'))):
    """Create a trial + visit templates via the public API; returns (trial, template_list)."""
    async with make_client() as cli:
        r = await cli.post('/api/trials', headers=sponsor_headers, json={
            'title': f'Test Trial {RUN_ID}', 'protocol_id': f'TEST-{RUN_ID}-{uuid.uuid4().hex[:4]}',
            'phase': 'Phase II', 'condition': 'Testing', 'sponsor_name': ORG_SPONSOR,
        })
        assert r.status_code == 200, r.text
        trial = r.json()
        _trial_ids.append(trial['id'])
        tpls = []
        for i, (off, name) in enumerate(templates, start=1):
            rv = await cli.post('/api/visits', headers=sponsor_headers, json={
                'trial_id': trial['id'], 'visit_number': i, 'name': name,
                'day_offset': off, 'window_days': 3, 'activities': ['Vitals'],
            })
            assert rv.status_code == 200, rv.text
            tpls.append(rv.json())
    return trial, tpls


async def _enroll(staff_headers, trial_id, pi_id=None, crc_id=None, days_ago=5):
    """Enroll a fresh patient via POST /api/patients; returns the patient doc."""
    enrolled = (server.now() - timedelta(days=days_ago)).date().isoformat()
    async with make_client() as cli:
        r = await cli.post('/api/patients', headers=staff_headers, json={
            'full_name': f'Test PATIENT {RUN_ID}',
            'email': f'test-{RUN_ID}-enrollee-{uuid.uuid4().hex[:6]}@example.com',
            'trial_id': trial_id, 'pi_id': pi_id, 'crc_id': crc_id,
            'enrolled_date': enrolled,
        })
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope='module', autouse=True)
def _cleanup():
    yield
    async def clean():
        db = server.db
        await db.users.delete_many({'email': {'$regex': f'^test-{RUN_ID}-'}})
        await db.organizations.delete_many({'name': {'$regex': RUN_ID}})
        await db.trials.delete_many({'id': {'$in': _trial_ids}})
        await db.visits.delete_many({'trial_id': {'$in': _trial_ids}})
        await db.patients.delete_many({'email': {'$regex': f'test-{RUN_ID}-'}})
        await db.visit_instances.delete_many({'trial_id': {'$in': _trial_ids + ['nonexistent-' + RUN_ID]}})
        await db.notifications.delete_many({'$or': [
            {'trial_id': {'$in': _trial_ids}}, {'title': {'$regex': RUN_ID}},
        ]})
        await db.conversations.delete_many({'id': {'$in': _conversation_ids}})
        await db.messages.delete_many({'conversation_id': {'$in': _conversation_ids}})
        await db.audit_logs.delete_many({'user_name': {'$regex': RUN_ID}})
    run(clean())
    LOOP.close()


@pytest.fixture(scope='module')
def sponsor():
    return run(_register('sponsor', org=ORG_SPONSOR))


@pytest.fixture(scope='module')
def pi():
    return run(_register('pi', org=ORG_SITE))


@pytest.fixture(scope='module')
def crc():
    return run(_register('crc', org=ORG_SITE))


@pytest.fixture(scope='module')
def trial(sponsor):
    return run(_make_trial(sponsor[1]))


# ── Enrollment materializes instances ────────────────────────────────────────
class TestEnrollmentMaterialization:
    def test_enroll_creates_one_instance_per_template(self, sponsor, pi, trial):
        trial_doc, tpls = trial
        _, pi_headers = pi
        async def flow():
            patient = await _enroll(pi_headers, trial_doc['id'], pi_id=pi[0]['id'], days_ago=5)
            async with make_client() as cli:
                r = await cli.get(f"/api/patients/{patient['id']}/visits", headers=pi_headers)
            assert r.status_code == 200, r.text
            insts = r.json()
            assert len(insts) == len(tpls)
            by_seq = sorted(insts, key=lambda i: i['seq'])
            for inst, tpl in zip(by_seq, tpls):
                uuid.UUID(inst['id'])                       # uuid4 string id
                assert inst['id'] != tpl['id']              # instance, not the template
                assert inst['patient_id'] == patient['id']
                assert inst['trial_id'] == trial_doc['id']
                assert inst['visit_template_id'] == tpl['id']
                assert inst['name'] == tpl['name']
                assert inst['seq'] == tpl['visit_number']
                for key in ('scheduled_date', 'window_start', 'window_end',
                            'status', 'note', 'updated_at'):
                    assert key in inst, f'missing field {key}'
                assert inst['window_start'] < inst['scheduled_date'] < inst['window_end']
            # enrolled 5 days ago: day 0 already past, day 7/14 in the future
            assert [i['status'] for i in by_seq] == ['missed', 'upcoming', 'upcoming']
            # the shared templates were NOT touched
            for tpl in tpls:
                raw = await server.db.visits.find_one({'id': tpl['id']}, {'_id': 0})
                assert 'status' not in raw and 'patient_id' not in raw
            # enrollment mutation is audited
            row = await server.db.audit_logs.find_one(
                {'action': 'patient.enroll', 'target_id': patient['id']})
            assert row, 'enrollment not audited'
        run(flow())

    def test_patient_detail_returns_patient_trial_and_instances(self, pi, trial):
        trial_doc, tpls = trial
        _, pi_headers = pi
        async def flow():
            patient = await _enroll(pi_headers, trial_doc['id'])
            async with make_client() as cli:
                r = await cli.get(f"/api/patients/{patient['id']}", headers=pi_headers)
            assert r.status_code == 200, r.text
            j = r.json()
            assert j['id'] == patient['id']
            assert j['trial']['id'] == trial_doc['id']
            assert j['trial']['protocol_id'] == trial_doc['protocol_id']
            assert len(j['instances']) == len(tpls)
        run(flow())

    def test_unknown_patient_404(self, pi):
        _, pi_headers = pi
        async def flow():
            async with make_client() as cli:
                r = await cli.get(f'/api/patients/{uuid.uuid4()}', headers=pi_headers)
                r2 = await cli.get(f'/api/patients/{uuid.uuid4()}/visits', headers=pi_headers)
            assert r.status_code == 404 and r2.status_code == 404
        run(flow())

    def test_trial_without_templates_yields_zero_instances(self, sponsor, pi):
        _, pi_headers = pi
        async def flow():
            bare_trial, _ = await _make_trial(sponsor[1], templates=())
            patient = await _enroll(pi_headers, bare_trial['id'])
            async with make_client() as cli:
                r = await cli.get(f"/api/patients/{patient['id']}/visits", headers=pi_headers)
            assert r.status_code == 200 and r.json() == []
        run(flow())

    def test_unknown_trial_yields_zero_instances(self, pi):
        _, pi_headers = pi
        async def flow():
            patient = await _enroll(pi_headers, 'nonexistent-' + RUN_ID)
            async with make_client() as cli:
                r = await cli.get(f"/api/patients/{patient['id']}/visits", headers=pi_headers)
            assert r.status_code == 200 and r.json() == []
        run(flow())

    def test_materialization_is_idempotent_per_patient(self, pi, trial):
        trial_doc, tpls = trial
        _, pi_headers = pi
        async def flow():
            patient = await _enroll(pi_headers, trial_doc['id'])
            doc = await server.db.patients.find_one({'id': patient['id']}, {'_id': 0})
            created_again = await server.materialize_visit_instances(doc)
            assert created_again == 0
            count = await server.db.visit_instances.count_documents({'patient_id': patient['id']})
            assert count == len(tpls)
        run(flow())


# ── PATCH /visit-instances/{id} ──────────────────────────────────────────────
class TestVisitInstancePatch:
    def test_patch_touches_only_that_patients_copy(self, pi, crc, trial):
        trial_doc, tpls = trial
        _, pi_headers = pi
        crc_user, crc_headers = crc
        async def flow():
            pa = await _enroll(pi_headers, trial_doc['id'])
            pb = await _enroll(pi_headers, trial_doc['id'])
            inst_a = await server.db.visit_instances.find(
                {'patient_id': pa['id']}, {'_id': 0}).sort('seq', 1).to_list(10)
            async with make_client() as cli:
                r = await cli.patch(f"/api/visit-instances/{inst_a[0]['id']}", headers=crc_headers,
                                    json={'status': 'completed', 'note': f'done {RUN_ID}'})
            assert r.status_code == 200, r.text
            j = r.json()
            assert j['status'] == 'completed' and j['note'] == f'done {RUN_ID}'
            assert j['updated_by'] == crc_user['id']
            # patient B is untouched
            b_completed = await server.db.visit_instances.count_documents(
                {'patient_id': pb['id'], 'status': 'completed'})
            assert b_completed == 0
            # shared template untouched
            raw = await server.db.visits.find_one({'id': tpls[0]['id']}, {'_id': 0})
            assert 'status' not in raw and 'note' not in raw
            # audited
            row = await server.db.audit_logs.find_one(
                {'action': 'visit_instance.patch', 'target_id': inst_a[0]['id']})
            assert row and row['user_id'] == crc_user['id']
        run(flow())

    def test_reschedule_moves_window_with_the_date(self, pi, trial):
        trial_doc, _ = trial
        _, pi_headers = pi
        async def flow():
            patient = await _enroll(pi_headers, trial_doc['id'])
            inst = await server.db.visit_instances.find_one({'patient_id': patient['id']}, {'_id': 0})
            new_date = (server.now() + timedelta(days=30)).isoformat()
            async with make_client() as cli:
                r = await cli.patch(f"/api/visit-instances/{inst['id']}", headers=pi_headers,
                                    json={'scheduled_date': new_date})
            assert r.status_code == 200, r.text
            j = r.json()
            assert j['scheduled_date'][:10] == new_date[:10]
            assert j['window_start'] < j['scheduled_date'] < j['window_end']
            assert j['window_start'][:10] != inst['window_start'].strftime('%Y-%m-%d')
        run(flow())

    def test_patch_guards(self, pi, trial):
        trial_doc, _ = trial
        _, pi_headers = pi
        async def flow():
            _, patient_headers = await _register('patient')
            patient = await _enroll(pi_headers, trial_doc['id'])
            inst = await server.db.visit_instances.find_one({'patient_id': patient['id']}, {'_id': 0})
            async with make_client() as cli:
                r = await cli.patch(f"/api/visit-instances/{inst['id']}",
                                    headers=patient_headers, json={'status': 'completed'})
                assert r.status_code == 403
                r2 = await cli.patch(f'/api/visit-instances/{uuid.uuid4()}',
                                     headers=pi_headers, json={'status': 'completed'})
                assert r2.status_code == 404
                r3 = await cli.patch(f"/api/visit-instances/{inst['id']}",
                                     headers=pi_headers, json={})
                assert r3.status_code == 400
        run(flow())


# ── GET /visits/mine reads instances (with template fallback) ────────────────
class TestVisitsMine:
    def test_reads_instances_for_logged_in_patient(self, pi, trial):
        trial_doc, tpls = trial
        _, pi_headers = pi
        async def flow():
            pt_user, pt_headers = await _register('patient')
            patient = await _enroll(pi_headers, trial_doc['id'], days_ago=5)
            await server.db.patients.update_one({'id': patient['id']},
                                                {'$set': {'user_id': pt_user['id']}})
            async with make_client() as cli:
                r = await cli.get('/api/visits/mine', headers=pt_headers)
            assert r.status_code == 200, r.text
            visits = r.json()
            assert len(visits) == len(tpls)
            inst_ids = {i['id'] for i in await server.db.visit_instances.find(
                {'patient_id': patient['id']}, {'_id': 0, 'id': 1}).to_list(10)}
            for v in visits:
                assert v['id'] in inst_ids                  # served from instances
                # every field the RN app consumes today is still present
                for key in ('name', 'scheduled_date', 'status', 'window_days',
                            'activities', 'visit_number', 'patient_id'):
                    assert key in v, f'missing field {key}'
            assert [v['status'] for v in visits] == ['missed', 'upcoming', 'upcoming']
        run(flow())

    def test_falls_back_to_templates_when_no_instances(self, trial):
        trial_doc, tpls = trial
        async def flow():
            pt_user, pt_headers = await _register('patient')
            # legacy patient record: exists in db but was never materialized
            await server.db.patients.insert_one({
                'id': str(uuid.uuid4()), 'user_id': pt_user['id'],
                'full_name': f'Legacy {RUN_ID}',
                'email': f'test-{RUN_ID}-legacy@example.com',
                'trial_id': trial_doc['id'],
                'enrolled_date': (server.now() - timedelta(days=5)).date().isoformat(),
                'completed_visit_ids': [], 'created_at': server.now(),
            })
            async with make_client() as cli:
                r = await cli.get('/api/visits/mine', headers=pt_headers)
            assert r.status_code == 200, r.text
            visits = r.json()
            assert len(visits) == len(tpls)
            for v in visits:
                assert v['scheduled_date'] and v['status'] in (
                    'upcoming', 'completed', 'missed', 'scheduled')
        run(flow())


# ── Schedule approve / flag ──────────────────────────────────────────────────
class TestScheduleReview:
    def test_approve_sets_status_notifies_sponsor_and_audits(self, sponsor, pi):
        sp_user, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            t, _ = await _make_trial(sp_headers, templates=())
            async with make_client() as cli:
                r = await cli.post(f"/api/schedules/{t['id']}/approve", headers=pi_headers)
            assert r.status_code == 200, r.text
            fresh = await server.db.trials.find_one({'id': t['id']}, {'_id': 0})
            assert fresh['schedule_status'] == 'approved'
            notif = await server.db.notifications.find_one(
                {'user_id': sp_user['id'], 'trial_id': t['id']}, {'_id': 0})
            assert notif and notif.get('read') is False
            row = await server.db.audit_logs.find_one(
                {'action': 'schedule.approve', 'target_id': t['id']})
            assert row and row['user_id'] == pi_user['id']
        run(flow())

    def test_flag_records_reason_and_notifies(self, sponsor, pi):
        sp_user, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            t, _ = await _make_trial(sp_headers, templates=())
            reason = f'Window too tight {RUN_ID}'
            async with make_client() as cli:
                r_missing = await cli.post(f"/api/schedules/{t['id']}/flag",
                                           headers=pi_headers, json={})
                assert r_missing.status_code == 422
                r = await cli.post(f"/api/schedules/{t['id']}/flag",
                                   headers=pi_headers, json={'reason': reason})
            assert r.status_code == 200, r.text
            fresh = await server.db.trials.find_one({'id': t['id']}, {'_id': 0})
            assert fresh['schedule_status'] == 'flagged'
            notif = await server.db.notifications.find_one(
                {'user_id': sp_user['id'], 'trial_id': t['id']}, {'_id': 0})
            assert notif and reason in notif['body']
            row = await server.db.audit_logs.find_one(
                {'action': 'schedule.flag', 'target_id': t['id']})
            assert row and row['user_id'] == pi_user['id']
        run(flow())

    def test_pi_only_and_unknown_trial(self, sponsor, pi, crc):
        _, sp_headers = sponsor
        _, pi_headers = pi
        _, crc_headers = crc
        async def flow():
            t, _ = await _make_trial(sp_headers, templates=())
            async with make_client() as cli:
                for headers in (crc_headers, sp_headers):
                    r = await cli.post(f"/api/schedules/{t['id']}/approve", headers=headers)
                    assert r.status_code == 403
                r404 = await cli.post(f'/api/schedules/{uuid.uuid4()}/approve', headers=pi_headers)
                assert r404.status_code == 404
        run(flow())


# ── Tasks queue ──────────────────────────────────────────────────────────────
class TestTasks:
    def test_queue_has_overdue_today_schedule_and_messages(self, sponsor, pi, crc):
        sp_user, sp_headers = sponsor
        pi_user, pi_headers = pi
        crc_user, crc_headers = crc
        async def flow():
            t, _ = await _make_trial(sp_headers, templates=((0, 'Only Visit'),))
            # day_offset 0, enrolled 10 days ago -> overdue; enrolled today -> due today
            overdue_pt = await _enroll(pi_headers, t['id'], pi_id=pi_user['id'],
                                       crc_id=crc_user['id'], days_ago=10)
            today_pt = await _enroll(pi_headers, t['id'], pi_id=pi_user['id'],
                                     crc_id=crc_user['id'], days_ago=0)
            # one unread chat message for the pi
            cid = str(uuid.uuid4())
            _conversation_ids.append(cid)
            await server.db.conversations.insert_one({
                'id': cid, 'participant_ids': sorted([pi_user['id'], crc_user['id']]),
                'title': '', 'is_group': False, 'last_message': 'hi',
                'created_at': server.now(), 'updated_at': server.now(),
            })
            await server.db.messages.insert_one({
                'id': str(uuid.uuid4()), 'conversation_id': cid,
                'sender_id': crc_user['id'], 'content': f'hello {RUN_ID}',
                'created_at': server.now(), 'read_by': {crc_user['id']: server.now()},
            })
            async with make_client() as cli:
                r = await cli.get('/api/tasks', headers=pi_headers)
            assert r.status_code == 200, r.text
            tasks = r.json()
            for task in tasks:
                for key in ('id', 'type', 'title', 'subtitle', 'due', 'priority'):
                    assert key in task, f'missing field {key}'
            by_type = {}
            for task in tasks:
                by_type.setdefault(task['type'], []).append(task)
            overdue = [x for x in by_type.get('overdue_visit', [])
                       if x.get('patient_id') == overdue_pt['id']]
            assert overdue, 'overdue visit instance not surfaced as a task'
            assert overdue[0]['priority'] == 'high'
            assert overdue[0]['trial_id'] == t['id']
            today = [x for x in by_type.get('visit_today', [])
                     if x.get('patient_id') == today_pt['id']]
            assert today, "today's visit not surfaced as a task"
            reviews = [x for x in by_type.get('schedule_review', [])
                       if x.get('trial_id') == t['id']]
            assert reviews, 'pending schedule review not surfaced'
            assert by_type.get('unread_messages'), 'unread messages count not surfaced'
            # crc sees the queue for their assigned patients too
            async with make_client() as cli:
                rc = await cli.get('/api/tasks', headers=crc_headers)
            assert rc.status_code == 200
            assert any(x['type'] == 'overdue_visit' and x.get('patient_id') == overdue_pt['id']
                       for x in rc.json())
        run(flow())

    def test_approved_schedule_leaves_the_queue(self, sponsor, pi):
        _, sp_headers = sponsor
        pi_user, pi_headers = pi
        async def flow():
            t, _ = await _make_trial(sp_headers, templates=())
            await _enroll(pi_headers, t['id'], pi_id=pi_user['id'])
            async with make_client() as cli:
                r1 = await cli.get('/api/tasks', headers=pi_headers)
                assert any(x['type'] == 'schedule_review' and x.get('trial_id') == t['id']
                           for x in r1.json())
                ra = await cli.post(f"/api/schedules/{t['id']}/approve", headers=pi_headers)
                assert ra.status_code == 200
                r2 = await cli.get('/api/tasks', headers=pi_headers)
                assert not any(x['type'] == 'schedule_review' and x.get('trial_id') == t['id']
                               for x in r2.json())
        run(flow())

    def test_tasks_role_guard(self, sponsor):
        _, sp_headers = sponsor
        async def flow():
            _, patient_headers = await _register('patient')
            async with make_client() as cli:
                r = await cli.get('/api/tasks', headers=patient_headers)
                assert r.status_code == 403
                r2 = await cli.get('/api/tasks', headers=sp_headers)
                assert r2.status_code == 403
        run(flow())
