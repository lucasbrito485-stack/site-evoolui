"""Local scheduling, task automation, internal notifications and financial records."""
import calendar
import datetime as dt
import re
import sqlite3
from zoneinfo import ZoneInfo
from app.file_storage import FileError

TZ = ZoneInfo('America/Sao_Paulo')
MANAGERS = ('administrador', 'socio')


def now_local():
    return dt.datetime.now(TZ).replace(tzinfo=None, microsecond=0)


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),
        client_id INTEGER REFERENCES clients(id),title TEXT NOT NULL,body TEXT NOT NULL,
        kind TEXT NOT NULL,entity_id TEXT, dedup TEXT NOT NULL,read_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(user_id,dedup));
    CREATE TABLE IF NOT EXISTS recordings(
        id INTEGER PRIMARY KEY,client_id INTEGER NOT NULL REFERENCES clients(id),
        requested_by INTEGER NOT NULL REFERENCES users(id),assignee_id INTEGER NOT NULL REFERENCES users(id),
        starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'solicitado',event_id INTEGER REFERENCES events(id),
        task_id INTEGER REFERENCES tasks(id),created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS contracts(
        id INTEGER PRIMARY KEY,client_id INTEGER NOT NULL REFERENCES clients(id),
        description TEXT NOT NULL,amount INTEGER NOT NULL,due_day INTEGER NOT NULL,
        starts_on TEXT NOT NULL,ends_on TEXT,status TEXT NOT NULL DEFAULT 'ativo',
        legacy_client_id INTEGER UNIQUE,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS ledger(
        id INTEGER PRIMARY KEY,client_id INTEGER REFERENCES clients(id),contract_id INTEGER REFERENCES contracts(id),
        period TEXT,kind TEXT NOT NULL,description TEXT NOT NULL,amount INTEGER NOT NULL,
        due_on TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pendente',paid_on TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(contract_id,period));
    ''')
    # Import the existing contract fields once, without changing client records.
    db.execute('''INSERT OR IGNORE INTO contracts(client_id,description,amount,due_day,starts_on,legacy_client_id)
        SELECT id,CASE WHEN services='' THEN 'Contrato mensal' ELSE services END,monthly_value,due_day,
        substr(created_at,1,10),id FROM clients WHERE monthly_value>0''')


def users_for(db, cid, roles=('administrador', 'socio', 'funcionario', 'cliente')):
    result = []
    for u in db.execute('SELECT id,role,client_id FROM users WHERE active=1'):
        if u['role'] not in roles:
            continue
        if (u['role'] in MANAGERS or (u['role'] == 'cliente' and u['client_id'] == cid)
                or (u['role'] == 'funcionario' and db.execute(
                    'SELECT 1 FROM assignments WHERE client_id=? AND user_id=?', (cid, u['id'])).fetchone())):
            result.append(u['id'])
    return result


def notify(db, recipients, cid, title, body, kind, entity, dedup):
    db.executemany('''INSERT OR IGNORE INTO notifications(user_id,client_id,title,body,kind,entity_id,dedup)
        VALUES(?,?,?,?,?,?,?)''', [(uid, cid, title, body, kind, str(entity), dedup) for uid in set(recipients)])


def on_file(db, user, fid, task_id=None, parent=None):
    row = db.execute('SELECT * FROM files WHERE id=?', (fid,)).fetchone()
    if parent:
        task_id = db.execute('SELECT task_id FROM files WHERE id=?', (parent,)).fetchone()[0]
    if task_id is not None:
        task = db.execute('SELECT * FROM tasks WHERE id=? AND client_id=?', (task_id, row['client_id'])).fetchone()
        if not task or (user['role'] == 'funcionario' and task['assignee_id'] != user['id']):
            raise FileError('Tarefa não encontrada ou sem permissão.', 404)
        if db.execute('SELECT 1 FROM recordings WHERE task_id=?',(task_id,)).fetchone():
            raise FileError('Use uma tarefa de conteúdo; a tarefa de gravação acompanha o compromisso.',409)
        # Multiple assets sharing a task would make approvals ambiguous.
        if not parent and db.execute('SELECT 1 FROM files WHERE task_id=?', (task_id,)).fetchone():
            raise FileError('Esta tarefa já está vinculada a outro conteúdo.', 409)
    else:
        due = (now_local() + dt.timedelta(days=3)).replace(hour=17, minute=0).isoformat(timespec='minutes')
        task_id = db.execute('''INSERT INTO tasks(title,client_id,assignee_id,due_at,status,priority,description)
            VALUES(?,?,?,?,'em_andamento','media',?)''',
            ('Aprovação: ' + row['name'], row['client_id'], user['id'], due, 'Acompanhar avaliação do conteúdo pelo cliente.')).lastrowid
    db.execute("UPDATE tasks SET status='em_andamento' WHERE id=?", (task_id,))
    db.execute('UPDATE files SET task_id=? WHERE id=?', (task_id, fid))
    notify(db, users_for(db, row['client_id'], ('cliente',)), row['client_id'],
           'Novo conteúdo para aprovação', row['name'] + ' · versão ' + str(row['version']),
           'arquivo', fid, 'file-ready:' + fid)


def on_review(db, row, decision, comment):
    task_id = row['task_id']
    task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone() if task_id else None
    if task:
        db.execute('UPDATE tasks SET status=? WHERE id=?',
                   ('concluida' if decision == 'aprovado' else 'em_andamento', task_id))
    recipients = users_for(db, row['client_id'], ('administrador', 'socio'))
    recipients.append(row['uploaded_by'])
    if task: recipients.append(task['assignee_id'])
    notify(db, recipients, row['client_id'],
           'Conteúdo aprovado' if decision == 'aprovado' else 'Alterações solicitadas',
           row['name'] + (' · ' + comment if comment else ''), 'arquivo', row['id'], 'file-review:' + row['id'])


def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise FileError('Data inválida.')
    try: return dt.date.fromisoformat(value)
    except ValueError: raise FileError('Data inválida.')


def staff_for(db, cid):
    return [dict(r) for r in db.execute('''SELECT u.id,u.name FROM users u JOIN assignments a ON a.user_id=u.id
        WHERE a.client_id=? AND u.active=1 AND u.role='funcionario' ORDER BY u.name''', (cid,))]


def free(db, uid, start, end):
    return not db.execute("SELECT 1 FROM events WHERE assignee_id=? AND status!='cancelado' AND starts_at<? AND ends_at>?",
                          (uid, end, start)).fetchone()


def check_slot(db, cid, uid, start, now=None):
    now = now or now_local()
    try: stamp = dt.datetime.fromisoformat(start)
    except (ValueError, TypeError): raise FileError('Horário inválido.')
    if (stamp.tzinfo is not None or stamp.second or stamp.microsecond or stamp.minute != 0
            or not 9 <= stamp.hour < 18 or stamp.weekday() >= 5
            or stamp <= now or stamp > now + dt.timedelta(days=90)):
        raise FileError('Escolha um horário futuro disponível, de segunda a sexta, entre 9h e 18h, nos próximos 90 dias.')
    if uid not in [r['id'] for r in staff_for(db, cid)]:
        raise FileError('Responsável não atribuído à empresa.', 400)
    end = (stamp + dt.timedelta(hours=1)).isoformat(timespec='minutes')
    start = stamp.isoformat(timespec='minutes')
    if not free(db, uid, start, end): raise FileError('O horário não está mais disponível.', 409)
    return start, end


def visibility(h, db, user, cid):
    if cid not in h.visible(db, user): raise FileError('Registro não encontrado.', 404)


def recordings(h, db, user, path, audit):
    match = re.fullmatch(r'/api/clients/(\d+)/availability', path)
    if match and h.command == 'GET':
        from urllib.parse import parse_qs, urlparse
        cid = int(match[1]); visibility(h, db, user, cid)
        day = valid_date(parse_qs(urlparse(h.path).query).get('date', [''])[0])
        slots = []
        if day.weekday() < 5:
            for staff in staff_for(db, cid):
                for hour in range(9, 18):
                    start = dt.datetime.combine(day, dt.time(hour))
                    end = start + dt.timedelta(hours=1)
                    if now_local() < start <= now_local() + dt.timedelta(days=90) and free(db, staff['id'], start.isoformat(timespec='minutes'), end.isoformat(timespec='minutes')):
                        slots.append({'assignee_id': staff['id'], 'name': staff['name'], 'starts_at': start.isoformat(timespec='minutes'), 'ends_at': end.isoformat(timespec='minutes')})
        h.reply({'slots': slots, 'timezone': 'America/Sao_Paulo'}); return
    if path == '/api/recordings' and h.command == 'GET':
        ids = h.visible(db, user)
        rows = db.execute('''SELECT r.*,c.company,u.name AS assignee_name FROM recordings r
            JOIN clients c ON c.id=r.client_id JOIN users u ON u.id=r.assignee_id ORDER BY r.starts_at DESC''')
        h.reply({'recordings': [dict(r) for r in rows if r['client_id'] in ids
                               and (user['role'] != 'funcionario' or r['assignee_id'] == user['id'])]}); return
    if path == '/api/recordings' and h.command == 'POST':
        if user['role'] != 'cliente': raise FileError('Solicitações são feitas pelo acesso do cliente.', 403)
        data = h.body(); cid = user['client_id']; visibility(h, db, user, cid)
        try: uid = int(data.get('assignee_id'))
        except (TypeError, ValueError): raise FileError('Responsável inválido.')
        note = data.get('note', '')
        if not isinstance(note, str) or len(note) > 3000: raise FileError('Observação inválida.')
        db.execute('BEGIN IMMEDIATE')
        start, end = check_slot(db, cid, uid, data.get('starts_at'))
        if db.execute("SELECT 1 FROM recordings WHERE client_id=? AND assignee_id=? AND starts_at=? AND status IN ('solicitado','confirmado')", (cid,uid,start)).fetchone():
            raise FileError('Você já solicitou este horário.', 409)
        rid = db.execute('INSERT INTO recordings(client_id,requested_by,assignee_id,starts_at,ends_at,note) VALUES(?,?,?,?,?,?)',
                         (cid,user['id'],uid,start,end,note.strip())).lastrowid
        notify(db, users_for(db,cid,MANAGERS) + [uid],cid,'Solicitação de gravação',start + ' · ' + note.strip(),'gravacao',rid,f'request:{rid}')
        audit(db,user,'solicitar','recordings',rid); db.commit(); h.reply({'id':rid,'status':'solicitado'},201); return
    match = re.fullmatch(r'/api/recordings/(\d+)/decision',path)
    if match and h.command == 'POST':
        data=h.body(); action=data.get('action')
        if action not in ('confirmar','recusar','cancelar'): raise FileError('Ação inválida.')
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM recordings WHERE id=?',(int(match[1]),)).fetchone()
        if not row: raise FileError('Solicitação não encontrada.',404)
        visibility(h,db,user,row['client_id'])
        if user['role']=='cliente' and action!='cancelar': raise FileError('A confirmação é feita pela equipe.',403)
        if user['role']=='funcionario' and row['assignee_id']!=user['id']: raise FileError('Somente o responsável pode confirmar.',403)
        target={'confirmar':'confirmado','recusar':'recusado','cancelar':'cancelado'}[action]
        if row['status']==target: h.reply({'ok':True,'status':target}); return
        if row['status'] in ('cancelado','recusado','realizado') or (action!='cancelar' and row['status']!='solicitado'):
            raise FileError('Esta solicitação já foi processada.',409)
        if action=='confirmar':
            start,end=check_slot(db,row['client_id'],row['assignee_id'],row['starts_at'])
            company=db.execute('SELECT company FROM clients WHERE id=?',(row['client_id'],)).fetchone()[0]
            title='Gravação · '+company
            eid=db.execute("INSERT INTO events(title,client_id,assignee_id,starts_at,ends_at,kind,description) VALUES(?,?,?,?,?,'gravacao',?)",(title,row['client_id'],row['assignee_id'],start,end,row['note'])).lastrowid
            tid=db.execute("INSERT INTO tasks(title,client_id,assignee_id,due_at,priority,description) VALUES(?,?,?,?,'alta',?)",(title,row['client_id'],row['assignee_id'],end,'Realizar gravação confirmada. '+row['note'])).lastrowid
            db.execute('UPDATE recordings SET event_id=?,task_id=? WHERE id=?',(eid,tid,row['id']))
        elif action=='cancelar' and row['event_id']:
            db.execute("UPDATE events SET status='cancelado' WHERE id=?",(row['event_id'],))
            # Cancellation is distinct from work actually delivered.
            db.execute("UPDATE tasks SET status='cancelada',description=description || ' [Cancelada com o agendamento; não executada.]' WHERE id=?",(row['task_id'],))
        db.execute('UPDATE recordings SET status=? WHERE id=?',(target,row['id']))
        notify(db,users_for(db,row['client_id'],('administrador','socio','cliente'))+[row['assignee_id']],row['client_id'],
               'Gravação '+target,row['starts_at'],'gravacao',row['id'],f'recording:{row["id"]}:{target}')
        audit(db,user,action,'recordings',row['id']); db.commit(); h.reply({'ok':True,'status':target}); return
    raise FileError('Método ou endereço não disponível.',405)


def generate_invoices(db, now):
    month=now.strftime('%Y-%m'); first=now.date().replace(day=1)
    last=first.replace(day=calendar.monthrange(first.year,first.month)[1])
    for c in db.execute("SELECT * FROM contracts WHERE status='ativo'").fetchall():
        if c['starts_on']>last.isoformat() or (c['ends_on'] and c['ends_on']<first.isoformat()): continue
        due=first.replace(day=min(c['due_day'],last.day)).isoformat()
        db.execute('''INSERT OR IGNORE INTO ledger(client_id,contract_id,period,kind,description,amount,due_on)
            VALUES(?,?,?,'receita',?,?,?)''',(c['client_id'],c['id'],month,'Mensalidade · '+c['description'],c['amount'],due))


def process(db, now=None):
    now=now or now_local(); today=now.date(); stamp=now.isoformat(timespec='minutes')
    generate_invoices(db,now)
    for t in db.execute("SELECT * FROM tasks WHERE status NOT IN ('concluida','cancelada')").fetchall():
        if t['due_at']<stamp:
            recipients=users_for(db,t['client_id'],MANAGERS)+[t['assignee_id']]
            notify(db,recipients,t['client_id'],'Tarefa atrasada',t['title'],'tarefa',t['id'],f'task-overdue:{t["id"]}:{today}')
        elif t['due_at']<=(now+dt.timedelta(hours=24)).isoformat(timespec='minutes'):
            notify(db,[t['assignee_id']],t['client_id'],'Prazo de tarefa se aproximando',t['title'],'tarefa',t['id'],f'task-due:{t["id"]}:{today}')
    for r in db.execute("SELECT * FROM recordings WHERE status='confirmado'").fetchall():
        if stamp<=r['starts_at']<=(now+dt.timedelta(hours=24)).isoformat(timespec='minutes'):
            notify(db,users_for(db,r['client_id'],('cliente',))+[r['assignee_id']],r['client_id'],'Lembrete de gravação',r['starts_at'],'gravacao',r['id'],f'recording-reminder:{r["id"]}:{r["starts_at"]}')
    for f in db.execute("SELECT f.* FROM files f WHERE f.status='pendente' AND NOT EXISTS(SELECT 1 FROM files c WHERE c.parent_id=f.id)").fetchall():
        if f['created_at'][:10]<today.isoformat():
            notify(db,users_for(db,f['client_id'],('cliente',)),f['client_id'],'Conteúdo aguardando aprovação',f['name'],'arquivo',f['id'],f'file-pending:{f["id"]}:{today}')
    managers=[r[0] for r in db.execute("SELECT id FROM users WHERE active=1 AND role IN ('administrador','socio')")]
    for entry in db.execute("SELECT * FROM ledger WHERE status='pendente'").fetchall():
        if entry['due_on']<=(today+dt.timedelta(days=3)).isoformat():
            title='Vencimento financeiro atrasado' if entry['due_on']<today.isoformat() else 'Vencimento financeiro próximo'
            notify(db,managers,entry['client_id'],title,entry['description']+' · '+entry['due_on'],'financeiro',entry['id'],f'finance-due:{entry["id"]}:{today}')
    for c in db.execute("SELECT * FROM contracts WHERE status='ativo' AND ends_on IS NOT NULL").fetchall():
        if today.isoformat()<=c['ends_on']<=(today+dt.timedelta(days=7)).isoformat():
            notify(db,managers,c['client_id'],'Contrato próximo do término',c['description']+' · '+c['ends_on'],'financeiro',c['id'],f'contract-end:{c["id"]}:{today}')


def financial(h,db,u,path,audit):
    if u['role'] not in MANAGERS: raise FileError('Área financeira restrita à gestão.',403)
    if path=='/api/finance' and h.command=='GET':
        from urllib.parse import parse_qs,urlparse
        month=parse_qs(urlparse(h.path).query).get('month',[now_local().strftime('%Y-%m')])[0]
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month): raise FileError('Mês inválido.')
        entries=[dict(r) for r in db.execute('SELECT l.*,c.company FROM ledger l LEFT JOIN clients c ON c.id=l.client_id ORDER BY l.due_on,l.id')]
        selected=[r for r in entries if r['due_on'].startswith(month) or (r['paid_on'] or '').startswith(month)]
        received=sum(r['amount'] for r in entries if r['kind']=='receita' and r['status']=='pago' and r['paid_on'].startswith(month))
        spent=sum(r['amount'] for r in entries if r['kind']=='despesa' and r['status']=='pago' and r['paid_on'].startswith(month))
        outstanding=sum(r['amount'] for r in entries if r['kind']=='receita' and r['status']=='pendente' and r['due_on'].startswith(month))
        h.reply({'contracts':[dict(r) for r in db.execute('SELECT ct.*,c.company FROM contracts ct JOIN clients c ON c.id=ct.client_id ORDER BY ct.id DESC')],
                 'entries':selected,'summary':{'received':received,'spent':spent,'balance':received-spent,'outstanding':outstanding},'month':month}); return
    match=re.fullmatch(r'/api/finance/(contracts|entries)(?:/(\d+))?',path)
    if not match or h.command not in ('POST','PUT'): raise FileError('Método não permitido.',405)
    resource,rid=match.groups(); table='contracts' if resource=='contracts' else 'ledger'
    if (rid is None)!=(h.command=='POST'): raise FileError('Método não permitido.',405)
    data=h.body()
    if rid and not db.execute(f'SELECT 1 FROM {table} WHERE id=?',(rid,)).fetchone(): raise FileError('Registro não encontrado.',404)
    try:
        amount=int(data.get('amount')); cid=int(data.get('client_id') or 0) or None
    except (ValueError,TypeError): raise FileError('Valor ou empresa inválidos.')
    if isinstance(data.get('amount'),bool) or amount<=0: raise FileError('O valor deve ser positivo, em centavos.')
    description=data.get('description','')
    if not isinstance(description,str) or not description.strip() or len(description)>3000: raise FileError('Descrição obrigatória (até 3000 caracteres).')
    if cid and not db.execute('SELECT 1 FROM clients WHERE id=?',(cid,)).fetchone(): raise FileError('Cliente inválido.')
    values={'client_id':cid,'description':description.strip(),'amount':amount}
    if resource=='contracts':
        if not cid: raise FileError('Selecione uma empresa.')
        start=valid_date(data.get('starts_on')).isoformat(); end=valid_date(data['ends_on']).isoformat() if data.get('ends_on') else None
        try: day=int(data.get('due_day'))
        except (ValueError,TypeError): raise FileError('Dia inválido.')
        if not 1<=day<=31 or (end and end<start) or data.get('status') not in ('ativo','encerrado'): raise FileError('Datas ou situação inválidas.')
        values.update(starts_on=start,ends_on=end,due_day=day,status=data['status'])
    else:
        due=valid_date(data.get('due_on')).isoformat(); status=data.get('status'); kind=data.get('kind')
        if status not in ('pendente','pago','cancelado') or kind not in ('receita','despesa'): raise FileError('Tipo ou situação inválida.')
        paid=valid_date(data.get('paid_on')).isoformat() if status=='pago' else None
        if paid and paid>now_local().date().isoformat(): raise FileError('Pagamento não pode ter data futura.')
        if rid:
            old=db.execute('SELECT * FROM ledger WHERE id=?',(rid,)).fetchone()
            if old['contract_id'] and (cid!=old['client_id'] or kind!='receita'): raise FileError('Mensalidades devem manter o contrato e a empresa de origem.')
        values.update(due_on=due,status=status,kind=kind,paid_on=paid)
    if rid: db.execute(f'UPDATE {table} SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),rid))
    else: rid=db.execute(f'INSERT INTO {table} ('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values())).lastrowid
    audit(db,u,'editar' if h.command=='PUT' else 'criar',table,int(rid))
    process(db); db.commit(); h.reply({'ok':True,'id':int(rid)},200 if h.command=='PUT' else 201)


def notifications(h,db,u,path):
    if path=='/api/notifications' and h.command=='GET':
        process(db); db.commit(); ids=h.visible(db,u)
        rows=db.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 200',(u['id'],))
        rows=[dict(r) for r in rows if (r['client_id'] is None or r['client_id'] in ids)
              and (r['kind']!='financeiro' or u['role'] in MANAGERS)]
        h.reply({'notifications':rows,'unread':sum(r['read_at'] is None for r in rows)}); return
    match=re.fullmatch(r'/api/notifications/(\d+)/read',path)
    if match and h.command=='POST':
        row=db.execute('SELECT * FROM notifications WHERE id=? AND user_id=?',(int(match[1]),u['id'])).fetchone()
        if not row: raise FileError('Notificação não encontrada.',404)
        if row['client_id'] is not None: visibility(h,db,u,row['client_id'])
        if row['kind']=='financeiro' and u['role'] not in MANAGERS: raise FileError('Notificação não encontrada.',404)
        db.execute('UPDATE notifications SET read_at=COALESCE(read_at,CURRENT_TIMESTAMP) WHERE id=?',(row['id'],)); db.commit(); h.reply({'ok':True}); return
    raise FileError('Método não permitido.',405)


def sync_event(db, user, event_id, values):
    """Keep requests, reminders and generated tasks consistent with manual calendar edits."""
    row=db.execute('SELECT * FROM recordings WHERE event_id=?',(event_id,)).fetchone()
    if not row: return
    status={'agendado':'confirmado','cancelado':'cancelado','concluido':'realizado'}[values['status']]
    db.execute('UPDATE recordings SET starts_at=?,ends_at=?,assignee_id=?,status=?,note=? WHERE id=?',
               (values['starts_at'],values['ends_at'],values['assignee_id'],status,values['description'],row['id']))
    task_status={'agendado':'pendente','cancelado':'cancelada','concluido':'concluida'}[values['status']]
    db.execute('UPDATE tasks SET assignee_id=?,due_at=?,status=?,title=? WHERE id=?',
               (values['assignee_id'],values['ends_at'],task_status,values['title'],row['task_id']))
    notify(db,users_for(db,row['client_id'],('cliente',))+[values['assignee_id']],row['client_id'],
           'Agenda da gravação atualizada',values['starts_at']+' · '+status,'gravacao',row['id'],
           f'recording-change:{row["id"]}:{values["starts_at"]}:{status}:{values["assignee_id"]}')
