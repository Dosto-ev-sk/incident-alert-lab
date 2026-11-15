"""Local synthetic incident lab. Importing this module never starts calls."""
import argparse
import hashlib
import os
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template, request, url_for

ROOT = Path(__file__).resolve().parent
MESSAGE = ('This is a cybersecurity lab test. High-severity alert on SRV-FILE01. '
           'Please acknowledge this alert on your computer.')


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def mask(number):
    return '***' + number[-2:] if re.fullmatch(r'\+[1-9]\d{7,14}', number or '') else '(not configured)'


class Provider:
    def __init__(self, live=False):
        self.live = live
        self.sid = os.getenv('TWILIO_ACCOUNT_SID', '')
        self.token = os.getenv('TWILIO_AUTH_TOKEN', '')
        self.caller = os.getenv('TWILIO_FROM_NUMBER', '')
        self.target = os.getenv('ALERT_TO_NUMBER', '')
        self.backup = os.getenv('ALERT_BACKUP_NUMBER', '').strip() or self.target
        if live:
            if not re.fullmatch(r'AC[0-9a-fA-F]{32}', self.sid) or not self.token.strip():
                raise ValueError('Enter a valid account SID and auth token in local .env.')
            if not re.fullmatch(r'\+[1-9]\d{7,14}', self.caller):
                raise ValueError('Caller must be an authorized E.164 number in local .env.')
            if not re.fullmatch(r'\+212[67]\d{8}', self.target):
                raise ValueError('Destination must be your Moroccan mobile: +2126xxxxxxxx or +2127xxxxxxxx.')
            if not re.fullmatch(r'\+212[67]\d{8}', self.backup):
                raise ValueError('Backup must be your Moroccan mobile in E.164 format.')
        # Credentials excluded: rotating a token does not change the destination.
        self.key = hashlib.sha256(f'{live}|{self.sid}|{self.caller}|{self.target}|{self.backup}'.encode()).hexdigest()

    def send(self, slot):
        if not self.live:
            return 'simulated', None, 'Dry-run only; no network request or telephone call.'
        speech = ('Backup engineer escalation. ' if slot == 2 else '') + MESSAGE
        twiml = f'<Response><Say voice="alice" language="en-US">{speech}</Say><Hangup/></Response>'
        try:
            # A fresh Session has no automatic POST retries. Do not add retry adapters.
            with requests.Session() as session:
                session.trust_env = False  # Avoid accidental proxy credentials/configuration.
                response = session.post(
                    f'https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Calls.json',
                    auth=(self.sid, self.token),
                    data={'To': self.backup if slot == 2 else self.target, 'From': self.caller, 'Twiml': twiml,
                          'Timeout': '20', 'TimeLimit': '45'},
                    timeout=(5, 20), allow_redirects=False)
            if 200 <= response.status_code < 300:
                body = response.json()
                sid = body.get('sid')
                if not isinstance(sid, str) or not re.fullmatch(r'CA[0-9a-fA-F]{32}', sid):
                    return 'unknown', None, 'Unexpected API response; outcome unknown. No retry.'
                return 'accepted', sid, 'API request accepted; receipt and speech playback are unconfirmed. Check Twilio Voice Logs.'
            # Never retain provider response text: it can contain numbers or secrets.
            try:
                code = response.json().get('code')
            except (ValueError, AttributeError):
                code = None
            code = str(code) if isinstance(code, int) else 'unavailable'
            state = 'unknown' if response.status_code >= 500 else 'failed'
            return state, None, f'HTTP {response.status_code}; Twilio error code {code}. Check Console Voice Logs/Debugger. No retry.'
        except Exception:
            # Connection loss may occur after Twilio accepted the request.
            return 'unknown', None, 'Request/response error; acceptance unknown. Check Twilio Console. No retry.'


