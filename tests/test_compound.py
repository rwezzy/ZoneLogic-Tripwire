import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from occlusive.compound import CompoundEngine, CompoundRule, combine
from occlusive.app import create_app

ZONE = {"id": "crosswalk", "name": "User marked crossing", "points": [[.2,.2],[.8,.2],[.8,.8],[.2,.8]]}


def rule(**changes):
    return {"id": "pair", "name": "Person and car for review", "a": {"class_name": "person", "zone_id": "crosswalk", "min_confidence": .4},
            "b": {"class_name": "car", "zone_id": "crosswalk", "min_confidence": .4}, "operator": "AND", "cooldown_seconds": 0, **changes}


def frame(time, a=False, b=False):
    return {"timestamp": time, "detections": [{"class_name": name, "confidence": .9, "bbox": [.4,.4,.6,.6], "track_id": name}
            for name, present in (("person", a), ("car", b)) if present]}


@pytest.mark.parametrize("a,b", [(False,False),(False,True),(True,False),(True,True)])
@pytest.mark.parametrize("operator", ["AND","OR","XOR"])
def test_all_truth_table_rows_on_actual_frame_geometry(a, b, operator):
    expected = {"AND": int(a) + int(b) == 2, "OR": int(a) + int(b) >= 1, "XOR": int(a) + int(b) == 1}[operator]
    assert combine(a, b, operator) is expected
    engine = CompoundEngine([ZONE], [rule(operator=operator)])
    assert engine.process(frame(0, a, b)) == []
    assert engine.values[0]["result"] is expected


def test_same_frame_not_latched_across_different_frames():
    engine = CompoundEngine([ZONE], [rule()])
    for f in [frame(0),frame(1,True),frame(2,False,True)]:
        assert engine.process(f) == []
    event = engine.process(frame(3,True,True))[0]
    assert event["compound"]["a"] and event["compound"]["b"]
    assert event["timestamp"] == event["compound"]["frame_timestamp"] == 3
    assert len(event["compound"]["matches_a"]) == len(event["compound"]["matches_b"]) == 1
    assert engine.process(frame(4,True,True)) == []


def test_dropped_detection_breaks_dwell_and_rearms():
    engine = CompoundEngine([ZONE], [rule(dwell_seconds=1)])
    for f in [frame(0),frame(1,True,True),frame(1.5,True),frame(2,True,True)]:
        assert not engine.process(f)
    assert len(engine.process(frame(3,True,True))) == 1
    assert not engine.process(frame(4,True,True))


def test_cooldown_suppresses_episode_without_delayed_duplicate():
    engine = CompoundEngine([ZONE], [rule(cooldown_seconds=5)])
    actual = []
    for t, value in [(0,False),(1,True),(2,False),(3,True),(4,True),(5,True),(6,True),(7,True),(8,False),(9,True)]:
        actual.extend(engine.process(frame(t,value,value)))
    assert [event["timestamp"] for event in actual] == [1,9]


def test_initial_true_seek_gap_duplicates_and_rewind_do_not_invent_edges():
    engine = CompoundEngine([ZONE], [rule()])
    for f in [frame(0,True,True),frame(0,True,True),frame(1,True,True),frame(5,True,True),frame(2,True,True)]:
        assert not engine.process(f)
    engine.reset()
    assert not engine.process(frame(2,True,True))
    assert not engine.process(frame(3))
    assert len(engine.process(frame(4,True,True))) == 1


def test_not_on_observed_empty_detection_and_missing_zone_unknown():
    item = rule(); item["a"]["negate"] = True
    engine = CompoundEngine([ZONE], [item])
    assert not engine.process(frame(0,True,True))
    event = engine.process(frame(1,False,True))[0]
    assert event["compound"]["raw_a"] is False and event["compound"]["a"] is True
    missing = CompoundEngine([], [item])
    assert not missing.process(frame(0))
    assert missing.values[0]["result"] is None


def test_confidence_geometry_boundary_and_disabled_rules():
    engine = CompoundEngine([ZONE], [rule()])
    engine.process(frame(0))
    low = frame(1,True,True); low["detections"][0]["confidence"] = .39
    assert not engine.process(low)
    outside = frame(2,True,True); outside["detections"][0]["bbox"] = [.81,.81,.9,.9]
    assert not engine.process(outside)
    boundary = frame(3,True,True); boundary["detections"][0]["bbox"] = [.1,.1,.3,.3]
    assert len(engine.process(boundary)) == 1
    disabled = CompoundEngine([ZONE], [rule(enabled=False)])
    assert not disabled.process(frame(0)) and not disabled.process(frame(1,True,True))


