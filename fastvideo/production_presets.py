"""Named local production configurations, encrypted with Windows DPAPI."""
import json
import re
import uuid
from datetime import datetime, timezone
from .output_settings import protect


class ProductionPresets:
    def __init__(self, folder):
        self.folder = folder
        self.active_file = folder.parent / 'active-production.json'

    def path(self, preset_id):
        if not isinstance(preset_id, str) or not re.fullmatch('[0-9a-f]{32}', preset_id):
            raise ValueError('Choose a production preset')
        return self.folder / (preset_id + '.json')

    def list(self):
        result = []
        for file in sorted(self.folder.glob('*.json')):
            try:
                row = json.loads(file.read_text(encoding='utf-8'))
                self.path(row['id'])
                if row['id'] != file.stem:
                    continue
                result.append({k: row[k] for k in ('id', 'name', 'updated')})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(result, key=lambda row: row['name'].casefold())

    def active(self):
        try:
            row = json.loads(self.active_file.read_text(encoding='utf-8'))
            return row if self.path(row['id']).exists() else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, name, data, preset_id=None):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError('Enter a production name (1–100 characters)')
        preset_id = preset_id or uuid.uuid4().hex
        path = self.path(preset_id)
        payload = json.dumps(data, ensure_ascii=False)
        if len(payload.encode('utf-8')) > 65536:
            raise ValueError('Production settings are too large')
        row = dict(id=preset_id, name=name.strip(), updated=datetime.now(timezone.utc).isoformat(),
                   schema=1, protected=protect(payload))
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
        active = self.active()
        if active and active['id'] == preset_id:
            self.set_active(row)
        return {k: row[k] for k in ('id', 'name', 'updated')}

    def load(self, preset_id):
        row = json.loads(self.path(preset_id).read_text(encoding='utf-8'))
        if row.get('schema') != 1 or row.get('id') != preset_id:
            raise ValueError('Unsupported production preset')
        return row, json.loads(protect(row['protected'], True))

    def set_active(self, row):
        self.active_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.active_file.with_suffix('.tmp')
        temporary.write_text(json.dumps({k: row[k] for k in ('id', 'name')}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.active_file)

    def rename(self, preset_id, name):
        # Re-saves the same encrypted content under a new name.
        row, data = self.load(preset_id)
        return self.save(name, data, preset_id)

    def delete(self, preset_id):
        self.path(preset_id).unlink(missing_ok=True)


class ConnectionProfiles(ProductionPresets):
    """Named settings for one connection type (WHIP send, SRT send, WHIP/WHEP receive).
    Same DPAPI-protected storage as productions, one folder per type, no 'active' entry."""

    def __init__(self, folder, kind):
        super().__init__(folder)
        self.kind = kind
        self.active_file = folder / 'unused-active.json'

    def active(self):
        return None

    def save(self, name, settings, preset_id=None):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError('Enter a profile name (1–100 characters)')
        return super().save(name, {'kind': self.kind, 'settings': settings}, preset_id)

    def load(self, preset_id):
        row, data = super().load(preset_id)
        if not isinstance(data, dict) or data.get('kind') != self.kind or not isinstance(data.get('settings'), dict):
            raise ValueError('This profile belongs to another connection type')
        return row, data

    def rename(self, preset_id, name):
        row, data = self.load(preset_id)
        return self.save(name, data['settings'], preset_id)
