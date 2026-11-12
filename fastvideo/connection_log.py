"""The last connections of this Bridge, newest first: who, when, how long, how much.

Fed once a second from the status sampler with the states of the WHIP sender, the SRT sender
and the receiver. A connection is one row; a series of failed attempts to the same address is
one row as well, so that a receiver that is switched off cannot push everything else out.
"""
from datetime import datetime, timezone
import json
import threading
import time
from urllib.parse import urlsplit

LIMIT = 100
CONNECTED = ('connected', 'sending', 'receiving')


def address(url):
    """Address without key or query: personal viewer links carry their key there."""
    try:
        parts = urlsplit(url or '')
        return f'{parts.scheme}://{parts.netloc.rsplit("@", 1)[-1]}{parts.path}' if parts.netloc else ''
    except ValueError:
        return ''


def describe(sample):
    """Per connection type: label, the other side, session id, connected, enabled, data counters."""
    whip, srt, receive = (sample.get(key) or {} for key in ('whip', 'srt', 'receive'))
    pull = receive.get('mode') == 'whep'
    return {
        'whip': dict(label='WHIP send', peer=whip.get('target', ''), session=whip.get('send_session_id'),
                     state=whip.get('state'), enabled=whip.get('enabled'), attempts=whip.get('attempts', 0),
                     detail=whip.get('detail', ''), codec=whip.get('codec', ''),
                     megabytes=((whip.get('video_bytes') or 0)+(whip.get('audio_bytes') or 0))/1e6,
                     pictures=whip.get('video_frames') or 0),
        'srt': dict(label='SRT send', peer=f"srt://{srt['host']}:{srt.get('port')}" if srt.get('host') else '',
                    session=None, state=srt.get('state'), enabled=srt.get('enabled'), attempts=srt.get('attempts', 0),
                    detail=srt.get('detail', ''), codec=srt.get('codec', ''),
                    megabytes=(srt.get('sent_bytes') or 0)/1e6, pictures=srt.get('video_frames') or 0),
        'receive': dict(label='WHEP receive' if pull else 'WHIP receive',
                        peer=address(receive.get('viewer_url')) if pull else receive.get('remote_address', ''),
                        session=receive.get('session'), state=receive.get('state'), enabled=receive.get('enabled'),
                        attempts=receive.get('attempts', 0) if pull else 0, detail=receive.get('detail', ''),
                        codec=receive.get('codec', ''), megabytes=None, pictures=receive.get('video_frames') or 0),
    }


