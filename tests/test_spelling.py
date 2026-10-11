import sqlite3
from pathlib import Path

from indexer import spelling as sp

SCHEMA = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()


def test_keep_filters_llama_noise():
    words = {"diffrent", "copyright", "there", "valut", "phase"}
    assert sp.keep("diffrent", "different", words)
    assert not sp.keep("somwhere", "somewhere", words)      # not in the text
    assert not sp.keep("copyright", "copy right", words)    # split word
    assert not sp.keep("there", "their", words)             # real word
    assert not sp.keep("phase", "phases", words)            # plural
    assert not sp.keep("valut", "vlaue", words)             # correction not a word


def test_record_dedupes_same_text():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    text = sp.clean("please impliment the diffrent parser")
    ms = [{"wrong": "impliment", "correct": "implement"}, {"wrong": "diffrent", "correct": "different"}]
    assert sp.record(conn, text, ms, "s1", "claude", 1000) == 2
    assert sp.record(conn, text, ms, "s1", "claude", 1000) == 0
    assert conn.execute("SELECT count(*) FROM spelling_checked").fetchone()[0] == 1
