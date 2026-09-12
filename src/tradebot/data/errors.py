"""Error lapisan data. Semuanya berarti bot tidak boleh melanjutkan dengan data ini."""

from __future__ import annotations


class DataError(Exception):
    """Data historis tidak bisa dipercaya atau tidak bisa diperoleh."""


class DataGapError(DataError):
    """Ada gap lebih panjang dari data.max_gap_bars. Cache tidak ditulis."""


class CacheError(DataError):
    """File cache tidak bisa dibaca atau isinya melanggar skema."""
