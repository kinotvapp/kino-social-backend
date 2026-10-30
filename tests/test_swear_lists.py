"""The server's list and the plugin's src/swear.js COMMON stay identical (both mask on their side; a hit is a strike)."""
import os
import re

import pytest

from app import profanity

HERE = os.path.dirname(__file__)
CANDIDATES = [
    os.environ.get("KINO_SOCIAL_PLUGIN", ""),
    r"C:\dev\kino-app\plugins\kino-social",
    r"C:\dev\kino-app\.claude\worktrees\release-0977\plugins\kino-social",
    os.path.expanduser("~/kino-app/plugins/kino-social"),
]


def _plugin_words() -> list[str]:
    for base in CANDIDATES:
        path = os.path.join(base, "src", "swear.js") if base else ""
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                source = f.read()
            block = source.split("const COMMON = [", 1)[1].split("];", 1)[0]
            block = re.sub(r"//[^\n]*", "", block)
            return re.findall(r'"([^"]+)"', block)
    pytest.skip("the plugin's src/swear.js is not on this machine (set KINO_SOCIAL_PLUGIN to its folder)")


def _server_words() -> list[str]:
    with open(os.path.join(HERE, "..", "app", "profanity.py"), encoding="utf-8") as f:
        source = f.read()
    block = source.split("SWEARS = {", 1)[1].split("\n}", 1)[0]
    block = re.sub(r"#[^\n]*", "", block)
    return re.findall(r'"([^"]+)"', block)


def test_server_and_plugin_lists_are_identical():
    server, plugin = _server_words(), _plugin_words()
    assert set(server) == profanity.SWEARS
    assert len(server) == len(set(server)), "a word repeats in app/profanity.py"
    assert len(plugin) == len(set(plugin)), "a word repeats in src/swear.js"
    assert sorted(server) == sorted(plugin), {"only server": set(server) - set(plugin), "only plugin": set(plugin) - set(server)}


def test_words_are_folded():
    for w in profanity.SWEARS:
        assert w == profanity.fold(w) and re.fullmatch(r"\w+", w), w
        assert w != profanity.MASK


def test_everyday_words_stay_out():
    for w in ["hp", "sapo", "perra", "zorra", "concha", "coger", "paja", "polla", "chimba", "berraco", "bloody", "ass",
              "negro", "gay", "indio", "marico", "pajero", "capullo", "bicho", "pija", "forro", "tortillera", "pito",
              "huevo", "huevos", "cola", "mamar", "chucha", "pico", "cabro", "conchudo", "pinches", "retrasado"]:
        assert w not in profanity.SWEARS, w
    for text in ["Vamos a coger el bus", "una paja de trigo", "qué chimba de película", "un negro muy elegante",
                 "el vuelo va retrasado", "Pajero 4x4", "bloody mary", "a gay old time",
                 "the indio game", "huevos fritos", "kick ass movie"]:
        assert not profanity.has_swear(text), text