class ConnectionLog:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.rows = []      # oldest first
        self.active = {}    # kind -> row
        self.session = {}   # kind -> session id of the active row
        self.attempts = {}  # kind -> attempt counter when the kind was last idle or connected
        try:
            self.rows = [row for row in json.loads(path.read_text(encoding='utf-8')) if isinstance(row, dict)][-LIMIT:]
        except (OSError, ValueError):
            pass
        # A row left open belongs to a process that no longer exists.
        changed = False
        for row in self.rows:
            if row.get('ended_at') is None:
                row['ended_at'] = row.get('seen_at') or row['started_at']
                row['status'] = 'Failed' if row.get('status') == 'Trying' else 'Interrupted'
                changed = True
        if changed:
            self._save()

    @staticmethod
    def utc(at):
        return datetime.fromtimestamp(at, timezone.utc).isoformat(timespec='seconds')

    def _save(self):
        temporary = self.path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps(self.rows), encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            pass

    def _open(self, kind, item, at, status):
        row = dict(id=f'{at:.3f}-{kind}', kind=item['label'], peer=item['peer'], status=status,
                   started_at=self.utc(at), ended_at=None, seen_at=self.utc(at), codec=item['codec'],
                   megabytes=None, pictures=0, attempts=0, detail='')
        self.rows.append(row)
        del self.rows[:-LIMIT]
        self.active[kind] = row
        self.session[kind] = item['session']
        return row

    def _close(self, kind, at, status, detail=''):
        row = self.active.pop(kind)
        self.session.pop(kind, None)
        row.update(ended_at=self.utc(at), status=status)
        if detail:
            row['detail'] = detail

    def update(self, sample, at=None):
        at = time.time() if at is None else at
        with self.lock:
            changed = False
            for kind, item in describe(sample).items():
                row = self.active.get(kind)
                connected = bool(item['enabled']) and item['state'] in CONNECTED
                if row and row['status'] == 'Active' and (not connected or (item['session'] and item['session'] != self.session.get(kind))):
                    # Ended by the operator, or lost while the output stays switched on.
                    self._close(kind, at, 'Lost' if item['enabled'] else 'Ended', item['detail'] if item['enabled'] else '')
                    self.attempts[kind] = item['attempts']
                    row = None
                    changed = True
                if connected:
                    if row and row['status'] == 'Trying':
                        self._close(kind, at, 'Failed')
                        row = None
                    if row is None:
                        row = self._open(kind, item, at, 'Active')
                        changed = True
                    # Counters only in memory: written when the connection ends.
                    row.update(seen_at=self.utc(at), pictures=item['pictures'], codec=item['codec'] or row['codec'])
                    if item['megabytes'] is not None:
                        row['megabytes'] = round(item['megabytes'], 2)
                    if item['peer'] and not row['peer']:
                        row['peer'] = item['peer']
                    self.attempts[kind] = item['attempts']
                elif item['enabled'] and item['attempts'] > self.attempts.get(kind, 0) and item['peer']:
                    # Switched on, but no connection: one row for the whole series of attempts.
                    if row is None:
                        row = self._open(kind, item, at, 'Trying')
                        changed = True
                    count = item['attempts']-self.attempts.get(kind, 0)
                    changed = changed or count != row['attempts']
                    row.update(seen_at=self.utc(at), attempts=count, detail=item['detail'] or row['detail'])
                elif not item['enabled']:
                    if row:
                        self._close(kind, at, 'Failed')
                        changed = True
                    self.attempts[kind] = 0
            if changed:
                self._save()

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            result = []
            for row in reversed(self.rows):
                row = dict(row)
                start = datetime.fromisoformat(row['started_at']).timestamp()
                end = datetime.fromisoformat(row['ended_at']).timestamp() if row['ended_at'] else now
                row['duration_seconds'] = round(max(0, end-start))
                result.append(row)
            return result

    def text(self):
        def duration(seconds):
            return f'{seconds//3600:02d}:{seconds%3600//60:02d}:{seconds%60:02d}'
        lines = [f'FastVideo Bridge · last {LIMIT} connections · newest first · local time', '']
        lines.append(f"{'Started':19}  {'Duration':>9}  {'Type':12}  {'Result':11}  {'Data':>12}  Other side")
        for row in self.snapshot():
            started = datetime.fromisoformat(row['started_at']).astimezone().strftime('%Y-%m-%d %H:%M:%S')
            data = f"{row['megabytes']:.1f} MB" if row.get('megabytes') is not None else f"{row.get('pictures', 0)} pic" if row['status'] != 'Trying' and row.get('pictures') else '—'
            notes = [row['peer'] or 'unknown', row.get('codec', '').upper(),
                     f"{row['attempts']} attempts" if row.get('attempts') else '', row.get('detail', '')]
            lines.append(f"{started:19}  {duration(row['duration_seconds']):>9}  {row['kind']:12}  {row['status']:11}  {data:>12}  "
                         + ' · '.join(note for note in notes if note))
        return '\r\n'.join(lines)+'\r\n'

    def close(self, at=None):
        at = time.time() if at is None else at
        with self.lock:
            for kind in list(self.active):
                self._close(kind, at, 'Failed' if self.active[kind]['status'] == 'Trying' else 'Ended', 'Bridge stopped')
            self._save()
