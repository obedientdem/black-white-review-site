"""Moderation, private business workflows and transactional giveaway draws."""
import hashlib
import hmac
import json
import secrets
from flask import g,request,jsonify
from backend.app import db,now,pack,fail,require,data_in,text,item,audit,published_article,limit,time_value

def configure_draw(conn,activity_id):
    seed=secrets.token_hex(32)
    conn.execute('INSERT OR IGNORE INTO draw_secrets VALUES(?,?,?)',(activity_id,seed,hashlib.sha256(bytes.fromhex(seed)).hexdigest()))

def run_draw(conn,activity_id,at=None):
    stamp=now() if at is None else at
    conn.execute('BEGIN IMMEDIATE')
    try:
        existing=conn.execute('SELECT * FROM draws WHERE activity_id=?',(activity_id,)).fetchone()
        if existing:conn.commit();return dict(existing)
        activity=conn.execute("SELECT * FROM activities WHERE id=? AND kind='giveaway' AND status='published'",(activity_id,)).fetchone()
        if not activity:raise ValueError('Published giveaway not found')
        data=json.loads(activity['data']);cutoff=time_value(data['end']);draw_at=time_value(data['draw_at'])
        if stamp<draw_at or stamp<cutoff:raise ValueError('Draw is not due')
        config=conn.execute('SELECT * FROM draw_secrets WHERE activity_id=?',(activity_id,)).fetchone()
        if not config:raise ValueError('Draw commitment missing; cannot draw uncommitted activity')
        # Qualification was enforced at entry. Account edits after the deadline do
        # not retroactively change the fixed eligible entry set.
        rows=conn.execute('SELECT id,user_id FROM entries WHERE activity_id=? AND created<? ORDER BY id',(activity_id,cutoff)).fetchall()
        snapshot=[row['id'] for row in rows]
        seed=bytes.fromhex(config['seed'])
        ordered=sorted(rows,key=lambda r:hmac.new(seed,str(r['id']).encode(),hashlib.sha256).digest())
        winners=[r['id'] for r in ordered[:int(data['slots'])]]
        conn.execute('INSERT INTO draws VALUES(?,?,?,?,?,?)',(activity_id,pack(snapshot),pack(winners),config['commitment'],config['seed'],stamp))
        for row in ordered[:int(data['slots'])]:
            if row['user_id'] is None:continue
            conn.execute('INSERT INTO awards(activity_id,user_id,updated) VALUES(?,?,?)',(activity_id,row['user_id'],stamp))
            conn.execute('INSERT INTO notifications(user_id,body,created) VALUES(?,?,?)',(row['user_id'],f'活动 #{activity_id} 已中奖 / Winner. Await manual fulfillment.',stamp))
        conn.execute('INSERT INTO audit(action,target,created) VALUES(?,?,?)',('giveaway-draw',str(activity_id),stamp))
        conn.commit();return dict(conn.execute('SELECT * FROM draws WHERE activity_id=?',(activity_id,)).fetchone())
    except:conn.rollback();raise

def validate_activity(data):
    result={k:text(data,k,0,20000 if k.startswith('description') else 1000) for k in ['title','title_en','description','description_en','cover','start','end','draw_at','region','prize']}
    if not result['title'] or not result['description']:fail('Title and rules are required')
    if not result['cover'].startswith(('assets/','/uploads/','https://')):fail('Unsafe cover URL')
    for key in ['start','end','draw_at']:
        if not result[key].endswith('Z') and not __import__('re').search(r'[+-]\d\d:\d\d$',result[key]):fail('Timestamps must include timezone')
    start,end,draw=[time_value(result[k]) for k in ['start','end','draw_at']]
    if not start<end<=draw:fail('Expected start < end <= draw time')
    for key,low,high in [('slots',1,10000),('min_age',18,120)]:
        v=data.get(key)
        if isinstance(v,bool) or not isinstance(v,int) or not low<=v<=high:fail('Invalid '+key)
        result[key]=v
    questions=data.get('questions',[])
    if not isinstance(questions,list) or len(questions)>10 or any(not isinstance(q,str) or len(q)>300 for q in questions):fail('Invalid questions')
    if not result['region']:fail('Region must be ALL or a country code')
    result['questions']=questions;result['demo']=bool(data.get('demo',False));return result

def owned_activity(id):
    row=db().execute('SELECT * FROM activities WHERE id=? AND owner_id=?',(id,g.user['id'])).fetchone()
    if not row:fail('Activity not found',404)
    return row

