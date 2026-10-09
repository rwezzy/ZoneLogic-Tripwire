"""The production mount must forward cleanup and preserve integration failures."""
from fastapi.testclient import TestClient

from zonelogic import main
from zonelogic.vast import VastError


def test_wrapper_forwards_client_cleanup():
    class Client:
        closed = 0

        async def close(self):
            self.closed += 1

    client = Client()
    original = main.inner_app.state.vast
    main.inner_app.state.vast = client
    try:
        with TestClient(main.app) as api:
            assert api.get("/health").status_code == 200
            assert api.get("/app/health").status_code == 200
        assert client.closed == 1
    finally:
        main.inner_app.state.vast = original


def test_vast_failure_preserves_actionable_status(tmp_path):
    class Client:
        async def list_videos(self, **kwargs):
            raise VastError("Server configuration is missing", 503)

        async def import_clip(self, source):
            raise VastError("Choose an S3 segment", 422)

    app = main.create_app(tmp_path, vast_client=Client())
    with TestClient(app) as api:
        assert api.get("/api/vast/videos").status_code == 503
        assert api.post("/api/vast/import", json={"source": "https://example.com"}).status_code == 422
