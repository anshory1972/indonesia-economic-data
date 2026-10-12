"""
Indonesia statistics warehouse (BPS series).

Layout
------
  download/                 inbox: raw .xlsx series exports (local only, never modified)
  warehouse/catalog.parquet  one row per series (metadata + parsed tags)
  warehouse/obs.parquet      long table: series_id, date, value
  warehouse/catalog.xlsx     same catalog, for browsing/filtering in Excel
  warehouse/ingest_log.csv   which files were loaded (local only)

Usage (Python)
--------------
  import idstats
  db = idstats.load()
  db.search("household consumption", topic="GDP", freq="Q", level="province")
  db.get(["Monthly Average Wage"])               # ids or exact names -> date x series
  db.panel("GDP", "GFCF: Buildings", freq="A", basis="current")   # date x province
  db.concepts("CPI")                              # list comparable concepts

Usage (command line)
--------------------
  python idstats.py ingest            # rebuild warehouse from everything in download/
  python idstats.py search wage bali  # keyword search of the catalog
  python idstats.py summary           # counts by topic / frequency / level
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import sys
from pathlib import Path

import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parent
INBOX = ROOT / "download"
WH = ROOT / "warehouse"

# export header labels -> column names in the catalog
META_FIELDS = {
    "Region": "country",
    "Subnational": "subnational",
    "Frequency": "frequency",
    "Unit": "unit",
    "Source": "source",
    "Status": "status",
    "Series ID": "src_id",
    "First Obs. Date": "first_obs",
    "Last Obs. Date": "last_obs",
    "Last Update Time": "last_update",
}

# first name segment -> topic
TOPICS = {
    "Gross Domestic Product": "GDP",
    "Consumer Price Index": "CPI",
    "Labour Force": "Labour",
    "Employment": "Labour",
    "Number of Workers": "Labour",
    "Percentage of Formal Labor": "Labour",
    "Percentage of Informal Labor": "Labour",
    "Average Monthly Net Wage": "Wages",
    "Monthly Average Wage": "Wages",
    "Percentage of Poor People": "Poverty",
    "Number of Poor People": "Poverty",
    "Poverty Gap Index": "Poverty",
    "Poverty Gap Index (P1)": "Poverty",
    "Poverty Severity Index": "Poverty",
    "Average Monthly Poverty Line per Capita": "Poverty",
}

# name segments that describe presentation, not the concept
NOISE_SEGMENTS = {"SNA 2008", "2010p", "Annual", "Quarterly", "Monthly",
                  "Semiannually", "Half-yearly"}
ISLAND_GROUPS = {"Sumatera", "Java", "Kalimantan", "Sulawesi", "Bali and Nusa Tenggara",
                 "Bali & Nusa Tenggara", "Nusa Tenggara", "Maluku", "Maluku and Papua", "Maluku & Papua"}
# province spellings that differ between the Subnational field and series names
ALIASES = {"DI Yogyakarta": "Yogyakarta", "Jakarta": "DKI Jakarta", "Bangka Belitung Islands": "Bangka Belitung",
           "South West Papua": "Southwest Papua"}


# --------------------------------------------------------------------------- parsing

def _freq_code(s: str | None) -> str:
    s = (s or "").lower()
    for key, code in (("month", "M"), ("quarter", "Q"), ("half", "H"), ("annual", "A"),
                      ("week", "W"), ("dai", "D")):
        if key in s:
            return code
    return "?"


def read_export(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse one wide series export -> (meta rows, long observations)."""
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    labels = [r[0] for r in rows]
    first_data = next(i for i, v in enumerate(labels) if isinstance(v, dt.datetime))
    ncol = len(rows[0])

    meta = {"name": [str(v).strip() if v is not None else None for v in rows[0][1:]]}
    for i in range(1, first_data):
        col = META_FIELDS.get(labels[i])
        if col:
            meta[col] = list(rows[i][1:]) + [None] * (ncol - len(rows[i]))
    meta = pd.DataFrame(meta)
    # source identifier: leading digits, used only to merge files during ingest (never stored)
    meta["src_id"] = meta["src_id"].astype(str).str.extract(r"^(\d+)", expand=False)

    records = []
    for r in rows[first_data:]:
        d = r[0]
        if not isinstance(d, dt.datetime):
            continue
        for j, v in enumerate(r[1:]):
            if v is not None and v != "":
                records.append((meta.at[j, "src_id"], d, float(v)))
    obs = pd.DataFrame(records, columns=["src_id", "date", "value"])
    obs["last_update"] = obs["src_id"].map(dict(zip(meta.src_id, meta.last_update)))
    return meta, obs


