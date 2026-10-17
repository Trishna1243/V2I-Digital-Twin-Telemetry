import math

import pytest

from src.channel_model import (
    ChannelParameters,
    calculate_link_budget,
    log_distance_path_loss_db,
    nearest_rsu,
    receiver_noise_power_dbm,
    shannon_capacity_bps,
    snr_linear_from_db,
)


def test_free_space_reference_path_loss_at_5_9_ghz_and_one_metre():
    parameters = ChannelParameters()
    assert parameters.reference_path_loss_db == pytest.approx(47.86, abs=0.02)
    assert log_distance_path_loss_db(1.0, parameters.reference_path_loss_db) == pytest.approx(
        parameters.reference_path_loss_db
    )


def test_log_distance_exponents_supported():
    reference = ChannelParameters().reference_path_loss_db
    assert log_distance_path_loss_db(10, reference, path_loss_exponent=3.0) == pytest.approx(reference + 30)
    assert log_distance_path_loss_db(10, reference, path_loss_exponent=3.5) == pytest.approx(reference + 35)


def test_receiver_noise_power_for_10_mhz_and_9_db_noise_figure():
    assert receiver_noise_power_dbm(10_000_000, 9) == pytest.approx(-95.0)
    assert ChannelParameters().bandwidth_hz == 10_000_000


def test_snr_db_to_linear_conversion():
    assert snr_linear_from_db(10) == pytest.approx(10)
    assert snr_linear_from_db(0) == pytest.approx(1)


def test_shannon_capacity():
    assert shannon_capacity_bps(10_000_000, 1) == pytest.approx(10_000_000)


def test_nearest_rsu_selection():
    assert nearest_rsu(420, {"rsu1": 250, "rsu2": 500, "rsu3": 750}) == ("rsu2", 80)


@pytest.mark.parametrize("distance", [0, -1, float("inf"), float("nan")])
def test_invalid_distance_rejected(distance):
    with pytest.raises(ValueError):
        log_distance_path_loss_db(distance, 47.86)


def test_distance_below_reference_rejected():
    with pytest.raises(ValueError, match="at least"):
        log_distance_path_loss_db(0.5, 47.86, reference_distance_m=1.0)


@pytest.mark.parametrize("kwargs", [
    {"carrier_frequency_ghz": 0},
    {"bandwidth_mhz": -1},
    {"path_loss_exponent": 0},
    {"noise_density_dbm_per_hz": float("nan")},
    {"noise_figure_db": -1},
    {"payload_bytes": 0},
])
def test_invalid_channel_parameters_rejected(kwargs):
    with pytest.raises(ValueError):
        ChannelParameters(**kwargs)


def test_invalid_noise_snr_capacity_and_rsu_inputs_rejected():
    with pytest.raises(ValueError):
        receiver_noise_power_dbm(0, 9)
    with pytest.raises(ValueError):
        snr_linear_from_db(math.inf)
    with pytest.raises(ValueError):
        shannon_capacity_bps(10, -1)
    with pytest.raises(ValueError):
        nearest_rsu(1, {})
    with pytest.raises(ValueError):
        nearest_rsu(1, {"rsu1": math.nan})


def test_link_budget_contains_power_snr_and_capacity():
    budget = calculate_link_budget(100, ChannelParameters())
    assert budget.received_power_dbm < 20
    assert budget.noise_power_dbm == pytest.approx(-95.0)
    assert budget.snr_linear == pytest.approx(10 ** (budget.snr_db / 10))
    assert budget.shannon_capacity_bps > 0
    assert budget.payload_transmission_time_seconds == pytest.approx(
        (32 * 8) / budget.shannon_capacity_bps
    )
