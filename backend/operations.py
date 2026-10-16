import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile
from backend.app import ROOT,connect,now,pack,validate_article
from backend.business import run_draw

def backup_database(conn,source_path,destination):
    destination=Path(destination)
    if destination.exists():raise ValueError('Refusing to overwrite a backup')
    destination.parent.mkdir(parents=True,exist_ok=True)
    # SQLite online backup includes committed WAL changes.
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        snapshot=Path(tmp)/'site.sqlite';target=sqlite3.connect(snapshot)
        try:conn.backup(target)
        finally:target.close()
        with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot,'site.sqlite')
            uploads=Path(source_path).parent/'uploads'
            if uploads.exists():
                for path in uploads.glob('*'):
                    if path.is_file():archive.write(path,'uploads/'+path.name)
    return destination

def restore_archive(source,database):
    target=Path(database).resolve()
    if target.exists():raise ValueError('Restore requires a new database path; never overwrite a live database')
    target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        if 'site.sqlite' not in archive.namelist():raise ValueError('Database missing in backup')
        for info in archive.infolist():
            name=Path(info.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in info.filename:raise ValueError('Unsafe backup path')
            if info.filename!='site.sqlite' and not (len(name.parts)==2 and name.parts[0]=='uploads'):raise ValueError('Unexpected backup content')
            if info.file_size>100*1024*1024:raise ValueError('Backup member exceeds size limit')
        with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
            # Paths are checked before extracting any files.
            archive.extractall(tmp)
            check=connect(str(Path(tmp)/'site.sqlite'))
            try:
                if check.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup failed integrity validation')
                if check.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Backup failed foreign-key validation')
            finally:check.close()
            shutil.copy2(Path(tmp)/'site.sqlite',target)
            if (Path(tmp)/'uploads').exists():shutil.copytree(Path(tmp)/'uploads',target.parent/'uploads',dirs_exist_ok=True)

def import_content(app,conn,source):
    rows=json.loads(Path(source).read_text(encoding='utf-8'))
    if not isinstance(rows,list) or not 1<=len(rows)<=50:raise ValueError('Import 1–50 selected historical articles at a time')
    prepared=[]
    with app.test_request_context():
        for row in rows:
            if not isinstance(row,dict) or row.get('kind') not in ['review','news']:raise ValueError('Invalid content')
            if not row.get('source') or not row.get('source_url','').startswith('https://') or row.get('rights_confirmed') is not True:raise ValueError('Source, HTTPS source URL and confirmed image/content rights are required')
            value=validate_article(row);value['demo']=False;prepared.append((row['kind'],pack(value)))
    conn.execute('BEGIN IMMEDIATE')
    try:
        for kind,value in prepared:conn.execute("INSERT INTO articles(kind,status,data) VALUES(?,'draft',?)",(kind,value))
        conn.execute('INSERT INTO audit(action,target,created) VALUES(?,?,?)',('content-import',str(len(rows)),now()));conn.commit()
    except:conn.rollback();raise
    return len(prepared)

def export_static(app,destination):
    target=Path(destination).resolve()
    if target.exists() and any(target.iterdir()):raise ValueError('Export directory must be new or empty')
    source=(ROOT/'site').resolve()
    if target==source or source in target.parents:raise ValueError('Cannot export into the source site')
    target.mkdir(parents=True,exist_ok=True)
    with app.test_client() as client:
        response=client.get('/api/public')
        if response.status_code!=200:raise ValueError('Public data export failed')
        data=response.json
    shutil.copytree(source,target,dirs_exist_ok=True)
    uploads=Path(app.config['DATABASE']).parent/'uploads'
    for row in data['articles']+data['activities']:
        if row.get('cover','').startswith('/uploads/'):
            name=Path(row['cover']).name
            if not (uploads/name).is_file():raise ValueError('Referenced upload is missing')
            (target/'assets/uploads').mkdir(parents=True,exist_ok=True);shutil.copy2(uploads/name,target/'assets/uploads'/name);row['cover']='assets/uploads/'+name
        for key in ['body','body_en']:
            import re
            def copy_inline(match):
                name=match.group(1)
                if not (uploads/name).is_file():raise ValueError('Referenced inline image is missing')
                (target/'assets/uploads').mkdir(parents=True,exist_ok=True);shutil.copy2(uploads/name,target/'assets/uploads'/name);return '(assets/uploads/'+name+')'
            if row.get(key):row[key]=re.sub(r'\(/uploads/([a-f0-9]+\.(?:png|jpg|webp))\)',copy_inline,row[key])
    encoded=pack(data).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    (target/'data.js').write_text('window.BW_DEMO='+encoded+';\n',encoding='utf-8')
    (target/'demo.json').unlink(missing_ok=True)
    (target/'.nojekyll').write_text('',encoding='utf-8')
    return target

def operation(args,app,conn):
    if args.command=='draw-due':
        count=0;failed=[]
        for row in conn.execute("SELECT id,data FROM activities WHERE kind='giveaway' AND status='published'").fetchall():
            from backend.app import time_value
            if time_value(json.loads(row['data'])['draw_at'])<=now():
                try:run_draw(conn,row['id']);count+=1
                except ValueError as error:failed.append((row['id'],str(error)))
        # Bound housekeeping data; do not modify the audit trail.
        conn.execute('DELETE FROM sessions WHERE expires<?',(now(),));conn.execute('DELETE FROM limits WHERE expires<?',(now(),));conn.execute('DELETE FROM tokens WHERE expires<?',(now()-86400,))
        print(f'Due draws processed: {count}; failures: {failed}')
        if failed:raise SystemExit(1)
    elif args.command=='backup':
        if not args.file:raise ValueError('--file required (private .zip destination)')
        print(backup_database(conn,args.database,args.file))
    elif args.command=='import-content':
        if not args.file:raise ValueError('--file required (selected article JSON)')
        print(f'Imported drafts: {import_content(app,conn,args.file)}')
    elif args.command=='export-static':
        if not args.file:raise ValueError('--file required (empty directory)')
        print(export_static(app,args.file))
