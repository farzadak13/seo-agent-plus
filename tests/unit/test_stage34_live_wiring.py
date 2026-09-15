from contextlib import contextmanager
from datetime import date, timedelta
import json
import threading
import pytest
from fastapi.testclient import TestClient
from app.persistence.memory import InMemoryRepository
from app.runtime.config import RuntimeConfig
from app.runtime.container import create_runtime_app
from app.runtime.worker import WorkerHandle
from app.gsc.transport import GoogleResponse
from app.runtime.gsc import LiveGSCGateway
from app.runtime.adapters import SiteAdapterFactory
from app.models.sites import Site, SiteAdapterConnection, SecretRef, GSCConnectionConfig
from app.runtime.lease import postgres_worker_lease


class TransactionalFakeRepository(InMemoryRepository):
    def __init__(self, dsn):super().__init__()
    @contextmanager
    def transaction(self):
        before=dict(self._records)
        try:yield
        except BaseException:
            self._records=before
            raise


def setup_api(monkeypatch, missing_baseline=False):
    monkeypatch.setattr('app.runtime.container.PostgresRepository',TransactionalFakeRepository)
    monkeypatch.setenv('TEST_GSC_TOKEN','PRIVATE_TOKEN')
    calls=[]
    def request(method,path,**kwargs):
        body=kwargs['json'];calls.append(body)
        start=date.fromisoformat(body['startDate']);end=date.fromisoformat(body['endDate'])
        rows=[]
        for i in range((end-start).days+1):
            if missing_baseline and start==date(2026,9,3) and i==1:continue
            day=(start+timedelta(days=i)).isoformat()
            rows += [dict(keys=['https://example.com/page','seo',day],impressions=100.0,clicks=5.0,position=5.0),
                     dict(keys=['https://unrelated.com/page','other',day],impressions=9999.0,clicks=900.0,position=1.0)]
        if body['dimensions'] == ['page', 'date']:
            rows = [{**row, 'keys': [row['keys'][0], row['keys'][2]]} for row in rows]
        return GoogleResponse(200,json.dumps({'rows':rows}).encode(),{})
    monkeypatch.setattr('app.runtime.service.LiveGSCGateway',
        lambda site,timeout,request_fn=None:LiveGSCGateway(site,timeout=timeout,request_fn=request))
    app=create_runtime_app(RuntimeConfig(api_key='test-key',database_dsn='unused',gsc_mode='live',worker_enabled=False))
    client=TestClient(app);client.headers['Authorization']='Bearer test-key'
    response=client.post('/v1/sites',json={'name':'Example','base_url':'https://example.com'})
    site_id=response.json()['site_id']
    assert client.put(f'/v1/sites/{site_id}/connections/gsc',json={'property_url':'https://example.com',
        'credential_ref':'TEST_GSC_TOKEN'}).status_code==200
    return client,app.state.runtime,site_id,calls


def create_run(client,site_id):
    return client.post(f'/v1/sites/{site_id}/runs',json={'start_date':'2026-09-05','end_date':'2026-09-06',
        'normalized_url':'https://example.com/page','normalized_query':'seo','candidate_id':'c'})


def test_api_job_live_gateway_pipeline_and_persisted_result(monkeypatch):
    client,runtime,site_id,calls=setup_api(monkeypatch)
    response=create_run(client,site_id);assert response.status_code==202
    run_id=response.json()['run_id']
    assert runtime.scheduler.run_once().status.value=='completed'
    result=client.get('/v1/runs/'+run_id).json()['result']
    assert result['status']=='completed'
    assert result['features']['volume']['current_impressions']==200
    assert {o['date'] for o in result['evidence']['current_observations']}=={'2026-09-05','2026-09-06'}
    assert {o['normalized_url'] for o in result['evidence']['current_observations']}=={'https://example.com/page'}
    assert [(c['startDate'],c['endDate']) for c in calls]==[('2026-09-03','2026-09-04'),('2026-09-03','2026-09-04'),('2026-09-05','2026-09-06'),('2026-09-05','2026-09-06')]
    assert len(runtime.repository.list(aggregate_type='runtime_gsc_response'))==4
    assert 'PRIVATE_TOKEN' not in str(runtime.repository.list())
    events=runtime.observability.event_sink.list_events()
    assert {e.event_type.value for e in events}=={'job','run','pipeline'}
    assert len({e.correlation_id for e in events})==1


def test_missing_baseline_days_cannot_pass_quality_gate(monkeypatch):
    client,runtime,site_id,_=setup_api(monkeypatch,missing_baseline=True)
    response=create_run(client,site_id);runtime.scheduler.run_once()
    result=client.get('/v1/runs/'+response.json()['run_id']).json()['result']
    assert result['status']=='rejected_data_quality'


def test_atomic_job_run_creation_rolls_back(monkeypatch):
    client,runtime,site_id,_=setup_api(monkeypatch)
    def fail(*a,**kw):raise RuntimeError('database failure')
    monkeypatch.setattr(runtime.job_store,'create',fail)
    assert create_run(client,site_id).status_code==409
    assert runtime.repository.list(aggregate_type='seo_run')==[]
    assert runtime.repository.list(aggregate_type='job')==[]


def test_worker_stop_timeout_keeps_thread_and_lease():
    entered=threading.Event();release=threading.Event();calls=[];states=[]
    @contextmanager
    def lease():
        states.append('acquired')
        try:yield
        finally:states.append('released')
    class Scheduler:
        def run_forever(self,**kwargs):calls.append(1);entered.set();release.wait(2)
    worker=WorkerHandle(Scheduler(),0.01,lease);worker.start()
    try:
        assert entered.wait(1)
        worker.stop(timeout_seconds=0)
        assert worker.running and states==['acquired']
        with pytest.raises(RuntimeError,match='stopping'):worker.start()
        assert calls==[1]
    finally:release.set();worker.stop(timeout_seconds=1)
    assert not worker.running and states==['acquired','released']


