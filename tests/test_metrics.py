import numpy as np
from src.metrics import (expectancy_R, win_rate, breakeven_win_rate,
                         profit_factor, kelly_fraction, to_R)


def test_breakeven_identity():
    # E = pB - (1-p) == 0 at p* = 1/(1+B)
    for B in [0.5, 1.0, 2.0, 3.0]:
        p = breakeven_win_rate(B)
        assert abs(p * B - (1 - p)) < 1e-12


def test_parable_ranking():
    rng = np.random.default_rng(0)
    t1 = np.where(rng.random(500) < 0.8, 0.3, -3.0)  # high win, neg E
    t2 = np.where(rng.random(500) < 0.5, 1.8, -1.0)  # balanced, pos E
    assert expectancy_R(t1) < 0 < expectancy_R(t2)
    assert win_rate(t1) > win_rate(t2)
    assert profit_factor(t2) > 1 > profit_factor(t1)
    assert kelly_fraction(0.8, 0.1) < 0  # tiny B => no edge


def test_R_accounting():
    R = to_R(np.array([200.0, -100.0]), np.array([100.0, 100.0]), cost_R=0.05)
    assert abs(R[0] - 1.95) < 1e-9 and abs(R[1] + 1.05) < 1e-9
