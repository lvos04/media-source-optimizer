"""Usenet engine: SABnzbd history ingestion, Usenet providers (NNTP status), article availability on actually
requested articles, provider contribution metrics, priority recommendations and the optional header index.

The API router lives in ``api`` (auto-mounted). This package deliberately exposes no ``router`` attribute so
the routes are mounted exactly once.
"""

from mso.features.usenet import settings  # noqa: F401  (register settings before anything reads them)