class Lab:
    def __init__(self, database, provider, clock=utcnow):
        self.database, self.provider, self.clock = str(database), provider, clock
        with self.db() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS alerts (
                  id INTEGER PRIMARY KEY, created_utc TEXT NOT NULL,
                  due_utc TEXT NOT NULL, acknowledged_utc TEXT, resolved_utc TEXT,
                  mode TEXT NOT NULL, config_key TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS calls (
                  alert_id INTEGER NOT NULL REFERENCES alerts(id),
                  slot INTEGER NOT NULL CHECK(slot IN (1,2)),
                  reserved_utc TEXT NOT NULL, finished_utc TEXT,
                  state TEXT NOT NULL, call_sid TEXT, detail TEXT NOT NULL,
                  PRIMARY KEY(alert_id,slot));
            ''')

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.database, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create(self):
        now = self.clock()
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # One unresolved alert at a time also prevents double-click call storms.
            existing = conn.execute('SELECT id FROM alerts WHERE resolved_utc IS NULL').fetchone()
            if existing:
                return existing['id']
            cur = conn.execute('INSERT INTO alerts(created_utc,due_utc,mode,config_key) VALUES(?,?,?,?)',
                               (stamp(now), stamp(now + timedelta(seconds=60)),
                                'live' if self.provider.live else 'dry-run', self.provider.key))
            return cur.lastrowid

    def claim(self):
        """Commit one slot before external I/O; acknowledgment shares the same lock."""
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            alert = conn.execute('''SELECT * FROM alerts a
                WHERE acknowledged_utc IS NULL AND resolved_utc IS NULL AND config_key=?
                AND (NOT EXISTS(SELECT 1 FROM calls c WHERE c.alert_id=a.id AND slot=1)
                  OR (due_utc<=? AND NOT EXISTS(SELECT 1 FROM calls c WHERE c.alert_id=a.id AND slot=2)))
                ORDER BY id LIMIT 1''', (self.provider.key, stamp(self.clock()))).fetchone()
            if not alert:
                return None
            slot = 2 if conn.execute('SELECT 1 FROM calls WHERE alert_id=? AND slot=1',
                                     (alert['id'],)).fetchone() else 1
            conn.execute('INSERT INTO calls(alert_id,slot,reserved_utc,state,detail) VALUES(?,?,?,?,?)',
                         (alert['id'], slot, stamp(self.clock()), 'reserved',
                          'Dispatch reserved; outcome unknown if interrupted. This slot will never be retried.'))
            return alert['id'], slot

    def tick(self):
        work = self.claim()
        if work is None:
            return False
        alert_id, slot = work
        state, sid, detail = self.provider.send(slot)
        with self.db() as conn:
            conn.execute('UPDATE calls SET state=?,call_sid=?,detail=?,finished_utc=? WHERE alert_id=? AND slot=?',
                         (state, sid, detail, stamp(self.clock()), alert_id, slot))
        return True

    def action(self, alert_id, action):
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM alerts WHERE id=?', (alert_id,)).fetchone()
            if row is None:
                return 404
            if action == 'ack':
                conn.execute('UPDATE alerts SET acknowledged_utc=COALESCE(acknowledged_utc,?) WHERE id=?',
                             (stamp(self.clock()), alert_id))
            elif action == 'resolve':
                if row['acknowledged_utc'] is None:
                    return 409
                conn.execute('UPDATE alerts SET resolved_utc=COALESCE(resolved_utc,?) WHERE id=?',
                             (stamp(self.clock()), alert_id))
            return 303

    def snapshot(self):
        with self.db() as conn:
            alerts = [dict(row) for row in conn.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT 50')]
            for alert in alerts:
                alert['calls'] = conn.execute('SELECT * FROM calls WHERE alert_id=? ORDER BY slot', (alert['id'],)).fetchall()
                alert['config_matches'] = alert['config_key'] == self.provider.key
            return alerts


def create_app(lab):
    app = Flask(__name__)
    csrf = secrets.token_urlsafe(32)

    @app.before_request
    def local_only():
        if request.remote_addr not in ('127.0.0.1', '::1') or request.host not in ('127.0.0.1:5000', 'localhost:5000'):
            abort(403)
        if request.method == 'POST' and not secrets.compare_digest(request.form.get('csrf', '').encode(), csrf.encode()):
            abort(403)

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/')
    def index():
        return render_template('index.html', alerts=lab.snapshot(), csrf=csrf,
                               live=lab.provider.live, target=mask(lab.provider.target), backup=mask(lab.provider.backup))

    @app.post('/generate')
    def generate():
        if lab.provider.live and request.form.get('live_consent') != 'yes':
            abort(400, 'Confirm that these are your numbers and you want up to two paid calls.')
        lab.create()
        return redirect(url_for('index'), code=303)

    @app.post('/alerts/<int:alert_id>/<action>')
    def action(alert_id, action):
        if action not in ('ack', 'resolve'):
            abort(404)
        result = lab.action(alert_id, action)
        if result != 303:
            abort(result)
        return redirect(url_for('index'), code=303)

    return app


def worker(lab, stop):
    while not stop.is_set():
        try:
            lab.tick()
        except Exception:
            # Fixed text only; do not dump exceptions that may expose secrets.
            print('Worker error; inspect local database and restart. No automatic retry of reserved slots.', flush=True)
        stop.wait(0.5)


def main():
    parser = argparse.ArgumentParser(description='Local synthetic telephone escalation lab')
    parser.add_argument('--live', action='store_true', help='Explicitly arm real paid calls; also requires DRY_RUN=false')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env', override=False)
    if args.live and os.getenv('DRY_RUN', 'true').lower() != 'false':
        parser.error('Live mode requires DRY_RUN=false in .env AND --live.')
    try:
        provider = Provider(live=args.live)
    except ValueError as exc:
        parser.error(str(exc))
    lab = Lab(ROOT / 'lab.sqlite3', provider)
    # On restart, never replay any slot reserved by a previous process. Leaving
    # its state untouched also avoids clobbering another running process.
    stop = threading.Event()
    from waitress import create_server
    # Bind first: a second launch on the same port must not start another worker.
    server = create_server(create_app(lab), host='127.0.0.1', port=5000, threads=4)
    thread = threading.Thread(target=worker, args=(lab, stop), daemon=True)
    thread.start()
    print(f"{'LIVE: paid calls armed' if args.live else 'DRY RUN: no calls'}; initial {mask(provider.target)}; backup {mask(provider.backup)}; http://127.0.0.1:5000", flush=True)
    try:
        server.run()
    finally:
        stop.set()
        thread.join(timeout=30)


if __name__ == '__main__':
    main()
