import pandas as pd
import pytest

from tests.conftest import PROCESSED_DIR
from tools.attack_rating import AFFINITY_OFFSETS, DAMAGE_TYPES, calculate_ar
from tools.weapon_lookup import lookup_weapon, scaling_grade

STATS_10 = {"str": 10, "dex": 10, "int": 10, "fai": 10, "arc": 10}


@pytest.mark.parametrize(
    ("value", "grade"),
    [
        (0, "-"),
        (0.5, "-"),
        (1, "E"),
        (24.9, "E"),
        (25, "D"),
        (59, "D"),
        (60, "C"),
        (90, "B"),
        (139.9, "B"),
        (140, "A"),
        (175, "S"),
        (300, "S"),
    ],
)
def test_scaling_grade_thresholds(value: float, grade: str) -> None:
    assert scaling_grade(value) == grade


def test_longsword_basics(param_db_url: str) -> None:
    info = lookup_weapon("Longsword", database_url=param_db_url)
    assert info.weapon_id == 2000000
    assert info.affinity == "Standard"
    assert info.max_upgrade_level == 25
    assert info.base_damage == {"physical": 110}
    assert info.requirements["str"] == 10
    assert info.requirements["dex"] == 10
    assert info.available_affinities == list(AFFINITY_OFFSETS)


def test_affinity_name_and_argument_agree(param_db_url: str) -> None:
    a = lookup_weapon("Heavy Longsword", database_url=param_db_url)
    b = lookup_weapon("Longsword", "Heavy", database_url=param_db_url)
    assert a == b
    assert a.weapon_id == 2000100


def test_scaling_uses_upgrade_rate(param_db_url: str) -> None:
    plus0 = lookup_weapon("Longsword", upgrade_level=0, database_url=param_db_url)
    plus25 = lookup_weapon("Longsword", upgrade_level=25, database_url=param_db_url)
    reinforce = pd.read_csv(PROCESSED_DIR / "ReinforceParamWeapon.csv").set_index("ID")
    rate = reinforce.loc[25, "correctStrengthRate"]
    assert plus25.scaling["str"] == pytest.approx(round(plus0.scaling["str"] * rate, 2))


def test_upgrade_level_too_high(param_db_url: str) -> None:
    with pytest.raises(ValueError):
        lookup_weapon("Longsword", upgrade_level=26, database_url=param_db_url)


def test_somber_weapon_max_level_is_10(param_db_url: str) -> None:
    weapons = pd.read_csv(PROCESSED_DIR / "EquipParamWeapon.csv")
    somber = weapons[
        (weapons.reinforceTypeId == 2200)
        & weapons.Name.notna()
        & ~weapons.Name.astype(str).str.startswith("[")
    ]
    if somber.empty:
        pytest.skip("no weapon uses reinforceTypeId 2200")
    info = lookup_weapon(int(somber.iloc[0].ID), database_url=param_db_url)
    assert info.max_upgrade_level == 10


@pytest.mark.parametrize(
    ("weapon", "affinity", "level"),
    [
        ("Longsword", None, 0),
        ("Longsword", "Sacred", 10),
        ("Uchigatana", "Keen", 25),
        ("Claymore", "Heavy", 7),
        ("Dagger", "Magic", 3),
    ],
)
def test_base_damage_matches_ar_calculator(
    param_db_url: str, weapon: str, affinity: str | None, level: int
) -> None:
    info = lookup_weapon(weapon, affinity, level, database_url=param_db_url)
    ar = calculate_ar(weapon, level, affinity, STATS_10)
    for dmg in DAMAGE_TYPES:
        # Lookup leaves out zero damage types; AR reports them as 0.
        assert info.base_damage.get(dmg, 0) == ar.line(dmg).base, dmg


def test_unknown_weapon(param_db_url: str) -> None:
    with pytest.raises(KeyError):
        lookup_weapon("Not A Real Sword", database_url=param_db_url)
