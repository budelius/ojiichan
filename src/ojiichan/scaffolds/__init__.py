"""Packaged observability scaffold templates for non-Python services.

Template bodies live next to this module as ``<language>/<path>.tmpl`` files so
that the real ``.ts``/``.rs`` source is never picked up by language tooling that
scans the installed package. The :mod:`ojiichan.scaffold` module reads and
renders them.
"""
