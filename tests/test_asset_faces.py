"""Where Immich found each face comes from the faces endpoint, not from the asset."""

import pytest

from immich_memories.analysis.subject_framing import FaceBox, face_boxes_of
from immich_memories.api.asset_service import AssetService

# The shape Immich answers /api/faces?id= with, trimmed to the fields read here.
WIRE = [
    {
        "id": "face-1",
        "person": {"id": "p1", "name": "Named"},
        "boundingBoxX1": 51,
        "boundingBoxY1": 415,
        "boundingBoxX2": 161,
        "boundingBoxY2": 531,
        "imageWidth": 2161,
        "imageHeight": 1440,
    },
    {
        "id": "face-2",
        "person": None,
        "boundingBoxX1": 1013,
        "boundingBoxY1": 433,
        "boundingBoxX2": 1155,
        "boundingBoxY2": 621,
        "imageWidth": 2161,
        "imageHeight": 1440,
    },
]


@pytest.mark.asyncio
async def test_the_faces_of_an_asset_are_asked_for_by_id_and_come_back_with_their_boxes():
    asked = {}

    async def request(method, endpoint, **kwargs):
        asked.update(method=method, endpoint=endpoint, params=kwargs.get("params"))
        return WIRE

    faces = await AssetService(request, "https://immich.test", lambda: None).get_asset_faces("a1")

    assert asked == {"method": "GET", "endpoint": "/faces", "params": {"id": "a1"}}
    assert face_boxes_of(faces) == (
        FaceBox(
            x1=pytest.approx(0.0236, abs=1e-4),
            y1=pytest.approx(0.2882, abs=1e-4),
            x2=pytest.approx(0.0745, abs=1e-4),
            y2=pytest.approx(0.3688, abs=1e-4),
            named=True,
        ),
        FaceBox(
            x1=pytest.approx(0.4688, abs=1e-4),
            y1=pytest.approx(0.3007, abs=1e-4),
            x2=pytest.approx(0.5345, abs=1e-4),
            y2=pytest.approx(0.4313, abs=1e-4),
            named=False,
        ),
    )


@pytest.mark.asyncio
async def test_a_server_that_answers_something_other_than_a_list_reports_no_faces():
    async def request(_method, _endpoint, **_kwargs):
        return {"message": "unexpected"}

    service = AssetService(request, "https://immich.test", lambda: None)

    assert await service.get_asset_faces("a1") == []
