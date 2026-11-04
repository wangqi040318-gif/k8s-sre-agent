"""场景目录只有一份，CLI 的 config 再导出必须和它对齐。"""

from __future__ import annotations

from src import config
from src.scenarios import (
    SCENARIO_ALIASES,
    SCENARIO_CHOICES,
    SCENARIO_LABELS,
    normalize_scenario,
)
from src.tools import mock_data

EXPECTED = [
    "imagepull",
    "oom",
    "crashloop",
    "pending",
    "configmount",
    "probe",
    "connectivity",
]


def test_scenario_catalog_lists_oncall_faults_in_stable_order():
    assert list(SCENARIO_CHOICES) == EXPECTED
    assert list(config.SCENARIO_CHOICES) == EXPECTED
    assert config.SCENARIO_CHOICES is SCENARIO_CHOICES
    assert list(SCENARIO_LABELS) == EXPECTED
    assert config.SCENARIO_LABELS is SCENARIO_LABELS
    assert all(SCENARIO_LABELS[scenario_id] for scenario_id in EXPECTED)
    assert set(SCENARIO_ALIASES.values()) == set(EXPECTED)
    assert set(mock_data.SCENARIO_PODS) == set(EXPECTED)


def test_normalize_scenario_maps_aliases_onto_catalog_ids():
    assert config.normalize_scenario is normalize_scenario
    assert normalize_scenario("ImagePullBackOff") == "imagepull"
    assert normalize_scenario("failed-scheduling") == "pending"
    assert normalize_scenario("create_container_config_error") == "configmount"
    assert normalize_scenario("readiness") == "probe"
    assert normalize_scenario("service-endpoints") == "connectivity"
    assert normalize_scenario("not-a-scenario") is None
