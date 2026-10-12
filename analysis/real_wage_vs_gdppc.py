"""
Real wages by sector group vs real GDP per capita, Indonesia 2016-2025.

Data
  Wages       BPS "Monthly Average Wage" by industry (August SAKERNAS), IDR/month
  Weights     employees by industry, Feb 2026 (only period in the warehouse) -> fixed weights
  Deflators   implicit deflators from the sum of provincial GRDP, current / constant 2010:
                household consumption (-> consumption real wage, main measure)
                total GDP             (-> product real wage, robustness)
  GDP         national real GDP (constant 2010), IDR bn
  Population  World Bank WDI SP.POP.TOTL (external/population_wdi.csv)

Outputs -> analysis/out/
"""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import idstats  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
db = idstats.load()
cat = db.catalog

# wage industry -> (sector group, employee-count series name fragment)
AGR, MFG, NMI = "Agriculture", "Manufacturing", "Non-manufacturing industry"
MOD, TRD, PUB = "Modern services", "Traditional services", "Public & social services"
INDUSTRIES = {
    "Agriculture, Forestry and Fishing": (AGR, "Agriculture, Forestry, and Fishing"),
    "Manufacturing": (MFG, "Manufacturing"),
    "Mining and Quarrying": (NMI, "Mining and Quarrying"),
    "Electricity and Gas Supply": (NMI, "Electricity, Gas, Steam and Air Conditioning Supply"),
    "Water Supply, Sewerage, Waste Mangement, and Remediation Activities":
        (NMI, "Water Supply, Sewerage, Waste Management and Remediation Activities"),
    "Construction": (NMI, "Construction"),
    "Information and Communication": (MOD, "Publishing and Telecommunication Activities"),
    "Financial and Insurance Activities": (MOD, "Financial and Insurance Activities"),
    "Real Estate Activities": (MOD, "Real Estate Activities"),
    "Business Activities": (MOD, "Professional, Scientific, Technical, Administrative, and Support Activities"),
    "Wholesale and Retail Trade; Repair of Motor Vehicles and Motorcycles": (TRD, "Wholesale and Retail Trade"),
    "Transportation and Storage": (TRD, "Transportation and Storage"),
    "Accomodation and Food Services Activities": (TRD, "Accommodation and Food Service Activities"),
    "Other Service Activities": (TRD, "Arts, Other Service Activities, Household Activities"),
    "Public Administration and Defence; Compulsory Social Security":
        (PUB, "Public Administration and Defence; Compulsory Social Security"),
    "Education": (PUB, "Education"),
    "Human Health Services and Social Work Activities": (PUB, "Human Health and Social Work Activities"),
}
SECTORS = [AGR, MFG, NMI, MOD, TRD, PUB]
# 2015->2016 wages jump 20-25% nominal in every series (SAKERNAS break), so start in 2016
BASE = 2016
PERIODS = [(2016, 2019), (2019, 2025), (2018, 2025), (2016, 2025)]


def annual(df):
    df = df.copy()
    df.index = df.index.year
    return df


# ---- nominal wages by industry (August) and the official all-industry average
w = cat[(cat.topic == "Wages") & (cat.level == "national") & cat.concept.isin(INDUSTRIES)]
wages = annual(db.get(w, label="concept")).loc[BASE:2025]
wage_all = annual(db.get(["Monthly Average Wage"])).iloc[:, 0].loc[BASE:2025]

# ---- fixed employee weights (Feb 2026)
weights = {}
for ind, (_, frag) in INDUSTRIES.items():
    s = cat[(cat.topic == "Labour") & cat.name.str.startswith(f"Labour Force: {frag}")
            & cat.name.str.endswith(": Employee")]
    assert len(s) == 1, (ind, len(s))
    weights[ind] = db.get(s).iloc[-1, 0]
weights = pd.Series(weights)
sector_of = pd.Series({k: v[0] for k, v in INDUSTRIES.items()})

nominal = pd.DataFrame({
    sec: (wages[sector_of.index[sector_of == sec]] * weights[sector_of == sec]).sum(axis=1)
         / weights[sector_of == sec].sum()
    for sec in SECTORS})
nominal["All (fixed weights)"] = (wages[weights.index] * weights).sum(axis=1) / weights.sum()
nominal["All (BPS average)"] = wage_all


# ---- deflators from provincial GRDP sums
def implicit_deflator(concept):
    parts = {}
    for b in ("current", "constant_2010"):
        s = db.search(topic="GDP", concept=concept, freq="A", level="province", basis=b)
        parts[b] = annual(db.get(s, label="region"))
    cols = parts["current"].columns.intersection(parts["constant_2010"].columns)
    ok = parts["current"][cols].notna() & parts["constant_2010"][cols].notna()
    d = parts["current"][cols].where(ok).sum(axis=1) / parts["constant_2010"][cols].where(ok).sum(axis=1)
    return d / d[2018] * 100


