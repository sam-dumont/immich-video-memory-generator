from immich_memories.config import Config
from immich_memories.preflight import CheckStatus
from immich_memories.preflight_homebase import check_homebase


def test_default_homebase_warns_with_the_exact_settings_to_change():
    result = check_homebase(Config())
    assert result.status is CheckStatus.WARNING
    assert "trips.homebase_latitude" in result.details
    assert "trips.homebase_longitude" in result.details


def test_a_real_location_on_the_equator_is_not_treated_as_missing():
    config = Config()
    config.trips.homebase_longitude = 30
    assert check_homebase(config).status is CheckStatus.OK
