"""
central.wikibase
================
The Glass Database's copy of its public record on **glass.wikibase.cloud**: the model
(classes and properties, each with its Wikidata twin), the staging of the site's tables
into items, deduplication, the OpenRefine round trip, the two-pass pusher, and the sync
back. Everything here is runnable from the admin page (``/wikibase.html`` → ``api.wikibase``)
and from the command line (``python -m central.wikibase``); the pages and the API keep
reading SQLite, and the wiki is the backstop. docs/WIKIBASE.md is the runbook.

Modules
  model     the schema: PROPERTIES, CLASSES, vocabularies, known Wikidata ids
  client    the MediaWiki action-API client (login, csrf, back-off, dry run)
  stage     site tables → ``wb_items`` (one row per would-be item, with its claims)
  dedupe    clusters of the same person / studio / place; one canonical per cluster
  refine    CSV out to OpenRefine, CSV back in, the reconciliation service's matching
  push      properties, then items bare, then every statement; ids written back
  sync      what changed since the last push, and the wiki's copy of what we pushed
  jobs      background runs with a log the page polls (shared by every API worker)
"""
from __future__ import annotations

from .model import SITE_DEFAULT  # noqa: F401

__all__ = ["SITE_DEFAULT"]
