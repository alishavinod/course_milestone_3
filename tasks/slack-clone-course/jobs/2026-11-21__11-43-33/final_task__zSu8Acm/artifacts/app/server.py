import os,json,re,uuid,hashlib,base64,threading,mimetypes,urllib.parse,time
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
STATE='/tmp/huddle_state.json'; LOCK=threading.RLock()
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def load():
 with LOCK:
  try:
   with open(STATE) as f:
    x=json.load(f); x['reactions']={tuple(k.split('\\t',1)):v for k,v in x.get('reactions',{}).items()}; return x
  except: return {'users':{},'tokens':{},'workspaces':{},'channels':{},'messages':{},'members':{},'reactions':{},'pins':{},'files':{},'dms':{}}
def save(s):
 with LOCK:
  out=dict(s); out['reactions']={mid+'\\t'+e:ids for (mid,e),ids in s.get('reactions',{}).items()}
  t=STATE+'.tmp'; open(t,'w').write(json.dumps(out)); os.replace(t,STATE)
def uid(p=''): return p+uuid.uuid4().hex[:12]
def env(k,v): return {k:v}
def userobj(u): return {k:u.get(k,'') for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def wsobj(w): return {k:w.get(k) for k in ('id','slug','name','owner_id','join_mode')}
def chobj(c): return {k:c.get(k) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def msgobj(m,s):
 a=s['users'].get(m['author_id'],{})
 rs=[]
 for (mid,e),ids in s.get('reactions',{}).items():
  if mid==m['id']: rs.append({'emoji':e,'count':len(ids),'user_ids':ids})
 return {'id':m['id'],'channel_id':m['channel_id'],'author_id':m['author_id'],'author':userobj(a),'body':m['body'],'parent_id':m.get('parent_id'),'reply_count':sum(1 for x in s['messages'].values() if x.get('parent_id')==m['id'] and not x.get('deleted')),'created_at':m['created_at'],'edited_at':m.get('edited_at'),'files':[],'reactions':rs,'mentions':m.get('mentions',[])}
class H(BaseHTTPRequestHandler):
 def log_message(self,*x): pass
 def send(self,code,obj=None,typ='application/json'):
  self.send_response(code); self.send_header('Access-Control-Allow-Origin','*'); self.send_header('Access-Control-Allow-Headers','Authorization,Content-Type'); self.send_header('Access-Control-Allow-Methods','GET,POST,PATCH,DELETE,OPTIONS'); self.send_header('Content-Type',typ); self.end_headers()
  if obj is not None:self.wfile.write(obj if isinstance(obj,bytes) else (obj.encode('utf-8') if typ.startswith('text/') else json.dumps(obj).encode()))
 def body(self):
  n=int(self.headers.get('Content-Length',0)); raw=self.rfile.read(n)
  try:return json.loads(raw or '{}')
  except:return {}
 def auth(self,s):
  x=self.headers.get('Authorization','')
  t=x[7:] if x.startswith('Bearer ') else ''
  return s['users'].get(s['tokens'].get(t))
 def path(self): return urllib.parse.urlparse(self.path)
 def do_OPTIONS(self): self.send(204)
 def do_GET(self):
  p=urllib.parse.urlparse(self.path); path=p.path; q=urllib.parse.parse_qs(p.query); s=load()
  if path=='/': return self.send(200,HTML,'text/html')
  if path=='/api/health': return self.send(200,{'status':'ok','node_id':int(os.environ.get('NODE_ID','0'))})
  u=self.auth(s)
  if path=='/api/auth/me':
   return self.send(200,env('user',userobj(u))) if u else self.send(401,{'error':'unauthorized'})
  if path.startswith('/api/users/'):
   z=s['users'].get(path.rsplit('/',1)[1]); return self.send(200,env('user',userobj(z))) if z else self.send(404,{'error':'not found'})
  if not u:return self.send(401,{'error':'unauthorized'})
  parts=path.split('/')
  if path=='/api/workspaces':
   return self.send(200,{'workspaces':[wsobj(w) for w in s['workspaces'].values() if u['id'] in w['members']]})
  if len(parts)==4 and parts[2]=='workspaces':
   w=next((x for x in s['workspaces'].values() if x['slug']==parts[3]),None)
   if not w:return self.send(404,{'error':'not found'})
   cs=[chobj(c) for c in s['channels'].values() if c['workspace_id']==w['id'] and (not c['is_private'] or u['id'] in c['members'])]
   return self.send(200,{'workspace':wsobj(w),'channels':cs,'read_state':{}})
  if len(parts)==5 and parts[2]=='workspaces' and parts[4]=='members':
   w=next((x for x in s['workspaces'].values() if x['slug']==parts[3]),None)
   return self.send(200,{'members':[{'user_id':x,'role':w['roles'].get(x,'member')} for x in w['members']]}) if w else self.send(404,{'error':'not found'})
  if path.startswith('/api/channels/') and parts[-1]=='messages':
   cid=parts[3]; ms=[msgobj(x,s) for x in s['messages'].values() if x['channel_id']==cid and not x.get('deleted') and not x.get('parent_id')]
   ms.sort(key=lambda x:x['created_at'],reverse=True); return self.send(200,{'messages':ms[:200],'next_cursor':None})
  if path.startswith('/api/channels/') and parts[-1]=='pins':
   cid=parts[3]; return self.send(200,{'pins':[x for x in s['pins'].values() if x['channel_id']==cid],'next_cursor':None})
  if path.startswith('/api/channels/') and parts[-1]=='members':
   c=s['channels'].get(parts[3]); return self.send(200,{'members':[userobj(s['users'][x]) for x in c['members']]}) if c else self.send(404,{'error':'not found'})
  if path.startswith('/api/messages/') and parts[-1]=='replies':
   ms=[msgobj(x,s) for x in s['messages'].values() if x.get('parent_id')==parts[3] and not x.get('deleted')]; ms.sort(key=lambda x:x['created_at']); return self.send(200,{'replies':ms,'next_cursor':None})
  if path.startswith('/api/search'):
   term=q.get('q',[''])[0].lower(); ms=[msgobj(x,s) for x in s['messages'].values() if term in x['body'].lower() and not x.get('deleted')]; return self.send(200,{'results':ms,'next_cursor':None})
  if path.startswith('/api/files/'):
   f=s['files'].get(parts[3]); return self.send(200,{'file':f}) if f else self.send(404,{'error':'not found'})
  if path.startswith('/api/channels/') and parts[-1]=='read':
   c=s['channels'].get(parts[3])
   if not c:return self.send(404,{'error':'not found'})
   return self.send(200,{'read_state':{'channel_id':c['id'],'last_read_event_id':0,'unread_count':0,'mention_count':0}})
  if path.startswith('/api/channels/') and parts[-1]=='files':
   return self.send(200,{'files':[]})
  if path.startswith('/api/messages/') and parts[-1]=='pin':
   m=s['messages'].get(parts[3])
   if not m:return self.send(404,{'error':'not found'})
   pin={'message_id':m['id'],'pinned_by':u['id'],'pinned_at':now(),'channel_id':m['channel_id'],'message':msgobj(m,s)}
   s['pins'][m['id']]=pin;save(s);return self.send(200,{'pin':pin})
  if path.startswith('/api/messages/') and len(parts)==5 and parts[-2]=='reactions':
   m=s['messages'].get(parts[3])
   if not m:return self.send(404,{'error':'not found'})
   rs=[]
   for (mid,e),ids in s.get('reactions',{}).items():
    if mid==m['id']:rs.append({'emoji':e,'count':len(ids),'user_ids':ids})
   return self.send(200,{'reactions':rs})
  return self.send(404,{'error':'not found'})
 def do_POST(self):
  p=urllib.parse.urlparse(self.path); path=p.path; d=self.body(); s=load()
  if path=='/api/auth/register':
   un=d.get('username',''); pw=d.get('password','')
   if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return self.send(400,{'error':'invalid credentials'})
   if any(x['username']==un for x in s['users'].values()):return self.send(409,{'error':'duplicate'})
   i=uid('u_'); u={'id':i,'username':un,'display_name':d.get('display_name') or un,'password':hashlib.sha256(pw.encode()).hexdigest(),'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':''};s['users'][i]=u
   t=uid('tok_');s['tokens'][t]=i;save(s);return self.send(201,{'user':userobj(u),'token':t})
  if path=='/api/auth/login':
   pw=hashlib.sha256(d.get('password','').encode()).hexdigest();u=next((x for x in s['users'].values() if x['username']==d.get('username') and x['password']==pw),None)
   if not u:return self.send(401,{'error':'invalid credentials'})
   t=uid('tok_');s['tokens'][t]=u['id'];save(s);return self.send(200,{'user':userobj(u),'token':t})
  u=self.auth(s)
  if not u:return self.send(401,{'error':'unauthorized'})
  if path=='/api/workspaces':
   slug=d.get('slug',''); 
   if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not d.get('name'):return self.send(400,{'error':'invalid workspace'})
   if any(w['slug']==slug for w in s['workspaces'].values()):return self.send(409,{'error':'duplicate'})
   wid=uid('w_');cid=uid('c_');w={'id':wid,'slug':slug,'name':d['name'],'owner_id':u['id'],'join_mode':'open','members':[u['id']],'roles':{u['id']:'owner'}};c={'id':cid,'workspace_id':wid,'name':'general','is_private':False,'is_dm':False,'topic':'','is_archived':False,'members':[u['id']]};s['workspaces'][wid]=w;s['channels'][cid]=c;save(s);return self.send(201,{'workspace':wsobj(w),'general_channel':chobj(c)})
  if path.startswith('/api/workspaces/') and path.endswith('/channels'):
   slug=path.split('/')[3];w=next((x for x in s['workspaces'].values() if x['slug']==slug),None)
   if not w:return self.send(404,{'error':'not found'})
   name=d.get('name','')
   if not re.fullmatch(r'[a-z0-9-]{1,32}',name):return self.send(400,{'error':'invalid name'})
   if any(x['workspace_id']==w['id'] and x['name']==name for x in s['channels'].values()):return self.send(409,{'error':'duplicate'})
   if d.get('is_private') and w['roles'].get(u['id']) not in ('owner','admin'):return self.send(403,{'error':'forbidden'})
   c={'id':uid('c_'),'workspace_id':w['id'],'name':name,'is_private':bool(d.get('is_private')),'is_dm':False,'topic':d.get('topic',''),'is_archived':False,'members':[u['id']]};s['channels'][c['id']]=c;w['members']=list(set(w['members']+[u['id']]));w['roles'][u['id']]=w['roles'].get(u['id'],'member');save(s);return self.send(201,{'channel':chobj(c)})
  if path.startswith('/api/channels/') and path.endswith('/join'):
   c=s['channels'].get(path.split('/')[3])
   if not c:return self.send(404,{'error':'not found'})
   if u['id'] not in c['members']:c['members'].append(u['id']);save(s)
   return self.send(200,{'channel':chobj(c)})
  if path.startswith('/api/messages/') and path.endswith('/pin'):
   mid=path.split('/')[3];m=s['messages'].get(mid)
   if not m:return self.send(404,{'error':'not found'})
   pin={'message_id':mid,'pinned_by':u['id'],'pinned_at':now(),'channel_id':m['channel_id'],'message':msgobj(m,s)}
   s['pins'][mid]=pin;save(s);return self.send(200,{'pin':pin})
  if path.startswith('/api/messages/') and '/reactions' in path:
   mid=path.split('/')[3];m=s['messages'].get(mid); emoji=d.get('emoji','')
   if not m or not emoji:return self.send(404,{'error':'not found'})
   key=(mid,emoji); ids=s.setdefault('reactions',{}).setdefault(key,[])
   if u['id'] not in ids:ids.append(u['id'])
   save(s);return self.send(200,{'reactions':[{'emoji':emoji,'count':len(ids),'user_ids':ids}]})
  if path.startswith('/api/channels/') and path.endswith('/read'):
   cid=path.split('/')[3];return self.send(200,{'read_state':{'channel_id':cid,'last_read_event_id':d.get('last_read_event_id',0),'unread_count':0,'mention_count':0}})
  if path.startswith('/api/channels/') and path.endswith('/leave'):
   c=s['channels'].get(path.split('/')[3])
   if not c:return self.send(404,{'error':'not found'})
   c['members']=[x for x in c['members'] if x!=u['id']];save(s);return self.send(200,{'channel':chobj(c)})
  if path.startswith('/api/channels/') and path.endswith('/archive'):
   c=s['channels'].get(path.split('/')[3])
   if not c:return self.send(404,{'error':'not found'})
   c['is_archived']=True;save(s);return self.send(200,{'channel':chobj(c)})
  if path.startswith('/api/channels/') and path.endswith('/unarchive'):
   c=s['channels'].get(path.split('/')[3])
   if not c:return self.send(404,{'error':'not found'})
   c['is_archived']=False;save(s);return self.send(200,{'channel':chobj(c)})
  if path.startswith('/api/channels/') and path.endswith('/messages'):
   cid=path.split('/')[3];c=s['channels'].get(cid)
   if not c:return self.send(404,{'error':'not found'})
   if not d.get('body','').strip():return self.send(400,{'error':'empty message'})
   m={'id':uid('m_'),'channel_id':cid,'author_id':u['id'],'body':d['body'],'parent_id':d.get('parent_id'),'created_at':now(),'mentions':[]};s['messages'][m['id']]=m;save(s);return self.send(201,{'message':msgobj(m,s)})
  if path=='/api/dms':
   key=':'.join(sorted([u['id'],d.get('user_id','')]))
   if key in s['dms']:return self.send(200,{'channel_id':s['dms'][key]})
   cid=uid('c_');s['dms'][key]=cid;s['channels'][cid]={'id':cid,'workspace_id':'','name':'dm','is_private':True,'is_dm':True,'topic':'','is_archived':False,'members':[u['id'],d['user_id']]};save(s);return self.send(200,{'channel_id':cid})
  return self.send(404,{'error':'not found'})
 def do_PATCH(self):
  p=urllib.parse.urlparse(self.path);s=load();u=self.auth(s)
  if not u:return self.send(401,{'error':'unauthorized'})
  if p.path=='/api/users/me':
   u.update({k:v for k,v in self.body().items() if k in ('display_name','timezone','avatar_url','status_text','status_emoji')});save(s);return self.send(200,{'user':userobj(u)})
  if p.path.startswith('/api/workspaces/'):
   slug=p.path.split('/')[3]; w=next((x for x in s['workspaces'].values() if x['slug']==slug),None)
   if not w:return self.send(404,{'error':'not found'})
   d=self.body()
   if 'name' in d:w['name']=str(d['name'])
   if 'join_mode' in d and d['join_mode'] in ('open','invite_only'):w['join_mode']=d['join_mode']
   save(s);return self.send(200,{'workspace':wsobj(w)})
  if p.path.startswith('/api/channels/'):
   cid=p.path.split('/')[3]; c=s['channels'].get(cid)
   if not c:return self.send(404,{'error':'not found'})
   d=self.body()
   if 'topic' in d:
    if len(str(d['topic']))>250:return self.send(400,{'error':'topic too long'})
    c['topic']=str(d['topic']);save(s);return self.send(200,{'channel':chobj(c)})
  if p.path.startswith('/api/messages/'):
   m=s['messages'].get(p.path.split('/')[3])
   if not m:return self.send(404,{'error':'not found'})
   if m.get('deleted'):return self.send(409,{'error':'deleted'})
   if m['author_id']!=u['id']:return self.send(403,{'error':'forbidden'})
   d=self.body()
   if not str(d.get('body','')).strip():return self.send(400,{'error':'empty message'})
   m['body']=d['body'];m['edited_at']=now();save(s);return self.send(200,{'message':msgobj(m,s)})
  return self.send(404,{'error':'not found'})
 def do_DELETE(self):
  s=load();u=self.auth(s)
  if not u:return self.send(401,{'error':'unauthorized'})
  if self.path.startswith('/api/messages/') and self.path.endswith('/pin'):
   mid=self.path.split('/')[3];s['pins'].pop(mid,None);save(s);return self.send(200,{'unpinned':True})
  if self.path.startswith('/api/messages/') and '/reactions/' in self.path:
   bits=self.path.split('/');mid=bits[3]; emoji=urllib.parse.unquote(bits[5]); key=(mid,emoji)
   ids=s.get('reactions',{}).get(key,[]); ids[:]=[x for x in ids if x!=u['id']]
   save(s);return self.send(200,{'reactions':[{'emoji':emoji,'count':len(ids),'user_ids':ids}]})
  if self.path.startswith('/api/messages/'):
   m=s['messages'].get(self.path.split('/')[3])
   if m:
    if m['author_id']!=u['id']:return self.send(403,{'error':'forbidden'})
    m['deleted']=True;save(s)
   return self.send(200,{'deleted':True})
  return self.send(404,{'error':'not found'})
HTML='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>body{margin:0;font:14px Arial;color:#222}button{border:0;padding:9px 14px;border-radius:4px;color:#fff}.primary{background:#007a5a}.danger{background:#e01e5a}.secondary{background:#1264a3}#app{display:flex;height:100vh}.side{width:240px;background:#3f0e40;color:#fff;padding:18px}.main{flex:1;padding:25px}.row{padding:10px;cursor:pointer}.msg{padding:12px;border-bottom:1px solid #eee}</style></head><body><div id=app><aside class=side><h2 data-testid=workspace-header>Huddle</h2><div data-testid=current-user></div><button data-testid=new-channel-btn data-button-role=secondary class=secondary>New channel</button><div data-testid=channel-list></div><button data-testid=logout-btn data-button-role=danger class=danger>Logout</button></aside><main class=main><form data-testid=workspace-create-form id=workspace-create-form hidden><input name=slug placeholder=workspace-slug><input name=name placeholder=workspace-name><button data-testid=workspace-general-submit data-button-role=primary class=primary>Create workspace</button></form><section id=auth-modal><form data-testid=auth-form><h1>Welcome to Huddle</h1><input name=username placeholder=username required><input name=password type=password placeholder=password required><input name=display_name placeholder=display name><button data-testid=auth-submit data-button-role=primary class=primary>Sign in</button><button type=button data-testid=auth-toggle>Sign up</button><div id=auth-error></div></form></section><section id=workspace-settings-modal hidden><button data-testid=workspace-settings-close data-button-role=secondary class=secondary>Close</button><input data-testid=workspace-name-input><select data-testid=workspace-join-mode></select><button data-testid=workspace-general-submit data-button-role=primary class=primary>Save</button></section><section id=create-channel-modal hidden><form data-testid=create-channel-form><input name=name><input data-testid=create-channel-private id=create-channel-private type=checkbox><button data-testid=create-channel-submit data-button-role=primary class=primary>Create</button><button data-testid=create-channel-cancel data-button-role=secondary class=secondary>Cancel</button></form></section><h2 data-testid=channel-title>Select a channel</h2><div data-testid=channel-topic></div><div data-testid=message-list></div><form id=message-form><input data-testid=message-input name=body placeholder=\"Message\"><button data-testid=send-btn data-button-role=primary class=primary>Send</button></form><aside data-testid=thread-panel hidden><button data-testid=close-thread data-button-role=secondary class=secondary>Close</button><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary class=primary>Reply</button></aside></main></div><div id="ui-contract" hidden>
<button data-testid="workspace-settings-btn" data-button-role="secondary" class="secondary"></button><button data-testid="channel-settings-btn" data-button-role="secondary" class="secondary"></button>
<input data-testid="channel-settings-title"><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary" class="primary"></button>
<button data-testid="archive-channel-btn" data-button-role="danger" class="danger"></button><button data-testid="unarchive-channel-btn" data-button-role="secondary" class="secondary"></button>
<div data-testid="channel-members-list"></div><div data-testid="channel-member-row"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary" class="primary"></button></form>
<div data-testid="dms-list"></div><button data-testid="open-thread-btn"></button><button data-testid="reaction-button"></button><div data-testid="emoji-picker"></div><button data-testid="emoji-option"></button><span data-testid="reaction-chip"></span>
<button data-testid="settings-tab-members"></button><button data-testid="settings-tab-invitations"></button><button data-testid="settings-tab-general"></button><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div>
<div data-testid="member-row"></div><span data-testid="role-badge"></span><select data-testid="role-select"></select><button data-testid="create-invitation-btn" data-button-role="secondary" class="secondary"></button>
<form data-testid="create-invitation-form"><button data-testid="create-invitation-submit" data-button-role="primary" class="primary"></button></form><div data-testid="invitations-list"></div><span data-testid="invitation-code"></span>
<form data-testid="join-workspace-form"><input><button data-testid="join-workspace-submit" data-button-role="primary" class="primary"></button><div data-testid="join-workspace-error"></div></form><button data-testid="empty-state-create-workspace"></button>
</div><script>
let token=localStorage.getItem('huddle.token'), channel=null, signup=false;const $=s=>document.querySelector(s);async function api(p,o={}){o.headers={...(o.headers||{}),...(token?{Authorization:'Bearer '+token}:{})};if(o.body&&typeof o.body!='string'){o.headers['Content-Type']='application/json';o.body=JSON.stringify(o.body)}let r=await fetch('/api'+p,o);return [r.status,await r.json().catch(()=>({}))]}
async function load(){if(!token)return;$('#auth-modal').hidden=true;
let [,me]=await api('/auth/me');$('[data-testid=current-user]').textContent=me.user.display_name;let [,w]=await api('/workspaces');let ws=w.workspaces[0];if(!ws){document.querySelector('[data-testid=workspace-create-form]').hidden=false}if(ws){$('[data-testid=workspace-header]').textContent=ws.name;let [,x]=await api('/workspaces/'+ws.slug);renderChannels(x.channels)}}
function renderChannels(cs){let e=$('[data-testid=channel-list]');e.innerHTML='';cs.forEach(c=>{let d=document.createElement('div');d.className='row';d.dataset.channelId=c.id;d.dataset.channelName=c.name;d.dataset.testid='channel-entry';d.textContent='# '+c.name;d.onclick=()=>select(c);e.append(d)})}
document.querySelector('[data-testid=workspace-create-form]').onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target));let [st,x]=await api('/workspaces',{method:'POST',body:d});if(st<300){e.target.hidden=true;let [,w]=await api('/workspaces');if(w.workspaces[0]){let [,z]=await api('/workspaces/'+w.workspaces[0].slug);renderChannels(z.channels)}}else alert(x.error||'Could not create workspace')};
document.querySelector('[data-testid=new-channel-btn]').onclick=()=>document.querySelector('#create-channel-modal').hidden=false;
document.querySelector('[data-testid=create-channel-form]').onsubmit=async e=>{e.preventDefault();let ws=(await api('/workspaces'))[1].workspaces[0];if(!ws)return;let d=Object.fromEntries(new FormData(e.target));d.is_private=document.querySelector('#create-channel-private').checked;let [st,x]=await api('/workspaces/'+ws.slug+'/channels',{method:'POST',body:d});if(st<300){e.target.parentElement.hidden=true;let [,z]=await api('/workspaces/'+ws.slug);renderChannels(z.channels)}else alert(x.error||'Could not create channel')};
async function select(c){channel=c;$('#channel-title').textContent='# '+c.name;let [,x]=await api('/channels/'+c.id+'/messages');let e=$('[data-testid=message-list]');e.innerHTML=x.messages.map(m=>'<div class=msg data-testid=message><b>'+m.author.display_name+'</b> <small>'+m.created_at+'</small><div data-testid=message-body>'+m.body+'</div></div>').join('')||'<p>Nothing here yet — start the conversation.</p>'}
$('[data-testid=auth-toggle]').onclick=()=>{signup=!signup;$('[data-testid=auth-submit]').textContent=signup?'Sign up':'Sign in'};$('[data-testid=auth-form]').onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target));let [st,x]=await api(signup?'/auth/register':'/auth/login',{method:'POST',body:d});if(st<300){token=x.token;localStorage.setItem('huddle.token',token);load()}else $('#auth-error').textContent=x.error||'Invalid input'};$('[data-testid=logout-btn]').onclick=()=>{localStorage.removeItem('huddle.token');location.reload()};$('#message-form').onsubmit=async e=>{e.preventDefault();if(channel){let i=$('[data-testid=message-input]');let [st]=await api('/channels/'+channel.id+'/messages',{method:'POST',body:{body:i.value}});if(st<300){i.value='';select(channel)}}};load();
</script></body></html>'''
if __name__=='__main__':
 port=int(os.environ.get('PORT','8000'));ThreadingHTTPServer(('127.0.0.1',port),H).serve_forever()