def tag(meta: pd.DataFrame) -> pd.DataFrame:
    """Add analysis-friendly tags parsed from the series name and metadata."""
    m = meta.copy()
    segs = m["name"].fillna("").str.split(":").apply(lambda xs: [x.strip() for x in xs])
    m["topic"] = segs.str[0].map(TOPICS).fillna(segs.str[0])
    m["freq"] = m["frequency"].map(_freq_code)

    def region_and_level(row, parts):
        sub = row.subnational
        idx = next((i for i, p in enumerate(parts) if re.search(r"(Regency|Municipality|City)$", p)), None)
        if idx:  # "...: Aceh: Aceh Besar Regency" -> province is the segment before
            return parts[idx - 1], parts[idx], "regency"
        if sub:
            return sub, None, "province"
        return None, None, "national"

    rl = [region_and_level(r, p) for r, p in zip(m.itertuples(), segs)]
    m["province"] = [x[0] for x in rl]
    m["regency"] = [x[1] for x in rl]
    m["level"] = [x[2] for x in rl]
    m["region"] = m["regency"].fillna(m["province"]).fillna(m["country"])

    # GDP price basis
    m["basis"] = None
    gdp = m.topic == "GDP"
    m.loc[gdp, "basis"] = segs[gdp].apply(lambda p: "constant_2010" if "2010p" in p else "current")

    # concept = name minus topic, presentation tokens and geography -> comparable across regions
    def concept(parts, row):
        drop = NOISE_SEGMENTS | ISLAND_GROUPS | {row.province, row.regency, row.country}
        drop |= {ALIASES.get(row.province)}
        body = [p for p in parts[1:] if p and p not in drop]
        if row.topic == "GDP" and body[:1] == ["SNA 2008"]:
            body = body[1:]
        if row.topic == "Poverty" and body:
            body = [parts[0]] + body
        return ": ".join(body) if body else parts[0]

    m["concept"] = [concept(p, r) for p, r in zip(segs, m.itertuples())]
    return m


# --------------------------------------------------------------------------- ingest

def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _make_ids(meta: pd.DataFrame) -> pd.Series:
    """Warehouse ids: TOPIC.FREQ.<hash of name/frequency/unit>; stable across rebuilds."""
    key = meta["name"].fillna("") + "|" + meta["frequency"].fillna("") + "|" + meta["unit"].fillna("")
    ids = (meta["topic"].str.upper().str.replace(r"\W+", "", regex=True).str[:8] + "." + meta["freq"] + "."
           + key.map(lambda k: hashlib.sha1(k.encode()).hexdigest()[:8]))
    # identical names (rare source duplicates): suffix in a deterministic order
    order = meta.assign(_id=ids).sort_values(["_id", "first_obs", "src_id"]).groupby("_id").cumcount()
    order = order.reindex(ids.index)
    return ids.where(order == 0, ids + "-" + (order + 1).astype(str))


