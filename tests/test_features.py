"""End-to-end HTTP tests for private files, automation and finance (isolated data)."""
import concurrent.futures
import datetime as dt
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import unittest
from urllib.parse import quote
import zipfile
import test_app as core
server = core.server

PNG = __import__('base64').b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jU1sAAAAASUVORK5CYII=')

class Features(unittest.TestCase):
    request = core.Integration.request
    login = core.Integration.login
    @classmethod
    def setUpClass(cls):
        core.Integration.setUpClass.__func__(cls)
        with server.connect() as db:
            # Seed created clients after schema initialization; import existing contracts once.
            server.operations.initialize(db)
            db.execute('INSERT INTO users(name,email,password,role,client_id) VALUES(?,?,?,?,?)',
                       ('Cliente 2 fictício','cliente2@evoolui.test',server.password_hash(core.TEST_PASSWORD),'cliente',2))
    @classmethod
    def tearDownClass(cls): core.Integration.tearDownClass.__func__(cls)
    def raw(self,path,method='GET',data=None,s=None,extra=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        headers={}
        if s: headers.update(Cookie=s['cookie'],**{'X-CSRF-Token':s['csrf']})
        if extra: headers.update(extra)
        c.request(method,path,data,headers); r=c.getresponse(); result=(r.status,r.read(),dict(r.getheaders())); c.close();return result
    def upload(self,s,cid=1,name='foto.png',body=PNG,parent=None,task=None):
        url=f'/api/files/{parent}/versions' if parent else f'/api/clients/{cid}/files'
        url+='?name='+quote(name)+(f'&task_id={task}' if task else '')
        status,raw,headers=self.raw(url,'POST',body,s)
        return status,json.loads(raw),headers
    def slot(self,days=2,hour=10):
        date=server.operations.now_local().date()+dt.timedelta(days=days)
        while date.weekday()>=5: date+=dt.timedelta(days=1)
        return date.isoformat()+f'T{hour:02}:00'
    def recording(self,client,days=2,hour=10):
        return self.request('/api/recordings','POST',{'assignee_id':3,'starts_at':self.slot(days,hour),'note':'Gravação fictícia'},client)
    def test_01_file_storage_preview_download(self):
        admin,_=self.login(); status,data,_=self.upload(admin,name='foto com acento ç.png')
        self.assertEqual(status,201,data); f=data['file']; fid=f['id']
        self.assertEqual(f['sha256'],hashlib.sha256(PNG).hexdigest());self.assertNotIn('storage_key',f)
        with server.connect() as db:
            row=db.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            path=server.file_storage.root_for(server.DB)/'1'/row['storage_key']
            self.assertEqual(path.read_bytes(),PNG);self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(path.parent.stat().st_mode&0o777,0o700)
            self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(f['task_id'],)).fetchone()[0],'em_andamento')
        client,_=self.login('cliente@evoolui.test')
        status,body,h=self.raw(f'/api/files/{fid}/content',s=client)
        self.assertEqual(status,200);self.assertEqual(body,PNG);self.assertEqual(h['Content-Type'],'image/png')
        self.assertIn('no-store',h['Cache-Control']);self.assertEqual(h['X-Content-Type-Options'],'nosniff')
        status,body,h=self.raw(f'/api/files/{fid}/download',s=client)
        self.assertEqual(body,PNG);self.assertTrue(h['Content-Disposition'].startswith('attachment;'))
        self.assertIn("filename*=UTF-8''",h['Content-Disposition'])
        self.assertEqual(self.raw('/data/private_files/1/'+row['storage_key'])[0],404)
    def test_02_cross_tenant_every_route(self):
        admin,_=self.login(); status,data,_=self.upload(admin,cid=2);self.assertEqual(status,201);fid=data['file']['id']
        client,_=self.login('cliente@evoolui.test');other,_=self.login('lucas@evoolui.test')
        for who in (client,other):
            self.assertEqual(self.request('/api/clients/2/files',session=who)[0],404)
            for suffix in ('','/content','/download'):
                self.assertEqual(self.raw('/api/files/'+fid+suffix,s=who)[0],404)
            self.assertEqual(self.request('/api/files/'+fid+'/reviews','POST',{'decision':'aprovado'},who)[0],404)
            self.assertEqual(self.upload(who,cid=2,parent=fid)[0],404)
        own,_=self.login('cliente2@evoolui.test');self.assertEqual(self.raw('/api/files/'+fid+'/download',s=own)[0],200)
    def test_03_upload_authorization_and_csrf(self):
        client,_=self.login('cliente@evoolui.test');employee,_=self.login('bia@evoolui.test')
        self.assertEqual(self.upload(client)[0],403)
        self.assertEqual(self.upload(employee,cid=3)[0],404)
        self.assertEqual(self.upload(employee)[0],201)
        self.assertEqual(self.raw('/api/clients/1/files?name=foto.png','POST',PNG,{'cookie':employee['cookie'],'csrf':''})[0],403)
        self.assertEqual(self.raw('/api/clients/1/files?name=foto.png','POST',PNG)[0],401)
        self.assertEqual(self.raw('/api/clients/1/files')[0],401)
    def test_04_validation_and_cleanup(self):
        admin,_=self.login()
        for name,body in [('../foto.png',PNG),('a\\b.png',PNG),('foto.png',b'<html>bad</html>'),('index.html',b'hello'),('vetor.svg',b'<svg/>'),('vazio.txt',b'')]:
            self.assertIn(self.upload(admin,name=name,body=body)[0],(400,413,415))
        old=server.file_storage.MAX_UPLOAD
        try:
            server.file_storage.MAX_UPLOAD=10
            self.assertEqual(self.upload(admin)[0],413)
        finally: server.file_storage.MAX_UPLOAD=old
        root=server.file_storage.root_for(server.DB)
        self.assertFalse(list(root.rglob('.upload-*')))
    def test_05_approval_task_and_notifications(self):
        employee,_=self.login('bia@evoolui.test');client,_=self.login('cliente@evoolui.test')
        status,data,_=self.upload(employee,name='aprovar.png');self.assertEqual(status,201);f=data['file']
        notes=self.request('/api/notifications',session=client)[1]['notifications']
        self.assertTrue(any(n['entity_id']==f['id'] and n['title']=='Novo conteúdo para aprovação' for n in notes))
        self.assertEqual(self.request(f'/api/files/{f["id"]}/reviews','POST',{'decision':'aprovado','comment':'Tudo certo.'},employee)[0],403)
        self.assertEqual(self.request(f'/api/files/{f["id"]}/reviews','POST',{'decision':'alteracoes_solicitadas','comment':''},client)[0],400)
        self.assertEqual(self.request(f'/api/files/{f["id"]}/reviews','POST',{'decision':'aprovado','comment':'Tudo certo.'},client)[0],200)
        self.assertEqual(self.request(f'/api/files/{f["id"]}/reviews','POST',{'decision':'aprovado'},client)[0],409)
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(f['task_id'],)).fetchone()[0],'concluida')
        self.assertTrue(any(n['entity_id']==f['id'] and n['title']=='Conteúdo aprovado' for n in self.request('/api/notifications',session=employee)[1]['notifications']))
    def test_06_changes_versions_immutable_history(self):
        admin,_=self.login();client,_=self.login('cliente@evoolui.test')
        f=self.upload(admin,name='arte-v1.png')[1]['file']
        self.assertEqual(self.request(f'/api/files/{f["id"]}/reviews','POST',{'decision':'alteracoes_solicitadas','comment':'Aumentar o logo.'},client)[0],200)
        status,data,_=self.upload(admin,parent=f['id'],name='arte-v2.png');self.assertEqual(status,201);new=data['file']
        self.assertEqual(new['version'],2);self.assertEqual(new['task_id'],f['task_id']);self.assertEqual(new['status'],'pendente')
        self.assertEqual(self.upload(admin,parent=f['id'])[0],409)
        details=self.request('/api/files/'+new['id'],session=client)[1]
        self.assertEqual(len(details['versions']),2);self.assertEqual(details['versions'][0]['reviews'][0]['comment'],'Aumentar o logo.')
        listed=self.request('/api/clients/1/files',session=client)[1]['files']
        self.assertNotIn(f['id'],[x['id'] for x in listed]);self.assertIn(new['id'],[x['id'] for x in listed])
        self.assertEqual(self.raw('/api/files/'+f['id']+'/download',s=client)[1],PNG)
    def test_07_ranges_head_and_private_documents(self):
        admin,_=self.login();client,_=self.login('cliente@evoolui.test')
        body=b'\x00\x00\x00\x18ftypmp42'+bytes(range(100));f=self.upload(admin,name='video.mp4',body=body)[1]['file']
        for value,wanted in [('bytes=5-10',body[5:11]),('bytes=-8',body[-8:]),('bytes=100-',body[100:])]:
            status,got,h=self.raw('/api/files/'+f['id']+'/content',s=client,extra={'Range':value});self.assertEqual(status,206);self.assertEqual(got,wanted);self.assertIn('Content-Range',h)
        for value in ('bytes=999-','bytes=5-1','bytes=-0','bytes=0-1,4-5'):
            self.assertEqual(self.raw('/api/files/'+f['id']+'/content',s=client,extra={'Range':value})[0],416)
        self.assertEqual(self.raw('/api/files/'+f['id']+'/content','HEAD',s=client)[0],200)
        f=self.upload(admin,name='documento.txt',body=b'<script>alert(1)</script>')[1]['file']
        status,body,h=self.raw('/api/files/'+f['id']+'/content',s=client)
        self.assertTrue(h['Content-Disposition'].startswith('attachment'));self.assertEqual(h['Content-Type'],'application/octet-stream')
    def test_08_assignment_revocation_immediate(self):
        admin,_=self.login();employee,_=self.login('bia@evoolui.test');f=self.upload(admin,cid=2)[1]['file']
        self.assertEqual(self.raw('/api/files/'+f['id']+'/content',s=employee)[0],200)
        with server.connect() as db: db.execute('DELETE FROM assignments WHERE user_id=3 AND client_id=2')
        try:
            self.assertEqual(self.raw('/api/files/'+f['id']+'/content',s=employee)[0],404)
            self.assertEqual(self.upload(employee,cid=2)[0],404)
        finally:
            with server.connect() as db: db.execute('INSERT INTO assignments VALUES(2,3)')
    def test_09_concurrent_versions(self):
        admin,_=self.login();f=self.upload(admin)[1]['file']
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(lambda _:self.upload(admin,parent=f['id'])[0],range(2)))
        self.assertEqual(sorted(statuses),[201,409])
    def test_10_recording_confirmation_idempotency(self):
        client,_=self.login('cliente@evoolui.test');admin,_=self.login()
        status,data,_=self.recording(client,days=4,hour=10);self.assertEqual(status,201,data);rid=data['id']
        self.assertEqual(self.request(f'/api/recordings/{rid}/decision','POST',{'action':'confirmar'},client)[0],403)
        for _ in range(2): self.assertEqual(self.request(f'/api/recordings/{rid}/decision','POST',{'action':'confirmar'},admin)[0],200)
        with server.connect() as db:
            row=db.execute('SELECT * FROM recordings WHERE id=?',(rid,)).fetchone()
            self.assertIsNotNone(row['event_id']);self.assertIsNotNone(row['task_id'])
            self.assertEqual(db.execute('SELECT count(*) FROM events WHERE id=?',(row['event_id'],)).fetchone()[0],1)
        self.assertEqual(self.request(f'/api/recordings/{rid}/decision','POST',{'action':'cancelar'},client)[0],200)
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM events WHERE id=?',(row['event_id'],)).fetchone()[0],'cancelado')
            self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(row['task_id'],)).fetchone()[0],'cancelada')
    def test_11_recording_conflict_confirmation_race(self):
        c1,_=self.login('cliente@evoolui.test');c2,_=self.login('cliente2@evoolui.test');admin,_=self.login()
        a=self.recording(c1,days=8,hour=11);b=self.recording(c2,days=8,hour=11)
        self.assertEqual(a[0],201);self.assertEqual(b[0],201)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda rid:self.request(f'/api/recordings/{rid}/decision','POST',{'action':'confirmar'},admin)[0],[a[1]['id'],b[1]['id']]))
        self.assertEqual(sorted(results),[200,409])
    def test_12_availability_and_scope(self):
        client,_=self.login('cliente@evoolui.test')
        day=self.slot(10)[:10];status,d,_=self.request('/api/clients/1/availability?date='+day,session=client)
        self.assertEqual(status,200);self.assertTrue(d['slots']);self.assertTrue(all(s['assignee_id']==3 for s in d['slots']))
        self.assertEqual(self.request('/api/clients/2/availability?date='+day,session=client)[0],404)
        self.assertEqual(self.request('/api/recordings','POST',{'assignee_id':4,'starts_at':self.slot(10)},client)[0],400)
        self.assertEqual(self.request('/api/recordings','POST',{'assignee_id':3,'starts_at':'2020-01-01T10:00'},client)[0],400)
        self.assertTrue(all(r['client_id']==1 for r in self.request('/api/recordings',session=client)[1]['recordings']))
    def test_13_finance_permissions_and_cash_report(self):
        admin,_=self.login();employee,_=self.login('bia@evoolui.test');client,_=self.login('cliente@evoolui.test')
        for s in (employee,client):
            self.assertEqual(self.request('/api/finance',session=s)[0],403)
            self.assertEqual(self.request('/api/finance/entries','POST',{},s)[0],403)
        day=server.operations.now_local().date().isoformat()
        for kind,amount in [('receita',150050),('despesa',50000)]:
            status,d,_=self.request('/api/finance/entries','POST',{'kind':kind,'amount':amount,'client_id':1,'description':'Registro fictício','due_on':day,'paid_on':day,'status':'pago'},admin);self.assertEqual(status,201,d)
        data=self.request('/api/finance',session=admin)[1]
        self.assertEqual(data['summary']['received'],150050);self.assertEqual(data['summary']['spent'],50000);self.assertEqual(data['summary']['balance'],100050)
        self.assertFalse(any(n['kind']=='financeiro' for n in self.request('/api/notifications',session=employee)[1]['notifications']))
    def test_14_contracts_invoice_once_and_month_rollover(self):
        admin,_=self.login();now=server.operations.now_local();month=now.strftime('%Y-%m')
        payload={'client_id':1,'description':'Contrato fictício teste','amount':99900,'due_day':31,'starts_on':now.date().replace(day=1).isoformat(),'ends_on':'2035-12-31','status':'ativo'}
        status,d,_=self.request('/api/finance/contracts','POST',payload,admin);self.assertEqual(status,201,d);cid=d['id']
        with server.connect() as db:
            server.operations.process(db,now);server.operations.process(db,now)
            self.assertEqual(db.execute('SELECT count(*) FROM ledger WHERE contract_id=? AND period=?',(cid,month)).fetchone()[0],1)
            feb=dt.datetime(2030,2,1,10)
            server.operations.process(db,feb)
            row=db.execute('SELECT * FROM ledger WHERE contract_id=? AND period=?',(cid,'2030-02')).fetchone()
            self.assertEqual(row['due_on'],'2030-02-28')
    def test_15_reminders_dedup_read_and_scope(self):
        client,_=self.login('cliente@evoolui.test');admin,_=self.login()
        status,data,_=self.recording(client,days=15,hour=15);self.assertEqual(status,201)
        rid=data['id'];self.assertEqual(self.request(f'/api/recordings/{rid}/decision','POST',{'action':'confirmar'},admin)[0],200)
        with server.connect() as db:
            r=db.execute('SELECT * FROM recordings WHERE id=?',(rid,)).fetchone();fake=dt.datetime.fromisoformat(r['starts_at'])-dt.timedelta(hours=3)
            server.operations.process(db,fake);server.operations.process(db,fake)
            self.assertEqual(db.execute("SELECT count(*) FROM notifications WHERE user_id=5 AND dedup LIKE ?",(f'recording-reminder:{rid}:%',)).fetchone()[0],1)
        notes=self.request('/api/notifications',session=client)[1]['notifications'];n=next(n for n in notes if n['entity_id']==str(rid) and n['kind']=='gravacao')
        self.assertEqual(self.request('/api/notifications/'+str(n['id'])+'/read','POST',{},client)[0],200)
        self.assertEqual(self.request('/api/notifications/'+str(n['id'])+'/read','POST',{},admin)[0],404)
        self.assertFalse(any(x['read_at'] is None for x in self.request('/api/notifications',session=client)[1]['notifications'] if x['id']==n['id']))
    def test_16_task_link_rejects_wrong_company(self):
        admin,_=self.login()
        with server.connect() as db: tid=db.execute('SELECT id FROM tasks WHERE client_id=3 LIMIT 1').fetchone()[0]
        self.assertEqual(self.upload(admin,cid=1,task=tid)[0],404)
        with server.connect() as db:
            self.assertFalse(db.execute('SELECT 1 FROM files WHERE client_id=1 AND task_id=?',(tid,)).fetchone())
    def test_17_schema_repeat_preserves_data(self):
        with server.connect() as db:
            before=db.execute('SELECT count(*) FROM files').fetchone()[0]
        server.initialize();server.initialize()
        with server.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM files').fetchone()[0],before)

    def test_18_manual_calendar_stays_synchronized(self):
        client,_=self.login('cliente@evoolui.test');admin,_=self.login()
        status,data,_=self.recording(client,days=25,hour=17);self.assertEqual(status,201,data)
        rid=data['id'];self.assertEqual(self.request(f'/api/recordings/{rid}/decision','POST',{'action':'confirmar'},admin)[0],200)
        with server.connect() as db:r=dict(db.execute('SELECT * FROM recordings WHERE id=?',(rid,)).fetchone())
        start=self.slot(28,16);end=(dt.datetime.fromisoformat(start)+dt.timedelta(hours=1)).isoformat(timespec='minutes')
        event={'client_id':1,'assignee_id':3,'title':'Gravação reagendada fictícia','starts_at':start,'ends_at':end,'kind':'gravacao','status':'agendado'}
        self.assertEqual(self.request('/api/events/'+str(r['event_id']),'PUT',event,admin)[0],200)
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT starts_at FROM recordings WHERE id=?',(rid,)).fetchone()[0],start)
            self.assertEqual(db.execute('SELECT due_at FROM tasks WHERE id=?',(r['task_id'],)).fetchone()[0],end)
        event['status']='cancelado';self.assertEqual(self.request('/api/events/'+str(r['event_id']),'PUT',event,admin)[0],200)
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM recordings WHERE id=?',(rid,)).fetchone()[0],'cancelado')
            self.assertEqual(db.execute('SELECT status FROM tasks WHERE id=?',(r['task_id'],)).fetchone()[0],'cancelada')
    def test_19_quota_and_finance_validation(self):
        admin,_=self.login();old=server.file_storage.CLIENT_QUOTA
        try:
            server.file_storage.CLIENT_QUOTA=0
            self.assertEqual(self.upload(admin)[0],413)
        finally:server.file_storage.CLIENT_QUOTA=old
        data={'kind':'receita','amount':-1,'client_id':1,'description':'Inválido','due_on':'2026-10-30','status':'pendente'}
        self.assertEqual(self.request('/api/finance/entries','POST',data,admin)[0],400)
        data['amount']=100;data['status']='pago';data['paid_on']='2099-01-01'
        self.assertEqual(self.request('/api/finance/entries','POST',data,admin)[0],400)
        self.assertEqual(self.request('/api/finance?month=2026-99',session=admin)[0],400)

    def test_20_overdue_task_notifications_are_internal_and_deduplicated(self):
        now=server.operations.now_local()
        with server.connect() as db:
            task=db.execute("SELECT * FROM tasks WHERE title='Entregar seleção de fotografias'").fetchone()
            self.assertIsNotNone(task)
            server.operations.process(db,now);server.operations.process(db,now)
            key=f'task-overdue:{task["id"]}:{now.date()}'
            self.assertEqual(db.execute('SELECT count(*) FROM notifications WHERE user_id=3 AND dedup=?',(key,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM notifications WHERE user_id=1 AND dedup=?',(key,)).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM notifications WHERE user_id=5 AND dedup=?',(key,)).fetchone()[0],0)

if __name__=='__main__':unittest.main(verbosity=2)