p_cons = implicit_deflator("Consumption Expenditure: Household")
p_gdp = implicit_deflator("Gross Domestic Product")

real = nominal.div(p_cons.reindex(nominal.index), axis=0) * 100
product = nominal.div(p_gdp.reindex(nominal.index), axis=0) * 100

# ---- real GDP per capita
gdp = annual(db.get(["Gross Domestic Product: SNA 2008: 2010p: Annual"])).iloc[:, 0]
pop = pd.read_csv(ROOT / "external" / "population_wdi.csv", index_col="year")["population"]
gdppc = (gdp * 1e9 / pop).dropna().loc[BASE:2025]          # IDR, constant 2010 prices


def cagr(s, a, b):
    return ((s[b] / s[a]) ** (1 / (b - a)) - 1) * 100


rows = []
for a, b in PERIODS:
    bench = cagr(gdppc, a, b)
    for col in real.columns:
        rows.append({"period": f"{a}-{b}", "series": col,
                     "nominal": cagr(nominal[col], a, b),
                     "real_consumption": cagr(real[col], a, b),
                     "real_product": cagr(product[col], a, b),
                     "gdp_per_capita": bench,
                     "gap_vs_gdppc": cagr(real[col], a, b) - bench})
growth = pd.DataFrame(rows).round(2)

ind_rows = []
for ind in INDUSTRIES:
    r = wages[ind].div(p_cons.reindex(wages.index)) * 100
    ind_rows.append({"industry": ind, "sector": sector_of[ind], "employees_feb2026": int(weights[ind]),
                     "wage_2025": int(wages.at[2025, ind]),
                     **{f"real_{a}_{b}": round(cagr(r, a, b), 2) for a, b in PERIODS}})
industry = pd.DataFrame(ind_rows).sort_values(["sector", "real_2018_2025"])

idx = pd.concat([real[SECTORS + ["All (BPS average)"]], gdppc.rename("Real GDP per capita")], axis=1)
idx = idx.div(idx.loc[BASE]).mul(100).round(1)

levels = pd.DataFrame({"deflator_hh_cons_2018=100": p_cons, "deflator_gdp_2018=100": p_gdp,
                       "real_gdp_idr_bn": gdp, "population": pop, "real_gdp_pc_idr": gdppc}).loc[BASE:2025]

with pd.ExcelWriter(OUT / "real_wage_vs_gdppc.xlsx") as xw:
    growth.to_excel(xw, sheet_name="growth_cagr", index=False)
    industry.to_excel(xw, sheet_name="by_industry", index=False)
    idx.to_excel(xw, sheet_name=f"index_{BASE}=100")
    nominal.round(0).to_excel(xw, sheet_name="nominal_wage")
    real.round(0).to_excel(xw, sheet_name="real_wage_2018prices")
    levels.to_excel(xw, sheet_name="deflators_gdp_pop")
    weights.rename("employees_feb2026").to_excel(xw, sheet_name="weights")


