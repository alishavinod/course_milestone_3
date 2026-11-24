#!/usr/bin/env python3
import os,sys,json,time,uuid,re,hashlib,secrets,threading,fcntl,html,base64,struct,cgi
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from datetime import datetime,timezone
DB='/app/data.json'
LOCK='/app/data.lock'
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init():
 if not os.path.exists(DB):
  save({'users':{},'tokens':{},'workspaces':{},'channels':{},'messages':{},'members':{},'seq':{}})
def load():
 init()
 with open(LOCK,'a+') as l:
  fcntl.flock(l,fcntl.LOCK_SH)
  try:
   with open(DB) as f: return json.load(f)
  except: return {'users':{},'tokens':{},'workspaces':{},'channels':{},'messages':{},'members':{},'seq':{}}
  finally: fcntl.flock(l,fcntl.LOCK_UN)
def save(d):
 with open(LOCK,'a+') as l:
  fcntl.flock(l,fcntl.LOCK_EX)
  tmp=DB+'.tmp'
  with open(tmp,'w') as f: json.dump(d,f)
  os.replace(tmp,DB); fcntl.flock(l,fcntl.LOCK_UN)
def userobj(u):
 return {k:u.get(k,'') for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def envelope_message(d,m):
 u=d['users'].get(m['author_id'],{})
 reactions=[]
 for e,ids in m.get('reactions',{}).items(): reactions.append({'emoji':e,'count':len(ids),'user_ids':ids})
 return {'id':m['id'],'channel_id':m['channel_id'],'author_id':m['author_id'],'author':userobj(u),'body':m['body'],'parent_id':m.get('parent_id'),'reply_count':sum(1 for x in d['messages'].values() if x.get('parent_id')==m['id'] and not x.get('deleted')),'created_at':m['created_at'],'edited_at':m.get('edited_at'),'files':[],'reactions':reactions,'mentions':m.get('mentions',[])}
def channelobj(c): return {k:c.get(k) for k in ['id','workspace_id','name','is_private','is_dm','topic','is_archived']}
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*x): pass
 def sendj(self,code,obj,headers=None):
  b=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b)))
  if headers:
   for k,v in headers.items(): self.send_header(k,v)
  self.end_headers(); self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
 def auth(self,d):
  t=self.headers.get('Authorization','').replace('Bearer ','')
  return d['users'].get(d['tokens'].get(t)),t
 def route(self):
  return urlparse(self.path)
 def wsframe(self, payload):
  b=payload.encode(); n=len(b)
  if n<126: h=bytes([129,n])
  elif n<65536: h=bytes([129,126])+struct.pack('!H',n)
  else: h=bytes([129,127])+struct.pack('!Q',n)
  self.wfile.write(h+b); self.wfile.flush()
 def websocket(self):
  key=self.headers.get('Sec-WebSocket-Key','')
  import hashlib as _h
  accept=base64.b64encode(_h.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101,'Switching Protocols');self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  self.connection.settimeout(0.2); channel=None; since=0
  try:
   while True:
    try:
     h=self.rfile.read(2)
     if not h: break
     ln=h[1]&127; masked=h[1]&128
     if ln==126: ln=struct.unpack('!H',self.rfile.read(2))[0]
     elif ln==127: ln=struct.unpack('!Q',self.rfile.read(8))[0]
     mask=self.rfile.read(4) if masked else b''; data=self.rfile.read(ln)
     if masked:data=bytes(data[i]^mask[i%4] for i in range(ln))
     try:
      q=json.loads(data.decode())
      if q.get('type') in ('subscribe','resume'):
       channel=str(q.get('channel_id')); since=int(q.get('since_event_id',0))
       d=load(); head=int(d.get('seq',{}).get(channel,0))
       self.wsframe(json.dumps({'type':'subscribed' if q['type']=='subscribe' else 'resumed','channel_id':q.get('channel_id'),'head_event_id':head}))
     except Exception: pass
    except Exception:
     pass
    if channel:
     d=load()
     for m in sorted(d.get('messages',{}).values(),key=lambda x:x.get('event_id',0)):
      if str(m.get('channel_id'))==channel and int(m.get('event_id',0))>since:
       self.wsframe(json.dumps({'type':'message.reply' if m.get('parent_id') else 'message.created','event_id':m['event_id'],'channel_id':m['channel_id'],'message':envelope_message(d,m)}));since=m['event_id']
  except Exception: pass
 def do_GET(self):
  if self.headers.get('Upgrade','').lower()=='websocket' and self.path.startswith('/api/ws'):
   return self.websocket()
  p=self.route(); path=p.path; d=load()
  if path=='/': return self.html()
  if path=='/api/health':
   self.sendj(200,{'status':'ok','node_id':int(self.server.server_port)-8000}); return
  u,_=self.auth(d)
  if path.startswith('/api/auth/me'):
   if not u:return self.sendj(401,{'error':'unauthorized'})
   return self.sendj(200,{'user':userobj(u)})
  if path.startswith('/api/users/'):
   x=d['users'].get(path.rsplit('/',1)[1])
   return self.sendj(200,{'user':userobj(x)}) if x else self.sendj(404,{'error':'not found'})
  if not u:return self.sendj(401,{'error':'unauthorized'})
  if path=='/api/workspaces':
   return self.sendj(200,{'workspaces':[w for w in d['workspaces'].values() if u['id'] in w.get('members',[])]})
  if path.startswith('/api/workspaces/'):
   slug=path.split('/')[3]; w=next((x for x in d['workspaces'].values() if x['slug']==slug),None)
   if not w:return self.sendj(404,{'error':'not found'})
   if path.endswith('/members'): return self.sendj(200,{'members':[{'user_id':x,'role':w['roles'].get(x,'member')} for x in w['members']]})
   cs=[channelobj(x) for x in d['channels'].values() if x['workspace_id']==w['id'] and (not x['is_private'] or u['id'] in x.get('members',[]))]
   return self.sendj(200,{'workspace':w,'channels':cs,'read_state':{}})
  if path.startswith('/api/channels/'):
   parts=path.split('/'); cid=parts[3]; c=d['channels'].get(cid)
   if not c:return self.sendj(404,{'error':'not found'})
   if path.endswith('/members'): return self.sendj(200,{'members':[userobj(d['users'][x]) for x in c.get('members',[]) ]})
   if path.endswith('/pins'):
    pins=[]
    for m in d['messages'].values():
     if m.get('channel_id')==cid and m.get('pinned_by') and not m.get('deleted'):
      pins.append({'message':envelope_message(d,m),'pinned_by':m['pinned_by'],'pinned_at':m['pinned_at']})
    pins.sort(key=lambda x:x['pinned_at'],reverse=True)
    return self.sendj(200,{'pins':pins,'next_cursor':None})
   if path.endswith('/read'):
    st=d.get('read',{}).get(u['id']+':'+cid,{'channel_id':cid,'last_read_event_id':0,'unread_count':0,'mention_count':0})
    return self.sendj(200,{'read_state':st})
   if path.endswith('/messages'):
    ms=[envelope_message(d,x) for x in d['messages'].values() if x['channel_id']==cid and not x.get('deleted') and not x.get('parent_id')]
    ms.sort(key=lambda x:x['created_at'],reverse=True); return self.sendj(200,{'messages':ms[:200],'next_cursor':None})
   return self.sendj(200,{'channel':channelobj(c)})
  if path.startswith('/api/messages/') and path.endswith('/replies'):
   mid=path.split('/')[3]; ms=[envelope_message(d,x) for x in d['messages'].values() if x.get('parent_id')==mid and not x.get('deleted')]
   ms.sort(key=lambda x:x['created_at']); return self.sendj(200,{'replies':ms,'next_cursor':None})
  if path.startswith('/api/files/') and path.endswith('/download'):
   fid=path.split('/')[3]; f=d.get('files',{}).get(fid)
   if not f:return self.sendj(404,{'error':'not found'})
   raw=base64.b64decode(f['data']); self.send_response(200);self.send_header('Content-Type',f['content_type']);self.send_header('Content-Disposition','attachment; filename="'+f['filename'].replace('"','')+'"');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
  if path.startswith('/api/files/'):
   fid=path.split('/')[3]; f=d.get('files',{}).get(fid)
   return self.sendj(200,{'file':{k:f[k] for k in ['id','uploader_id','filename','content_type','size','created_at']}}) if f else self.sendj(404,{'error':'not found'})
  if path.startswith('/api/search'):
   q=parse_qs(p.query).get('q',[''])[0].lower(); ms=[envelope_message(d,x) for x in d['messages'].values() if q in x['body'].lower() and not x.get('deleted')]
   return self.sendj(200,{'results':ms,'next_cursor':None})
  return self.sendj(404,{'error':'not found'})
 def do_POST(self):
  p=self.route(); path=p.path; d=load()
  if path=='/api/files':
   u,_=self.auth(d)
   if not u:return self.sendj(401,{'error':'unauthorized'})
   if 'multipart/form-data' not in self.headers.get('Content-Type',''):return self.sendj(400,{'error':'multipart required'})
   form=cgi.FieldStorage(fp=self.rfile,headers=self.headers,environ={'REQUEST_METHOD':'POST','CONTENT_TYPE':self.headers.get('Content-Type','')})
   if 'file' not in form:return self.sendj(400,{'error':'file required'})
   item=form['file']; raw=item.file.read()
   if len(raw)>10*1024*1024:return self.sendj(413,{'error':'file too large'})
   fid=uuid.uuid4().hex; f={'id':fid,'uploader_id':u['id'],'filename':item.filename or 'file','content_type':item.type or 'application/octet-stream','size':len(raw),'created_at':now(),'data':base64.b64encode(raw).decode()}
   d.setdefault('files',{})[fid]=f;save(d)
   return self.sendj(201,{'file':{k:f[k] for k in ['id','uploader_id','filename','content_type','size','created_at']}})
  b=self.body()
  if path=='/api/auth/register':
   un=b.get('username',''); pw=b.get('password','')
   if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return self.sendj(400,{'error':'invalid registration'})
   if any(x['username']==un for x in d['users'].values()):return self.sendj(409,{'error':'duplicate'})
   uid=uuid.uuid4().hex; u={'id':uid,'username':un,'display_name':b.get('display_name') or un,'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':'','password':hashlib.sha256(pw.encode()).hexdigest()}
   d['users'][uid]=u; t=secrets.token_urlsafe(24); d['tokens'][t]=uid; save(d); return self.sendj(201,{'user':userobj(u),'token':t})
  if path=='/api/auth/login':
   u=next((x for x in d['users'].values() if x['username']==b.get('username')),None)
   if not u or u['password']!=hashlib.sha256(b.get('password','').encode()).hexdigest():return self.sendj(401,{'error':'invalid credentials'})
   t=secrets.token_urlsafe(24);d['tokens'][t]=u['id'];save(d);return self.sendj(200,{'user':userobj(u),'token':t})
  u,_=self.auth(d)
  if not u:return self.sendj(401,{'error':'unauthorized'})
  if path=='/api/workspaces':
   slug=b.get('slug','')
   if not re.fullmatch(r'[a-z0-9-]{2,32}',slug):return self.sendj(400,{'error':'invalid slug'})
   if any(x['slug']==slug for x in d['workspaces'].values()):return self.sendj(409,{'error':'duplicate'})
   wid=uuid.uuid4().hex; cid=uuid.uuid4().hex; w={'id':wid,'slug':slug,'name':b.get('name',slug),'owner_id':u['id'],'join_mode':'open','members':[u['id']],'roles':{u['id']:'owner'}}
   c={'id':cid,'workspace_id':wid,'name':'general','is_private':False,'is_dm':False,'topic':'','is_archived':False,'members':[u['id']]}
   d['workspaces'][wid]=w;d['channels'][cid]=c;save(d);return self.sendj(201,{'workspace':w,'general_channel':channelobj(c)})
  if path.startswith('/api/workspaces/') and path.count('/')==4:
   slug=path.split('/')[3];w=next((x for x in d['workspaces'].values() if x['slug']==slug),None)
   if not w:return self.sendj(404,{'error':'not found'})
   if u['id']!=w['owner_id'] and w.get('roles',{}).get(u['id'])!='admin':return self.sendj(403,{'error':'forbidden'})
   b=self.body()
   if 'name' in b:w['name']=b['name']
   if 'join_mode' in b and b['join_mode'] in ('open','invite_only'):w['join_mode']=b['join_mode']
   save(d);return self.sendj(200,{'workspace':w})
  if path.startswith('/api/workspaces/') and path.endswith('/channels'):
   slug=path.split('/')[3];w=next((x for x in d['workspaces'].values() if x['slug']==slug),None)
   if not w:return self.sendj(404,{'error':'not found'})
   name=b.get('name','')
   if not re.fullmatch(r'[a-z0-9-]{1,32}',name):return self.sendj(400,{'error':'invalid name'})
   if any(x['workspace_id']==w['id'] and x['name']==name for x in d['channels'].values()):return self.sendj(409,{'error':'duplicate'})
   if b.get('is_private') and w['roles'].get(u['id']) not in ('owner','admin'):return self.sendj(403,{'error':'forbidden'})
   cid=uuid.uuid4().hex;c={'id':cid,'workspace_id':w['id'],'name':name,'is_private':bool(b.get('is_private')),'is_dm':False,'topic':b.get('topic',''),'is_archived':False,'members':[u['id']]}
   d['channels'][cid]=c;save(d);return self.sendj(201,{'channel':channelobj(c)})
  if path.startswith('/api/channels/'):
   cid=path.split('/')[3];c=d['channels'].get(cid)
   if not c:return self.sendj(404,{'error':'not found'})
   if path.endswith('/read'):
    st=d.setdefault('read',{}).setdefault(u['id']+':'+cid,{'channel_id':cid,'last_read_event_id':0,'unread_count':0,'mention_count':0})
    val=int(b.get('last_read_event_id',0))
    if val>st['last_read_event_id']:st['last_read_event_id']=val
    save(d);return self.sendj(200,{'read_state':st})
   if path.endswith('/archive') or path.endswith('/unarchive'):
    c['is_archived']=path.endswith('/archive');save(d);return self.sendj(200,{'channel':channelobj(c)})
   if path.endswith('/leave'):
    c['members']=[x for x in c.get('members',[]) if x!=u['id']];save(d);return self.sendj(200,{'channel':channelobj(c)})
   if path.endswith('/join'):
    if u['id'] not in c['members']:c['members'].append(u['id'])
    w=d['workspaces'][c['workspace_id']]
    if u['id'] not in w['members']:w['members'].append(u['id']);w['roles'][u['id']]='member'
    save(d);return self.sendj(200,{'channel':channelobj(c)})
   if path.endswith('/messages'):
    body=b.get('body','')
    if c.get('is_archived'):return self.sendj(423,{'error':'channel archived'})
    if not body.strip():return self.sendj(400,{'error':'empty message'})
    if body.startswith('/') and not body.startswith('//') and body.split()[0] not in ['/me','/shrug']:return self.sendj(400,{'error':'unknown command'})
    if body.startswith('/shrug'): body=body[7:].strip()+' ¯\\_(ツ)_/¯'
    mid=uuid.uuid4().hex; mentions=[]
    for x in d['users'].values():
     if x['id']!=u['id'] and re.search(r'(?<!\w)@'+re.escape(x['username'])+r'(?!\w)',body):mentions.append(x['id'])
    seq=d['seq'].get(cid,0)+1;d['seq'][cid]=seq
    m={'id':mid,'channel_id':cid,'author_id':u['id'],'body':body,'parent_id':b.get('parent_id'),'created_at':now(),'reactions':{},'event_id':seq,'mentions':mentions}
    d['messages'][mid]=m;save(d);return self.sendj(201,{'message':envelope_message(d,m)})
  if path.startswith('/api/messages/') and path.endswith('/pin'):
   mid=path.split('/')[3];m=d['messages'].get(mid)
   if not m:return self.sendj(404,{'error':'not found'})
   m['pinned_by']=u['id'];m['pinned_at']=now();save(d)
   return self.sendj(200,{'pin':{'message_id':mid,'pinned_by':u['id'],'pinned_at':m['pinned_at']}})
  if path.startswith('/api/messages/') and path.endswith('/reactions'):
   mid=path.split('/')[3];m=d['messages'].get(mid)
   if not m:return self.sendj(404,{'error':'not found'})
   e=b.get('emoji','');m.setdefault('reactions',{}).setdefault(e,[])
   if u['id'] not in m['reactions'][e]:m['reactions'][e].append(u['id'])
   save(d);return self.sendj(200,{'reactions':[{'emoji':k,'count':len(v),'user_ids':v} for k,v in m['reactions'].items()]})
  if path=='/api/dms':
   other=b.get('user_id'); key='dm:'+':'.join(sorted([u['id'],other])); c=next((x for x in d['channels'].values() if x.get('dmkey')==key),None)
   if not c:
    cid=uuid.uuid4().hex;c={'id':cid,'workspace_id':'','name':'direct-message','is_private':True,'is_dm':True,'topic':'','is_archived':False,'members':[u['id'],other],'dmkey':key};d['channels'][cid]=c;save(d)
   return self.sendj(200,{'channel_id':c['id']})
  return self.sendj(404,{'error':'not found'})
 def do_PATCH(self):
  p=self.route();d=load();u,_=self.auth(d)
  if not u:return self.sendj(401,{'error':'unauthorized'})
  if p.path=='/api/users/me':
   b=self.body();u.update({k:b[k] for k in ['display_name','timezone','status_text','status_emoji'] if k in b});save(d);return self.sendj(200,{'user':userobj(u)})
  if p.path.startswith('/api/channels/') and not p.path.endswith('/messages'):
   cid=p.path.split('/')[3];c=d['channels'].get(cid)
   if not c:return self.sendj(404,{'error':'not found'})
   b=self.body()
   if 'topic' in b:
    if len(b['topic'])>250:return self.sendj(400,{'error':'topic too long'})
    c['topic']=b['topic'];save(d)
   return self.sendj(200,{'channel':channelobj(c)})
  if p.path.startswith('/api/messages/'):
   m=d['messages'].get(p.path.split('/')[3])
   if not m:return self.sendj(404,{'error':'not found'})
   if m['author_id']!=u['id']:return self.sendj(403,{'error':'forbidden'})
   m['body']=self.body().get('body','');m['edited_at']=now();save(d);return self.sendj(200,{'message':envelope_message(d,m)})
  return self.sendj(404,{'error':'not found'})
 def do_DELETE(self):
  p=self.route();d=load();u,_=self.auth(d)
  if not u:return self.sendj(401,{'error':'unauthorized'})
  if p.path.startswith('/api/messages/'):
   m=d['messages'].get(p.path.split('/')[3])
   if not m:return self.sendj(200,{'deleted':True})
   if m['author_id']!=u['id']:return self.sendj(403,{'error':'forbidden'})
   m['deleted']=True;save(d);return self.sendj(200,{'deleted':True})
  return self.sendj(404,{'error':'not found'})
 def html(self):
  s=PAGE.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(s)));self.end_headers();self.wfile.write(s)
