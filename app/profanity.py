"""Swear words become "xxxx"; 18+ text is recognised. The plugin asks Kino's AI first; this list is the floor."""
import re
import unicodedata

MASK = "xxxx"

# Compared after lower-casing and removing accents (ñ kept), as whole words. Kept in step with the plugin's src/swear.js
# COMMON (tests/test_swear_lists.py). A hit is a strike now (moderation.py), so a word that is an everyday one in some
# country stays out: "hp", "sapo", "perra", "zorra", "concha", "coger", "paja", "polla", "chimba", "berraco", "bloody",
# "ass", "negro", "gay", "indio", "marico", "pajero", "capullo", "bicho", "pija", "forro", "tortillera" and the like.
SWEARS = {
    # Spanish, everywhere
    "mierda", "mierdas", "puta", "putas", "puto", "putos", "putamadre", "putita", "putitas", "putona", "putonas",
    "putazo", "putazos", "putero", "ramera", "rameras", "hijueputa", "hijueputas", "hijoputa", "hijoputas",
    "hijaputa", "hijodeputa", "hijosdeputa", "jueputa", "jueputas", "ijueputa", "hpta", "hptas", "hpt", "hdp", "hdps",
    "hdlgp", "malparido", "malparida", "malparidos", "malparidas", "malpario", "malnacido", "malnacida", "malnacidos",
    "pendejo", "pendeja", "pendejos", "pendejas", "pendejada", "pendejadas", "carajo", "cabron", "cabrona",
    "cabrones", "cabronas", "cabronazo", "kbron", "kabron", "verga", "vergas", "vergazo", "vergazos", "culo", "culos",
    "culero", "culera", "culeros", "culeras", "coño", "imbecil", "imbeciles", "idiota", "idiotas", "estupido",
    "estupida", "estupidos", "estupidas", "comemierda", "comemierdas", "caremierda", "careculo", "lameculos", "ojete",
    "ojetes", 
    # Colombia
    "gonorrea", "gonorreas", "careverga", "caremonda", "carechimba", "monda", "culicagado", "lambon", "mamaguevo",
    "mamaguevos", "mamahuevo", "mamahuevos",
    # Mexico
    "chingada", "chingado", "chingadas", "chingados", "chingar", "chinga", "chingate", "chingadera", "chingaderas",
    "pinche", "joto", "jotos",
    # Spain
    "joder", "jodido", "jodida", "jodidos", "jodidas", "jodete", "gilipollas", "gilipollez", "gilipolleces", "follar",
    "cojones", "chupapollas", "soplapollas", "comepollas", "furcia", "furcias", "bollera", "bolleras", "travelo",
    "travelos", "subnormal", "subnormales",
    # Argentina, Uruguay
    "boludo", "boluda", "boludos", "boludas", "pelotudo", "pelotuda", "pelotudos", "pelotudas", "orto", "sorete",
    "soretes", "garca", "chupapija", "trolo", "trolos", "lpqtp", "lpmqtp",
    # Chile
    "huevon", "huevona", "huevones", "huevonas", "weon", "weona", "weones", "weonas", "aweonao", "aweonado", "culiao",
    "culiado", "culiada", "culiaos", "conchetumare", "conchetumadre", "maraco", "maracos", "ctm", "ctmr", "csm",
    "csmr",
    # Peru
    "cojudo", "cojuda", "cojudos", "cojudas", "cojudez", "guevon", "guevona", "guevones", "webon", "webona",
    "webones", "conchatumadre", "conchatumare", "conchasumadre", "conchasumare", "conchesumadre", "ptm", "ptmr",
    # Venezuela
    "coñoetumadre", "coñodetumadre", "coñazo", "coñazos",
    # Central America and the Caribbean
    "cerote", "cerotes", "mamaverga", "mamavergas", "chupavergas", "mamabicho", "pinga", "singao", "singada",
    "singar", "puñeta", "puñetas",
    # Spanish slurs (racist, homophobic, ableist), every country
    "sudaca", "sudacas", "moraco", "moracos", "negrata", "negratas", "veneco", "veneca", "venecos", "marica",
    "maricas", "maricon", "maricones", "maricona", "mariconas", "mariconazo", "marikon", "mariposon", "marimacho",
    "marimacha", "sidoso", "sidosa", "mongolo", "mongola", "mongolos", "mongolico", "mongolica", "tarado", "tarada",
    "tarados",
    # English
    "fuck", "fucks", "fucking", "fuckin", "fucken", "fucked", "fucker", "fuckers", "fuckface", "fuckhead", "fuckoff",
    "fuckwit", "fuk", "fuking", "fukin", "fck", "fcking", "fking", "fvck", "phuck", "motherfucker", "motherfuckers",
    "motherfucking", "motherfucka", "mofo", "wtf", "stfu", "gtfo", "shit", "shits", "shitty", "shitting", "shithead",
    "shitheads", "shithole", "shitface", "shite", "sh1t", "bullshit", "dipshit", "horseshit", "bitch", "bitches",
    "bitchy", "bitching", "biatch", "sonofabitch", "asshole", "assholes", "arsehole", "arseholes", "asshat",
    "asswipe", "dumbass", "dumbasses", "bastard", "bastards", "dickhead", "dickheads", "cocksucker",
    "cocksuckers", "cunt", "cunts", "twat", "twats", "wanker", "wankers", "wank", "tosser", "tossers", "bellend",
    "knobhead", "bollocks", "jackoff", "jerkoff", "prick", "douchebag", "douchebags", "whore", "whores", "slut",
    "sluts", "slutty", "skank", "skanks", "dammit", "goddamn", "goddamnit",
    # English slurs (racist, homophobic, transphobic, ableist)
    "nigger", "niggers", "nigga", "niggas", "sandnigger", "gook", "gooks", "spic", "spics", "wetback", "wetbacks",
    "beaner", "beaners", "kike", "kikes", "raghead", "ragheads", "towelhead", "towelheads", "paki", "pakis",
    "chingchong", "faggot", "faggots", "tranny", "trannies", "shemale", "retard", "retards", "retarded", "spaz",
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


def has_swear(text: str) -> bool:
    """Whether mask() would hide a word of this list."""
    return any(fold(w) in _SWEARS for w in _WORD.findall(text))


def has_mask(text: str) -> bool:
    """Whether the text already holds the mask word as a whole word (the device's list or Kino's AI put it there)."""
    return any(fold(w) == MASK for w in _WORD.findall(text))


def looks_adult(text: str | None) -> bool:
    if not text:
        return False
    folded = fold(text)
    if any(p in folded for p in ADULT_PHRASES):
        return True
    return any(w in ADULT_WORDS for w in _WORD.findall(folded))
