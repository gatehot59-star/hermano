"""Every text a child or an adult reads, in English and Spanish.

English is the default so that the hackathon judges can follow the demo. Spanish is the
language of the schools Hermano is built for. The companion's name is never translated:
the child picks hermano or hermana.
"""
from __future__ import annotations

from typing import Dict

LANGS = ("en", "es")
DEFAULT_LANG = "en"

TEXT: Dict[str, Dict[str, str]] = {
    "en": {
        "greeting.hermano": "I'm your hermano. Today I'll walk with you through every step of the day.",
        "greeting.hermana": "I'm your hermana. Today I'll walk with you through every step of the day.",
        "first_then": "First: {activity}. Then: {then}.",
        "then_home": "home",
        "warning": "In 5 minutes {prev} ends. Next comes {next}.",
        "reading": "For {topic}: {book} can be a way in.",
        "reward.book": "read a little of {x}",
        "reward.artist": "listen to a song by {x}",
        "reward.tv_show": "draw something from {x}",
        "reward.video_game": "tell your favorite part of {x}",
        "reward.movie": "pick a scene from {x} and tell it",
        "reward.default": "choose a game with your teacher",
        "calm.breathe": "breathe and count to 5",
        "calm.corner": "go to the quiet corner for a bit",
        "calm.ball": "squeeze the stress ball",
        "calm.music": "listen to {x} with headphones",
        "face.bien": "good",
        "face.mas_o_menos": "so-so",
        "face.dificil": "hard",
        "domain.book": "book",
        "domain.artist": "music",
        "domain.tv_show": "TV",
        "domain.video_game": "game",
        "domain.movie": "movie",
        "redacted": "[health detail removed]",
        "teacher.title": "Daily report for {name}",
        "teacher.worked": "What worked: {items}.",
        "teacher.with": "{activity}, with {item}",
        "teacher.hard": "What was hard: {items}.",
        "teacher.helped": "{activity} (helped: {support})",
        "teacher.tomorrow": "For tomorrow: prepare {activity} with a visual warning and a new motivator.",
        "teacher.readings": "Suggested readings: {items}",
        "pending.teacher": "New Qloo ideas waiting for your OK: {items}.",
        "principal.title": "Daily summary for {name}",
        "principal.children": "Children supported: {n}",
        "principal.moments": "Moments logged: {total} ({good}, {so}, {hard})",
        "principal.supports": "Most used supports: {items}",
        "principal.watch": "To watch tomorrow: {names}",
        "principal.nobody": "nobody with two or more hard moments",
        "family.hello": "Hi, {alias}'s family.",
        "family.count": "Today {alias} said {good} of {n} moments went well.",
        "family.best": "What went best: {activity}.",
        "family.hardest": "What was hardest: {activity}.",
        "family.helped": " At school, this helped: {support}.",
        "family.talk": "To talk about at home: ask about {item}.",
        "pending.family": "New ideas from Qloo for {alias}, waiting for your OK: {items}.",
        "suggested.teacher": "Hermano suggests these new ideas from Qloo, waiting for your OK: {items}.",
        "suggested.family": "Hermano suggests these new ideas from Qloo for {alias}, waiting for your OK: {items}.",
        "cli.planner.rules": "Planner: rules (no language model)",
        "cli.planner.llm": "Planner: {model}, checked by Hermano's rules",
        "cli.planner.fallback": "Planner: rules ({reason})",
        "specialist.title": "Observation log for {name}",
        "specialist.child": "{alias} \u00b7 {grade} \u00b7 agreed supports: {supports}",
        "specialist.header": "time | activity | how it went (self-report) | support used | motivator",
        "cli.day": "=== Day {day} \u00b7 Qloo in {mode} mode \u00b7 {school} ===",
        "cli.warning": "heads-up",
        "cli.degraded": "Qloo is not answering for {alias} ({reason}): generic rewards today.",
        "cli.refused": "{alias}: {n} interest(s) not sent to Qloo (they looked like a name, a health detail or contact data).",
        "cli.approved": "(simulated) {adult} approved: {item}",
        "cli.privacy": "Qloo in {mode} mode: {n} requests",
    },
    "es": {
        "greeting.hermano": "Soy tu hermano. Hoy te acompa\u00f1o en cada paso del d\u00eda.",
        "greeting.hermana": "Soy tu hermana. Hoy te acompa\u00f1o en cada paso del d\u00eda.",
        "first_then": "Primero: {activity}. Despu\u00e9s: {then}.",
        "then_home": "a casa",
        "warning": "En 5 minutos termina {prev}. Despu\u00e9s viene {next}.",
        "reading": "Para {topic}: {book} puede ser la puerta de entrada.",
        "reward.book": "leer un ratito de {x}",
        "reward.artist": "escuchar un tema de {x}",
        "reward.tv_show": "dibujar algo de {x}",
        "reward.video_game": "contar tu parte favorita de {x}",
        "reward.movie": "elegir una escena de {x} y contarla",
        "reward.default": "elegir un juego con tu docente",
        "calm.breathe": "respirar contando hasta 5",
        "calm.corner": "ir un ratito al rinc\u00f3n tranquilo",
        "calm.ball": "apretar la pelotita",
        "calm.music": "escuchar {x} con auriculares",
        "face.bien": "bien",
        "face.mas_o_menos": "m\u00e1s o menos",
        "face.dificil": "dif\u00edcil",
        "domain.book": "libro",
        "domain.artist": "m\u00fasica",
        "domain.tv_show": "serie",
        "domain.video_game": "juego",
        "domain.movie": "pel\u00edcula",
        "redacted": "[dato de salud omitido]",
        "teacher.title": "Informe del d\u00eda para {name}",
        "teacher.worked": "Funcion\u00f3: {items}.",
        "teacher.with": "{activity}, con {item}",
        "teacher.hard": "Cost\u00f3: {items}.",
        "teacher.helped": "{activity} (sirvi\u00f3: {support})",
        "teacher.tomorrow": "Para ma\u00f1ana: anticipar {activity} con un aviso visual y un motivador nuevo.",
        "teacher.readings": "Lecturas sugeridas: {items}",
        "pending.teacher": "Ideas nuevas de Qloo esperando tu OK: {items}.",
        "principal.title": "Resumen del d\u00eda para {name}",
        "principal.children": "Chicos acompa\u00f1ados: {n}",
        "principal.moments": "Momentos registrados: {total} ({good}, {so}, {hard})",
        "principal.supports": "Apoyos m\u00e1s usados: {items}",
        "principal.watch": "Para mirar ma\u00f1ana: {names}",
        "principal.nobody": "nadie con dos o m\u00e1s momentos dif\u00edciles",
        "family.hello": "Hola, familia de {alias}.",
        "family.count": "Hoy {alias} cont\u00f3 que {good} de {n} momentos le fueron bien.",
        "family.best": "Lo que mejor le sali\u00f3: {activity}.",
        "family.hardest": "Lo que m\u00e1s le cost\u00f3: {activity}.",
        "family.helped": " En la escuela sirvi\u00f3: {support}.",
        "family.talk": "Para charlar en casa: preguntale por {item}.",
        "pending.family": "Ideas nuevas de Qloo para {alias}, esperando tu OK: {items}.",
        "suggested.teacher": "Hermano sugiere estas ideas nuevas de Qloo, esperando tu OK: {items}.",
        "suggested.family": "Hermano sugiere estas ideas nuevas de Qloo para {alias}, esperando tu OK: {items}.",
        "cli.planner.rules": "Planificador: reglas (sin modelo de lenguaje)",
        "cli.planner.llm": "Planificador: {model}, revisado por las reglas de Hermano",
        "cli.planner.fallback": "Planificador: reglas ({reason})",
        "specialist.title": "Registro de observaci\u00f3n para {name}",
        "specialist.child": "{alias} \u00b7 {grade} \u00b7 apoyos acordados: {supports}",
        "specialist.header": "hora | actividad | c\u00f3mo le fue (autoinforme) | apoyo usado | motivador",
        "cli.day": "=== D\u00eda {day} \u00b7 Qloo en modo {mode} \u00b7 {school} ===",
        "cli.warning": "aviso",
        "cli.degraded": "Qloo no responde para {alias} ({reason}): hoy van premios gen\u00e9ricos.",
        "cli.refused": "{alias}: {n} inter\u00e9s(es) no se mandaron a Qloo (parec\u00edan un nombre, un dato de salud o un contacto).",
        "cli.approved": "(simulado) {adult} aprob\u00f3: {item}",
        "cli.privacy": "Qloo en modo {mode}: {n} pedidos",
    },
}

QUOTES = {"en": ("\u201c", "\u201d"), "es": ("\u00ab", "\u00bb")}


def t(lang: str, key: str, **kw) -> str:
    """The text `key` in `lang`, filled in."""
    table = TEXT[lang] if lang in TEXT else TEXT[DEFAULT_LANG]
    return table[key].format(**kw)


def q(lang: str, title: str) -> str:
    """A title in the quotation marks of the language."""
    left, right = QUOTES.get(lang, QUOTES[DEFAULT_LANG])
    return left + title + right


def pick(value, lang: str) -> str:
    """A field from the data files: plain text, or {"en": ..., "es": ...}."""
    if isinstance(value, dict):
        return value.get(lang) or value.get(DEFAULT_LANG) or next(iter(value.values()), "")
    return value or ""
