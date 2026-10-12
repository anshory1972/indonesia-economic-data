# Indonesia economic data

A compiled, analysis-ready set of about 10,300 Indonesian statistical series (GDP/GRDP, CPI,
labour force, wages, poverty), with a small Python library to search and reshape them.
The underlying statistics are published by **BPS (Statistics Indonesia)** and compiled here.

```
warehouse/          compiled data (committed)
  catalog.parquet   one row per series: tags + metadata
  catalog.xlsx      same catalog, filterable in Excel
  obs.parquet       long table: series_id, date, value
external/           other inputs (World Bank population)
analysis/           analysis scripts; results in analysis/out/
idstats.py          ingest + query library / CLI
download/           raw .xlsx exports (local only, not in git)
```

| Topic | Series | Coverage |
|---|---|---|
| GDP / GRDP (SNA 2008) | ~7,900 | national + 38 provinces, annual/quarterly, current and constant 2010 prices, 2008 to Q2 2026 |
| Wages | ~1,100 | national by industry/sex/area; province; informal wages for ~310 regencies |
| CPI (2022=100) | ~500 | national and province, monthly, 2022 to Aug 2026 |
| Labour | ~500 | labour force by education, hours, industry, status; formal/informal by province |
| Poverty | ~290 | poverty rate, poor people, poverty line, P1/P2 by province; 98 regencies |

## Quick start
```bash
pip install -r requirements.txt
python idstats.py summary
python idstats.py search wage bali
```

## Analyses
- [`analysis/real_wage_vs_gdppc.py`](analysis/real_wage_vs_gdppc.py): real wages by sector group vs
  real GDP per capita, 2016 to 2025.
- [`analysis/real_wage_by_province.py`](analysis/real_wage_by_province.py): real wages vs real GRDP
  per capita for 32 provinces (population derived from Susenas poverty data).

## Adding data
Rebuilding needs the raw series exports, which are not in the repo.
1. Download the series as .xlsx and save the .xlsx into `download/`. Partial downloads that only
   cover recent periods are fine: they are merged into the existing history.
2. `python idstats.py ingest` to rebuild. This replaces `warehouse/` with what is in `download/`, so keep
   every export you want included there (it refuses to run when the folder is empty). A series found in several files is merged; where
   files disagree on a date, the more recently updated file wins.
3. Browse `warehouse/catalog.xlsx`, or search from Python or the command line.

## Catalog tags
| column | meaning |
|---|---|
| `topic` | GDP, CPI, Labour, Wages, Poverty |
| `concept` | the series name with topic, geography and presentation tokens removed, so it is comparable across regions (e.g. `GFCF: Buildings`) |
| `level` / `region` / `province` / `regency` | national, province or regency |
| `freq` | M, Q, H (half-yearly Feb/Aug), A |
| `basis` | GDP only: `current` or `constant_2010` |

## Python
```python
import idstats
db = idstats.load()
db.summary()                                        # what is in the warehouse
db.concepts("GDP", freq="Q", level="province")      # comparable concepts
db.search("household", topic="GDP", freq="Q", level="national")
db.panel("GDP", "Gross Domestic Product", freq="A", basis="constant_2010")  # date x province
db.panel("CPI", "Health", freq="M")
db.get(["Monthly Average Wage"])                     # series ids or exact names -> wide
db.long(db.search(topic="CPI", level="province"))   # tidy long table + metadata
```

## Things to watch
- Units differ within a topic: quarterly provincial GDP is in **IDR mn**, but national GDP is in IDR bn.
  Check the `unit` column before you combine series.
- Dates mark the month the period ends (for example, Q1 is `2010-03-01`).
- 44 series come with no observations (mostly new Papua provinces and casual-worker splits). `n_obs = 0`.
