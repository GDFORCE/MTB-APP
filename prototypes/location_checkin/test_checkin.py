import pytest

from checkin import Fix, Outcome, Thresholds, contains, evaluate, haversine_m

# ~200m x 200m square campus
CAMPUS = [(17.4000, 78.4000), (17.4000, 78.4019), (17.4018, 78.4019), (17.4018, 78.4000)]
DEPT = (17.4009, 78.40095)
T = Thresholds()


def at(dlat_m: float = 0.0, acc=10.0, age=5.0) -> Fix:
    return Fix(DEPT[0] + dlat_m / 111_320, DEPT[1], acc, age)


def test_haversine_one_degree_lat():
    assert haversine_m((0, 0), (1, 0)) == pytest.approx(111_195, rel=1e-3)


def test_contains():
    assert contains(CAMPUS, DEPT)
    assert not contains(CAMPUS, (17.3990, 78.4009))


@pytest.mark.parametrize("fix,expected", [
    (at(age=46), Outcome.REJECTED_STALE),
    (Fix(DEPT[0], DEPT[1], 10, None), Outcome.REJECTED_STALE),
    (at(acc=None), Outcome.QR_FALLBACK),
    (at(acc=150), Outcome.QR_FALLBACK),
    (Fix(17.3980, 78.4009, 10, 5), Outcome.DENIED_OUT_OF_BOUNDS),
    (at(10, acc=8), Outcome.VERIFIED),
    (at(35, acc=8), Outcome.PARTIAL_MANUAL_BACKUP),       # good signal, too far
    (at(35, acc=40), Outcome.VERIFIED_LENIENT),           # radius expands to 40m
    (at(45, acc=40), Outcome.PARTIAL_MANUAL_BACKUP),      # beyond the 40m expansion
    (at(48, acc=90), Outcome.VERIFIED_LENIENT),           # expansion capped at 50m
    (at(55, acc=90), Outcome.PARTIAL_MANUAL_BACKUP),
])
def test_decision_matrix(fix, expected):
    assert evaluate(fix, CAMPUS, DEPT, T).outcome is expected


def test_campus_only_without_target():
    assert evaluate(at(), CAMPUS, None, T).outcome is Outcome.CAMPUS_ONLY
