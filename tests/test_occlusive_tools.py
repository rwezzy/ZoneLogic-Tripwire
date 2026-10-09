import json
from pathlib import Path
import sqlite3

import pytest
from scripts.copy_occlusive_workspace import copy_workspace
from scripts.prepare_occlusive_deployment import build
from scripts.verify_compound_vast import verify
from occlusive.app import create_app
from fastapi.testclient import TestClient


def test_copy_keeps_original_bytes_and_copies_media_and_refuses_overwrite(tmp_path):
    source=tmp_path/'original'; app=create_app(source)
    store=app.state.core.state.store
    media=source/'media'/'sample.mp4'; media.write_bytes(b'saved video')
    store.save_source({'id':'test','kind':'uploaded','media_path':str(media)})
    store.save_configuration({'source_id':'test','zones':[],'rules':[]})
    before=(source/'zonelogic.sqlite3').read_bytes()
    destination=copy_workspace(source,source/'occlusive')
    assert (source/'zonelogic.sqlite3').read_bytes()==before
    with sqlite3.connect(destination/'zonelogic.sqlite3') as db:
        saved=json.loads(db.execute('SELECT payload FROM sources WHERE id=?',('test',)).fetchone()[0])
    assert Path(saved['media_path']).parent==destination/'media'
    assert Path(saved['media_path']).read_bytes()==b'saved video'
    with pytest.raises(ValueError):copy_workspace(source,destination)


def test_package_uses_new_launcher_and_safe_separate_data_location():
    manifest,summary=build(Path(__file__).resolve().parents[1])
    code=manifest['items'][0]['data']
    assert any('Launch the separate Occlusive Logic' in contents for contents in code.values())
    assert any('class CompoundEngine' in contents for contents in code.values())
    assert summary['configmap_bytes']<900*1024
    container=manifest['items'][1]['spec']['template']['spec']['containers'][0]
    env={item['name']:item['value'] for item in container['env']}
    assert env['OCCLUSIVE_DATA_DIR'] != env['ZONELOGIC_DATA_DIR']
    assert manifest['items'][3]['spec']['rules'][0]['http']['paths'][0]['path']=='/app(/|$)(.*)'


def test_verifier_does_not_claim_real_vast_for_absent_database_or_simulations(tmp_path):
    assert verify(tmp_path)['verified_positive_and_negative'] is False
    create_app(tmp_path)
    result=verify(tmp_path)
    assert result['verified_positive_and_negative'] is False and result['sources']==[]
