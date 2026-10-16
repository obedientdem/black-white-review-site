import argparse
import getpass
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app import create_app,connect,pack
from werkzeug.security import generate_password_hash

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['serve','worker','create-admin','seed-demo','outbox','draw-due','backup','restore','import-content','export-static']);parser.add_argument('--file');parser.add_argument('--database',default=os.environ.get('BW_DATABASE',str(Path(__file__).resolve().parents[1]/'instance/site.sqlite')));args=parser.parse_args()
    production=os.environ.get('BW_ENV')=='production'
    if args.command=='restore':
        from backend.operations import restore_archive
        if not args.file:raise SystemExit('--file required (private backup .zip)')
        restore_archive(args.file,args.database);print('Restored to a new database path.');return
    app=create_app({'DATABASE':args.database,'PUBLIC_URL':os.environ.get('BW_PUBLIC_URL','http://127.0.0.1:8766'),'PRODUCTION':production,'MAIL_MODE':'smtp' if production else 'outbox'})
    conn=connect(args.database)
    if args.command=='serve':
        from waitress import serve
        if not production:
            import threading
            from backend.scheduler import worker
            threading.Thread(target=worker,args=(app,),daemon=True).start()
        print('Local: http://127.0.0.1:8766',flush=True);serve(app,host='127.0.0.1',port=8766)
    elif args.command=='worker':
        from backend.scheduler import worker
        print('Draw worker active (60-second ticks). Stop with Ctrl+C.',flush=True);worker(app)
    elif args.command=='create-admin':
        email=input('Admin email: ').strip().lower();password=getpass.getpass('Admin password (12+ chars): ')
        import re
        if len(password)<12 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise SystemExit('Invalid email or password')
        conn.execute("INSERT INTO users(email,password,name,role,verified,adult,created) VALUES(?,?,?,'admin',1,1,?)",(email,generate_password_hash(password),'Administrator',int(time.time())));print('Administrator created. Password not stored in source.')
    elif args.command=='seed-demo':
        if production:raise SystemExit('Demo seeding forbidden in production')
        if conn.execute('SELECT 1 FROM articles LIMIT 1').fetchone():raise SystemExit('Database already contains content; refusing to overwrite')
        data=json.loads((Path(__file__).resolve().parents[1]/'site/demo.json').read_text(encoding='utf-8'))
        conn.execute('BEGIN IMMEDIATE')
        try:
            conn.execute('INSERT OR IGNORE INTO settings VALUES(1,?)',(pack(data['settings']),))
            for game in data['games']:conn.execute('INSERT INTO games(id,data) VALUES(?,?)',(game['id'],pack(game)))
            for row in data['articles']:conn.execute("INSERT INTO articles(id,kind,status,data) VALUES(?,?,'published',?)",(row['id'],row['kind'],pack(row)))
            for row in data['activities']:conn.execute("INSERT INTO activities(id,kind,status,data) VALUES(?,?,'published',?)",(row['id'],row['kind'],pack(row)))
            conn.commit()
        except:conn.rollback();raise
        print('Explicit demonstration content inserted.')
    elif args.command=='outbox':
        if production:raise SystemExit('Development outbox disabled in production')
        for row in conn.execute('SELECT id,subject,body FROM outbox ORDER BY id DESC LIMIT 20'):print(dict(row))
    else:
        from backend.operations import operation
        operation(args,app,conn)
    conn.close()

if __name__=='__main__':main()
