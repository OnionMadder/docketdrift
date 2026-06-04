"""Web scrapers for external judicial-data sources.

Distinct from ``opinions.parsing``, which operates on plain text (opinion
bodies). Scrapers operate on remote HTML from sites like mncourts.gov:
they fetch, parse, and normalize into ``Judge`` / ``Opinion`` rows.
"""
