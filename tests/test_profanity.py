from app.profanity import looks_adult, mask


def test_mask_case_accents_punctuation():
    assert mask("Qué MIERDA!") == "Qué xxxx!"
    assert mask("pendéjo y pendejo") == "xxxx y xxxx"
    assert mask("Shit happens") == "xxxx happens"
    assert mask("Hijueputa, qué partido") == "xxxx, qué partido"


def test_mask_leaves_innocent_words():
    assert mask("qué coño") == "qué xxxx"
    for text in ["Escupir no es bonito", "a classic film", "Assassin's Creed", "computadora", "😀 hola 🎬",
                 "un cono de helado", "el sapo y la perra", "motor de 300 hp"]:
        assert mask(text) == text


def test_looks_adult():
    assert looks_adult("Videos porno gratis")
    assert looks_adult("XXX hot")
    assert looks_adult("Contenido +18")
    assert not looks_adult("Stranger Things")
