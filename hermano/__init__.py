"""Hermano: a school-day companion for neurodivergent students, powered by Qloo taste data."""

__version__ = "0.2.0"
# Sent on every request. Groq's edge answers 403 "error code: 1010" to the default
# Python-urllib user agent (measured 2026-10-08), so neither client may rely on it.
USER_AGENT = "hermano/" + __version__
