import json
from pathlib import Path
import tempfile
import unittest
from backend.app import create_app,connect,now,pack,digest
from werkzeug.security import generate_password_hash

class ApiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.password_hash=generate_password_hash('Testing-password-123')
    def setUp(self):
        directory=Path(__file__).resolve().parents[1]/'test-output';directory.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=directory);self.path=str(Path(self.tmp.name)/'test.sqlite')
        self.app=create_app({'DATABASE':self.path,'TESTING':True});self.conn=connect(self.path);self.client=self.app.test_client()
        self.csrf=self.client.get('/api/bootstrap').json['csrf']
        self.uid=self.user('admin@test.invalid','admin');self.login('admin@test.invalid')
    def tearDown(self):self.conn.close();self.tmp.cleanup()
    def user(self,email,role='player',verified=1):
        return self.conn.execute('INSERT INTO users(email,password,name,role,verified,adult,created) VALUES(?,?,?,?,?,1,?)',(email,self.password_hash,email.split('@')[0],role,verified,now())).lastrowid
    def login(self,email,client=None):
        client=client or self.client;csrf=client.get('/api/bootstrap').json['csrf'];r=client.post('/api/auth/login',json={'email':email,'password':'Testing-password-123'},headers={'X-CSRF-Token':csrf})
        self.assertEqual(r.status_code,200,r.json)
        if client is self.client:self.csrf=r.json['csrf']
        return r.json['csrf']
    def call(self,path,method='POST',data=None,client=None,csrf=None):return (client or self.client).open('/api'+path,method=method,json=data or {},headers={'X-CSRF-Token':csrf or self.csrf,'Origin':'http://127.0.0.1:8766'})
    def article(self,**changes):
        data={'kind':'review','status':'published','title':'Test Review','body':'Original review text','cover':'assets/embers.svg','category':'RPG','platform':'PC','score':9,'pros':['Strong world'],'cons':['Slow start'],'published':'2026-10-16','source':'Original test'};data.update(changes)
        r=self.call('/admin/articles',data=data);self.assertEqual(r.status_code,200,r.json);return r.json['id']