@pytest.mark.parametrize("change", [{"operator":"eval('True')"},{"dwell_seconds":-1},{"dwell_seconds":float('nan')},{"enabled":"false"}])
def test_invalid_rule_rejected(change):
    with pytest.raises(ValueError): CompoundRule.model_validate(rule(**change))


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        document = {"width":640,"height":360,"duration":6,"frames":[frame(0),frame(1,True,True),frame(2,True,True)],"provenance":"Test fixture, not a real VAST observation"}
        result = client.post('/api/sources/upload',data={"name":"Synchronized fixture"},files={"video":('fixture.mp4',b'fixture','video/mp4'),"detections":('frames.json',json.dumps(document),'application/json')})
        assert result.status_code == 201
        client.source = result.json()['id']
        config = {"source_id":client.source,"zones":[ZONE],"rules":[]}
        assert client.put('/api/config',json=config).status_code == 200
        yield client


def save(client, item=None):
    response = client.put('/api/compound/config',json={"source_id":client.source,"rules":[item or rule()]})
    assert response.status_code == 200, response.text


def session(client):
    return client.post('/api/sessions',json={"source_id":client.source}).json()['id']


def evaluate(client, sid, time, **options):
    return client.post(f'/api/sessions/{sid}/evaluate',json={"timestamp":time,**options})


def test_api_same_pipeline_persists_resolves_and_deduplicates(client):
    save(client); sid=session(client)
    assert not evaluate(client,sid,0).json()['events']
    response=evaluate(client,sid,1).json(); event=response['events'][0]
    assert response['compound_states'][0]['result'] is True
    assert event['source_id']==client.source and event['condition']=='compound'
    assert client.patch('/api/incidents/'+event['id'],json={"status":"resolved"}).status_code==200
    for replay in [sid,session(client)]:
        assert not evaluate(client,replay,0,seek=True).json()['events']
        assert not evaluate(client,replay,2).json()['events']
    rows=client.get('/api/incidents').json()['incidents']
    assert len(rows)==1 and rows[0]['status']=='resolved'
    assert client.get('/api/incidents/export').json()['incidents'][0]['compound']['result'] is True


def test_api_uses_existing_screenshot_storage(client):
    import base64, io
    from PIL import Image
    save(client); sid=session(client); evaluate(client,sid,0)
    event=evaluate(client,sid,1).json()['events'][0]
    image=io.BytesIO(); Image.new('RGB',(8,8),'green').save(image,format='PNG')
    url='/api/incidents/'+event['id']+'/evidence'
    result=client.post(url,json={"timestamp":1,"data_url":"data:image/png;base64,"+base64.b64encode(image.getvalue()).decode()})
    assert result.status_code==200 and client.get(url).content==image.getvalue()


def test_config_is_separate_validation_atomic_and_sessions_expire(client):
    before=client.get('/api/config',params={"source_id":client.source}).json()
    save(client); sid=session(client)
    for sensor in [{'class_name':'forklift','zone_id':'crosswalk'},{'class_name':'person','zone_id':'missing'}]:
        invalid=rule(); invalid['a']=sensor
        assert client.put('/api/compound/config',json={"source_id":client.source,"rules":[invalid]}).status_code==422
    assert client.get('/api/config',params={"source_id":client.source}).json()==before
    assert evaluate(client,sid,0).status_code==200
    save(client,rule(operator='OR'))
    assert evaluate(client,sid,1).status_code==409


def test_seeks_and_stale_indicators_and_missing_zone_safe(client):
    save(client); sid=session(client)
    assert not evaluate(client,sid,1,seek=True).json()['events']
    stale=evaluate(client,sid,5).json()
    assert not stale['compound_fresh'] and stale['compound_states'][0]['result'] is None
    assert client.put('/api/config',json={"source_id":client.source,"zones":[],"rules":[]}).status_code==200
    fresh=session(client); evaluate(client,fresh,0)
    result=evaluate(client,fresh,1).json()
    assert not result['events'] and result['compound_states'][0]['result'] is None


def test_original_and_compound_rules_run_together_and_restart(client):
    save(client)
    single={"id":"single","name":"Person dwells","zone_id":"crosswalk","classes":["person"],"condition":"dwell","dwell_seconds":0,"anchor":"center"}
    assert client.put('/api/config',json={"source_id":client.source,"zones":[ZONE],"rules":[single]}).status_code==200
    sid=session(client); evaluate(client,sid,0)
    assert {event['condition'] for event in evaluate(client,sid,1).json()['events']} == {'dwell','compound'}
    directory=client.app.state.core.state.data_dir
    with TestClient(create_app(directory)) as restarted:
        assert len(restarted.get('/api/incidents').json()['incidents'])==2
        assert restarted.get('/api/compound/config',params={"source_id":client.source}).json()['rules'][0]['id']=='pair'


def test_branding_mounts_and_guard(client,tmp_path):
    for prefix in ('','/app'):
        assert 'Occlusive Logic' in client.get(prefix+'/').text
        assert client.get(prefix+'/compound-static/compound.js').status_code==200
    assert client.get('/api/compound/config?source_id=missing').status_code==404
    assert evaluate(client,'missing',0).status_code==404
    with patch.dict('os.environ',{'ZONELOGIC_DATA_DIR':str(tmp_path)}):
        with pytest.raises(ValueError): create_app(tmp_path)
