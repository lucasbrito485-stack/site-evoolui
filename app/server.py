import argparse, re, threading, datetime as dt, hashlib, hmac, json, os, secrets, sqlite3, time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parent.parent
DB=Path(os.environ.get('EVOOLUI_DB',str(ROOT/'data/evoolui.db')))
import sys
sys.path.insert(0, str(ROOT))
from app import file_storage, operations
MANAGERS=('administrador','socio')
ROLES=(*MANAGERS,'funcionario','cliente')
ATTEMPTS={}
def connect():
    db=sqlite3.connect(DB,timeout=20); db.row_factory=sqlite3.Row; db.execute('PRAGMA foreign_keys=ON'); return db
def password_hash(p,salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256',p.encode(),bytes.fromhex(salt),310000).hex()
def verify(p,stored): return hmac.compare_digest(password_hash(p,stored.split(':')[0]),stored)
def initialize():
    DB.parent.mkdir(parents=True,exist_ok=True)
    with connect() as db:
        db.executescript('''
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY,company TEXT NOT NULL,contact TEXT NOT NULL,phone TEXT DEFAULT '',instagram TEXT DEFAULT '',services TEXT DEFAULT '',monthly_value INTEGER DEFAULT 0,due_day INTEGER DEFAULT 10,active INTEGER DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL,client_id INTEGER REFERENCES clients(id),active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS assignments(client_id INTEGER REFERENCES clients(id),user_id INTEGER REFERENCES users(id),PRIMARY KEY(client_id,user_id));
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY,title TEXT NOT NULL,client_id INTEGER NOT NULL REFERENCES clients(id),assignee_id INTEGER NOT NULL REFERENCES users(id),due_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pendente',priority TEXT NOT NULL DEFAULT 'media',description TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,title TEXT NOT NULL,client_id INTEGER NOT NULL REFERENCES clients(id),assignee_id INTEGER NOT NULL REFERENCES users(id),starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,kind TEXT NOT NULL DEFAULT 'gravacao',status TEXT NOT NULL DEFAULT 'agendado',description TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),csrf TEXT NOT NULL,expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),action TEXT NOT NULL,entity TEXT NOT NULL,entity_id INTEGER,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
''')
        file_storage.initialize(db)
        operations.initialize(db)
    os.chmod(DB,0o600)
def audit(db,u,action,entity,rid): db.execute('INSERT INTO audit(user_id,action,entity,entity_id) VALUES(?,?,?,?)',(u['id'],action,entity,rid))
def seed():
    initialize(); pw=os.environ.get('EVOOLUI_DEMO_PASSWORD','')
    if len(pw)<12: raise SystemExit('Defina EVOOLUI_DEMO_PASSWORD com pelo menos 12 caracteres.')
    with connect() as db:
        if db.execute('SELECT count(*) FROM users').fetchone()[0]: raise SystemExit('Banco já possui usuários; nada sobrescrito.')
        db.execute("INSERT INTO settings VALUES('demo','1')")
        for name,contact,services,value in [('Aurora Studio • fictício','Marina Costa','Social media, Reels',280000),('Botânica Café • fictício','Pedro Lima','Produção de conteúdo',190000),('Forma Arquitetura • fictício','Ana Souza','Social media, Tráfego pago',350000)]:
            db.execute('INSERT INTO clients(company,contact,services,monthly_value) VALUES(?,?,?,?)',(name,contact,services,value))
        for name,email,role,cid in [('Administrador Demo','admin@evoolui.test','administrador',None),('Sócia Demo','socia@evoolui.test','socio',None),('Beatriz Demo','bia@evoolui.test','funcionario',None),('Lucas Demo','lucas@evoolui.test','funcionario',None),('Cliente Demo','cliente@evoolui.test','cliente',1)]:
            db.execute('INSERT INTO users(name,email,password,role,client_id) VALUES(?,?,?,?,?)',(name,email,password_hash(pw),role,cid))
        db.executemany('INSERT INTO assignments VALUES(?,?)',[(1,3),(2,3),(3,4)])
        today=dt.datetime.now(ZoneInfo('America/Sao_Paulo')).date()
        for title,cid,uid,offset,status,priority in [('Planejar campanha de novembro',1,3,2,'em_andamento','alta'),('Finalizar roteiro de Reels',2,3,0,'pendente','alta'),('Revisar calendário editorial',3,4,3,'pendente','media'),('Entregar seleção de fotografias',1,3,-1,'pendente','alta'),('Organizar referências visuais',3,4,1,'concluida','baixa')]:
            db.execute('INSERT INTO tasks(title,client_id,assignee_id,due_at,status,priority) VALUES(?,?,?,?,?,?)',(title,cid,uid,f'{today+dt.timedelta(days=offset)}T17:00',status,priority))
        for title,cid,uid,hour,kind in [('Gravação • nova coleção',1,3,10,'gravacao'),('Alinhamento de campanha',3,4,14,'reuniao'),('Fotos do novo cardápio',2,3,16,'gravacao')]:
            db.execute('INSERT INTO events(title,client_id,assignee_id,starts_at,ends_at,kind) VALUES(?,?,?,?,?,?)',(title,cid,uid,f'{today}T{hour:02}:00',f'{today}T{hour+1:02}:00',kind))
    print('Dados fictícios criados. Contas em README.md; senha definida pela variável fornecida.')
def bootstrap():
    initialize(); email=os.environ.get('EVOOLUI_ADMIN_EMAIL','').lower().strip(); pw=os.environ.get('EVOOLUI_ADMIN_PASSWORD','')
    if '@' not in email or len(pw)<12: raise SystemExit('Defina EVOOLUI_ADMIN_EMAIL e EVOOLUI_ADMIN_PASSWORD (mínimo 12 caracteres).')
    with connect() as db:
        if db.execute('SELECT count(*) FROM users').fetchone()[0]: raise SystemExit('Banco já inicializado.')
        db.execute('INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)',('Administrador',email,password_hash(pw),'administrador'))
    print('Administrador criado.')
class Error(Exception):
    def __init__(self,message,status=400): self.message,self.status=message,status
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def headers_out(self,kind,size):
        for k,v in {'Content-Type':kind,'Content-Length':str(size),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'same-origin','Content-Security-Policy':"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items(): self.send_header(k,v)
    def reply(self,data,status=200,cookie=None):
        body=json.dumps(data,ensure_ascii=False).encode(); self.send_response(status); self.headers_out('application/json; charset=utf-8',len(body))
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(body)
    def body(self):
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<0 or length>65536: raise Error('Requisição muito grande.',413)
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict): raise ValueError()
            return data
        except (ValueError,UnicodeError): raise Error('JSON inválido.')
    def session(self,db):
        cookie=SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except Exception: raise Error('Sessão inválida.',401)
        token=cookie.get('evoolui_session'); token=hashlib.sha256(token.value.encode()).hexdigest() if token else ''
        r=db.execute('SELECT u.*,s.csrf,s.token FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1',(token,int(time.time()))).fetchone()
        if not r: raise Error('Entre na sua conta para continuar.',401)
        if self.command not in ('GET','HEAD') and not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),r['csrf']): raise Error('Token de segurança inválido. Atualize a página.',403)
        return dict(r)
    def visible(self,db,u):
        if u['role'] in MANAGERS: return {r[0] for r in db.execute('SELECT id FROM clients')}
        if u['role']=='cliente': return {u['client_id']} if u['client_id'] else set()
        return {r[0] for r in db.execute('SELECT client_id FROM assignments WHERE user_id=?',(u['id'],))}
    def data(self,db,u):
        ids=self.visible(db,u); clients=[]; staff=[]
        for r in db.execute('SELECT * FROM clients ORDER BY company'):
            if r['id'] not in ids: continue
            c=dict(r); c['responsibles']=[x[0] for x in db.execute('SELECT user_id FROM assignments WHERE client_id=?',(r['id'],))]
            if u['role'] not in MANAGERS:
                for k in ['monthly_value','due_day']: c.pop(k)
            clients.append(c)
        for r in db.execute('SELECT id,name,email,role,client_id,active FROM users ORDER BY name'):
            if u['role'] in MANAGERS or r['id']==u['id']: staff.append(dict(r))
            elif u['role']=='funcionario' and r['role']=='funcionario': staff.append({'id':r['id'],'name':r['name'],'role':r['role']})
        def items(table):
            rows=db.execute(f'SELECT t.*,u.name AS assignee_name,c.company FROM {table} t JOIN users u ON u.id=t.assignee_id JOIN clients c ON c.id=t.client_id ORDER BY t.id DESC')
            return [dict(r) for r in rows if r['client_id'] in ids and (u['role'] in MANAGERS or u['role']=='cliente' or r['assignee_id']==u['id'])]
        contracted=db.execute("SELECT COALESCE(sum(amount),0) FROM contracts WHERE status='ativo' AND starts_on<=? AND (ends_on IS NULL OR ends_on>=?)",(operations.now_local().date().isoformat(),operations.now_local().date().isoformat())).fetchone()[0] if u['role'] in MANAGERS else None
        return {'contracted_monthly':contracted,'now':dt.datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat(timespec='minutes')[:16],'user':{k:u[k] for k in ['id','name','email','role','client_id']},'csrf':u['csrf'],'demo':bool(db.execute("SELECT 1 FROM settings WHERE key='demo' AND value='1'").fetchone()),'clients':clients,'users':staff,'tasks':items('tasks'),'events':items('events'),'audit':[dict(r) for r in db.execute('SELECT a.*,u.name FROM audit a JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 30')] if u['role']=='administrador' else []}
    def handle_api(self,path):
        with connect() as db:
            if path=='/api/login' and self.command=='POST':
                d=self.body(); key=self.client_address[0]; now=time.time(); ATTEMPTS[key]=[t for t in ATTEMPTS.get(key,[]) if now-t<900]
                if len(ATTEMPTS[key])>=15: raise Error('Muitas tentativas. Aguarde 15 minutos.',429)
                r=db.execute('SELECT * FROM users WHERE email=? AND active=1',(str(d.get('email','')).lower().strip(),)).fetchone()
                if not r or not verify(str(d.get('password','')),r['password']):
                    ATTEMPTS[key].append(now); raise Error('E-mail ou senha incorretos.',401)
                ATTEMPTS.pop(key,None); token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
                db.execute('DELETE FROM sessions WHERE expires<=?',(int(now),)); db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),r['id'],csrf,int(now)+28800)); audit(db,r,'login','users',r['id'])
                secure='; Secure' if os.environ.get('EVOOLUI_SECURE_COOKIE')=='1' else ''
                db.commit(); self.reply({'ok':True},cookie=f'evoolui_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{secure}'); return
            u=self.session(db)
            if path.startswith('/api/notifications'):
                operations.notifications(self,db,u,path); return
            if path.startswith('/api/recordings') or re.fullmatch(r'/api/clients/\d+/availability',path):
                operations.recordings(self,db,u,path,audit); return
            if path.startswith('/api/finance'):
                operations.financial(self,db,u,path,audit); return
            if path.startswith('/api/files/') or re.fullmatch(r'/api/clients/\d+/files',path):
                file_storage.handle(self,db,u,path,DB,audit); return
            if path=='/api/state' and self.command=='GET': self.reply(self.data(db,u)); return
            if path=='/api/logout' and self.command=='POST':
                db.execute('DELETE FROM sessions WHERE token=?',(u['token'],)); db.commit(); self.reply({'ok':True},cookie='evoolui_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'); return
            parts=path.strip('/').split('/')
            if len(parts) not in (2,3) or parts[1] not in ('clients','users','tasks','events'): raise Error('Não encontrado.',404)
            table=parts[1]
            try: rid=int(parts[2]) if len(parts)==3 else None
            except ValueError: raise Error('Identificador inválido.')
            if self.command not in ('POST','PUT') or ((rid is None)!=(self.command=='POST')): raise Error('Método não permitido.',405)
            d=self.body(); old=db.execute(f'SELECT * FROM {table} WHERE id=?',(rid,)).fetchone() if rid else None
            if rid and not old: raise Error('Registro não encontrado.',404)
            manager=u['role'] in MANAGERS
            if table in ('clients','users') and not manager: raise Error('Acesso restrito à gestão.',403)
            if table=='users' and u['role']!='administrador': raise Error('Somente administradores gerenciam acessos.',403)
            def txt(k,required=False):
                v=str(d.get(k,'')).strip()
                if (required and not v) or len(v)>3000: raise Error('Preencha os campos obrigatórios corretamente.')
                return v
            def num(k,default=0):
                try: return int(d.get(k,default))
                except (ValueError,TypeError): raise Error('Valor numérico inválido.')
            def choice(k,options):
                v=d.get(k)
                if v not in options: raise Error(f'Valor inválido: {k}.')
                return v
            def date(k):
                try: v=dt.datetime.fromisoformat(txt(k,True))
                except ValueError: raise Error('Data e horário inválidos.')
                if v.tzinfo is not None: raise Error('Use o horário local da agência.')
                return v.isoformat(timespec='minutes')
            if table=='clients':
                value=num('monthly_value'); day=num('due_day',10); active=num('active',1)
                if value<0 or not 1<=day<=31 or active not in (0,1): raise Error('Valor, situação ou vencimento inválido.')
                values={k:txt(k,k in ('company','contact')) for k in ['company','contact','phone','instagram','services']}; values.update(monthly_value=value,due_day=day,active=active)
            elif table=='users':
                role=choice('role',ROLES); cid=num('client_id') or None; email=txt('email',True).lower()
                if role=='cliente' and (not cid or not db.execute('SELECT 1 FROM clients WHERE id=?',(cid,)).fetchone()): raise Error('Selecione a empresa do cliente.')
                if '@' not in email or len(email)>254: raise Error('E-mail inválido.')
                values={'name':txt('name',True),'email':email,'role':role,'client_id':cid if role=='cliente' else None,'active':num('active',1)}
                if values['active'] not in (0,1): raise Error('Estado inválido.')
                if rid==u['id'] and (role!='administrador' or not values['active']): raise Error('Você não pode remover seu próprio acesso administrativo.')
                pw=txt('password')
                if pw or not rid:
                    if len(pw)<12: raise Error('A senha deve ter pelo menos 12 caracteres.')
                    values['password']=password_hash(pw)
            else:
                if u['role']=='cliente': raise Error('Clientes podem consultar sua agenda e tarefas.',403)
                ids=self.visible(db,u)
                if old and (old['client_id'] not in ids or (not manager and old['assignee_id']!=u['id'])): raise Error('Sem acesso a este registro.',403)
                cid=num('client_id'); uid=num('assignee_id')
                if cid not in ids: raise Error('Cliente sem permissão.',403)
                if not manager and uid!=u['id']: raise Error('Você só pode atribuir atividades a si mesmo.',403)
                assignee=db.execute("SELECT role FROM users WHERE id=? AND active=1 AND role!='cliente'",(uid,)).fetchone()
                if not assignee: raise Error('Responsável inválido.')
                if assignee['role']=='funcionario' and not db.execute('SELECT 1 FROM assignments WHERE user_id=? AND client_id=?',(uid,cid)).fetchone(): raise Error('Atribua primeiro o funcionário ao cliente no cadastro da empresa.',403)
                if old and cid!=old['client_id']:
                    linked=(db.execute('SELECT 1 FROM files WHERE task_id=?',(rid,)).fetchone() or db.execute('SELECT 1 FROM recordings WHERE task_id=?',(rid,)).fetchone()) if table=='tasks' else db.execute('SELECT 1 FROM recordings WHERE event_id=?',(rid,)).fetchone()
                    if linked: raise Error('Uma atividade vinculada não pode mudar de empresa.')
                values={'title':txt('title',True),'client_id':cid,'assignee_id':uid,'description':txt('description')}
                if table=='tasks': values.update(due_at=date('due_at'),status=choice('status',('pendente','em_andamento','concluida','cancelada')),priority=choice('priority',('baixa','media','alta')))
                else:
                    start=date('starts_at'); end=date('ends_at'); status=choice('status',('agendado','concluido','cancelado'))
                    if end<=start: raise Error('O término deve ser posterior ao início.')
                    # Serialize conflict check + write across concurrent scheduling requests.
                    db.execute('BEGIN IMMEDIATE')
                    if status!='cancelado' and db.execute("SELECT 1 FROM events WHERE assignee_id=? AND id!=? AND status!='cancelado' AND starts_at<? AND ends_at>?",(uid,rid or 0,end,start)).fetchone(): raise Error('O responsável já possui compromisso neste horário.',409)
                    values.update(starts_at=start,ends_at=end,status=status,kind=choice('kind',('gravacao','reuniao','entrega','publicacao','outro')))
            if rid:
                db.execute(f'UPDATE {table} SET '+','.join(f'{k}=?' for k in values)+' WHERE id=?',(*values.values(),rid))
                if table=='users' and ('password' in values or values['role']!=old['role'] or values['client_id']!=old['client_id'] or not values['active']): db.execute('DELETE FROM sessions WHERE user_id=?',(rid,))
            else: rid=db.execute(f'INSERT INTO {table} ('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values())).lastrowid
            if table=='clients':
                assigned=d.get('responsibles',[])
                if not isinstance(assigned,list): raise Error('Responsáveis inválidos.')
                for uid in assigned:
                    if not isinstance(uid,int) or not db.execute("SELECT 1 FROM users WHERE id=? AND role='funcionario' AND active=1",(uid,)).fetchone(): raise Error('Funcionário inválido.')
                db.execute('DELETE FROM assignments WHERE client_id=?',(rid,)); db.executemany('INSERT INTO assignments VALUES(?,?)',[(rid,uid) for uid in set(assigned)])
            audit(db,u,'editar' if old else 'criar',table,rid);
            if table=='events' and old:
                operations.sync_event(db,u,rid,values)
            if table=='tasks':
                operations.notify(db,[values['assignee_id']],values['client_id'],'Tarefa atualizada' if old else 'Nova tarefa',values['title'],'tarefa',rid,'task-change:'+str(rid)+':'+secrets.token_hex(6))
            db.commit(); self.reply({'ok':True,'id':rid},200 if old else 201)
    def dispatch(self):
        path=urlparse(self.path).path
        try:
            if path.startswith('/api/'): self.handle_api(path); return
            if self.command not in ('GET','HEAD'): raise Error('Método não permitido.',405)
            files={'/':('index.html','text/html; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/workspace.js':('workspace.js','text/javascript; charset=utf-8')}
            if path not in files: raise Error('Não encontrado.',404)
            name,kind=files[path]; body=(ROOT/'static'/name).read_bytes(); self.send_response(200); self.headers_out(kind,len(body)); self.end_headers();
            if self.command!='HEAD': self.wfile.write(body)
        except (Error,file_storage.FileError) as e: self.reply({'error':e.message},e.status)
        except sqlite3.IntegrityError: self.reply({'error':'Dados inválidos ou e-mail já cadastrado.'},400)
        except Exception: self.reply({'error':'Falha interna. Consulte o administrador.'},500)
    do_GET=dispatch
    do_HEAD=dispatch
    do_POST=dispatch
    do_PUT=dispatch
    do_DELETE=dispatch
def automation_loop():
    while True:
        try:
            with connect() as db:
                operations.process(db)
        except Exception as exc:
            print('Falha no ciclo de automação: '+type(exc).__name__,flush=True)
        time.sleep(60)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('command',choices=['serve','seed','bootstrap'],nargs='?',default='serve'); p.add_argument('--host',default='127.0.0.1'); p.add_argument('--port',type=int,default=8000); a=p.parse_args(); initialize()
    if a.command=='seed': seed()
    elif a.command=='bootstrap': bootstrap()
    else:
        threading.Thread(target=automation_loop,daemon=True).start()
        print(f'EVOOLUI GESTÃO iniciada na porta {a.port}',flush=True); ThreadingHTTPServer((a.host,a.port),Handler).serve_forever()
