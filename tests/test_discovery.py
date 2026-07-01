from pathlib import Path

from engine.discovery import load_algorithm_specs, discover


def _write_tree(tmp_path: Path) -> Path:
    root = tmp_path / "algorithms"
    (root / "Riñihue").mkdir(parents=True)
    (root / "Riñihue" / "FR_SH2.py").write_text(
        "from engine.algorithms import ThresholdAlgorithm\n"
        "ALGORITHM = ThresholdAlgorithm(company='Riñihue', device_key='FR SH2',\n"
        "    power_column='phase_a_active_power', threshold_w=1100, smoothing_minutes=10)\n"
    )
    (root / "Repairco").mkdir(parents=True)
    (root / "Repairco" / "R1.py").write_text(
        "from engine.algorithms import KMeansAlgorithm\n"
        "ALGORITHM = KMeansAlgorithm(company='Repairco', device_key='R1',\n"
        "    power_column='total_active_power', n_clusters=3, smoothing_minutes=5)\n"
    )
    (root / "Repairco" / "_notes.py").write_text("X = 1\n")  # ignored (underscore-prefixed)
    return root


def test_load_algorithm_specs_finds_both(tmp_path):
    specs = load_algorithm_specs(_write_tree(tmp_path))
    keys = sorted((s.company, s.device_key) for s in specs)
    assert keys == [("Repairco", "R1"), ("Riñihue", "FR SH2")]


def test_discover_resolves_device_ids(tmp_path):
    root = _write_tree(tmp_path)
    mapping = {("Riñihue", "FR SH2"): 52, ("Repairco", "R1"): None}
    out = discover(root, resolver=lambda c, d: mapping[(c, d)])
    resolved = {(d.algorithm.company, d.algorithm.device_key): d.device_id for d in out}
    assert resolved == {("Riñihue", "FR SH2"): 52, ("Repairco", "R1"): None}


def test_load_algorithm_specs_skips_broken_file(tmp_path):
    """A file that raises on import must be skipped; valid files still load."""
    root = tmp_path / "algorithms"
    (root / "GoodCo").mkdir(parents=True)
    (root / "GoodCo" / "G1.py").write_text(
        "from engine.algorithms import ThresholdAlgorithm\n"
        "ALGORITHM = ThresholdAlgorithm(company='GoodCo', device_key='G1',\n"
        "    power_column='phase_a_active_power', threshold_w=500, smoothing_minutes=5)\n"
    )
    (root / "BrokenCo").mkdir(parents=True)
    (root / "BrokenCo" / "broken.py").write_text('raise RuntimeError("boom from broken algo")\n')

    specs = load_algorithm_specs(root)
    assert len(specs) == 1
    assert specs[0].company == "GoodCo"
    assert specs[0].device_key == "G1"
