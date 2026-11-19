import os, re, json, sqlite3, hashlib, secrets, threading, time, socket, base64, struct, mimetypes
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
DB='/app/huddle.db'
dbinit=threading.Lock()
def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def conn():
    c=sqlite3.connect(DB,timeout=30,check_same_thread=False); c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=30000')
    try: c.execute('PRAGMA journal_mode=WAL')
    except sqlite3.OperationalError: pass
    return c
def init():
 with dbinit:
  c=conn()
  c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,created TEXT);
  CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
  CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT);
  CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
  CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',archived INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
  CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created TEXT,edited TEXT,deleted INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
  CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload TEXT,created TEXT);
  CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,user_id INTEGER,created TEXT);
  CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,data BLOB,created TEXT);
  CREATE TABLE IF NOT EXISTS groupsx(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT UNIQUE,name TEXT,creator_id INTEGER,users TEXT);
  '''); c.commit(); c.close()
def q(sql,args=(),one=False):
 c=conn(); x=c.execute(sql,args); r=x.fetchone() if one else x.fetchall(); c.close(); return r
def ex(sql,args=()):
 c=conn(); x=c.execute(sql,args); c.commit(); r=x.lastrowid; c.close(); return r
def userobj(r):
 return dict(id=r['id'],username=r['username'],display_name=r['display_name'],timezone='UTC',avatar_url='',status_text='',status_emoji='')
def msgobj(r):
 u=q('select * from users where id=?',(r['author_id'],),True)
 rr=q('select emoji,user_id from reactions where message_id=?',(r['id'],))
 em={}
 for x in rr: em.setdefault(x['emoji'],[]).append(x['user_id'])
 return dict(id=r['id'],channel_id=r['channel_id'],author_id=r['author_id'],author=userobj(u),body=r['body'],parent_id=r['parent_id'],reply_count=q('select count(*) n from messages where parent_id=? and deleted=0',(r['id'],),True)['n'],created_at=r['created'],edited_at=r['edited'],files=[],reactions=[{'emoji':k,'count':len(v),'user_ids':v} for k,v in em.items()],mentions=[])
def cobj(r): return dict(id=r['id'],workspace_id=r['workspace_id'],name=r['name'],is_private=bool(r['is_private']),is_dm=bool(r['is_dm']),topic=r['topic'] or '',is_archived=bool(r['archived']))
def event(cid,typ,payload):
 c=conn(); row=c.execute('select coalesce(max(event_id),0)+1 n from events where channel_id=?',(cid,)).fetchone(); n=row['n']; c.execute('insert into events values(?,?,?,?,?)',(cid,n,typ,json.dumps(payload),now())); c.commit(); c.close(); return n
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def body(self):
  n=int(self.headers.get('Content-Length','0')); b=self.rfile.read(n)
  try:return json.loads(b or '{}')
  except:return {}
 def send(self,status,obj=None,headers={}):
  b=b'' if obj is None else json.dumps(obj).encode()
  self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b)))
  for k,v in headers.items(): self.send_header(k,v)
  self.end_headers()
  if b:self.wfile.write(b)
 def auth(self):
  t=self.headers.get('Authorization','').replace('Bearer ','')
  r=q('select u.* from tokens t join users u on u.id=t.user_id where t.token=?',(t,),True)
  return r
 def route(self):
  return urlparse(self.path).path.split('/'),parse_qs(urlparse(self.path).query)
 def do_GET(self):
  if self.path.startswith('/api/ws') and self.headers.get('Upgrade','').lower()=='websocket':
   key=self.headers.get('Sec-WebSocket-Key','')
   import hashlib,base64
   accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
   self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
   def frame(x):
    b=json.dumps(x).encode(); n=len(b)
    if n<126: h=bytes([129,n])
    else: h=bytes([129,126])+struct.pack('!H',n)
    self.wfile.write(h+b);self.wfile.flush()
   while True:
    h=self.rfile.read(2)
    if not h: break
    n=h[1]&127
    if n==126:n=struct.unpack('!H',self.rfile.read(2))[0]
    mask=self.rfile.read(4); d=bytearray(self.rfile.read(n))
    for i in range(n): d[i]^=mask[i%4]
    try: x=json.loads(d)
    except: continue
    if x.get('type')=='subscribe':
     cid=int(x['channel_id']); head=q('select coalesce(max(event_id),0) n from events where channel_id=?',(cid,),True)['n'];frame({'type':'subscribed','channel_id':cid,'head_event_id':head})
    elif x.get('type')=='resume':
     cid=int(x['channel_id']); since=int(x.get('since_event_id',0)); rows=q('select * from events where channel_id=? and event_id>? order by event_id',(cid,since))
     for e in rows:
      z=json.loads(e['payload']);z.update(type=e['type'],event_id=e['event_id'],channel_id=cid);frame(z)
     head=q('select coalesce(max(event_id),0) n from events where channel_id=?',(cid,),True)['n'];frame({'type':'resumed','channel_id':cid,'head_event_id':head})
   return
  p,qs=self.route()
  if p==['',''] or self.path.startswith('/?'):
   return self.html()
  if p==['','api','health']: return self.send(200,{'status':'ok','node_id':int(os.environ.get('NODE_ID','0'))})
  if p[:2]==['','api'] and p[2:]==['auth','me']:
   u=self.auth(); return self.send(200,{'user':userobj(u)} if u else {'error':'unauthorized'}) if u else self.send(401,{'error':'unauthorized'})
  if p[:3]==['','api','users'] and len(p)==4:
   r=q('select * from users where id=?',(p[3],),True); return self.send(200,{'user':userobj(r)}) if r else self.send(404,{'error':'not found'})
  u=self.auth()
  if p[:3]==['','api','workspaces'] and len(p)==3:
   if not u:return self.send(401,{'error':'unauthorized'})
   rows=q('select w.* from workspaces w join members m on m.workspace_id=w.id where m.user_id=?',(u['id'],))
   return self.send(200,{'workspaces':[dict(x) for x in rows]})
  if len(p)>=4 and p[:3]==['','api','workspaces']:
   if not u:return self.send(401,{'error':'unauthorized'})
   w=q('select * from workspaces where slug=?',(p[3],),True)
   if not w:return self.send(404,{'error':'not found'})
   if len(p)==4:
    cs=q('select * from channels where workspace_id=? and (archived=0 or ?)',(w['id'],qs.get('include_archived',['0'])[0] in ('1','true')))
    return self.send(200,{'workspace':dict(w),'channels':[cobj(x) for x in cs],'read_state':[]})
   if len(p)==5 and p[4]=='members':
    ms=q('select m.*,u.username,u.display_name from members m join users u on u.id=m.user_id where workspace_id=?',(w['id'],))
    return self.send(200,{'members':[dict(x) for x in ms]})
  if p[:3]==['','api','channels'] and len(p)>=4:
   if not u:return self.send(401,{'error':'unauthorized'})
   cid=int(p[3]); ch=q('select * from channels where id=?',(cid,),True)
   if not ch:return self.send(404,{'error':'not found'})
   if len(p)==5 and p[4]=='messages':
    lim=min(int(qs.get('limit',['50'])[0]),200); rows=q('select * from messages where channel_id=? and deleted=0 and parent_id is null order by id desc limit ?',(cid,lim))
    return self.send(200,{'messages':[msgobj(x) for x in rows],'next_cursor':None})
   if len(p)==5 and p[4]=='replies':
    lim=min(int(qs.get('limit',['50'])[0]),200); rows=q('select * from messages where parent_id=? and deleted=0 order by id asc limit ?',(cid,lim))
    return self.send(200,{'replies':[msgobj(x) for x in rows],'next_cursor':None})
   if len(p)==5 and p[4]=='read':
    head=q('select coalesce(max(event_id),0) n from events where channel_id=?',(cid,),True)['n']
    return self.send(200,{'read_state':{'channel_id':cid,'last_read_event_id':head,'unread_count':0,'mention_count':0}})
   if len(p)==5 and p[4]=='members':
    rows=q('select u.* from channel_members cm join users u on u.id=cm.user_id where cm.channel_id=?',(cid,))
    return self.send(200,{'members':[userobj(x) for x in rows]})
   if len(p)==5 and p[4]=='pins':
    rows=q('select m.*,p.user_id pinned_by,p.created pinned_at from pins p join messages m on m.id=p.message_id where m.channel_id=? order by p.created desc',(cid,))
    return self.send(200,{'pins':[{'message':msgobj(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rows],'next_cursor':None})
  if p[:3]==['','api','files'] and len(p)==4:
   r=q('select id,uploader_id,filename,content_type,size,created from files where id=?',(p[3],),True)
   return self.send(200,{'file':dict(r)}) if r else self.send(404,{'error':'not found'})
  if p[:3]==['','api','search']:
   if not u:return self.send(401,{'error':'unauthorized'})
   text=qs.get('q',[''])[0]; rows=q('select * from messages where body like ? and deleted=0 order by id desc',(f'%{text}%',))
   return self.send(200,{'results':[msgobj(x) for x in rows],'next_cursor':None})
  if p[:3]==['','api','dms'] and len(p)==3:
   if not u:return self.send(401,{'error':'unauthorized'})
   other=qs.get('user_id',[None])[0]
   if not other:return self.send(400,{'error':'user_id required'})
   a,b=sorted((u['id'],int(other))); name='dm-%s-%s'%(a,b)
   r=q('select * from channels where name=? and is_dm=1',(name,),True)
   if not r:
    wid=q('select workspace_id from members where user_id=? limit 1',(u['id'],),True)
    if not wid:return self.send(400,{'error':'workspace required'})
    cid=ex('insert into channels(workspace_id,name,is_dm) values(?,?,1)',(wid['workspace_id'],name))
    ex('insert or ignore into channel_members values(?,?)',(cid,u['id'])); ex('insert or ignore into channel_members values(?,?)',(cid,int(other)))
   else: cid=r['id']
   return self.send(200,{'channel_id':cid})
  return self.send(404,{'error':'not found'})
 def do_POST(self):
  p,qs=self.route(); b=self.body()
  if p[:4]==['','api','auth','register']:
   if not re.fullmatch(r'[A-Za-z0-9_]{2,32}',b.get('username','')) or len(b.get('password',''))<8:return self.send(400,{'error':'invalid'})
   try: uid=ex('insert into users(username,password,display_name,created) values(?,?,?,?)',(b['username'],hashlib.sha256(b['password'].encode()).hexdigest(),b.get('display_name') or b['username'],now()))
   except sqlite3.IntegrityError:return self.send(409,{'error':'duplicate'})
   tok=secrets.token_urlsafe(24); ex('insert into tokens values(?,?)',(tok,uid)); return self.send(201,{'user':userobj(q('select * from users where id=?',(uid,),True)),'token':tok})
  if p[:4]==['','api','auth','login']:
   r=q('select * from users where username=? and password=?',(b.get('username'),hashlib.sha256(b.get('password','').encode()).hexdigest()),True)
   if not r:return self.send(401,{'error':'invalid'})
   tok=secrets.token_urlsafe(24);ex('insert into tokens values(?,?)',(tok,r['id']));return self.send(200,{'user':userobj(r),'token':tok})
  u=self.auth()
  if not u:return self.send(401,{'error':'unauthorized'})
  if p[:3]==['','api','dms'] and len(p)==3:
   other=b.get('user_id')
   if not other:return self.send(400,{'error':'user_id required'})
   a,bb=sorted((u['id'],int(other))); name='dm-%s-%s'%(a,bb)
   r=q('select * from channels where name=? and is_dm=1',(name,),True)
   if r: return self.send(200,{'channel_id':r['id']})
   wid=q('select workspace_id from members where user_id=? limit 1',(u['id'],),True)
   if not wid:return self.send(400,{'error':'workspace required'})
   cid=ex('insert into channels(workspace_id,name,is_dm) values(?,?,1)',(wid['workspace_id'],name))
   ex('insert or ignore into channel_members values(?,?)',(cid,u['id']))
   ex('insert or ignore into channel_members values(?,?)',(cid,int(other)))
   return self.send(200,{'channel_id':cid})
  if p[:3]==['','api','channels'] and len(p)==5 and p[4]=='read':
   cid=int(p[3]); ch=q('select * from channels where id=?',(cid,),True)
   if not ch:return self.send(404,{'error':'not found'})
   head=int(b.get('last_read_event_id',0)); return self.send(200,{'read_state':{'channel_id':cid,'last_read_event_id':head,'unread_count':0,'mention_count':0}})
  if p[:3]==['','api','workspaces'] and len(p)==3:
   if not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')):return self.send(400,{'error':'invalid slug'})
   try: wid=ex('insert into workspaces(slug,name,owner_id,join_mode) values(?,?,?,?)',(b['slug'],b.get('name',b['slug']),u['id'],'open'))
   except sqlite3.IntegrityError:return self.send(409,{'error':'duplicate'})
   ex('insert into members values(?,?,?)',(wid,u['id'],'owner')); cid=ex('insert into channels(workspace_id,name) values(?,?)',(wid,'general'));ex('insert into channel_members values(?,?)',(cid,u['id']))
   return self.send(201,{'workspace':dict(q('select * from workspaces where id=?',(wid,),True)),'general_channel':cobj(q('select * from channels where id=?',(cid,),True))})
  if p[:3]==['','api','workspaces'] and len(p)==5 and p[4]=='channels':
   w=q('select * from workspaces where slug=?',(p[3],),True)
   if not w:return self.send(404,{'error':'not found'})
   if not q('select 1 from members where workspace_id=? and user_id=?',(w['id'],u['id']),True):return self.send(403,{'error':'forbidden'})
   if not re.fullmatch(r'[a-z0-9-]{1,32}',b.get('name','')) or len(b.get('topic',''))>250:return self.send(400,{'error':'invalid channel'})
   if q('select 1 from channels where workspace_id=? and name=?',(w['id'],b.get('name')),True):return self.send(409,{'error':'duplicate'})
   try: cid=ex('insert into channels(workspace_id,name,is_private,topic) values(?,?,?,?)',(w['id'],b.get('name'),int(bool(b.get('is_private'))),b.get('topic','')))
   except sqlite3.IntegrityError:return self.send(409,{'error':'duplicate'})
   ex('insert into channel_members values(?,?)',(cid,u['id']));return self.send(201,{'channel':cobj(q('select * from channels where id=?',(cid,),True))})
  if p[:3]==['','api','channels'] and len(p)==5 and p[4]=='join':
   ch=q('select * from channels where id=?',(p[3],),True)
   if not ch:return self.send(404,{'error':'not found'})
   ex('insert or ignore into channel_members values(?,?)',(ch['id'],u['id']));ex('insert or ignore into members values(?,?,?)',(ch['workspace_id'],u['id'],'member'));return self.send(200,{'channel':cobj(ch)})
  if p[:3]==['','api','channels'] and len(p)==5 and p[4]=='messages':
   cid=int(p[3]); ch=q('select * from channels where id=?',(cid,),True)
   if not ch or not q('select 1 from channel_members where channel_id=? and user_id=?',(cid,u['id']),True):return self.send(403,{'error':'forbidden'})
   body=b.get('body','')
   if not body:return self.send(400,{'error':'empty'})
   if body.startswith('/') and not body.startswith('//'):
    if body.startswith('/shrug'): body=body[6:].strip()+' ¯\\_(ツ)_/¯'
    elif body.startswith('/me') and len(body)<=4:return self.send(400,{'error':'text required'})
    elif body.split()[0] not in ('/me','/shrug'):return self.send(400,{'error':'unknown command'})
   mid=ex('insert into messages(channel_id,author_id,body,parent_id,created) values(?,?,?,?,?)',(cid,u['id'],body,b.get('parent_id'),now())); m=msgobj(q('select * from messages where id=?',(mid,),True)); typ='message.reply' if b.get('parent_id') else 'message.created'; event(cid,typ,m);return self.send(201,{'message':m})
  if p[:3]==['','api','messages'] and len(p)==5 and p[4]=='reactions':
   mid=int(p[3]); ex('insert or ignore into reactions values(?,?,?)',(mid,u['id'],b.get('emoji','👍'))); r=q('select emoji,user_id from reactions where message_id=?',(mid,)); return self.send(200,{'reactions':[{'emoji':x['emoji'],'user_ids':[y['user_id'] for y in r if y['emoji']==x['emoji']],'count':sum(y['emoji']==x['emoji'] for y in r)} for x in r]})
  if p[:3]==['','api','messages'] and len(p)==5 and p[4]=='pin':
   ex('insert or ignore into pins values(?,?,?)',(p[3],u['id'],now()));return self.send(200,{'pin':{'message_id':int(p[3]),'pinned_by':u['id'],'pinned_at':now()}})
  return self.send(404,{'error':'not found'})
 def do_PATCH(self):
  p,qs=self.route();u=self.auth();b=self.body()
  if not u:return self.send(401,{'error':'unauthorized'})
  if p[:3]==['','api','users'] and len(p)==4 and p[3]=='me':
   allowed={'display_name','timezone','status_text','status_emoji'}; vals={k:v for k,v in b.items() if k in allowed}; 
   if vals: ex('update users set '+','.join(k+'=?' for k in vals)+' where id=?',tuple(vals.values())+(u['id'],))
   return self.send(200,{'user':userobj(q('select * from users where id=?',(u['id'],),True))})
  if p[:3]==['','api','messages'] and len(p)==4:
   r=q('select * from messages where id=?',(p[3],),True)
   if not r:return self.send(404,{'error':'not found'})
   if r['author_id']!=u['id']:return self.send(403,{'error':'forbidden'})
   if not b.get('body'):return self.send(400,{'error':'empty'})
   ex('update messages set body=?,edited=? where id=?',(b.get('body',''),now(),p[3]));return self.send(200,{'message':msgobj(q('select * from messages where id=?',(p[3],),True))})
  if p[:3]==['','api','channels'] and len(p)==4:
   ch=q('select * from channels where id=?',(p[3],),True)
   if not ch:return self.send(404,{'error':'not found'})
   if 'topic' in b and len(b.get('topic',''))>250:return self.send(400,{'error':'topic too long'})
   if 'topic' in b: ex('update channels set topic=? where id=?',(b['topic'],p[3]))
   return self.send(200,{'channel':cobj(q('select * from channels where id=?',(p[3],),True))})
  return self.send(404,{'error':'not found'})
 def do_DELETE(self):
  p,qs=self.route();u=self.auth()
  if not u:return self.send(401,{'error':'unauthorized'})
  if p[:3]==['','api','messages'] and len(p)==4:
   ex('update messages set deleted=1 where id=?',(p[3],));return self.send(200,{'deleted':True})
  if p[:3]==['','api','messages'] and len(p)==5 and p[4]=='pin':
   ex('delete from pins where message_id=?',(p[3],));return self.send(200,{'unpinned':True})
  if p[:3]==['','api','messages'] and len(p)==6 and p[4]=='reactions':
   ex('delete from reactions where message_id=? and user_id=? and emoji=?',(p[3],u['id'],p[5]))
   rows=q('select emoji,user_id from reactions where message_id=?',(p[3],))
   return self.send(200,{'reactions':[{'emoji':e,'count':sum(x['emoji']==e for x in rows),'user_ids':[x['user_id'] for x in rows if x['emoji']==e]} for e in sorted(set(x['emoji'] for x in rows))]})
  return self.send(404,{'error':'not found'})
 def html(self):
  b=HTML.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
HTML='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>
*{box-sizing:border-box}body{margin:0;font:14px Arial;color:#222}button{border:0;padding:9px 14px;border-radius:4px;color:#fff;cursor:pointer}.primary{background:#007a5a}.danger{background:#e01e5a}.secondary{background:#1264a3}.app{display:flex;height:100vh}.side{width:250px;background:#3f0e40;color:white;padding:18px}.main{flex:1;display:flex;flex-direction:column}.head{padding:18px;border-bottom:1px solid #ddd}.msgs{padding:20px;overflow:auto;flex:1}.row{padding:12px;border-bottom:1px solid #eee}.composer{padding:15px;display:flex;gap:8px}.composer input{flex:1;padding:12px}.modal{position:fixed;inset:0;background:#0008;display:flex;align-items:center;justify-content:center}.card{background:white;padding:28px;width:380px;border-radius:8px}.card input{display:block;width:100%;padding:10px;margin:10px 0}.chan{padding:8px;cursor:pointer}.chan:hover{background:#542052}</style></head><body><div id=app></div><script>
const A='/api';let token=localStorage.getItem('huddle.token'),me=null,cur=null;
const el=(x,a={})=>Object.assign(document.createElement(x),a);async function api(u,o={}){o.headers=Object.assign({'Content-Type':'application/json'},o.headers||{},token?{Authorization:'Bearer '+token}:{});let r=await fetch(A+u,o);let j=await r.json().catch(()=>({}));if(!r.ok)throw Error(j.error||'Request failed');return j}
function auth(){app.innerHTML='<div class=modal><form class=card data-testid=auth-form><h2>Huddle</h2><input name=username placeholder=username required><input name=password type=password placeholder=password required><input name=display_name placeholder=\"display name\"><button class=primary data-testid=auth-submit>Sign up</button><button type=button class=secondary data-testid=auth-toggle>Use login</button><p id=err></p></form></div>';let f=document.querySelector('form'),login=false;f.querySelector('[data-testid=auth-toggle]').onclick=()=>{login=!login;f.querySelector('[data-testid=auth-submit]').textContent=login?'Login':'Sign up'};f.onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(f));try{let x=await api(login?'/auth/login':'/auth/register',{method:'POST',body:JSON.stringify(d)});token=x.token;localStorage.setItem('huddle.token',token);load()}catch(x){err.textContent=x.message}}}
async function load(){try{me=(await api('/auth/me')).user}catch(e){return auth()}let ws=(await api('/workspaces')).workspaces;if(!ws.length){app.innerHTML='<div class=modal><form class=card data-testid=workspace-create-form><h2>Create workspace</h2><input name=slug placeholder=slug><input name=name placeholder=name><button class=primary data-testid=workspace-general-submit>Create</button></form></div>';document.querySelector('form').onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target));await api('/workspaces',{method:'POST',body:JSON.stringify(d)});load()};return}render(ws[0])}
async function render(w){let d=await api('/workspaces/'+w.slug),channels=d.channels;app.innerHTML='<div class=app><aside class=side><h2 data-testid=workspace-header>'+w.name+'</h2><div data-testid=current-user>'+me.display_name+'</div><button class=danger data-testid=logout-btn>Logout</button><h3>Channels</h3><div data-testid=channel-list></div><button class=secondary data-testid=new-channel-btn>+ Channel</button></aside><main class=main><header class=head><b data-testid=channel-title></b><span data-testid=channel-topic></span></header><section class=msgs data-testid=message-list></section><form class=composer><input data-testid=message-input name=body placeholder=\"Message\"><button class=primary data-testid=send-btn>Send</button></form></main></div>';let list=document.querySelector('[data-testid=channel-list]');channels.forEach(c=>{let x=el('div',{className:'chan',textContent:'# '+c.name});x.dataset.channelId=c.id;x.dataset.channelName=c.name;x.onclick=()=>open(c);list.append(x)});document.querySelector('[data-testid=logout-btn]').onclick=()=>{localStorage.removeItem('huddle.token');auth()};document.querySelector('form.composer').onsubmit=async e=>{e.preventDefault();let v=e.target.body.value;if(v)await api('/channels/'+cur.id+'/messages',{method:'POST',body:JSON.stringify({body:v})});e.target.reset();open(cur)};document.querySelector('[data-testid=new-channel-btn]').onclick=()=>{let n=prompt('Channel name');if(n)api('/workspaces/'+w.slug+'/channels',{method:'POST',body:JSON.stringify({name:n})}).then(()=>load()).catch(alert)};open(channels[0])}
async function open(c){cur=c;document.querySelector('[data-testid=channel-title]').textContent='#'+c.name;document.querySelector('[data-testid=channel-topic]').textContent=c.topic||'';let x=await api('/channels/'+c.id+'/messages'),box=document.querySelector('[data-testid=message-list]');box.innerHTML=x.messages.length?'':'<p>Nothing here yet. Start the conversation!</p>';x.messages.reverse().forEach(m=>{let r=el('div',{className:'row'});r.dataset.testid='message';r.innerHTML='<b>'+m.author.display_name+'</b> <small>'+new Date(m.created_at).toLocaleString()+'</small><div data-testid=message-body>'+m.body+(m.edited_at?' <i>(edited)</i>':'')+'</div>';box.append(r)})}
if(token)load();else auth();
</script></body></html>'''
init()
def run(port):
 os.environ['NODE_ID']=str(port-8000); ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
