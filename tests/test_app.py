"""Integration tests using a real HTTP server and an isolated SQLite database."""
import concurrent.futures
import http.client
import importlib.util
import json
import os
import secrets
from pathlib import Path
import tempfile
import threading
import unittest

spec=importlib.util.spec_from_file_location('server',Path(__file__).resolve().parents[1]/'app/server.py')
server=importlib.util.module_from_spec(spec); spec.loader.exec_module(server)

TEST_PASSWORD=secrets.token_urlsafe(24)

class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); server.DB=Path(cls.tmp.name)/'test.db'
        os.environ['EVOOLUI_DEMO_PASSWORD']=TEST_PASSWORD; server.seed()
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.port=cls.http.server_address[1]
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True); cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.tmp.cleanup()
    def request(self,path,method='GET',data=None,session=None,csrf=True):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        headers={'Content-Type':'application/json'}
        if session:
            headers['Cookie']=session['cookie']
            if csrf: headers['X-CSRF-Token']=session['csrf']
        conn.request(method,path,json.dumps(data) if data is not None else None,headers)
        res=conn.getresponse(); status=res.status; headers=dict(res.getheaders()); raw=res.read(); conn.close()
        try: body=json.loads(raw)
        except ValueError: body=raw.decode()
        return status,body,headers
    def login(self,email='admin@evoolui.test'):
        status,_,h=self.request('/api/login','POST',{'email':email,'password':TEST_PASSWORD})
        self.assertEqual(status,200)
        self.assertIn('HttpOnly',h['Set-Cookie']); self.assertIn('SameSite=Strict',h['Set-Cookie'])
        s={'cookie':h['Set-Cookie'].split(';')[0],'csrf':''}
        status,state,_=self.request('/api/state',session=s); self.assertEqual(status,200); s['csrf']=state['csrf']
        return s,state
    def task(self,**kw):
        return dict(title='Tarefa de teste',client_id=1,assignee_id=3,due_at='2030-05-01T15:00',status='pendente',priority='alta',description='',**kw)
    def event(self,title='Teste agenda',start='2031-06-02T10:00',end='2031-06-02T11:00'):
        return dict(title=title,client_id=1,assignee_id=3,starts_at=start,ends_at=end,status='agendado',kind='gravacao',description='')
    def test_01_unauthenticated_and_bad_login(self):
        self.assertEqual(self.request('/api/state')[0],401)
        self.assertEqual(self.request('/api/login','POST',{'email':'admin@evoolui.test','password':'wrong'})[0],401)
    def test_02_static_security(self):
        for path in ['/','/app.js','/style.css']:
            status,_,headers=self.request(path); self.assertEqual(status,200)
            self.assertIn("default-src 'self'",headers['Content-Security-Policy'])
        self.assertEqual(self.request('/../app/server.py')[0],404)
    def test_03_client_isolation_and_financial_protection(self):
        s,state=self.login('cliente@evoolui.test')
        self.assertEqual([c['id'] for c in state['clients']],[1])
        self.assertTrue(all(t['client_id']==1 for t in state['tasks']))
        self.assertTrue(all(e['client_id']==1 for e in state['events']))
        self.assertNotIn('monthly_value',state['clients'][0]); self.assertNotIn('due_day',state['clients'][0])
        self.assertEqual(state['audit'],[])
        for table in ['clients','users','tasks','events']:
            self.assertEqual(self.request('/api/'+table,'POST',{},s)[0],403)
    def test_04_employee_scope_and_write_authorization(self):
        s,state=self.login('bia@evoolui.test')
        self.assertEqual({c['id'] for c in state['clients']},{1,2})
        self.assertTrue(all(t['assignee_id']==3 for t in state['tasks']))
        task=self.task(); task['client_id']=3
        self.assertEqual(self.request('/api/tasks','POST',task,s)[0],403)
        task=self.task(); task['assignee_id']=4
        self.assertEqual(self.request('/api/tasks','POST',task,s)[0],403)
        other=next(t for t in self.login()[1]['tasks'] if t['assignee_id']==4)
        self.assertEqual(self.request(f"/api/tasks/{other['id']}",'PUT',self.task(),s)[0],403)
        self.assertEqual(self.request('/api/clients','POST',{},s)[0],403)
    def test_05_csrf_required(self):
        s,_=self.login()
        self.assertEqual(self.request('/api/tasks','POST',self.task(),s,False)[0],403)
    def test_06_client_create_edit_and_audit(self):
        s,_=self.login(); data=dict(company='Teste persistência',contact='Pessoa fictícia',phone='11999999999',instagram='@exemplo',services='Reels',monthly_value=123456,due_day=15,active=1,responsibles=[3])
        status,res,_=self.request('/api/clients','POST',data,s); self.assertEqual(status,201)
        cid=res['id']; data['company']='Empresa editada'
        self.assertEqual(self.request(f'/api/clients/{cid}','PUT',data,s)[0],200)
        state=self.request('/api/state',session=s)[1]; c=next(c for c in state['clients'] if c['id']==cid)
        self.assertEqual(c['company'],'Empresa editada'); self.assertEqual(c['monthly_value'],123456); self.assertEqual(c['responsibles'],[3])
        self.assertTrue(any(a['entity']=='clients' and a['entity_id']==cid and a['action']=='editar' for a in state['audit']))
        with server.connect() as db: self.assertEqual(db.execute('SELECT company FROM clients WHERE id=?',(cid,)).fetchone()[0],'Empresa editada')
    def test_07_task_lifecycle(self):
        s,_=self.login('bia@evoolui.test'); data=self.task()
        status,res,_=self.request('/api/tasks','POST',data,s); self.assertEqual(status,201)
        for step in ['em_andamento','concluida']:
            data['status']=step; self.assertEqual(self.request(f"/api/tasks/{res['id']}",'PUT',data,s)[0],200)
        task=next(t for t in self.request('/api/state',session=s)[1]['tasks'] if t['id']==res['id'])
        self.assertEqual(task['status'],'concluida')
    def test_08_conflict_reschedule_cancel(self):
        s,_=self.login(); ev=self.event()
        status,res,_=self.request('/api/events','POST',ev,s); self.assertEqual(status,201)
        self.assertEqual(self.request('/api/events','POST',ev,s)[0],409)
        ev['starts_at']='2031-06-02T12:00'; ev['ends_at']='2031-06-02T13:00'
        self.assertEqual(self.request(f"/api/events/{res['id']}",'PUT',ev,s)[0],200)
        self.assertEqual(self.request('/api/events','POST',self.event(),s)[0],201)
        ev['status']='cancelado'; self.assertEqual(self.request(f"/api/events/{res['id']}",'PUT',ev,s)[0],200)
        ev['status']='agendado'; self.assertEqual(self.request('/api/events','POST',ev,s)[0],201)
    def test_09_simultaneous_conflict(self):
        s,_=self.login(); data=self.event(start='2032-01-01T12:00',end='2032-01-01T13:00')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(lambda _:self.request('/api/events','POST',data,s)[0],range(2)))
        self.assertEqual(sorted(statuses),[201,409])
    def test_10_validation_and_rollback(self):
        s,_=self.login(); ev=self.event(start='2033-01-01T15:00',end='2033-01-01T14:00')
        self.assertEqual(self.request('/api/events','POST',ev,s)[0],400)
        task=self.task(); task['status']='inventado'; self.assertEqual(self.request('/api/tasks','POST',task,s)[0],400)
        data=dict(company='Deve reverter',contact='Exemplo',monthly_value=0,due_day=1,responsibles=[999])
        self.assertEqual(self.request('/api/clients','POST',data,s)[0],400)
        self.assertFalse(any(c['company']=='Deve reverter' for c in self.request('/api/state',session=s)[1]['clients']))
    def test_11_accounts_and_revocation(self):
        s,_=self.login(); data=dict(name='Teste acesso',email='novo@evoolui.test',role='funcionario',password=TEST_PASSWORD,active=1)
        status,res,_=self.request('/api/users','POST',data,s); self.assertEqual(status,201)
        user,_=self.login('novo@evoolui.test'); data['active']=0; data['password']=''
        self.assertEqual(self.request(f"/api/users/{res['id']}",'PUT',data,s)[0],200)
        self.assertEqual(self.request('/api/state',session=user)[0],401)
        self.assertEqual(self.request('/api/users','POST',data,s)[0],400)
    def test_12_partner_cannot_escalate_and_logout(self):
        s,_=self.login('socia@evoolui.test')
        self.assertEqual(self.request('/api/users','POST',{},s)[0],403)
        self.assertEqual(self.request('/api/logout','POST',{},s)[0],200)
        self.assertEqual(self.request('/api/state',session=s)[0],401)
    def test_13_no_passwords_or_tokens_in_state(self):
        _,state=self.login()
        self.assertNotIn('password',json.dumps(state)); self.assertNotIn('token',json.dumps(state))
    def test_14_self_lockout_prevention(self):
        s,state=self.login(); u=state['user']; data=dict(name=u['name'],email=u['email'],role='funcionario',active=1)
        self.assertEqual(self.request(f"/api/users/{u['id']}",'PUT',data,s)[0],400)

if __name__=='__main__': unittest.main(verbosity=2)
