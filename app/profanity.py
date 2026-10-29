"""Swear words become "xxxx"; 18+ text is recognised. The plugin asks Kino's AI first; this list is the floor."""
import re
import unicodedata

MASK = "xxxx"

# Compared after lower-casing and removing accents, as whole words.
SWEARS = {
    # Spanish (Colombia, Mexico, Spain, Rioplatense)
    "mierda", "mierdas", "puta", "putas", "puto", "putos", "putamadre", "hijueputa", "hijueputas", "hpta",
    "malparido", "malparida", "malparidos", "gonorrea",  # no "hp", "sapo", "perra", "zorra", "concha": everyday words too
 "pendejo", "pendeja", "pendejos", "pendejas", "pendejada",
    "marica", "maricon", "maricones", "carajo", "cabron", "cabrona", "cabrones", "verga", "vergas", "chingada",
    "chingado", "chingar", "chinga", "pinche", "culero", "culera", "culo", "coño", "joder", "jodido",
    "jodida", "gilipollas", "imbecil", "imbeciles", "idiota", "idiotas", "estupido", "estupida", "boludo", "boluda",
    "pelotudo", "pelotuda", "huevon", "huevona", "weon", "mamaguevo", "mamahuevo",
    "careverga", "caremonda", "monda", "culicagado", "lambon", "ojete", "mamon",
    # English
    "fuck", "fucking", "fucked", "fucker", "motherfucker", "shit", "shitty", "bullshit", "bitch", "bitches",
    "asshole", "assholes", "bastard", "dick", "dickhead", "cunt", "whore", "slut", "wanker", "twat", "prick",
    "damn", "crap", "retard", "retarded",
}

ADULT_WORDS = {"porn", "porno", "pornografia", "xxx", "hentai", "nsfw", "sexo", "sex", "onlyfans", "adultos",
               "desnuda", "desnudas", "desnudo", "nude", "nudes", "erotico", "erotica", "milf", "camgirl"}
ADULT_PHRASES = ("+18", "18+", "contenido adulto", "adult content", "solo adultos", "para adultos")

_WORD = re.compile(r"\w+", re.UNICODE)


def fold(text: str) -> str:
    """Lower case without accents; ñ stays ñ ("coño" is not "cono")."""
    decomposed = unicodedata.normalize("NFD", text.lower().replace("ñ", "\0"))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn").replace("\0", "ñ")


_SWEARS = {fold(w) for w in SWEARS}


def mask(text: str) -> str:
    return _WORD.sub(lambda m: MASK if fold(m.group(0)) in _SWEARS else m.group(0), text)


def looks_adult(text: str | None) -> bool:
    if not text:
        return False
    folded = fold(text)
    if any(p in folded for p in ADULT_PHRASES):
        return True
    return any(w in ADULT_WORDS for w in _WORD.findall(folded))
