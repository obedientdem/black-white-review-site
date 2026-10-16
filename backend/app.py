"""Persistent single-host API. Run with a production WSGI server behind HTTPS."""
import base64
import functools
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import smtplib
import sqlite3
import time
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import urlparse

from flask import Flask, g, request, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, UnidentifiedImageError

ROOT=Path(__file__).resolve().parents[1]
ROLES={'player','publisher','editor','admin'}
def now(): return int(time.time())
def pack(value): return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def fail(message,status=400):
    from flask import abort
    abort(status,description=message)
def connect(path):
    db=sqlite3.connect(path,timeout=20,isolation_level=None)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    return db
def db():
    if 'db' not in g:g.db=connect(g.app.config['DATABASE'])
    return g.db
def text(data,key,minimum=1,maximum=500):
    value=data.get(key,'')
    if not isinstance(value,str) or not minimum<=len(value.strip())<=maximum:fail(f'Invalid {key}')
    return value.strip()
def email_value(data):
    value=text(data,'email',3,254).lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value):fail('Invalid email')
    return value
def password_value(data): return text(data,'password',12,128)
def time_value(value):
    try:return int(datetime.fromisoformat(value.replace('Z','+00:00')).timestamp())
    except (ValueError,TypeError,AttributeError):fail('Invalid timestamp; use ISO 8601 with timezone')
def require(*roles,verified=True):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args,**kwargs):
            if not g.user:fail('Sign in required',401)
            if verified and not g.user['verified']:fail('Verify your email first',403)
            if roles and g.user['role'] not in roles:fail('Insufficient permission',403)
            return fn(*args,**kwargs)
        return wrapped
    return decorate
def audit(action,target):db().execute('INSERT INTO audit(actor,action,target,created) VALUES(?,?,?,?)',(g.user['id'] if g.user else None,action,str(target),now()))
def public_user(user):return {k:user[k] for k in ['id','email','name','role','verified','active']} if user else None
def item(row):
    value=json.loads(row['data']);value['id']=row['id']
    for k in ['owner_id','kind','status','game_id']:
        if k in row.keys():value[k]=row[k]
    return value
def data_in():
    value=request.get_json(silent=True)
    if not isinstance(value,dict):fail('JSON object required')
    return value
def published_article(id):
    row=db().execute("SELECT * FROM articles WHERE id=? AND status='published'",(id,)).fetchone()
    if not row:fail('Article not found',404)
    return row
def limit(key,maximum,window=900):
    conn=db();stamp=now()
    conn.execute('INSERT INTO limits(key,count,expires) VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=CASE WHEN expires<=? THEN 1 ELSE count+1 END,expires=CASE WHEN expires<=? THEN ? ELSE expires END',(key,stamp+window,stamp,stamp,stamp+window))
    row=conn.execute('SELECT count FROM limits WHERE key=?',(key,)).fetchone()
    if row['count']>maximum:fail('Too many requests; try again later',429)
def rotate_session(user_id=None):
    if getattr(g,'session_hash',None):db().execute('DELETE FROM sessions WHERE token=?',(g.session_hash,))
    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
    db().execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(token),user_id,csrf,now()+86400))
    g.new_cookie=token;g.session_hash=digest(token);g.csrf=csrf
    return csrf
def send_token(user,kind):
    token=secrets.token_urlsafe(32)
    db().execute('UPDATE tokens SET used=1 WHERE user_id=? AND kind=?',(user['id'],kind))
    db().execute('INSERT INTO tokens VALUES(?,?,?,?,0)',(digest(token),user['id'],kind,now()+(86400 if kind=='verify' else 1800)))
    route='verify' if kind=='verify' else 'new-password'
    url=g.app.config['PUBLIC_URL'].rstrip('/')+'/#/'+route+'?token='+token
    subject='BW: Verify email' if kind=='verify' else 'BW: Reset password'
    if g.app.config['MAIL_MODE']=='outbox':
        db().execute('INSERT INTO outbox(user_id,subject,body,created) VALUES(?,?,?,?)',(user['id'],subject,url,now()))
    else:
        msg=EmailMessage();msg['From']=os.environ['SMTP_FROM'];msg['To']=user['email'];msg['Subject']=subject;msg.set_content(url)
        with smtplib.SMTP(os.environ['SMTP_HOST'],int(os.environ.get('SMTP_PORT','587')),timeout=15) as smtp:
            smtp.starttls();smtp.login(os.environ['SMTP_USER'],os.environ['SMTP_PASSWORD']);smtp.send_message(msg)