def ingest(inbox: Path = INBOX, out: Path = WH, verbose: bool = True) -> None:
    """Rebuild the warehouse from every .xlsx in the inbox.

    Series appearing in several files are merged: observations are unioned and,
    where two files disagree on a date, the one with the later
    'Last Update Time' wins. Partial downloads (only recent periods) therefore
    extend the history loaded from older, fuller downloads.
    """
    out.mkdir(exist_ok=True)
    files = sorted(p for p in inbox.glob("*.xlsx") if not p.name.startswith("~$"))
    if not files:  # e.g. a fresh clone: raw exports are not in git, keep the committed warehouse
        raise SystemExit(f"no .xlsx export files in {inbox}; warehouse left unchanged")
    metas, obss, log = [], [], []
    for p in files:
        meta, obs = read_export(p)
        meta["file_mtime"] = dt.datetime.fromtimestamp(p.stat().st_mtime)
        obs["file_mtime"] = meta["file_mtime"].iat[0]
        metas.append(meta)
        obss.append(obs)
        log.append({"file": p.name, "sha1": _sha1(p), "series": len(meta),
                    "observations": len(obs), "ingested_at": dt.datetime.now()})
        if verbose:
            print(f"  {p.name:<22} {len(meta):>5} series {len(obs):>8} obs")

    meta = pd.concat(metas, ignore_index=True)
    obs = pd.concat(obss, ignore_index=True)

    # latest metadata per series (a series can appear in several files)
    meta = (meta.sort_values(["last_update", "file_mtime"])
                .groupby("src_id", as_index=False).last())
    # merged observations, latest vintage wins
    obs = (obs.sort_values(["last_update", "file_mtime"])
              .drop_duplicates(["src_id", "date"], keep="last")
              .drop(columns=["last_update", "file_mtime"]))

    # recompute coverage from merged observations
    cov = obs.groupby("src_id")["date"].agg(first_obs="min", last_obs="max", n_obs="size")
    meta = meta.drop(columns=["first_obs", "last_obs"]).join(cov, on="src_id")
    meta["n_obs"] = meta["n_obs"].fillna(0).astype(int)
    meta = tag(meta)

    # replace the source identifier with warehouse ids
    meta["series_id"] = _make_ids(meta)
    assert meta.series_id.is_unique
    obs["series_id"] = obs["src_id"].map(dict(zip(meta.src_id, meta.series_id)))
    obs = obs[["series_id", "date", "value"]].sort_values(["series_id", "date"]).reset_index(drop=True)

    cols = ["series_id", "topic", "concept", "level", "region", "province", "regency",
            "freq", "basis", "unit", "name", "frequency", "source", "status", "first_obs",
            "last_obs", "n_obs", "last_update"]
    meta = meta[cols].sort_values(["topic", "freq", "level", "region", "concept"]).reset_index(drop=True)
    for c in ("basis", "province", "regency"):
        meta[c] = meta[c].astype("string")

    meta.to_parquet(out / "catalog.parquet", index=False)
    obs.to_parquet(out / "obs.parquet", index=False)
    pd.DataFrame(log).to_csv(out / "ingest_log.csv", index=False)
    try:
        with pd.ExcelWriter(out / "catalog.xlsx") as xw:
            meta.to_excel(xw, sheet_name="catalog", index=False)
            ws = xw.sheets["catalog"]
            ws.autofilter(0, 0, len(meta), len(meta.columns) - 1)
            ws.freeze_panes(1, 2)
    except PermissionError:
        print("  (catalog.xlsx is open in Excel - skipped writing it)")
    if verbose:
        print(f"\n{len(files)} files -> {len(meta):,} unique series, {len(obs):,} observations")
        print(f"written to {out}")


# --------------------------------------------------------------------------- query

