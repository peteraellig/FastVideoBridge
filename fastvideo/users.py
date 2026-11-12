"""Persistent personal viewer links. Admin data stays on the local control API."""
import copy
import json
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

class UserStore:
    def __init__(self, directory):
        self.path=Path(directory)/'users.json'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.data=json.loads(self.path.read_text(encoding='utf-8-sig')) if self.path.exists() else {'version':1,'require_links':False,'users':[]}
        if self.data.get('version')!=1 or not isinstance(self.data.get('users'),list):
            raise ValueError('Unsupported users database')

    def commit(self,data):
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
        temporary.replace(self.path)
        self.data=data

    def save(self,fields):
        if not isinstance(fields,dict):raise ValueError('Invalid user data')
        name,email=fields.get('name'),fields.get('email','')
        limit,enabled=fields.get('limit',1),fields.get('enabled',True)
        if not isinstance(name,str) or not 1<=len(name.strip())<=100:raise ValueError('Enter a name (1–100 characters)')
        if not isinstance(email,str) or len(email)>254 or (email.strip() and ('@' not in email or ' ' in email.strip())):raise ValueError('Enter a valid email address')
        if any(ord(c)<32 for c in name+email):raise ValueError('Invalid name or email')
        if type(limit) is not int or limit not in (1,2):raise ValueError('Choose 1 or 2 connections')
        if type(enabled) is not bool:raise ValueError('Invalid enabled setting')
        data=copy.deepcopy(self.data)
        identifier=fields.get('id')
        user=next((u for u in data['users'] if u['id']==identifier),None)
        if identifier and not user:raise ValueError('User not found')
        if not user:
            user={'id':uuid.uuid4().hex,'token':secrets.token_urlsafe(32),'created':datetime.now(timezone.utc).isoformat()}
            data['users'].append(user)
        user.update(name=name.strip(),email=email.strip(),limit=limit,enabled=enabled)
        self.commit(data)
        return user

    def replace(self,identifier):
        data=copy.deepcopy(self.data)
        user=next((u for u in data['users'] if u['id']==identifier),None)
        if not user:raise ValueError('User not found')
        user['token']=secrets.token_urlsafe(32)
        self.commit(data)
        return user

    def authorize(self,token):
        if not isinstance(token,str) or len(token)!=43 or not all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_' for c in token):return None
        return next((u for u in self.data['users'] if u['enabled'] and secrets.compare_digest(u['token'],token)),None)

    def require_links(self,value):
        if type(value) is not bool:raise ValueError('Invalid personal link setting')
        data=copy.deepcopy(self.data);data['require_links']=value;self.commit(data)

    def remove(self,identifier):
        data=copy.deepcopy(self.data)
        user=next((u for u in data['users'] if u['id']==identifier),None)
        if not user:raise ValueError('User not found')
        data['users'].remove(user);self.commit(data)