def test_worker_reports_failure_without_saving_exception_text():
    class Scheduler:
        def run_forever(self,**kw):raise ValueError('PRIVATE_TOKEN')
    worker=WorkerHandle(Scheduler(),0.01);worker.start();worker.stop(1)
    assert worker.last_error_type=='ValueError' and not worker.running


@pytest.mark.parametrize('key',['',' ','change-me'])
def test_direct_configuration_cannot_bypass_key_validation(key):
    with pytest.raises(ValueError):RuntimeConfig(api_key=key,database_dsn='unused')


def test_config_repr_omits_credentials():
    config=RuntimeConfig(api_key='PRIVATE_KEY',database_dsn='PRIVATE_DSN')
    assert 'PRIVATE_KEY' not in repr(config) and 'PRIVATE_DSN' not in repr(config)


def test_generic_and_custom_cms_registration(monkeypatch):
    client,runtime,site_id,_=setup_api(monkeypatch)
    response=client.put(f'/v1/sites/{site_id}/connections/site-adapter',json={
        'adapter_type':'generic_rest','config':{'enabled_capabilities':['read_page']}})
    assert response.status_code==200
    assert client.get(f'/v1/sites/{site_id}/capabilities').json()['capabilities']==['read_page']
    from app.models.rest_adapter import RESTAdapterConfig
    from app.site_adapters.generic_rest import GenericRESTSiteAdapter
    runtime.adapters.register('custom',RESTAdapterConfig,GenericRESTSiteAdapter)
    site=runtime.site_store.get(site_id).model_copy(update={'site_adapter':SiteAdapterConnection(adapter_type='custom')})
    assert runtime.adapters.build(site).adapter_id=='custom:'+site_id


def test_wordpress_factory_uses_secrets_and_unique_identity(monkeypatch):
    monkeypatch.setenv('WP_USER','user');monkeypatch.setenv('WP_PASS','password')
    from app.onboarding.secrets import EnvironmentSecretResolver
    site=Site(site_id='s',principal_id='p',name='Example',base_url='https://example.com',
        site_adapter=SiteAdapterConnection(adapter_type='wordpress',secret_refs={
            'username':SecretRef(key='WP_USER'),'application_password':SecretRef(key='WP_PASS')}))
    assert SiteAdapterFactory(EnvironmentSecretResolver()).build(site).adapter_id=='wordpress:s'


def test_readiness_reports_database_failure(monkeypatch):
    # Readiness probes with ping, not list: a health check must not scan the
    # record table. The guarantee under test is unchanged -- an unreachable
    # store answers 503 and never leaks the driver's message.
    client,runtime,_,_=setup_api(monkeypatch)
    assert client.get('/readyz').status_code==200
    def fail(*args,**kw):raise RuntimeError('private database error')
    monkeypatch.setattr(runtime.repository,'ping',fail)
    assert client.get('/readyz').status_code==503
    assert 'private' not in client.get('/readyz').text


def test_readiness_does_not_scan_the_record_table(monkeypatch):
    client,runtime,_,_=setup_api(monkeypatch)
    def forbidden(*args,**kw):raise AssertionError('readiness must not list records')
    monkeypatch.setattr(runtime.repository,'list',forbidden)
    assert client.get('/readyz').status_code==200


def test_readiness_reports_the_title_path(monkeypatch):
    client,_,_,_=setup_api(monkeypatch)
    body=client.get('/readyz').json()
    assert body['title_workflow']=='disabled'
    assert body['serp_mode']=='none'
    assert body['llm_mode']=='none'


def test_postgres_lease_refuses_other_worker():
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql):pass
        def fetchone(self):return {'acquired':False}
    class Connection:
        def cursor(self):return Cursor()
    class Repository:
        @contextmanager
        def _connect(self):yield Connection()
    with pytest.raises(RuntimeError,match='Another SEO worker'):
        with postgres_worker_lease(Repository()):raise AssertionError('must not enter')


def test_an_unknown_auth_mode_does_not_send_request():
    """A credential kind we cannot build must fail before the token is sent
    anywhere, not after a request goes out with the wrong header."""
    site=Site(site_id='s',principal_id='p',name='Example',base_url='https://example.com',
        gsc=GSCConnectionConfig(property_url='https://example.com',credential_ref=SecretRef(key='TOKEN'),auth_mode='basic_auth'))
    def fail(*a,**kw):raise AssertionError('network')
    with pytest.raises(ValueError):
        LiveGSCGateway(site,request_fn=fail).fetch(site_id='s',property_url='https://example.com',credential='x',start_date=date(2026,9,1),end_date=date(2026,9,1))


def test_a_service_account_site_is_now_supported():
    """It used to be rejected outright; the credential layer handles it now."""
    site=Site(site_id='s',principal_id='p',name='Example',base_url='https://example.com',
        gsc=GSCConnectionConfig(property_url='https://example.com',credential_ref=SecretRef(key='TOKEN'),auth_mode='service_account'))
    captured={}
    def provider_factory(*,kind,secret,session):
        captured['kind']=kind
        return type('P',(),{'token':staticmethod(lambda:'minted')})()
    def request(method,path,**kwargs):
        captured['auth']=kwargs['headers'].get('Authorization')
        return GoogleResponse(200,json.dumps({'rows':[]}).encode(),{})
    LiveGSCGateway(site,request_fn=request,provider_factory=provider_factory).fetch(
        site_id='s',property_url='https://example.com',credential='{}',
        start_date=date(2026,9,1),end_date=date(2026,9,1))
    assert captured['kind']=='service_account'
    assert captured['auth']=='Bearer minted'
