import base64
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import zipfile
from PIL import Image
from helpers import ApiCase
from backend.app import connect,now,pack
from backend.operations import backup_database,restore_archive,import_content,export_static,operation
from scripts.build_standalone import build

class ReleaseTests(ApiCase):
    def test_backup_restore_with_integrity_and_uploads(self):
        id=self.article();uploads=Path(self.path).parent/'uploads';uploads.mkdir();(uploads/'a.webp').write_bytes(b'private-test-image')
        backup=Path(self.tmp.name)/'backup.zip';backup_database(self.conn,self.path,backup)
        restored=Path(self.tmp.name)/'restored/site.sqlite';restore_archive(backup,restored);other=connect(restored)
        try:self.assertEqual(other.execute('SELECT id FROM articles').fetchone()[0],id);self.assertEqual(other.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        finally:other.close()
        self.assertEqual((restored.parent/'uploads/a.webp').read_bytes(),b'private-test-image')
        with self.assertRaises(ValueError):restore_archive(backup,restored)

    def test_restore_rejects_path_traversal(self):
        bad=Path(self.tmp.name)/'bad.zip'
        with zipfile.ZipFile(bad,'w') as z:z.writestr('site.sqlite',b'x');z.writestr('../escape',b'x')
        with self.assertRaises(ValueError):restore_archive(bad,Path(self.tmp.name)/'restored.sqlite')

    def test_import_requires_source_rights_and_is_all_or_nothing(self):
        source=Path(self.tmp.name)/'import.json';good={'kind':'review','title':'Historical review','body':'Original','cover':'assets/embers.svg','category':'RPG','score':9,'pros':['Good'],'cons':['Bad'],'source':'Original author','source_url':'https://example.com/review','rights_confirmed':True}
        source.write_text(json.dumps([good,{**good,'rights_confirmed':False}]),encoding='utf-8')
        with self.assertRaises(ValueError):import_content(self.app,self.conn,source)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM articles').fetchone()[0],0)
        source.write_text(json.dumps([good]),encoding='utf-8');self.assertEqual(import_content(self.app,self.conn,source),1)
        self.assertEqual(self.client.get('/api/public').json['articles'],[])

    def test_export_only_public_and_standalone_has_embedded_assets(self):
        self.article();self.article(status='draft',title='PRIVATE DRAFT')
        self.conn.execute('INSERT INTO business(user_id,data,created) VALUES(?,?,?)',(self.uid,pack({'company':'PRIVATE COMPANY'}),now()))
        self.call('/admin/settings',method='PATCH',data={'tagline':'Updated statement','contact':'Public contact'})
        target=Path(self.tmp.name)/'published';export_static(self.app,target);data=(target/'data.js').read_text(encoding='utf-8')
        self.assertIn('Updated statement',data);self.assertNotIn('PRIVATE DRAFT',data);self.assertNotIn('PRIVATE COMPANY',data);self.assertNotIn('password',data)
        standalone=Path(self.tmp.name)/'standalone.html';build(target,standalone);html=standalone.read_text(encoding='utf-8')
        self.assertNotIn('src="app.js"',html);self.assertIn('data:image/svg+xml;base64,',html)

    def test_raster_upload_reencodes_and_rejects_truncated(self):
        source=io.BytesIO();Image.new('RGB',(20,20),'green').save(source,format='PNG')
        r=self.call('/admin/upload',data={'base64':base64.b64encode(source.getvalue()).decode()});self.assertEqual(r.status_code,200,r.json)
        self.assertTrue(r.json['url'].endswith('.webp'));blob=self.client.get(r.json['url']);self.assertEqual(blob.status_code,200);self.assertIn(b'WEBP',blob.data[:12]);blob.close()
        self.assertEqual(self.call('/admin/upload',data={'base64':base64.b64encode(b'\x89PNG\r\n\x1a\n').decode()}).status_code,400)

    def test_no_sql_injection_private_api_and_missing_routes(self):
        self.article(title="'; DROP TABLE users; --")
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
        visitor=self.app.test_client()
        for path in ['/api/admin/users','/api/admin/comments','/api/publisher/dashboard','/api/account/dashboard']:
            self.assertEqual(visitor.get(path).status_code,401)
        for path in ['/instance/site.sqlite','/../instance/site.sqlite','/backend/app.py','/.env']:
            self.assertEqual(visitor.get(path).status_code,404)
        self.assertEqual(self.call('/admin/not-a-resource').status_code,404)

    def test_security_headers_and_malformed_json(self):
        response=self.client.get('/')
        for key in ['X-Content-Type-Options','X-Frame-Options','Content-Security-Policy','Referrer-Policy']:self.assertIn(key,response.headers)
        response.close()
        self.assertEqual(self.client.post('/api/auth/register',data='broken',content_type='application/json',headers={'X-CSRF-Token':self.csrf}).status_code,400)

    def test_scheduler_uses_due_time_and_safe_retries(self):
        from test_p3 import iso
        data={'kind':'giveaway','status':'published','title':'Due draw','description':'Rules','cover':'assets/forest.svg','start':iso(now()-100),'end':iso(now()+20),'draw_at':iso(now()+30),'region':'ALL','min_age':18,'slots':1,'prize':'Demo test prize','questions':[]}
        id=self.call('/admin/activities',data=data).json['id'];args=SimpleNamespace(command='draw-due')
        with patch('backend.operations.now',return_value=now()+40),patch('backend.business.now',return_value=now()+40):operation(args,self.app,self.conn);operation(args,self.app,self.conn)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM draws').fetchone()[0],1)

    def test_public_cases_need_permission_and_team_is_editable(self):
        self.assertEqual(self.call('/admin/settings',method='PATCH',data={'cases':[{'title':'Partner','description':'Case','rights_confirmed':False}]}).status_code,400)
        self.assertEqual(self.call('/admin/settings',method='PATCH',data={'team':'Verified team','communities':'Verified community','cases':[{'title':'Partner','description':'Public case','rights_confirmed':True}]}).status_code,200)
        settings=self.client.get('/api/public').json['settings'];self.assertEqual(settings['team'],'Verified team');self.assertEqual(len(settings['cases']),1)

    def test_smtp_adapter_without_sending_real_mail(self):
        import os
        with patch.dict(os.environ,{'SMTP_HOST':'mail.test.invalid','SMTP_USER':'test','SMTP_PASSWORD':'synthetic-test-only','SMTP_FROM':'noreply@test.invalid'}),patch('backend.app.smtplib.SMTP') as smtp:
            self.app.config['MAIL_MODE']='smtp'
            r=self.call('/auth/register',data={'email':'smtp@test.invalid','password':'Testing-password-123','name':'SMTP Test','adult':True})
            self.assertEqual(r.status_code,202)
            sender=smtp.return_value.__enter__.return_value;sender.starttls.assert_called_once();sender.send_message.assert_called_once()
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)
