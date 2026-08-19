"""User-facing research organization capabilities.

Four modules and one facade since `V2-P4-006` split `research.py`'s three responsibilities:

- `screening.py` -- the governed screen: filter, rank, and say why each rejected name is out.
- `governance.py` -- what a risk flag is worth, asked of the two gates this build already ships
  rather than restated as a fourth list of flag strings.
- `watchlist.py` -- the `WatchlistStore` extension contract.
- `reporting.py` -- the `ReportStore` contract and `ResearchReportFactory`.
- `research.py` -- re-exports all of the above unchanged, so every existing
  `from openalpha_cn.product.research import ...` still resolves to the same object.

Nothing is imported here: `runtime/__init__.py`'s lesson is that a package `__init__` always
runs before any of its submodules, so an eager re-export would hand every importer of one
module the dependencies of all four.
"""