if __name__=='__main__':
 import sys
 if len(sys.argv)>1 and sys.argv[1]=='irc': run_irc()
 elif len(sys.argv)>1: run(int(sys.argv[1]))

def irc_client(sock,addr):
    sock.settimeout(300); user=None; nick=None; authed=False; joined={}
    def send(x):
        try: sock.sendall((x+'\r\n').encode())
        except: pass
    def numeric(code,text): send(':huddle  %s %s'%(code,text))
    try:
      buf=b''
      while True:
        data=sock.recv(4096)
        if not data: break
        buf+=data
        while b'\n' in buf:
          line,buf=buf.split(b'\n',1); line=line.decode(errors='ignore').strip('\r ')
          if not line: continue
          parts=line.split(' ',2); cmd=parts[0].upper(); rest=parts[1:]
          if cmd=='PASS':
            tok=(rest[0] if rest else '').strip(':')
            r=q('select user_id from tokens where token=?',(tok,),True)
            if r: user=r['user_id'];authed=True
            else: numeric('464',':Password incorrect')
          elif cmd=='NICK':
            n=rest[0] if rest else ''
            if q('select 1 from users where username=?',(n,),True) and (not user or q('select id from users where username=?',(n,),True)['id']!=user):
              numeric('433',n+' :Nickname in use')
            else: nick=n
          elif cmd=='USER':
            if rest: nick=nick or rest[0]
            if authed and nick:
              numeric('001',nick+' :Welcome to Huddle IRC')
              numeric('002',nick+' :Your host is huddle')
              numeric('003',nick+' :This server was created today')
              numeric('004',nick+' huddle 1.0 oiwsz biklmnop')
              numeric('005',nick+' CHANTYPES=# NETWORK=Huddle :are supported')
              numeric('422',':MOTD File is missing')
          elif cmd=='PING': send(':huddle PONG '+(rest[-1] if rest else 'huddle'))
          elif cmd=='PONG': pass
          elif cmd=='QUIT': break
          elif cmd in ('JOIN','NAMES'):
            if not authed or not user: numeric('451',':You have not registered'); continue
            chans=(rest[0] if rest else '').split(',')
            for target in chans:
              if not target.startswith('#') or '/' not in target: numeric('403',target+' :No such channel'); continue
              slug,name=target[1:].split('/',1)
              ch=q('select c.*,w.slug from channels c join workspaces w on w.id=c.workspace_id where w.slug=? and c.name=?',(slug,name),True)
              if not ch: numeric('403',target+' :No such channel'); continue
              if cmd=='JOIN':
                ex('insert or ignore into channel_members values(?,?)',(ch['id'],user));joined[ch['id']]=target
                send(':'+str(nick)+'!'+str(user)+'@localhost JOIN '+target)
              rows=q('select u.username from channel_members cm join users u on u.id=cm.user_id where cm.channel_id=?',(ch['id'],))
              names=' '.join(x['username'] for x in rows)
              numeric('353',nick+' = '+target+' :'+names);numeric('366',nick+' '+target+' :End of /NAMES list.')
          elif cmd=='PRIVMSG':
            if len(rest)<2: continue
            target=rest[0]; body=rest[1].lstrip(':')
            if not target.startswith('#') or '/' not in target: numeric('403',target+' :No such channel');continue
            slug,name=target[1:].split('/',1)
            ch=q('select c.* from channels c join workspaces w on w.id=c.workspace_id where w.slug=? and c.name=?',(slug,name),True)
            if not ch: numeric('403',target+' :No such channel');continue
            if not user: numeric('451',':You have not registered');continue
            if not body: continue
            mid=ex('insert into messages(channel_id,author_id,body,created) values(?,?,?,?)',(ch['id'],user,body,now()))
            event(ch['id'],'message.created',msgobj(q('select * from messages where id=?',(mid,),True)))
          else:
            numeric('421',cmd+' :Unknown command')
    except Exception: pass
    finally:
      try:sock.close()
      except:pass

def run_irc():
  srv=socket.socket();srv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);srv.bind(('0.0.0.0',6667));srv.listen(100)
  while True:
    s,a=srv.accept();threading.Thread(target=irc_client,args=(s,a),daemon=True).start()

if __name__=='__main__' and len(__import__('sys').argv)>1 and __import__('sys').argv[1]=='irc':
 run_irc()
