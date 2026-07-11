import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "verify_oil_decode",
    Path(__file__).parent.parent / "tools" / "verify_oil_decode.py",
)
tool = importlib.util.module_from_spec(spec)
sys.modules["verify_oil_decode"] = tool
spec.loader.exec_module(tool)


def warmup_series(rate_c_per_s: float, start_c: float, t_end: float, step: float = 2.0):
    """Linear warm-up capped at start + realistic plateau."""
    out = []
    t = 0.0
    while t <= t_end:
        out.append((t, min(start_c + rate_c_per_s * t, 95.0)))
        t += step
    return out


def test_correct_oil_decode_passes_all_checks():
    # Coolant warms ~3x faster than oil, both from 15 C; 25-minute run.
    coolant = warmup_series(0.09, 15.0, 1500)   # 80 C at ~720 s
    oil = warmup_series(0.05, 15.0, 1500)       # 80 C at ~1300 s
    checks = tool.evaluate(coolant, oil)
    assert all(c.passed for c in checks), [f"{c.name}: {c.detail}" for c in checks]


def test_wrong_signal_fails():
    coolant = warmup_series(0.09, 15.0, 1500)
    # A counter-like signal: sawtooth 0..255, scaled — utterly unlike oil temp.
    sawtooth = [(t, (int(t) * 7 % 256) * 0.75 - 48) for t in range(0, 1500, 2)]
    checks = tool.evaluate(coolant, sawtooth)
    assert not all(c.passed for c in checks)


def test_coolant_itself_fails_lag_check():
    # If the candidate IS coolant (a likely false positive in correlation
    # analysis), it reaches 80 C at the same time — LAG must catch it.
    coolant = warmup_series(0.09, 15.0, 1500)
    checks = tool.evaluate(coolant, list(coolant))
    lag = next(c for c in checks if c.name == "LAG")
    assert not lag.passed


def test_too_little_data_is_rejected():
    checks = tool.evaluate([(0, 20.0)], [(0, 20.0)])
    assert checks[0].name == "DATA" and not checks[0].passed


def test_pearson_basics():
    assert abs(tool.pearson([1, 2, 3, 4], [2, 4, 6, 8]) - 1.0) < 1e-9
    assert tool.pearson([1, 2, 3, 4], [8, 6, 4, 2]) < -0.99
    assert tool.pearson([1, 1, 1], [1, 2, 3]) is None  # zero variance
