from datetime import date, datetime, timezone
from copy import deepcopy
from types import SimpleNamespace
import pytest

from app.models.gsc import RawGSCResponse
from app.models.outcomes import OutcomeMetric, OutcomeStatus
from app.normalization.gsc import normalize_gsc_response, normalize_url_gsc_response
from app.gsc.client import GSCClient, GSCClientError
from app.gsc.config import GSCClientConfig
from app.learning.engine import build_learning_context
from app.learning.decision import calculate_learning_adjustment
from app.runtime.data_quality import validate_final_response, validate_daily_totals
from tests.unit.test_learning_engine import event
from tests.unit.test_learning_decision_integration import make_evidence
from app.engine.decision import run_decision_engine
from tests.unit.test_measurement_engine import run_measurement, make_observation
from tests.unit.test_stage34_live_wiring import setup_api, create_run


def raw(rows, **extra):
    return RawGSCResponse(response_id='test',site_id='site-1',fetch_date=date(2026,9,13),
        created_at=datetime(2026,9,13,tzinfo=timezone.utc),raw_payload={'rows':rows,**extra})


def row(url='https://example.com/page',query='seo',day='2026-09-05',impressions=100,clicks=10,position=5):
    return {'keys':[url,query,day],'impressions':impressions,'clicks':clicks,'position':position}


@pytest.mark.parametrize('metric,change,direction',[ (OutcomeMetric.POSITION,-0.5,'positive'),
    (OutcomeMetric.POSITION,0.5,'negative'),(OutcomeMetric.CTR,0.5,'positive'),(OutcomeMetric.CTR,-0.5,'negative')])
def test_learning_orients_priority_but_preserves_raw_metric_change(metric,change,direction):
    events=[event(str(i),OutcomeStatus.SUCCESS if direction=='positive' else OutcomeStatus.FAILURE,change)
            .model_copy(update={'primary_metric':metric}) for i in range(5)]
    context=build_learning_context(context_id='c',feedback_events=events)
    assert context.signals[0].direction==direction
    assert context.signals[0].average_relative_change==change
    strategy=run_decision_engine(evidence=make_evidence(),candidate_id='c').strategy
    adjustment=calculate_learning_adjustment(strategy=strategy,context=context)
    assert adjustment.applied
    assert (adjustment.adjustment>0)==(direction=='positive')
    assert abs(adjustment.adjustment)<=0.1


def test_duplicate_feedback_does_not_inflate_sample_confidence():
    one=event('one')
    context=build_learning_context(context_id='c',feedback_events=[one]*5)
    assert context.performances[0].sample_size==1
    assert context.signals[0].direction=='insufficient_sample'
    with pytest.raises(ValueError,match='Conflicting'):
        build_learning_context(context_id='c',feedback_events=[one,one.model_copy(update={'relative_change':0.9})])


def test_exact_gsc_duplicates_are_counted_once_without_mutating_source():
    original=raw([row(),row()]);before=deepcopy(original.raw_payload)
    values=normalize_gsc_response(original)
    assert len(values)==1 and values[0].impressions==100
    assert original.raw_payload==before


def test_conflicting_duplicate_is_rejected():
    with pytest.raises(ValueError,match='Conflicting'):
        normalize_gsc_response(raw([row(),row(impressions=101)]))


def test_distinct_source_keys_merge_with_impression_weighted_position():
    rows=[row(query='كفش',impressions=100,position=4),row(query='کفش',impressions=300,position=8)]
    merged=normalize_gsc_response(raw(rows))
    assert len(merged)==1 and merged[0].impressions==400 and merged[0].avg_position==7
    assert merged==normalize_gsc_response(raw(list(reversed(rows))))


def test_missing_position_does_not_gain_false_precision_when_merging():
    merged=normalize_gsc_response(raw([row(query='كفش',position=None),row(query='کفش')]))
    assert merged[0].avg_position is None


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,True])
def test_invalid_position_is_rejected(value):
    with pytest.raises(ValueError):normalize_gsc_response(raw([row(position=value)]))


def test_query_rows_for_other_dates_and_targets_stay_separate():
    rows=normalize_gsc_response(raw([row(),row(day='2026-09-06'),row(query='other')]))
    assert len(rows)==3


def test_url_totals_are_normalized_from_page_date_dimensions():
    a={'keys':['https://example.com/page','2026-09-05'],'impressions':200,'clicks':20}
    values=normalize_url_gsc_response(raw([a,a],dimensions=['page','date']))
    assert len(values)==1 and values[0].total_impressions==200
    with pytest.raises(ValueError):normalize_gsc_response(raw([a],dimensions=['page','date']))


def test_daily_discrepancies_cannot_cancel_across_dates():
    query=normalize_gsc_response(raw([row(impressions=110),row(day='2026-09-06',impressions=90)]))
    urls=normalize_url_gsc_response(raw([
        {'keys':['https://example.com/page',day],'impressions':100,'clicks':10}
        for day in ['2026-09-05','2026-09-06']]))
    with pytest.raises(ValueError,match='exceed'):validate_daily_totals(query,urls)


def test_partial_query_totals_are_valid_but_missing_url_day_is_not():
    query=normalize_gsc_response(raw([row()]))
    urls=normalize_url_gsc_response(raw([{'keys':['https://example.com/page','2026-09-05'],'impressions':200,'clicks':20}]))
    validate_daily_totals(query,urls)
    with pytest.raises(ValueError,match='missing'):validate_daily_totals(query,[])


def client_response(payload):return SimpleNamespace(status_code=200,json=lambda:payload)


def query(client,dimensions=('page','query','date')):
    return client.query(site_id='site-1',site_url='https://example.com',start_date=date(2026,9,5),end_date=date(2026,9,6),dimensions=dimensions)