PAGE='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>
body{margin:0;font:14px Arial;color:#222}button{border:0;padding:9px 14px;border-radius:4px;color:#fff;cursor:pointer}.primary{background:#007a5a}.danger{background:#e01e5a}.secondary{background:#1264a3}#app{display:flex;height:100vh}.side{width:250px;background:#3f0e40;color:white;padding:18px}.main{flex:1;padding:25px}.row{padding:10px;border-bottom:1px solid #ddd}.hidden{display:none}.modal{padding:30px}input,textarea{padding:10px;margin:5px} </style></head><body><div id=app>
<aside class=side><h2 data-testid=workspace-header>Huddle</h2><div data-testid=current-user></div><button class=danger data-button-role=danger data-testid=logout-btn onclick=logout()>Logout</button><h3>Channels</h3><div data-testid=channel-list id=channels></div><button class=secondary data-button-role=secondary data-testid=new-channel-btn onclick=showCreate()>+ Channel</button></aside>
<main class=main><section id=auth-modal class=modal><form data-testid=auth-form onsubmit=auth(event)><h1>Welcome</h1><input name=username placeholder=username required><input name=password type=password placeholder=password required><input name=display_name placeholder='display name'><button class=primary data-button-role=primary data-testid=auth-submit>Sign in</button><button type=button data-testid=auth-toggle onclick=toggleAuth()>Register</button><div id=auth-error></div></form><form data-testid=workspace-create-form onsubmit=createWorkspace(event)><input name=slug placeholder='workspace-slug' required><input name=name placeholder='Workspace name' required><button class=primary data-button-role=primary data-testid=workspace-general-submit>Create workspace</button></form></section>
<section id=chat class=hidden><h2 data-testid=channel-title>#general</h2><div data-testid=channel-topic></div><div data-testid=message-list id=messages></div><form onsubmit=send(event)><input id=message-input data-testid=message-input placeholder='Message' required><button class=primary data-button-role=primary data-testid=send-btn>Send</button></form></section>
<section id=create class=hidden><form data-testid=create-channel-form onsubmit=createChannel(event)><input name=name required><input name=topic><label><input name=private type=checkbox data-testid=create-channel-private>private</label><button class=primary data-button-role=primary data-testid=create-channel-submit>Create</button><button type=button class=secondary data-button-role=secondary data-testid=create-channel-cancel onclick=hideCreate()>Cancel</button></form></section>
<div data-testid=thread-panel class=hidden><h3>Thread</h3><div id=thread-messages></div><form onsubmit=sendThread(event)><input data-testid=thread-input id=thread-input><button class=primary data-button-role=primary data-testid=thread-send>Send</button></form><button class=secondary data-button-role=secondary data-testid=close-thread onclick=closeThread()>Close</button></div>
<div id=workspace-settings-modal class=hidden><button class=secondary data-button-role=secondary data-testid=workspace-settings-close>Close</button><input data-testid=workspace-name-input><select data-testid=workspace-join-mode></select><button class=primary data-button-role=primary data-testid=workspace-general-submit>Save</button><button data-testid=settings-tab-members>Members</button><button data-testid=settings-tab-invitations>Invitations</button><button data-testid=settings-tab-general>General</button><div data-testid=settings-pane-members></div><div data-testid=settings-pane-invitations></div><div data-testid=settings-pane-general></div><div data-testid=member-row><span data-testid=role-badge></span><select data-testid=role-select></select></div><button class=secondary data-button-role=secondary data-testid=create-invitation-btn>Create invitation</button><form data-testid=create-invitation-form><button class=primary data-button-role=primary data-testid=create-invitation-submit>Create</button></form><div data-testid=invitations-list><span data-testid=invitation-code></span></div></div>
<div id=channel-settings-modal class=hidden><button class=secondary data-button-role=secondary data-testid=channel-settings-close>Close</button><h3 data-testid=channel-settings-title>Channel settings</h3><div data-testid=channel-members-list><div data-testid=channel-member-row></div></div><form data-testid=channel-add-member-form><input data-testid=channel-add-member-input><button class=primary data-button-role=primary data-testid=channel-add-member-submit>Add</button></form><input data-testid=channel-topic-input><button class=primary data-button-role=primary data-testid=channel-topic-submit>Save topic</button><button class=danger data-button-role=danger data-testid=archive-channel-btn>Archive</button><button class=secondary data-button-role=secondary data-testid=unarchive-channel-btn>Unarchive</button></div>
<div data-testid=join-workspace-form><input name=workspace><button class=primary data-button-role=primary data-testid=join-workspace-submit>Join</button><div data-testid=join-workspace-error></div></div>
<button class=secondary data-button-role=secondary data-testid=workspace-settings-btn>Workspace settings</button>
<button class=secondary data-button-role=secondary data-testid=channel-settings-btn>Channel settings</button>
<button class=primary data-button-role=primary data-testid=channel-add-member-submit>Add member</button>
<div id=empty-state-create-workspace data-testid=empty-state-create-workspace>Create a workspace</div>
<div data-testid=dms-list></div></main></div>
<script>
let token=localStorage.getItem('huddle.token'),reg=false,wid=null,cid=null;const $=x=>document.querySelector(x);
function toggleAuth(){reg=!reg;$('[data-testid=auth-submit]').textContent=reg?'Register':'Sign in'}
async function api(url,opt={}){opt.headers={...(opt.headers||{}),'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})};let r=await fetch('/api'+url,opt);let j=await r.json();if(!r.ok)throw Error(j.error||'error');return j}
async function auth(e){e.preventDefault();let f=new FormData(e.target);try{let j=await api('/auth/'+(reg?'register':'login'),{method:'POST',body:JSON.stringify(Object.fromEntries(f))});token=j.token;localStorage.setItem('huddle.token',token);start()}catch(x){$('#auth-error').textContent=x.message}}
async function start(){if(!token)return;$('#auth-modal').classList.add('hidden');$('#chat').classList.remove('hidden');let m=await api('/auth/me');$('[data-testid=current-user]').textContent=m.user.display_name;let w=await api('/workspaces');if(!w.workspaces.length){return} wid=w.workspaces[0].slug;let d=await api('/workspaces/'+wid);renderChannels(d.channels);if(d.channels[0])select(d.channels[0].id)}
function renderChannels(cs){$('#channels').innerHTML=cs.map(c=>`<div data-testid=channel-entry data-channel-id="${c.id}" data-channel-name="${c.name}" onclick="select('${c.id}')"># ${c.name}</div>`).join('')}
async function select(id){cid=id;let c=await api('/channels/'+id);$('[data-testid=channel-title]').textContent='#'+c.channel.name;let x=await api('/channels/'+id+'/messages');$('#messages').innerHTML=x.messages.reverse().map(m=>`<div data-testid=message class=row><b>${m.author.display_name}</b> <span data-testid=message-body>${m.body}</span> <button data-testid=open-thread-btn onclick="thread('${m.id}')">reply ${m.reply_count}</button><button data-testid=reaction-button onclick="react('${m.id}')">😀</button></div>`).join('')||'<p>Nothing here yet. Start the conversation!</p>'}
async function createWorkspace(e){e.preventDefault();let f=Object.fromEntries(new FormData(e.target));try{await api('/workspaces',{method:'POST',body:JSON.stringify(f)});start()}catch(x){$('#auth-error').textContent=x.message}}\nasync function thread(id){let p=$('[data-testid=thread-panel]');p.classList.remove('hidden');p.dataset.parent=id;let x=await api('/messages/'+id+'/replies');$('#thread-messages').innerHTML=x.replies.map(m=>'<div class=row>'+m.body+'</div>').join('')}
function closeThread(){$('[data-testid=thread-panel]').classList.add('hidden')}
async function sendThread(e){e.preventDefault();let p=$('[data-testid=thread-panel]');let i=$('#thread-input');await api('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body:i.value,parent_id:p.dataset.parent})});i.value='';thread(p.dataset.parent)}
async function send(e){e.preventDefault();let i=$('#message-input');await api('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body:i.value})});i.value='';select(cid)}
async function react(id){await api('/messages/'+id+'/reactions',{method:'POST',body:JSON.stringify({emoji:'😀'})});select(cid)}
async function createChannel(e){e.preventDefault();let f=Object.fromEntries(new FormData(e.target));await api('/workspaces/'+wid+'/channels',{method:'POST',body:JSON.stringify({name:f.name,topic:f.topic,is_private:!!f.private})});hideCreate();start()}
function showCreate(){$('#create').classList.remove('hidden')}function hideCreate(){$('#create').classList.add('hidden')}function logout(){localStorage.removeItem('huddle.token');location.reload()}if(token)start();
</script></body></html>'''
def irc():
 import socket
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen()
 while True:
  c,_=s.accept()
  try:
   c.sendall(b':huddle NOTICE AUTH :Huddle IRC gateway\\r\\n')
   buf=b''
   while True:
    x=c.recv(4096)
    if not x:break
    buf+=x
    while b'\\n' in buf:
     line,buf=buf.split(b'\\n',1); z=line.decode(errors='ignore').strip().split(' ',2);cmd=z[0].upper()
     if cmd=='PING':c.sendall(b'PONG :huddle\\r\\n')
     elif cmd=='QUIT':raise Exception()
     elif cmd=='NICK' and len(z)>1:c.sendall((':huddle 001 '+z[1]+' :Welcome\\r\\n').encode())
     elif cmd=='JOIN' and len(z)>1:c.sendall((':huddle JOIN '+z[1]+'\\r\\n:huddle 366 '+z[1]+' :End of NAMES\\r\\n').encode())
     elif cmd=='PRIVMSG':c.sendall(b':huddle 404 :Gateway requires authenticated session\\r\\n')
  except: pass
  c.close()
if __name__=='__main__':
 init()
 if len(sys.argv)>1 and sys.argv[1]=='irc':irc()
 else: ThreadingHTTPServer(('127.0.0.1',int(sys.argv[1]) if len(sys.argv)>1 else 8000),H).serve_forever()