def validate_article(data):
    allowed={'title','title_en','summary','summary_en','body','body_en','game','game_en','score','category','platform','version','cover','pros','cons','author','published','source','source_url','demo'}
    result={k:v for k,v in data.items() if k in allowed}
    for k in ['title','body','cover','category']:result[k]=text(data,k,1,100000 if k=='body' else 500)
    for k,v in result.items():
        if k in ['pros','cons']:
            if not isinstance(v,list) or len(v)>30 or any(not isinstance(x,str) or len(x)>500 for x in v):fail('Invalid black/white points')
        elif k not in ['score','demo'] and not isinstance(v,str):fail(f'Invalid {k}')
    if not re.match(r'^(assets/[\w-]+\.svg|/uploads/[a-f0-9]+\.(png|jpg|webp)|https://)',result['cover']):fail('Unsafe cover URL')
    if data.get('kind')=='review':
        score=data.get('score')
        if isinstance(score,bool) or not isinstance(score,(int,float)) or not 0<=score<=10 or score*2%1:fail('Official score must be 0–10 in 0.5 steps')
        if not result.get('pros') or not result.get('cons'):fail('Review needs Pros and Cons')
    if result.get('published'):
        try:datetime.fromisoformat(result['published'])
        except ValueError:fail('Invalid publication date')
    result.setdefault('published',datetime.now().date().isoformat());result.setdefault('demo',False)
    return result

