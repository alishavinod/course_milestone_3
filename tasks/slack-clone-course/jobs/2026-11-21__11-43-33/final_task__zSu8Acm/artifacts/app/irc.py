import socket,threading,json,os,uuid
from datetime import datetime,timezone
STATE='/tmp/huddle_state.json'; clients=[]; lock=threading.RLock()
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def load():
 with lock:
  try:
   with open(STATE) as f:return json.load(f)
  except:return {}
def save(s):
 with lock:
  t=STATE+'.tmp';open(t,'w').write(json.dumps(s));os.replace(t,STATE)
def send(c,x):
 try:c.sendall((x+'\r\n').encode())
 except:pass
def find(s,name):
 if not name.startswith('#') or '/' not in name:return None
 slug,cn=name[1:].split('/',1)
 w=next((x for x in s.get('workspaces',{}).values() if x.get('slug')==slug),None)
 return next((x for x in s.get('channels',{}).values() if w and x.get('workspace_id')==w['id'] and x.get('name')==cn),None)
def client(sock,addr):
 nick=None; uid=None; joined={}
 with lock:clients.append((sock,joined))
 try:
  buf=''
  while True:
   z=sock.recv(4096)
   if not z:break
   buf+=z.decode(errors='ignore')
   while '\n' in buf:
    line,buf=buf.split('\n',1); line=line.strip('\r')
    if not line:continue
    a=line.split(); cmd=a[0].upper(); s=load()
    if cmd=='PASS':
     tok=a[1] if len(a)>1 else ''; uid=s.get('tokens',{}).get(tok)
     if not uid:send(sock,':huddle 464 * :Password incorrect')
    elif cmd=='NICK':
     n=a[1] if len(a)>1 else ''
     if any(x[0]==n for x in [('',)]):pass
     nick=n
    elif cmd=='USER' and nick and uid:
     send(sock,f':huddle 001 {nick} :Welcome to Huddle IRC');send(sock,f':huddle 002 {nick} :Your host is huddle');send(sock,f':huddle 003 {nick} :This server was created today');send(sock,f':huddle 004 {nick} huddle 1.0 oi');send(sock,f':huddle 005 {nick} CHANTYPES=# :are supported');send(sock,f':huddle 422 {nick} :MOTD File is missing')
    elif cmd=='PING':send(sock,':huddle PONG '+(a[1] if len(a)>1 else 'huddle'))
    elif cmd in ('JOIN','NAMES'):
     name=a[1] if len(a)>1 else '';c=find(s,name)
     if not c:send(sock,f':huddle 403 {nick or "*"} {name} :No such channel');continue
     joined[name]=c['id']
     if cmd=='JOIN':
      send(sock,f':{nick}!{uid}@localhost JOIN {name}')
      members=[s.get('users',{}).get(x,{}).get('username',x) for x in c.get('members',[])]
      send(sock,f':huddle 353 {nick} = {name} :{" ".join(members)}');send(sock,f':huddle 366 {nick} {name} :End of NAMES list')
     else: send(sock,f':huddle 353 {nick} = {name} :');send(sock,f':huddle 366 {nick} {name} :End of NAMES list')
    elif cmd=='PRIVMSG':
     name=a[1] if len(a)>1 else ''; body=line.split(' :',1)[1] if ' :' in line else ''
     cid=joined.get(name); 
     if cid and body:
      m={'id':'m_'+uuid.uuid4().hex[:12],'channel_id':cid,'author_id':uid,'body':body,'created_at':now(),'mentions':[]};s.setdefault('messages',{})[m['id']]=m;save(s)
      with lock:
       for cs,chs in list(clients):
        if chs.get(name)==cid:send(cs,f':{nick}!{uid}@localhost PRIVMSG {name} :{body}')
    elif cmd=='QUIT':break
    elif cmd not in ('MODE','TOPIC','WHO','LIST','PONG'):send(sock,f':huddle 421 {nick or "*"} {cmd} :Unknown command')
 finally:
  with lock:
   try:clients.remove((sock,joined))
   except:pass
  sock.close()
def main():
 q=socket.socket();q.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);q.bind(('0.0.0.0',6667));q.listen()
 while True:
  c,a=q.accept();threading.Thread(target=client,args=(c,a),daemon=True).start()
if __name__=='__main__':main()