class Warehouse:
    def __init__(self, wh: Path = WH):
        self.catalog = pd.read_parquet(wh / "catalog.parquet")
        self.obs = pd.read_parquet(wh / "obs.parquet")

    def __repr__(self):
        return f"<Warehouse {len(self.catalog):,} series, {len(self.obs):,} obs>"

    # -- discovery
    def search(self, text: str = "", **filters) -> pd.DataFrame:
        """Keyword search (all words must match name/region) plus exact-match filters,
        e.g. search("export", topic="GDP", freq="Q", level="province", basis="current").
        A filter value may be a list."""
        c = self.catalog
        hay = (c["name"] + " " + c["region"].fillna("")).str.lower()
        for w in text.lower().split():
            c, hay = c[hay.str.contains(w, regex=False)], hay[hay.str.contains(w, regex=False)]
        for k, v in filters.items():
            c = c[c[k].isin(v if isinstance(v, (list, tuple, set)) else [v])]
        return c

    def concepts(self, topic: str, **filters) -> pd.DataFrame:
        """Distinct concepts within a topic, with how many regions/series each has."""
        c = self.search(topic=topic, **filters)
        return (c.groupby(["concept", "freq"] + (["basis"] if topic == "GDP" else []), dropna=False)
                 .agg(series=("series_id", "size"), regions=("region", "nunique"),
                      unit=("unit", "first"), first=("first_obs", "min"), last=("last_obs", "max"))
                 .reset_index())

    # -- data
    def _select(self, series) -> pd.DataFrame:
        """Catalog rows for a catalog slice, series id(s) or exact series name(s)."""
        if isinstance(series, pd.DataFrame):
            return series
        keys = [series] if isinstance(series, str) else list(series)
        c = self.catalog
        sel = c[c.series_id.isin(keys) | c.name.isin(keys)]
        missing = set(keys) - set(sel.series_id) - set(sel.name)
        if missing:
            raise KeyError(f"unknown series: {sorted(missing)}")
        return sel

    def get(self, series, label: str = "name") -> pd.DataFrame:
        """Wide DataFrame (date x series). `series` = catalog slice, series id(s) or exact name(s).
        `label` picks the column header: 'name', 'region', 'concept', 'series_id'."""
        sel = self._select(series)
        o = self.obs[self.obs.series_id.isin(sel.series_id)]
        wide = o.pivot(index="date", columns="series_id", values="value")
        wide = wide[[s for s in sel.series_id if s in wide.columns]]
        names = dict(zip(sel.series_id, sel[label]))
        if pd.Series(list(names.values())).duplicated().any():
            names = {k: f"{v} [{k}]" for k, v in names.items()}
        return wide.rename(columns=names).rename_axis(columns=None)

    def long(self, series) -> pd.DataFrame:
        """Tidy long table with metadata joined, best for groupby / plotting."""
        sel = self._select(series)
        keep = ["series_id", "topic", "concept", "level", "region", "province", "freq", "basis", "unit"]
        return self.obs.merge(sel[keep], on="series_id")

    def panel(self, topic: str, concept: str, freq: str, level: str = "province",
              **filters) -> pd.DataFrame:
        """Region panel for one concept: date x region (e.g. GDP component by province)."""
        sel = self.search(topic=topic, concept=concept, freq=freq, level=level, **filters)
        if sel.empty:
            raise KeyError(f"no series for {topic} / {concept!r} / {freq} / {level} {filters}")
        if sel.region.duplicated().any():
            dup = sel[sel.region.duplicated(keep=False)][["region", "name"]]
            raise ValueError("ambiguous: several series per region, add a filter "
                             f"(e.g. basis=, unit=):\n{dup.head(10).to_string()}")
        return self.get(sel, label="region")

    def summary(self) -> pd.DataFrame:
        return (self.catalog.groupby(["topic", "freq", "level"])
                .agg(series=("series_id", "size"), first=("first_obs", "min"), last=("last_obs", "max"))
                .reset_index())


def load(wh: Path = WH) -> Warehouse:
    return Warehouse(wh)


# --------------------------------------------------------------------------- CLI

if __name__ == "__main__":
    pd.set_option("display.width", 200, "display.max_colwidth", 90, "display.max_rows", 200)
    cmd, *args = sys.argv[1:] or ["help"]
    if cmd == "ingest":
        ingest()
    elif cmd == "search":
        r = load().search(" ".join(args))
        print(r[["series_id", "topic", "freq", "basis", "region", "unit", "concept"]].to_string(index=False))
        print(f"\n{len(r)} series")
    elif cmd == "summary":
        print(load().summary().to_string(index=False))
    else:
        print(__doc__)
