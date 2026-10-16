from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import hmac
import json
from helpers import ApiCase
from backend.app import now,connect,pack
from backend.business import run_draw

def iso(stamp):return datetime.fromtimestamp(stamp,timezone.utc).isoformat().replace('+00:00','Z')

class WorkflowTests(ApiCase):
    def event(self,kind='giveaway',**changes):
        data={'kind':kind,'status':'published','title':'Test event','description':'Rules','cover':'assets/forest.svg','start':iso(now()-3600),'end':iso(now()+3600),'draw_at':iso(now()+7200),'region':'ALL','min_age':18,'slots':2,'prize':'Test prize','questions':[]};data.update(changes)
        r=self.call('/admin/activities',data=data);self.assertEqual(r.status_code,200,r.json);return r.json['id'],data
    def enter(self,id,**changes):
        data={'region':'CN','consent':True,'answers':[],'message':'Test notes'};data.update(changes);return self.call(f'/activities/{id}/enter',data=data)
    def test_comments_moderated_and_owner_visible(self):
        article=self.article();player=self.user('p@test.invalid');self.login('p@test.invalid')
        r=self.call(f'/articles/{article}/comments',data={'body':'<script>alert(1)</script>'});id=r.json['id']
        self.assertEqual(self.client.get(f'/api/articles/{article}/interactions').json['comments'][0]['status'],'pending')
        stranger=self.app.test_client();self.assertEqual(stranger.get(f'/api/articles/{article}/interactions').json['comments'],[])
        self.assertEqual(self.call(f'/admin/comments/{id}',method='PATCH',data={'status':'approved'}).status_code,403)
        self.login('admin@test.invalid');self.assertEqual(self.call(f'/admin/comments/{id}',method='PATCH',data={'status':'approved'}).status_code,200)
        self.assertEqual(len(stranger.get(f'/api/articles/{article}/interactions').json['comments']),1)
        self.call(f'/admin/comments/{id}',method='PATCH',data={'status':'deleted'});self.assertEqual(stranger.get(f'/api/articles/{article}/interactions').json['comments'],[])

    def test_rating_upsert_favorite_and_independent_score(self):
        id=self.article(score=9);self.user('p@test.invalid');self.login('p@test.invalid')
        for score in [7,8.5]:self.assertEqual(self.call(f'/articles/{id}/rating',data={'score':score}).status_code,200)
        r=self.client.get(f'/api/articles/{id}/interactions').json;self.assertEqual(r['average'],8.5);self.assertEqual(r['count'],1)
        self.assertEqual(self.client.get('/api/public').json['articles'][0]['score'],9)
        self.assertTrue(self.call(f'/articles/{id}/favorite').json['saved']);self.assertFalse(self.call(f'/articles/{id}/favorite').json['saved'])
        self.assertEqual(self.call(f'/articles/{id}/rating',data={'score':8.2}).status_code,400)

    def test_publisher_certification_private_business(self):
        uid=self.user('p@test.invalid');self.login('p@test.invalid');request=self.call('/publisher/apply',data={'company':'Studio','game':'Game','contact':'p@test.invalid','proof':'https://example.com'}).json['id'];biz=self.call('/business',data={'company':'Studio','game':'Game','contact':'p@test.invalid','message':'Private brief'}).json['id']
        self.assertEqual(self.client.get('/api/admin/business').status_code,403)
        self.login('admin@test.invalid');self.assertEqual(self.call(f'/admin/publisher_requests/{request}',method='PATCH',data={'status':'approved'}).status_code,200)
        self.assertEqual(self.call(f'/admin/business/{biz}',method='PATCH',data={'status':'negotiating','notes':'Private communication'}).status_code,200)
        self.login('p@test.invalid');dashboard=self.client.get('/api/publisher/dashboard').json;self.assertEqual(len(dashboard['business']),1);self.assertEqual(dashboard['business'][0]['status'],'negotiating')
        self.user('other@test.invalid','publisher');self.login('other@test.invalid');self.assertEqual(self.client.get('/api/publisher/dashboard').json['business'],[])

    def test_publisher_draft_requires_admin_and_is_owner_scoped(self):
        _,data=self.event(kind='recruitment');self.user('pub@test.invalid','publisher');self.login('pub@test.invalid')
        r=self.call('/publisher/activities',data=data);id=r.json['id'];self.assertEqual(r.json['status'],'draft')
        self.assertNotIn(id,[a['id'] for a in self.client.get('/api/public').json['activities']])
        self.assertEqual(self.call(f'/publisher/activities/{id}/submit').status_code,200)
        self.assertEqual(self.call(f'/publisher/activities/{id}',method='PATCH',data=data).status_code,409)
        self.assertEqual(self.call(f'/admin/activities/{id}',method='PATCH',data=data).status_code,403)
        self.user('other@test.invalid','publisher');self.login('other@test.invalid');self.assertEqual(self.call(f'/publisher/activities/{id}/submit').status_code,404)
        self.login('admin@test.invalid');self.assertEqual(self.call(f'/admin/activities/{id}',method='PATCH',data=data).status_code,200)

    def test_recruitment_requires_separate_referral_consent(self):
        pub=self.user('pub@test.invalid','publisher');id,data=self.event(kind='recruitment');self.conn.execute('UPDATE activities SET owner_id=? WHERE id=?',(pub,id))
        self.user('p@test.invalid');self.login('p@test.invalid');application=self.enter(id).json['id']
        self.login('admin@test.invalid');self.assertEqual(self.call(f'/admin/applications/{application}',method='PATCH',data={'status':'referred'}).status_code,403)
        self.login('p@test.invalid');self.call(f'/applications/{application}/consent');self.login('admin@test.invalid');self.assertEqual(self.call(f'/admin/applications/{application}',method='PATCH',data={'status':'referred'}).status_code,200)
        self.login('pub@test.invalid');self.assertEqual(len(self.client.get('/api/publisher/dashboard').json['referrals']),1)
        self.login('p@test.invalid');self.call(f'/applications/{application}/consent');self.login('pub@test.invalid');self.assertEqual(self.client.get('/api/publisher/dashboard').json['referrals'],[])

    def test_entry_eligibility_unique_and_locked_rules(self):
        id,data=self.event(region='CN',questions=['Preferred genre?']);self.user('p@test.invalid');self.login('p@test.invalid')
        self.assertEqual(self.enter(id,region='US',answers=['RPG']).status_code,403)
        self.assertEqual(self.enter(id,consent=False,answers=['RPG']).status_code,400)
        self.assertEqual(self.enter(id).status_code,400)
        self.assertEqual(self.enter(id,answers=['RPG']).status_code,200)
        self.assertEqual(self.enter(id,answers=['RPG']).status_code,409)
        self.login('admin@test.invalid');data['slots']=3
        self.assertEqual(self.call(f'/admin/activities/{id}',method='PATCH',data=data).status_code,409)
        self.assertEqual(self.call(f'/admin/activities/{id}',method='DELETE').status_code,409)

    def test_unverified_and_closed_and_stricter_age(self):
        id,_=self.event();self.user('u@test.invalid',verified=0);self.login('u@test.invalid');self.assertEqual(self.enter(id).status_code,403)
        self.login('admin@test.invalid');closed,_=self.event(end=iso(now()-10),draw_at=iso(now()+10));older,_=self.event(min_age=21)
        self.assertEqual(self.enter(closed).status_code,409);self.assertEqual(self.enter(older).status_code,403)

    def test_concurrent_draw_is_exactly_once_and_verifiable(self):
        id,data=self.event();users=[]
        for i in range(6):
            uid=self.user(f'p{i}@test.invalid');users.append(uid);self.login(f'p{i}@test.invalid');self.assertEqual(self.enter(id).status_code,200)
        # Shift time forward in the worker, keeping production rules unchanged.
        due=now()+8000
        def worker():
            conn=connect(self.path)
            try:return run_draw(conn,id,due)
            finally:conn.close()
        with ThreadPoolExecutor(max_workers=4) as executor:results=list(executor.map(lambda _:worker(),range(8)))
        self.assertTrue(all(r['winners']==results[0]['winners'] for r in results))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM draws').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM awards').fetchone()[0],2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM notifications').fetchone()[0],2)
        result=self.client.get(f'/api/activities/{id}/results').json
        seed=bytes.fromhex(result['seed']);self.assertEqual(hashlib.sha256(seed).hexdigest(),result['commitment'])
        expected=sorted(result['snapshot'],key=lambda x:hmac.new(seed,str(x).encode(),hashlib.sha256).digest())[:2]
        self.assertEqual(expected,result['winner_ids']);self.assertNotIn('email',json.dumps(result))

    def test_draw_failure_rolls_back_and_safe_retry(self):
        id,_=self.event();self.user('p@test.invalid');self.login('p@test.invalid');self.enter(id)
        self.conn.execute("CREATE TRIGGER fail_award BEFORE INSERT ON awards BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(Exception):run_draw(self.conn,id,now()+8000)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM draws').fetchone()[0],0)
        self.conn.execute('DROP TRIGGER fail_award');run_draw(self.conn,id,now()+8000)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM awards').fetchone()[0],1)

    def test_manual_fulfillment_and_suspension(self):
        id,_=self.event();self.user('p@test.invalid');self.login('p@test.invalid');self.enter(id);run_draw(self.conn,id,now()+8000)
        award=self.conn.execute('SELECT id FROM awards').fetchone()[0]
        self.assertEqual(self.call(f'/admin/awards/{award}',method='PATCH',data={'status':'sent','note':'sent manually'}).status_code,403)
        self.login('admin@test.invalid');self.assertEqual(self.call(f'/admin/awards/{award}',method='PATCH',data={'status':'sent','note':'Manual dispatch confirmed'}).status_code,200)
        self.assertEqual(self.call(f'/admin/awards/{award}',method='PATCH',data={'status':'pending'}).status_code,409)
        uid=self.conn.execute("SELECT id FROM users WHERE email='p@test.invalid'").fetchone()[0];self.call(f'/admin/users/{uid}',method='PATCH',data={'active':0});client=self.app.test_client();self.assertIsNone(client.get('/api/me').json['user'])

    def test_invitation_still_requires_certification(self):
        uid=self.user('invited@test.invalid');r=self.call('/admin/publisher-invitations',data={'email':'invited@test.invalid','company':'Invited studio','game':'Game'})
        self.assertEqual(r.status_code,200);self.assertEqual(self.conn.execute('SELECT role FROM users WHERE id=?',(uid,)).fetchone()[0],'player')
        self.assertEqual(self.call(f"/admin/publisher_requests/{r.json['id']}",method='PATCH',data={'status':'approved'}).status_code,200)
        self.assertEqual(self.conn.execute('SELECT role FROM users WHERE id=?',(uid,)).fetchone()[0],'publisher')

    def test_after_cutoff_deleted_account_preserves_anonymous_draw_entry(self):
        from unittest.mock import patch
        id,_=self.event();uid=self.user('delete@test.invalid');self.login('delete@test.invalid');entry=self.enter(id).json['id']
        with patch('backend.app.now',return_value=now()+4000):
            r=self.call('/account',method='DELETE',data={'password':'Testing-password-123'});self.assertEqual(r.status_code,200,r.json)
        row=self.conn.execute('SELECT * FROM entries WHERE id=?',(entry,)).fetchone();self.assertIsNone(row['user_id']);self.assertEqual(row['data'],'{}')
        draw=run_draw(self.conn,id,now()+8000);self.assertIn(entry,json.loads(draw['snapshot']))