# ---- chart
def chart(path=OUT / "real_wage_vs_gdppc.png"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0"
    PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]   # fixed slot order
    COLORS = dict(zip(SECTORS, PALETTE))
    BENCH, ALL = "Real GDP per capita", "All (BPS average)"
    plt.rcParams.update({"font.family": "Segoe UI", "font.size": 9.5, "axes.edgecolor": GRID,
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                         "axes.spines.top": False, "axes.spines.right": False})
    fig = plt.figure(figsize=(14, 7.4), dpi=200, facecolor="#fcfcfb")
    outer = fig.add_gridspec(1, 2, width_ratios=[1.45, 1], wspace=0.3)
    left = outer[0].subgridspec(2, 3, hspace=0.42, wspace=0.12)
    right = outer[1].subgridspec(2, 1, hspace=0.38)
    yrs = idx.index
    lo, hi = min(88, idx.min().min() - 2), idx.max().max() + 3

    # (a) small multiples: each group vs GDP per capita and the all-worker average
    for k, sec in enumerate(SECTORS):
        ax = fig.add_subplot(left[k // 3, k % 3])
        ax.axvspan(2019.5, 2021.5, color="#f1f0ec", zorder=0)
        ax.axhline(100, color=GRID, lw=1, zorder=1)
        ax.plot(yrs, idx[BENCH], color=INK, lw=1.6, ls=(0, (5, 3)), zorder=3)
        ax.plot(yrs, idx[ALL], color=MUTED, lw=1.3, zorder=2)
        ax.plot(yrs, idx[sec], color=COLORS[sec], lw=2.2, marker="o", ms=3.5, zorder=4)
        ax.text(yrs[-1] + 0.25, idx[sec].iat[-1], f"{idx[sec].iat[-1]:.0f}", va="center",
                fontsize=9, color=INK, fontweight="semibold")
        ax.set_ylim(lo, hi)
        ax.set_xlim(yrs[0] - 0.4, yrs[-1] + 1.4)
        ax.set_xticks([2016, 2019, 2022, 2025], ["2016", "19", "22", "2025"])
        ax.grid(axis="y", color=GRID, lw=0.8)
        if k % 3:
            ax.tick_params(axis="y", labelleft=False)
        w = weights[sector_of == sec].sum() / weights.sum() * 100
        ax.set_title(f"{sec}", loc="left", fontsize=10, color=INK, fontweight="semibold", pad=14)
        ax.text(0, 1.015, f"{w:.0f}% of employees", transform=ax.transAxes, fontsize=8, color=INK2)
        if k == 0:
            ax.text(2022.3, idx[BENCH].at[2022] + 6, "GDP per capita", fontsize=8, color=INK, ha="right")
            ax.text(2021.6, lo + 2, "All workers", fontsize=8, color=MUTED)
    fig.text(0.035, 0.955, f"Real wage index by sector group ({BASE} = 100): group (colour) vs GDP per "
             "capita (dashed) and all workers (grey)", fontsize=11.5, color=INK, fontweight="semibold")

    # (b) CAGR by period, GDP per capita as reference line
    names = SECTORS + [ALL]
    for i, (a, b) in enumerate([(2016, 2019), (2019, 2025)]):
        axb = fig.add_subplot(right[i])
        g = growth[growth.period == f"{a}-{b}"].set_index("series")
        vals = [g.at[n, "real_consumption"] for n in names]
        yy = np.arange(len(names))[::-1]
        axb.barh(yy, vals, color=[COLORS.get(n, MUTED) for n in names], height=0.66, zorder=3)
        bench = g["gdp_per_capita"].iat[0]
        axb.axvline(bench, color=INK, lw=1.6, ls=(0, (5, 3)), zorder=4)
        axb.axvline(0, color=INK2, lw=0.8, zorder=2)
        axb.text(bench - 0.08, len(names) - 0.45, f"GDP per capita {bench:+.1f}%", color=INK,
                 fontsize=8.5, va="bottom", ha="right")
        for y, v in zip(yy, vals):
            axb.text(v + (0.1 if v >= 0 else -0.1), y, (f"{v:+.1f}%" if round(v, 1) else "0.0%"), va="center",
                     ha="left" if v >= 0 else "right", fontsize=8.5, color=INK)
        axb.set_yticks(yy, [n if n != ALL else "All workers" for n in names])
        axb.set_xlim(-2.2, 5.4)
        axb.set_ylim(-0.6, len(names) + 0.3)
        axb.grid(axis="x", color=GRID, lw=0.8)
        axb.tick_params(axis="y", length=0)
        axb.set_title(f"Real wage growth, {a}–{b} (% per year)", loc="left", fontsize=10.5,
                      color=INK, fontweight="semibold")

    fig.text(0.01, 0.005,
             "Real wage = August SAKERNAS average monthly wage by industry (BPS), deflated by the household "
             "consumption deflator (sum of provincial GRDP); groups use fixed Feb-2026 employee weights.\n"
             "Modern services = ICT, finance, real estate, business services. Traditional = trade, transport, "
             "accommodation & food, other services. Public & social = public administration, education, health.\n"
             "GDP per capita = real GDP (constant 2010 prices) / population (World Bank WDI). Series start in 2016 "
             "because of a break in the SAKERNAS wage data between 2015 and 2016.",
             fontsize=7.5, color=INK2, va="bottom")
    fig.subplots_adjust(left=0.035, right=0.985, top=0.87, bottom=0.14)
    fig.savefig(path, facecolor=fig.get_facecolor())
    return path


if __name__ == "__main__":
    pd.set_option("display.width", 200, "display.max_colwidth", 60)
    print("weights (share of employees):"); print((weights.groupby(sector_of).sum() / weights.sum()).round(3))
    print("\nindex 2015=100:"); print(idx.to_string())
    print("\nCAGR %:"); print(growth.to_string(index=False))
    print("\nby industry:"); print(industry.to_string(index=False))
    print("\nchart:", chart())
