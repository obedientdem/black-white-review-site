import base64
import json
import re
from helpers import ApiCase
from backend.app import create_app,now,digest

class AccountsTests(ApiCase):
    def test_register_verify_and_one_time_token(self):
        r=self.call('/auth/register',data={'email':'new@test.invalid','password':'Testing-password-123','name':'New','adult':True,'role':'admin','verified':1});self.assertEqual(r.status_code,202)
        row=self.conn.execute('SELECT * FROM users WHERE email=?',('new@test.invalid',)).fetchone();self.assertEqual(row['role'],'player');self.assertEqual(row['verified'],0);self.assertNotIn('Testing-password',row['password'])
        token=self.conn.execute('SELECT body FROM outbox WHERE user_id=?',(row['id'],)).fetchone()[0].split('token=')[1]
        self.assertEqual(self.call('/auth/verify',data={'token':token}).status_code,200)
        self.assertEqual(self.call('/auth/verify',data={'token':token}).status_code,400)
        self.assertEqual(self.conn.execute('SELECT verified FROM users WHERE id=?',(row['id'],)).fetchone()[0],1)

    def test_age_password_and_email_validation(self):
        for change in [{'adult':False},{'password':'short'},{'email':'broken'}]:
            data={'email':'new@test.invalid','password':'Testing-password-123','name':'New','adult':True};data.update(change)
            self.assertEqual(self.call('/auth/register',data=data).status_code,400)

    def test_reset_expires_tokens_and_revokes_sessions(self):
        uid=self.user('reset@test.invalid');other=self.app.test_client();token=self.login('reset@test.invalid',other)
        self.call('/auth/reset-request',data={'email':'reset@test.invalid'})
        reset=self.conn.execute('SELECT body FROM outbox WHERE user_id=?',(uid,)).fetchone()[0].split('token=')[1]
        self.assertEqual(self.call('/auth/reset',data={'token':reset,'password':'Another-strong-password'}).status_code,200)
        self.assertIsNone(other.get('/api/me').json['user'])
        self.assertEqual(self.call('/auth/reset',data={'token':reset,'password':'Another-strong-password'}).status_code,400)
        self.call('/auth/reset-request',data={'email':'reset@test.invalid'});expired=self.conn.execute('SELECT body FROM outbox WHERE user_id=? ORDER BY id DESC',(uid,)).fetchone()[0].split('token=')[1];self.conn.execute('UPDATE tokens SET expires=0 WHERE token=?',(digest(expired),))
        self.assertEqual(self.call('/auth/reset',data={'token':expired,'password':'Another-strong-password'}).status_code,400)

    def test_cookie_csrf_origin_and_unverified(self):
        client=self.app.test_client();boot=client.get('/api/bootstrap');self.assertIn('HttpOnly',boot.headers['Set-Cookie']);self.assertIn('SameSite=Lax',boot.headers['Set-Cookie'])
        self.assertEqual(client.post('/api/auth/login',json={}).status_code,403)
        self.assertEqual(client.post('/api/auth/login',json={},headers={'X-CSRF-Token':boot.json['csrf'],'Origin':'https://evil.invalid'}).status_code,403)
        self.user('unverified@test.invalid',verified=0);self.login('unverified@test.invalid')
        self.assertEqual(self.call('/admin/articles',data={}).status_code,403)

    def test_rate_limit_login(self):
        for _ in range(8):self.call('/auth/login',data={'email':'absent@test.invalid','password':'wrong'})
        self.assertEqual(self.call('/auth/login',data={'email':'absent@test.invalid','password':'wrong'}).status_code,429)

class ContentTests(ApiCase):
    def test_content_persistence_and_archive(self):
        id=self.article(title_en='English version',body_en='English body')
        self.assertEqual(self.client.get('/api/public').json['articles'][0]['title_en'],'English version')
        fresh=create_app({'DATABASE':self.path,'TESTING':True});self.assertEqual(fresh.test_client().get('/api/public').json['articles'][0]['id'],id)
        self.assertEqual(self.call(f'/admin/articles/{id}',method='DELETE').status_code,200)
        self.assertEqual(self.client.get('/api/public').json['articles'],[])

    def test_editor_permissions_and_score_validation(self):
        self.user('edit@test.invalid','editor');self.login('edit@test.invalid');self.article()
        for path in ['/admin/users','/admin/settings','/admin/audit']:self.assertEqual(self.client.get('/api'+path).status_code,403)
        self.assertEqual(self.call('/admin/settings',method='PATCH',data={'impact':'fake'}).status_code,403)
        for score in [9.2,11,-1,True,'9']:
            r=self.call('/admin/articles',data={'kind':'review','title':'x','body':'x','cover':'assets/embers.svg','category':'RPG','score':score,'pros':['a'],'cons':['b']});self.assertEqual(r.status_code,400)

    def test_player_cannot_publish_or_set_role(self):
        uid=self.user('player@test.invalid');self.login('player@test.invalid')
        for path in ['/admin/articles','/admin/games','/admin/upload']:
            self.assertEqual(self.call(path,data={'role':'admin'}).status_code,403)
        self.assertEqual(self.call(f'/admin/users/{uid}',method='PATCH',data={'role':'admin'}).status_code,403)

    def test_settings_games_and_audit(self):
        self.assertEqual(self.call('/admin/settings',method='PATCH',data={'tagline':'Edited','impact':'100万+','updated':'2026-10-16'}).status_code,200)
        self.assertEqual(self.client.get('/api/public').json['settings']['tagline'],'Edited')
        r=self.call('/admin/games',data={'title':'Game','developer':'Dev','publisher':'Pub','release':'2026','genre':'RPG','platforms':['PC','PS5'],'store':'https://example.com'});self.assertEqual(r.status_code,200)
        self.article(game_id=r.json['id']);self.assertEqual(self.call('/admin/games/'+str(r.json['id']),method='DELETE').status_code,409)
        self.assertGreaterEqual(len(self.client.get('/api/admin/audit').json['rows']),3)

    def test_recent_views_do_not_use_all_time(self):
        id=self.article();self.call(f'/articles/{id}/view');self.call(f'/articles/{id}/view')
        self.conn.execute('INSERT INTO views VALUES(?,?,?,?)',(id,'old',1,now()-31*86400))
        self.assertEqual(self.client.get('/api/public').json['articles'][0]['views30'],1)

    def test_upload_rejects_active_content(self):
        for raw in [b'<svg onload="alert(1)"></svg>',b'<html>bad</html>',b'not an image']:
            self.assertEqual(self.call('/admin/upload',data={'base64':base64.b64encode(raw).decode()}).status_code,400)

    def test_production_requires_real_services(self):
        with self.assertRaises(RuntimeError):create_app({'DATABASE':self.path,'PRODUCTION':True})
