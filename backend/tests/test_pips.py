from pipeline.model import count_pips


def test_normal_cost() -> None:
    assert count_pips("{2}{W}{W}") == {"W": 2, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}


def test_multicolor_cost() -> None:
    pips = count_pips("{W}{U}{B}{R}{G}")
    assert pips == {"W": 1, "U": 1, "B": 1, "R": 1, "G": 1, "C": 0}


def test_hybrid_counts_both_colors() -> None:
    pips = count_pips("{W/U}{W/U}")
    assert pips["W"] == 2
    assert pips["U"] == 2


def test_two_generic_hybrid_counts_color_only() -> None:
    pips = count_pips("{2/W}")
    assert pips["W"] == 1
    assert sum(pips.values()) == 1


def test_phyrexian_counts_color() -> None:
    pips = count_pips("{W/P}{G/P}")
    assert pips["W"] == 1
    assert pips["G"] == 1
    assert sum(pips.values()) == 2


def test_x_and_generic_do_not_count() -> None:
    assert sum(count_pips("{X}{X}{3}").values()) == 0


def test_colorless_pip() -> None:
    pips = count_pips("{C}{C}{1}")
    assert pips["C"] == 2
    assert sum(pips.values()) == 2


def test_empty_cost() -> None:
    assert sum(count_pips("").values()) == 0