def create_app(config=None):
    app=Flask(__name__,static_folder=None)
    app.config.update(DATABASE=str(ROOT/'instance/site.sqlite'),PUBLIC_URL='http://127.0.0.1:8766',MAIL_MODE='outbox',PRODUCTION=False,MAX_CONTENT_LENGTH=5*1024*1024,TESTING=False)
    app.config.update(config or {})
    if app.config['PRODUCTION']:
        if not app.config['PUBLIC_URL'].startswith('https://') or app.config['MAIL_MODE']!='smtp':raise RuntimeError('Production requires HTTPS and SMTP')
        if not all(os.environ.get(k) for k in ['SMTP_HOST','SMTP_USER','SMTP_PASSWORD','SMTP_FROM','BW_SECRET']):raise RuntimeError('Missing production secrets')
    Path(app.config['DATABASE']).parent.mkdir(parents=True,exist_ok=True)
    conn=connect(app.config['DATABASE'])
    try:conn.executescript((ROOT/'backend/schema.sql').read_text(encoding='utf-8'))
    finally:conn.close()

    @app.before_request
    def before():
        g.app=app;g.user=None;g.session_hash=None;g.csrf=None
        token=request.cookies.get('bw_session','')
        if token:
            row=db().execute('SELECT * FROM sessions WHERE token=? AND expires>?',(digest(token),now())).fetchone()
            if row:
                g.session_hash=digest(token);g.csrf=row['csrf']
                if row['user_id']:g.user=db().execute('SELECT * FROM users WHERE id=? AND active=1',(row['user_id'],)).fetchone()
        if request.path.startswith('/api'):
            limit('ip:'+request.remote_addr,300,60)
            if request.method not in ['GET','HEAD','OPTIONS']:
                origin=request.headers.get('Origin')
                allowed=app.config['PUBLIC_URL'].rstrip('/')
                if origin and origin!=allowed:fail('Origin rejected',403)
                if not g.csrf or not hmac.compare_digest(request.headers.get('X-CSRF-Token',''),g.csrf):fail('CSRF rejected',403)

    @app.after_request
    def after(response):
        response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='same-origin'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' https:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        if app.config['PRODUCTION']:response.headers['Strict-Transport-Security']='max-age=31536000'
        if request.path.startswith('/api'):response.headers['Cache-Control']='no-store'
        if getattr(g,'new_cookie',None):response.set_cookie('bw_session',g.new_cookie,httponly=True,secure=app.config['PRODUCTION'],samesite='Lax',max_age=86400)
        return response

    @app.teardown_appcontext
    def close(error):
        conn=g.pop('db',None)
        if conn:conn.close()

    @app.errorhandler(HTTPException)
    def http_error(error):return jsonify(error=error.description),error.code
    @app.errorhandler(sqlite3.IntegrityError)
    def conflict(error):return jsonify(error='Conflicting or duplicate record'),409

    @app.get('/api/bootstrap')
    def bootstrap():
        if not g.csrf:rotate_session()
        return jsonify(csrf=g.csrf,user=public_user(g.user))
    @app.get('/api/me')
    def me():return jsonify(user=public_user(g.user))
    @app.get('/api/account/dashboard')
    @require(verified=False)
    def dashboard():
        result={}
        for table in ['favorites','ratings','comments','applications','entries','business','publisher_requests','awards','notifications']:
            values=[]
            for row in db().execute('SELECT * FROM '+table+' WHERE user_id=?',(g.user['id'],)):
                value=dict(row)
                if 'data' in value:value['data']=json.loads(value['data'])
                values.append(value)
            result[table]=values
        return jsonify(result)
    @app.post('/api/auth/register')
    def register():
        limit('register:'+request.remote_addr,10,3600);data=data_in();email=email_value(data);password=password_value(data);name=text(data,'name',1,60)
        if data.get('adult') is not True:fail('Only adults aged 18+ can register')
        if db().execute('SELECT id FROM users WHERE email=?',(email,)).fetchone():return jsonify(message='If eligible, check your email'),202
        cursor=db().execute('INSERT INTO users(email,password,name,adult,created) VALUES(?,?,?,1,?)',(email,generate_password_hash(password),name,now()))
        user=db().execute('SELECT * FROM users WHERE id=?',(cursor.lastrowid,)).fetchone();send_token(user,'verify')
        return jsonify(message='If eligible, check your email'),202
    @app.post('/api/auth/login')
    def login():
        data=data_in();email=email_value(data);password=text(data,'password',1,128);limit('login:'+request.remote_addr+':'+digest(email),8)
        user=db().execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        # Perform a password derivation even for unknown accounts.
        dummy=app.config.get('DUMMY_HASH')
        if not dummy:dummy=generate_password_hash(secrets.token_hex(20));app.config['DUMMY_HASH']=dummy
        valid=check_password_hash(user['password'] if user else dummy,password)
        if not user or not valid or not user['active']:fail('Invalid credentials',401)
        csrf=rotate_session(user['id']);return jsonify(user=public_user(user),csrf=csrf)
    @app.post('/api/auth/logout')
    def logout():return jsonify(csrf=rotate_session(),user=None)
    @app.post('/api/auth/verify')
    def verify():
        data=data_in();token=text(data,'token',20,200);conn=db();conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT * FROM tokens WHERE token=? AND kind='verify' AND used=0 AND expires>?",(digest(token),now())).fetchone()
        if not row:conn.rollback();fail('Invalid or expired verification token')
        conn.execute('UPDATE tokens SET used=1 WHERE token=?',(digest(token),));conn.execute('UPDATE users SET verified=1 WHERE id=?',(row['user_id'],));conn.commit();return jsonify(ok=True)
    @app.post('/api/auth/resend')
    @require(verified=False)
    def resend():
        limit('resend:'+str(g.user['id']),3,3600)
        if not g.user['verified']:send_token(g.user,'verify')
        return jsonify(ok=True)
    @app.post('/api/auth/reset-request')
    def reset_request():
        data=data_in();email=email_value(data);limit('reset:'+request.remote_addr,5,3600)
        user=db().execute('SELECT * FROM users WHERE email=? AND active=1',(email,)).fetchone()
        if user:send_token(user,'reset')
        return jsonify(message='If eligible, check your email')
    @app.post('/api/auth/reset')
    def reset_password():
        data=data_in();token=text(data,'token',20,200);password=password_value(data);hashed=generate_password_hash(password);conn=db();conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT * FROM tokens WHERE token=? AND kind='reset' AND used=0 AND expires>?",(digest(token),now())).fetchone()
        if not row:conn.rollback();fail('Invalid or expired reset token')
        conn.execute('UPDATE users SET password=? WHERE id=?',(hashed,row['user_id']));conn.execute('UPDATE tokens SET used=1 WHERE user_id=?',(row['user_id'],));conn.execute('DELETE FROM sessions WHERE user_id=?',(row['user_id'],));conn.commit();return jsonify(ok=True)
    @app.patch('/api/account')
    @require(verified=False)
    def edit_account():
        name=text(data_in(),'name',1,60);db().execute('UPDATE users SET name=? WHERE id=?',(name,g.user['id']));return jsonify(ok=True)
    @app.delete('/api/account')
    @require(verified=False)
    def delete_account():
        data=data_in()
        if not check_password_hash(g.user['password'],text(data,'password',1,128)):fail('Invalid password',403)
        if g.user['role']=='admin':fail('Transfer administration before deleting admin account',409)
        uid=g.user['id']
        if db().execute('SELECT 1 FROM activities WHERE owner_id=?',(uid,)).fetchone():fail('Ask an administrator to transfer activities first',409)
        db().execute('UPDATE articles SET owner_id=NULL WHERE owner_id=?',(uid,));audit('account-delete',uid);db().execute('DELETE FROM users WHERE id=?',(uid,));return jsonify(csrf=rotate_session(),ok=True)

    @app.get('/api/public')
    def public():
        setting=db().execute('SELECT data FROM settings WHERE id=1').fetchone()
        articles=[]
        for row in db().execute("SELECT * FROM articles WHERE status='published'"):
            value=item(row);value.pop('owner_id',None);value['views30']=db().execute('SELECT COUNT(*) FROM views WHERE article_id=? AND created>=?',(row['id'],now()-30*86400)).fetchone()[0];articles.append(value)
        activities=[item(row) for row in db().execute("SELECT * FROM activities WHERE status='published'")]
        for value in activities:value.pop('owner_id',None)
        return jsonify(settings=json.loads(setting[0]) if setting else {},games=[item(r) for r in db().execute('SELECT * FROM games')],articles=articles,activities=activities)
    @app.post('/api/articles/<int:id>/view')
    def view(id):
        published_article(id);visitor=g.session_hash
        db().execute('INSERT OR IGNORE INTO views VALUES(?,?,?,?)',(id,visitor,now()//86400,now()))
        return jsonify(ok=True)

    @app.get('/api/admin/<resource>')
    @require('admin','editor')
    def admin_list(resource):
        if resource in ['articles','games']:
            return jsonify(rows=[item(r) for r in db().execute('SELECT * FROM '+resource)])
        if g.user['role']!='admin':fail('Insufficient permission',403)
        if resource=='settings':
            row=db().execute('SELECT data FROM settings WHERE id=1').fetchone();return jsonify(data=json.loads(row[0]) if row else {})
        if resource=='users':return jsonify(rows=[public_user(r) for r in db().execute('SELECT * FROM users')])
        if resource=='audit':return jsonify(rows=[dict(r) for r in db().execute('SELECT * FROM audit ORDER BY id DESC LIMIT 500')])
        fail('Unknown resource',404)
    @app.route('/api/admin/articles',methods=['POST'])
    @app.route('/api/admin/articles/<int:id>',methods=['PATCH','DELETE'])
    @require('admin','editor')
    def admin_articles(id=None):
        row=db().execute('SELECT * FROM articles WHERE id=?',(id,)).fetchone() if id else None
        if id and not row:fail('Article not found',404)
        if request.method=='DELETE':db().execute('UPDATE articles SET status=? WHERE id=?',('archived',id));audit('article-archive',id);return jsonify(ok=True)
        data=data_in();kind=data.get('kind','review');status=data.get('status','draft')
        if kind not in ['review','news'] or status not in ['draft','published','archived']:fail('Invalid article state')
        value=validate_article({**data,'kind':kind});game_id=data.get('game_id') or None
        if game_id and not db().execute('SELECT id FROM games WHERE id=?',(game_id,)).fetchone():fail('Game not found')
        if id:db().execute('UPDATE articles SET game_id=?,kind=?,status=?,data=? WHERE id=?',(game_id,kind,status,pack(value),id))
        else:id=db().execute('INSERT INTO articles(game_id,owner_id,kind,status,data) VALUES(?,?,?,?,?)',(game_id,g.user['id'],kind,status,pack(value))).lastrowid
        audit('article-save',id);return jsonify(id=id)
    @app.route('/api/admin/games',methods=['POST'])
    @app.route('/api/admin/games/<int:id>',methods=['PATCH','DELETE'])
    @require('admin','editor')
    def admin_games(id=None):
        if id and not db().execute('SELECT id FROM games WHERE id=?',(id,)).fetchone():fail('Game not found',404)
        if request.method=='DELETE':
            if db().execute('SELECT 1 FROM articles WHERE game_id=?',(id,)).fetchone():fail('Game is referenced by an article',409)
            db().execute('DELETE FROM games WHERE id=?',(id,));audit('game-delete',id);return jsonify(ok=True)
        data=data_in();value={k:text(data,k,0,1000) for k in ['title','developer','publisher','release','genre','store']};platforms=data.get('platforms',[])
        if not value['title'] or not isinstance(platforms,list) or any(not isinstance(p,str) or len(p)>50 for p in platforms):fail('Invalid game')
        value['platforms']=platforms
        if value['store'] and not value['store'].startswith('https://'):fail('Store URL must use HTTPS')
        if id:db().execute('UPDATE games SET data=? WHERE id=?',(pack(value),id))
        else:id=db().execute('INSERT INTO games(data) VALUES(?)',(pack(value),)).lastrowid
        audit('game-save',id);return jsonify(id=id)
    @app.patch('/api/admin/settings')
    @require('admin')
    def admin_settings():
        data=data_in();allowed=['brand','tagline','tagline_en','intro','intro_en','impact','reviews','groups','metric_note','metric_note_en','updated','contact','hero_id']
        if any(k not in allowed for k in data):fail('Unknown setting')
        value={k:text(data,k,0,4000) for k in data if k!='hero_id'}
        if 'hero_id' in data:
            if not isinstance(data['hero_id'],int):fail('Invalid hero_id')
            value['hero_id']=data['hero_id']
        previous=db().execute('SELECT data FROM settings WHERE id=1').fetchone();merged={**(json.loads(previous[0]) if previous else {}),**value}
        db().execute('INSERT INTO settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(pack(merged),));audit('settings-save',1);return jsonify(ok=True)
    @app.patch('/api/admin/users/<int:id>')
    @require('admin')
    def admin_users(id):
        data=data_in();row=db().execute('SELECT * FROM users WHERE id=?',(id,)).fetchone()
        if not row:fail('User not found',404)
        role=data.get('role',row['role']);active=data.get('active',row['active'])
        if role not in ROLES or active not in [0,1]:fail('Invalid role or active state')
        if id==g.user['id'] and (role!='admin' or active!=1):fail('Cannot demote or suspend your own administrator',409)
        db().execute('UPDATE users SET role=?,active=? WHERE id=?',(role,active,id));db().execute('DELETE FROM sessions WHERE user_id=?',(id,));audit('user-permission',id);return jsonify(ok=True)
    @app.post('/api/admin/upload')
    @require('admin','editor')
    def upload():
        data=data_in();raw=text(data,'base64',1,7_000_000)
        try:blob=base64.b64decode(raw,validate=True)
        except ValueError:fail('Invalid base64')
        if len(blob)>4*1024*1024:fail('Image too large',413)
        # SVG/HTML are intentionally rejected. Only raster image signatures accepted.
        ext='png' if blob.startswith(b'\x89PNG\r\n\x1a\n') else 'jpg' if blob.startswith(b'\xff\xd8\xff') else 'webp' if blob.startswith(b'RIFF') and blob[8:12]==b'WEBP' else None
        if not ext:fail('Only PNG, JPEG and WebP images are accepted')
        try:
            with Image.open(io.BytesIO(blob)) as probe:
                if probe.width*probe.height>20_000_000:fail('Image dimensions too large')
                probe.verify()
            with Image.open(io.BytesIO(blob)) as source:
                clean=source.convert('RGB');output=io.BytesIO();clean.save(output,format='WEBP',quality=88);blob=output.getvalue();ext='webp'
        except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):fail('Corrupt or unsafe image')
        directory=Path(app.config['DATABASE']).parent/'uploads';directory.mkdir(exist_ok=True);name=secrets.token_hex(24)+'.'+ext;(directory/name).write_bytes(blob);audit('upload',name);return jsonify(url='/uploads/'+name)
    @app.get('/uploads/<path:name>')
    def uploaded(name):return send_from_directory(Path(app.config['DATABASE']).parent/'uploads',name)
    @app.get('/')
    def index():return send_from_directory(ROOT/'site','index.html')
    @app.get('/<path:name>')
    def static(name):return send_from_directory(ROOT/'site',name)

    return app