def test_final_state_and_dimension_identity_are_persisted():
    calls=[]
    def request(*a,**kw):calls.append(kw['json']);return client_response({'rows':[]})
    client=GSCClient(GSCClientConfig(oauth_access_token='SECRET'),request_fn=request)
    a=query(client);b=query(client,('page','date'))
    assert a.response_id!=b.response_id
    assert all(c['dataState']=='final' and c['aggregationType']=='auto' for c in calls)
    assert a.raw_payload['dimensions']==['page','query','date']
    assert 'SECRET' not in str(a)


def test_pagination_retains_earliest_incomplete_date():
    count=[]
    def request(*a,**kw):
        count.append(1)
        return client_response({'rows':[row(day='2026-09-05') ] if len(count)==1 else [],
                                'metadata':{'first_incomplete_date':'2026-09-06' if len(count)==1 else '2026-09-05'}})
    result=query(GSCClient(GSCClientConfig(data_state='all',page_size=1),request_fn=request))
    assert result.raw_payload['metadata']['first_incomplete_date']=='2026-09-05'


@pytest.mark.parametrize('mode',['repeat','limit'])
def test_bad_pagination_fails_instead_of_returning_partial_data(mode):
    calls=[]
    def request(*a,**kw):
        calls.append(1)
        return client_response({'rows':[row(query=str(len(calls)) if mode=='limit' else 'seo')]})
    client=GSCClient(GSCClientConfig(page_size=1,max_pages=2),request_fn=request)
    with pytest.raises(GSCClientError):query(client)
    assert len(calls)==2


@pytest.mark.parametrize('change',[{'dataState':'all'},{'metadata':{'first_incomplete_date':'2026-09-06'}},
    {'startDate':'2026-09-04'},{'responseAggregationType':'byProperty'}])
def test_unfit_response_is_rejected_before_decision(change):
    payload={'dataState':'final','startDate':'2026-09-05','endDate':'2026-09-06',**change}
    with pytest.raises(ValueError):
        validate_final_response(raw([],**payload),site_id='site-1',begin=date(2026,9,5),end=date(2026,9,6))


def test_measurement_duplicate_days_do_not_inflate_completeness():
    a=make_observation(day=date(2026,8,1),impressions=100,clicks=10,position=5)
    b=make_observation(day=date(2026,9,1),impressions=100,clicks=10,position=5)
    result=run_measurement(baseline_observations=[a,a],current_observations=[b,b])
    assert result.baseline.completeness==0.5 and result.baseline.impressions==100
    assert result.status.value=='insufficient_data'
    with pytest.raises(ValueError,match='Conflicting'):
        run_measurement(baseline_observations=[a,a.model_copy(update={'clicks':11})],current_observations=[b])


@pytest.mark.parametrize('kwargs',[{'current_start_date':date(2026,8,2),'current_end_date':date(2026,8,3)},
                                   {'current_end_date':date(2026,9,3)}])
def test_measurement_rejects_overlapping_or_unequal_windows(kwargs):
    with pytest.raises(ValueError):run_measurement(baseline_observations=[],current_observations=[],**kwargs)


def test_live_run_persists_four_sources_and_bundle_and_retry_is_idempotent(monkeypatch):
    client,runtime,site_id,calls=setup_api(monkeypatch)
    response=create_run(client,site_id);runtime.scheduler.run_once()
    result=client.get('/v1/runs/'+response.json()['run_id']).json()['result']
    snapshot=result['evidence']['snapshot']
    bundle=runtime.repository.get(aggregate_type='runtime_data_bundle',aggregate_id=snapshot['data_snapshot_id'])
    assert len(bundle.payload['source_response_ids'])==4
    assert len(set(bundle.payload['source_response_ids']))==4
    assert [c['dimensions'] for c in calls]==[['page','query','date'],['page','date'],['page','query','date'],['page','date']]
    create_run(client,site_id);runtime.scheduler.run_once()
    assert len(runtime.repository.list(aggregate_type='runtime_gsc_response'))==4
    assert len(runtime.repository.list(aggregate_type='runtime_data_bundle'))==1


def test_runtime_uses_independent_url_counts_not_query_sum(monkeypatch):
    client,runtime,site_id,_=setup_api(monkeypatch)
    import app.runtime.service as service_module
    factory=service_module.LiveGSCGateway
    def independent_factory(site,timeout):
        gateway=factory(site,timeout)
        request=gateway.request_fn
        def independent_request(*args,**kwargs):
            result=request(*args,**kwargs)
            payload=result.json()
            if kwargs['json']['dimensions']==['page','date']:
                for value in payload['rows']:
                    value['impressions'] *= 2
                    value['clicks'] *= 2
            return client_response(payload)
        gateway.request_fn=independent_request
        return gateway
    monkeypatch.setattr(service_module,'LiveGSCGateway',independent_factory)
    response=create_run(client,site_id);runtime.scheduler.run_once()
    result=client.get('/v1/runs/'+response.json()['run_id']).json()['result']
    assert result['features']['visibility']['url_total_impressions']==400
    assert result['features']['visibility']['query_visibility_share']==0.5
    assert result['reconciliation']['is_partial'] is True


def test_runtime_refuses_conflicting_content_under_same_response_id(monkeypatch):
    client,runtime,site_id,_=setup_api(monkeypatch)
    from app.runtime.service import PersistentSEORunService
    from app.onboarding.secrets import EnvironmentSecretResolver
    from app.persistence.contracts import PersistenceConflictError
    service=PersistentSEORunService(repository=runtime.repository,settings=runtime.config,secret_resolver=EnvironmentSecretResolver())
    original=raw([row()]);service._save_response(original)
    with pytest.raises(PersistenceConflictError):service._save_response(raw([row(impressions=101)]))
