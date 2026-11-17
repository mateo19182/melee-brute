"""Generated input tapes and the checks built on them. Needs the built library and extracted game data."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("MSL_DATA_DIR", "data"))
pytestmark = pytest.mark.skipif(not DATA.is_dir(), reason="game data not extracted (set MSL_DATA_DIR)")


def test_a_recipe_always_gives_the_same_tape_and_a_replay_the_same_frames():
    from tools.validation import tapes
    recipe = tapes.Recipe("YOSHIS_STORY", ("KIRBY", "PEACH"), 5, frames=400, percents=(30, 60))
    tape, digests = tapes.record(recipe)
    again, same = tapes.record(recipe)
    assert tape.shape == (400, 2, 7) and np.array_equal(tape, again) and np.array_equal(digests, same)
    replayed, kept = tapes.replay(recipe, tape, keep=399)
    assert np.array_equal(replayed, digests) and tapes.digest(kept) == digests[-1]
    changed = tape.copy()
    changed[200:, 0] = (tapes.B, 0, -80, 0, 0, 0, 0)
    assert not np.array_equal(tapes.replay(recipe, changed)[0], digests)
    assert len(np.unique(digests)) > 300  # the fighters do things


def test_take_back_is_exact_on_a_team_match_with_followers_and_items():
    from tools.validation import tapes
    recipe = tapes.Recipe("FOUNTAIN_OF_DREAMS", ("ICE_CLIMBERS", "PEACH", "LINK", "KIRBY"), 3, frames=600,
                          teams=(0, 0, 1, 1))
    tape, _ = tapes.record(recipe)
    checks, failures = tapes.takeback(recipe, tape, every=37)
    assert checks >= 10 and failures == []


def test_the_corpus_names_matches_the_simulator_can_play():
    import melee_sim as msl
    from tools.validation import differential, tapes
    rows = json.loads(differential.CORPUS.read_text())["tapes"]
    names = set()
    for row in rows:
        recipe = tapes.Recipe.from_json(row)
        assert recipe.stage in msl.Stage.__members__ and all(f in msl.Character.__members__ for f in recipe.fighters)
        assert recipe.to_json() == row and recipe.name not in names
        names.add(recipe.name)
    assert {f for row in rows for f in row["fighters"]} == set(differential.FIGHTERS)