def register_business(app):
    @app.get('/api/articles/<int:id>/interactions')
    def interactions(id):
        published_article(id)
        score=db().execute('SELECT AVG(score),COUNT(*) FROM ratings WHERE article_id=?',(id,)).fetchone()
        uid=g.user['id'] if g.user else -1
        comments=[dict(r) for r in db().execute("SELECT c.id,c.body,c.status,u.name FROM comments c JOIN users u ON c.user_id=u.id WHERE article_id=? AND (status='approved' OR (c.user_id=? AND status!='deleted')) ORDER BY c.id DESC",(id,uid))]
        return jsonify(average=score[0],count=score[1],comments=comments)
    @app.post('/api/articles/<int:id>/rating')
    @require()
    def rating(id):
        row=published_article(id)
        if row['kind']!='review':fail('Only reviews accept scores')
        score=data_in().get('score')
        if isinstance(score,bool) or not isinstance(score,(int,float)) or not 0<=score<=10 or score*2%1:fail('Score must be 0–10 in 0.5 steps')
        limit('rating:'+str(g.user['id']),20,60)
        db().execute('INSERT INTO ratings VALUES(?,?,?,?) ON CONFLICT(user_id,article_id) DO UPDATE SET score=excluded.score,updated=excluded.updated',(g.user['id'],id,score,now()));return jsonify(ok=True)
    @app.post('/api/articles/<int:id>/favorite')
    @require()
    def favorite(id):
        published_article(id);uid=g.user['id'];conn=db();conn.execute('BEGIN IMMEDIATE')
        exists=conn.execute('SELECT 1 FROM favorites WHERE user_id=? AND article_id=?',(uid,id)).fetchone()
        if exists:conn.execute('DELETE FROM favorites WHERE user_id=? AND article_id=?',(uid,id))
        else:conn.execute('INSERT INTO favorites VALUES(?,?)',(uid,id))
        conn.commit();return jsonify(saved=not bool(exists))
    @app.post('/api/articles/<int:id>/comments')
    @require()
    def comment(id):
        published_article(id);body=text(data_in(),'body',1,3000);limit('comment:'+str(g.user['id']),5,300)
        id=db().execute('INSERT INTO comments(user_id,article_id,body,created) VALUES(?,?,?,?)',(g.user['id'],id,body,now())).lastrowid;return jsonify(id=id,status='pending')
    @app.post('/api/publisher/apply')
    @require()
    def publisher_apply():
        data=data_in();value={k:text(data,k,1,3000 if k=='proof' else 500) for k in ['company','game','contact','proof']}
        if db().execute("SELECT 1 FROM publisher_requests WHERE user_id=? AND status='pending'",(g.user['id'],)).fetchone():fail('Pending request already exists',409)
        id=db().execute('INSERT INTO publisher_requests(user_id,data,created) VALUES(?,?,?)',(g.user['id'],pack(value),now())).lastrowid;return jsonify(id=id)
    @app.post('/api/admin/publisher-invitations')
    @require('admin')
    def publisher_invitation():
        data=data_in();email=text(data,'email',3,254).lower();user=db().execute("SELECT * FROM users WHERE email=? AND active=1 AND verified=1 AND role='player'",(email,)).fetchone()
        if not user:fail('Invitation requires an existing verified player',404)
        value={'company':text(data,'company',1,500),'game':text(data,'game',1,500),'contact':email,'proof':'Admin-initiated invitation; certification still pending'}
        if db().execute("SELECT 1 FROM publisher_requests WHERE user_id=? AND status='pending'",(user['id'],)).fetchone():fail('Pending certification already exists',409)
        id=db().execute('INSERT INTO publisher_requests(user_id,data,created) VALUES(?,?,?)',(user['id'],pack(value),now())).lastrowid
        db().execute('INSERT INTO notifications(user_id,body,created) VALUES(?,?,?)',(user['id'],'BW publisher invitation created; awaiting super administrator certification.',now()));audit('publisher-invitation',id);return jsonify(id=id)
    @app.post('/api/business')
    @require()
    def business_apply():
        data=data_in();value={k:text(data,k,1,5000 if k=='message' else 500) for k in ['company','game','contact','message']};limit('business:'+str(g.user['id']),5,3600)
        id=db().execute('INSERT INTO business(user_id,data,created) VALUES(?,?,?)',(g.user['id'],pack(value),now())).lastrowid;return jsonify(id=id)
    @app.get('/api/publisher/dashboard')
    @require('publisher')
    def publisher_dashboard():
        activities=[item(r) for r in db().execute('SELECT * FROM activities WHERE owner_id=?',(g.user['id'],))]
        business=[{**dict(r),'data':json.loads(r['data'])} for r in db().execute('SELECT * FROM business WHERE user_id=?',(g.user['id'],))]
        referrals=[{**dict(r),'data':json.loads(r['data'])} for r in db().execute("SELECT p.id,p.activity_id,p.data,u.name,u.email FROM applications p JOIN activities a ON a.id=p.activity_id JOIN users u ON u.id=p.user_id WHERE a.owner_id=? AND p.consent=1 AND p.status='referred'",(g.user['id'],))]
        return jsonify(activities=activities,business=business,referrals=referrals)
    @app.route('/api/publisher/activities',methods=['POST'])
    @app.route('/api/publisher/activities/<int:id>',methods=['PATCH'])
    @require('publisher')
    def publisher_activity(id=None):
        row=owned_activity(id) if id else None
        if row and row['status'] not in ['draft','rejected']:fail('Only draft or rejected events can be edited',409)
        data=data_in();kind=data.get('kind','recruitment')
        if kind not in ['recruitment','giveaway']:fail('Invalid event type')
        value=validate_activity(data)
        if id:db().execute("UPDATE activities SET kind=?,status='draft',data=? WHERE id=?",(kind,pack(value),id))
        else:id=db().execute("INSERT INTO activities(owner_id,kind,status,data) VALUES(?,?,'draft',?)",(g.user['id'],kind,pack(value))).lastrowid
        audit('publisher-activity-draft',id);return jsonify(id=id,status='draft')
    @app.post('/api/publisher/activities/<int:id>/submit')
    @require('publisher')
    def submit_activity(id):
        row=owned_activity(id)
        if row['status'] not in ['draft','rejected']:fail('Invalid transition',409)
        db().execute("UPDATE activities SET status='pending' WHERE id=?",(id,));audit('activity-submit',id);return jsonify(ok=True)
    @app.route('/api/admin/activities',methods=['POST'])
    @app.route('/api/admin/activities/<int:id>',methods=['PATCH','DELETE'])
    @require('admin')
    def admin_activity(id=None):
        row=db().execute('SELECT * FROM activities WHERE id=?',(id,)).fetchone() if id else None
        if id and not row:fail('Activity not found',404)
        if request.method=='DELETE':
            if db().execute('SELECT 1 FROM entries WHERE activity_id=?',(id,)).fetchone():fail('Cannot archive a giveaway with entries; preserve draw obligations',409)
            db().execute("UPDATE activities SET status='archived' WHERE id=?",(id,));audit('activity-archive',id);return jsonify(ok=True)
        data=data_in();kind=data.get('kind','recruitment');status=data.get('status','draft')
        if kind not in ['recruitment','giveaway'] or status not in ['draft','pending','rejected','published','archived']:fail('Invalid event state')
        value=validate_activity(data)
        conn=db();conn.execute('BEGIN IMMEDIATE')
        if row:
            previous=json.loads(row['data']);has_entries=conn.execute('SELECT 1 FROM entries WHERE activity_id=? UNION ALL SELECT 1 FROM applications WHERE activity_id=?',(id,id)).fetchone()
            protected=['start','end','draw_at','region','min_age','slots','questions','prize']
            if has_entries and (kind!=row['kind'] or status!=row['status'] or any(previous.get(k)!=value.get(k) for k in protected)):
                conn.rollback();fail('Participation rules are locked after the first entry',409)
            conn.execute('UPDATE activities SET kind=?,status=?,data=? WHERE id=?',(kind,status,pack(value),id))
        else:id=conn.execute('INSERT INTO activities(owner_id,kind,status,data) VALUES(?,?,?,?)',(g.user['id'],kind,status,pack(value))).lastrowid
        if kind=='giveaway' and status=='published':configure_draw(conn,id)
        audit('activity-save',id);conn.commit();return jsonify(id=id)

    @app.post('/api/activities/<int:id>/enter')
    @require()
    def enter(id):
        data=data_in();conn=db();conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT * FROM activities WHERE id=? AND status='published'",(id,)).fetchone()
        if not row:conn.rollback();fail('Event not found',404)
        a=json.loads(row['data']);stamp=now()
        if a.get('demo'):conn.rollback();fail('Demonstration events do not accept real entries')
        if not time_value(a['start'])<=stamp<time_value(a['end']):conn.rollback();fail('Event is not accepting entries',409)
        # V1 supports adult-only registration; stricter age gates need a verified
        # age provider rather than accepting an unverified numeric age field.
        if a['min_age']>18:conn.rollback();fail('This event requires additional age verification unavailable in V1',403)
        region=text(data,'region',2,10).upper()
        if a['region']!='ALL' and region!=a['region'].upper():conn.rollback();fail('Region is ineligible',403)
        if data.get('consent') is not True:conn.rollback();fail('Event processing consent is required')
        answers=data.get('answers',[])
        if not isinstance(answers,list) or len(answers)!=len(a['questions']) or any(not isinstance(x,str) or not 1<=len(x)<=1000 for x in answers):conn.rollback();fail('Answer all event questions')
        value={'region':region,'answers':answers,'message':text(data,'message',0,2000),'processing_consent':True}
        table='entries' if row['kind']=='giveaway' else 'applications'
        if table=='entries' and not conn.execute('SELECT 1 FROM draw_secrets WHERE activity_id=?',(id,)).fetchone():conn.rollback();fail('Draw commitment is missing',409)
        cursor=conn.execute('INSERT INTO '+table+'(user_id,activity_id,data,created) VALUES(?,?,?,?)',(g.user['id'],id,pack(value),stamp));conn.commit();return jsonify(id=cursor.lastrowid)
    @app.post('/api/applications/<int:id>/consent')
    @require()
    def referral_consent(id):
        row=db().execute('SELECT * FROM applications WHERE id=? AND user_id=?',(id,g.user['id'])).fetchone()
        if not row:fail('Application not found',404)
        db().execute('UPDATE applications SET consent=? WHERE id=?',(0 if row['consent'] else 1,id));audit('referral-consent',id);return jsonify(ok=True)
    @app.get('/api/activities/<int:id>/results')
    def results(id):
        activity=db().execute("SELECT id FROM activities WHERE id=? AND status='published'",(id,)).fetchone()
        if not activity:fail('Event not found',404)
        config=db().execute('SELECT commitment FROM draw_secrets WHERE activity_id=?',(id,)).fetchone()
        row=db().execute('SELECT * FROM draws WHERE activity_id=?',(id,)).fetchone()
        if not row:return jsonify(drawn=False,commitment=config['commitment'] if config else None)
        winners=json.loads(row['winners']);return jsonify(drawn=True,winners=[{'entry_id':i,'label':f'BW-{hashlib.sha256(str(id).encode()+b":"+str(i).encode()).hexdigest()[:8]}'} for i in winners],snapshot=json.loads(row['snapshot']),winner_ids=winners,seed=row['seed'],commitment=row['commitment'],created=row['created'],algorithm='HMAC-SHA256(seed, decimal entry ID), ascending digest; take configured winner count')
    @app.post('/api/admin/activities/<int:id>/draw')
    @require('admin')
    def draw(id):
        try:run_draw(db(),id)
        except ValueError as e:fail(str(e),409)
        return jsonify(ok=True)

    @app.get('/api/workflow-admin/<resource>')
    @require('admin')
    def workflow_list(resource):
        if resource=='activities':return jsonify(rows=[item(r) for r in db().execute('SELECT * FROM activities')])
        if resource not in ['comments','publisher_requests','business','applications','awards']:fail('Unknown resource',404)
        rows=[]
        for row in db().execute('SELECT * FROM '+resource+' ORDER BY id DESC'):
            value=dict(row)
            if 'data' in value:value['data']=json.loads(value['data'])
            if resource=='comments':value['name']=db().execute('SELECT name FROM users WHERE id=?',(row['user_id'],)).fetchone()[0]
            rows.append(value)
        return jsonify(rows=rows)
    @app.patch('/api/admin/<resource>/<int:id>')
    @require('admin')
    def workflow_update(resource,id):
        if resource not in ['comments','publisher_requests','business','applications','awards']:fail('Unknown resource',404)
        conn=db();data=data_in();status=data.get('status')
        states={'comments':['approved','rejected','deleted'],'publisher_requests':['approved','rejected'],'business':['pending','negotiating','cooperated','declined'],'applications':['pending','selected','rejected','referred'],'awards':['pending','sent']}
        if status not in states[resource]:fail('Invalid workflow state')
        conn.execute('BEGIN IMMEDIATE');row=conn.execute('SELECT * FROM '+resource+' WHERE id=?',(id,)).fetchone()
        if not row:conn.rollback();fail('Record not found',404)
        if resource=='applications' and status=='referred' and not row['consent']:conn.rollback();fail('Separate referral consent is required',403)
        if resource=='publisher_requests':
            if row['status']!='pending':conn.rollback();fail('Certification request already decided',409)
            if status=='approved':
                user=conn.execute('SELECT role FROM users WHERE id=?',(row['user_id'],)).fetchone()
                if user['role']!='player':conn.rollback();fail('Only player accounts can become publishers',409)
                conn.execute("UPDATE users SET role='publisher' WHERE id=?",(row['user_id'],));conn.execute('DELETE FROM sessions WHERE user_id=?',(row['user_id'],))
        if resource=='business':conn.execute('UPDATE business SET notes=? WHERE id=?',(text(data,'notes',0,10000),id))
        if resource=='awards':
            if row['status']=='sent' and status!='sent':conn.rollback();fail('Sent award cannot return to pending',409)
            conn.execute('UPDATE awards SET note=?,updated=? WHERE id=?',(text(data,'note',0,2000),now(),id))
        conn.execute('UPDATE '+resource+' SET status=? WHERE id=?',(status,id));audit(resource+'-'+status,id);conn.commit();return jsonify(ok=True)
