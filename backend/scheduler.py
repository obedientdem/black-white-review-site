"""Local draw worker; production runs it as a separate supervised service."""
import logging
import threading
from types import SimpleNamespace
from backend.app import connect
from backend.operations import operation

def tick(app):
    conn=connect(app.config['DATABASE'])
    try:operation(SimpleNamespace(command='draw-due'),app,conn)
    finally:conn.close()

def worker(app,stop=None,interval=60):
    stop=stop or threading.Event()
    while not stop.is_set():
        try:tick(app)
        except (Exception,SystemExit):logging.exception('Draw worker failed; next tick will retry safely')
        stop.wait(interval)
