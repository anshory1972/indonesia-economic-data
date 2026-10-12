"""
Real wages vs real GRDP per capita by province, Indonesia 2016-2025.

Data (all from the warehouse)
  Wages       average monthly net wage of employees, August SAKERNAS, by province (IDR)
              + average informal net wage by province, 2018-2025 (supplementary)
  Deflator    province household-consumption deflator = current / constant-2010 GRDP household consumption
  GRDP        province real GRDP, constant 2010 prices (IDR bn)
  Population  derived: number of poor people / poverty rate (Susenas, March round).
              Sums to ~98.5% of the WDI national total with the same growth, so fine for per-capita growth.

Papua and West Papua are excluded from trend comparisons: they were split into six provinces
in 2022, so their GRDP, population and wage series change boundaries mid-period.
2016 is the first year because of the 2015->2016 break in SAKERNAS wages (see real_wage_vs_gdppc.py).

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

BASE, LAST = 2016, 2025
PERIODS = [(2016, 2019), (2019, 2025), (2016, 2025)]
SPLIT = {"Papua", "West Papua", "Central Papua", "Papua Mountains", "South Papua", "South West Papua"}


def yearly(df, month=None):
    df = df.copy()
    if month:
        df = df[df.index.month == month]
    df.index = df.index.year
    return df


# ---- wages (employees, August) and informal wages
emp_wage = yearly(db.get(db.search(topic="Wages", level="province", freq="H"), label="region"), month=8)
inf_wage = yearly(db.get(db.search(topic="Wages", level="province", freq="A",
                                   concept="Informal"), label="region"))


# ---- province deflator and real GRDP
def grdp(concept, basis):
    s = db.search(topic="GDP", concept=concept, freq="A", level="province", basis=basis)
    return yearly(db.get(s, label="region"))


cur = grdp("Consumption Expenditure: Household", "current")
con = grdp("Consumption Expenditure: Household", "constant_2010")
ok = cur.notna() & con.notna()
nat_defl = cur.where(ok).sum(axis=1) / con.where(ok).sum(axis=1)
defl = cur / con
# provinces lacking current-price consumption for part of the period use the national deflator
FALLBACK = [p for p in defl.columns if defl[p].loc[BASE:LAST].isna().any() and p not in SPLIT]
for p in FALLBACK:
    defl[p] = defl[p].fillna(nat_defl) if defl[p].loc[BASE:LAST].notna().sum() == 0 else nat_defl
real_grdp = grdp("Gross Domestic Product", "constant_2010")


# ---- population from poverty data (province-level series only: "Number of Poor People: <province>")
def poverty(concept):
    s = db.search(topic="Poverty", concept=concept, freq="A", level="province")
    return yearly(db.get(s[s.name.str.count(":") == 1], label="region"))


pop = poverty("Number of Poor People") / poverty("Percentage of Poor People") * 100 * 1000
grdp_pc = real_grdp * 1e9 / pop

provinces = sorted(set(emp_wage.columns) & set(defl.columns) & set(grdp_pc.columns) - SPLIT)
provinces = [p for p in provinces if emp_wage[p].loc[[BASE, LAST]].notna().all()]

real_emp = emp_wage[provinces].div(defl[provinces]).mul(defl[provinces].loc[LAST])   # 2025 prices
real_inf = inf_wage.reindex(columns=provinces).div(defl[provinces]).mul(defl[provinces].loc[LAST])


def cagr(df, a, b):
    return ((df.loc[b] / df.loc[a]) ** (1 / (b - a)) - 1) * 100


# national reference row: same definitions, national series
nat_wage = yearly(db.get(["Average Monthly Net Wage: Semiannually"]), month=8).iloc[:, 0]
nat_real = nat_wage / nat_defl
wdi = pd.read_csv(ROOT / "external" / "population_wdi.csv", index_col="year")["population"]
nat_gdppc = yearly(db.get(["Gross Domestic Product: SNA 2008: 2010p: Annual"])).iloc[:, 0] * 1e9 / wdi

rows = []
for p in provinces + ["Indonesia"]:
    r = {"province": p}
    for a, b in PERIODS:
        if p == "Indonesia":
            w = ((nat_real[b] / nat_real[a]) ** (1 / (b - a)) - 1) * 100
            g = ((nat_gdppc[b] / nat_gdppc[a]) ** (1 / (b - a)) - 1) * 100
        else:
            w = cagr(real_emp[[p]], a, b).iat[0]
            g = cagr(grdp_pc[[p]], a, b).iat[0]
        r[f"wage_{a}_{b}"], r[f"grdppc_{a}_{b}"], r[f"gap_{a}_{b}"] = w, g, w - g
    if p != "Indonesia":
        r["national_deflator"] = p in FALLBACK
        r["informal_wage_2018_2025"] = cagr(real_inf[[p]], 2018, 2025).iat[0]
        r["wage_2025_idr"] = emp_wage.at[LAST, p]
        r["grdppc_2025_idr_2010p"] = grdp_pc.at[LAST, p]
        r["index_wage_2025"] = real_emp.at[LAST, p] / real_emp.at[BASE, p] * 100
        r["index_grdppc_2025"] = grdp_pc.at[LAST, p] / grdp_pc.at[BASE, p] * 100
    rows.append(r)
res = pd.DataFrame(rows).set_index("province").round(2)
prov = res.drop(index="Indonesia")

with pd.ExcelWriter(OUT / "real_wage_by_province.xlsx") as xw:
    res.sort_values(f"gap_{BASE}_{LAST}").to_excel(xw, sheet_name="growth_cagr")
    (real_emp.loc[BASE:LAST] / real_emp.loc[BASE] * 100).round(1).to_excel(xw, sheet_name="real_wage_index")
    (grdp_pc[provinces].loc[BASE:LAST] / grdp_pc[provinces].loc[BASE] * 100).round(1).to_excel(
        xw, sheet_name="grdppc_index")
    emp_wage.loc[BASE:LAST].to_excel(xw, sheet_name="nominal_wage_employees")
    inf_wage.loc[2018:LAST].to_excel(xw, sheet_name="nominal_wage_informal")
    (defl.loc[BASE:LAST] * 100).round(2).to_excel(xw, sheet_name="hh_cons_deflator")
    pop.loc[BASE:].round(0).to_excel(xw, sheet_name="population_derived")


# ---- chart: dumbbells, wage growth vs GRDP per capita growth, two periods
def chart(path=OUT / "real_wage_by_province.png"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"
    WAGE, BENCH, NEG = "#2a78d6", "#0b0b0b", "#e34948"
    plt.rcParams.update({"font.family": "Segoe UI", "font.size": 9, "axes.edgecolor": GRID,
                         "xtick.color": INK2, "ytick.color": INK, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.spines.left": False})
    order = res.sort_values(f"gap_{BASE}_{LAST}", ascending=False).index.tolist()
    order.remove("Indonesia")
    order = order + ["Indonesia"]
    shown = [f"{p}*" if p in FALLBACK else p for p in order]   # national row at the bottom
    n = len(order)
    y = np.arange(n)[::-1].astype(float)
    y[-1] -= 0.8                     # gap before the national row
    fig, axes = plt.subplots(1, 3, figsize=(14, 11), dpi=200, facecolor=SURF, sharey=True,
                             gridspec_kw={"wspace": 0.08})
    for ax, (a, b) in zip(axes, [(2016, 2025), (2016, 2019), (2019, 2025)]):
        w = res.loc[order, f"wage_{a}_{b}"].values
        g = res.loc[order, f"grdppc_{a}_{b}"].values
        for yi, wi, gi in zip(y, w, g):
            ax.plot([wi, gi], [yi, yi], color=GRID if wi < gi else "#b9d3f2", lw=3, zorder=1,
                    solid_capstyle="round")
        XMAX = 9
        for yi, gi in zip(y, g):
            if gi > XMAX:   # off-scale benchmark (nickel-boom provinces): clip and label
                ax.annotate(f"{gi:.0f}%", xy=(XMAX, yi), xytext=(XMAX - 0.15, yi + 0.38), ha="right",
                            fontsize=8, color=INK, fontweight="semibold")
                ax.scatter([XMAX - 0.05], [yi], marker=">", s=36, color=BENCH, zorder=3)
        ax.scatter(np.where(g > XMAX, np.nan, g), y, marker="|", s=110, lw=2.2, color=BENCH, zorder=3)
        ax.scatter(w, y, s=34, color=[WAGE if v >= 0 else NEG for v in w], zorder=4,
                   edgecolor=SURF, lw=1)
        ax.axvline(0, color=INK2, lw=0.8, zorder=0)
        ax.axhline(y[-1] + 0.9, color=GRID, lw=1)
        ax.grid(axis="x", color=GRID, lw=0.8)
        ax.set_xlim(-6.5, 9)
        ax.tick_params(axis="y", length=0)
        ax.set_title(f"{a}–{b}", loc="left", fontsize=11, color=INK, fontweight="semibold", pad=18)
        ax.text(0, 1.008, f"median gap {np.median(w[:-1] - g[:-1]):+.1f} pp a year", transform=ax.transAxes,
                fontsize=8.5, color=INK2)
        ax.set_xlabel("% per year", color=INK2)
    axes[0].set_yticks(y, shown)
    axes[0].get_yticklabels()[-1].set_fontweight("semibold")
    behind = (prov[f"gap_{BASE}_{LAST}"] < 0).sum()
    fig.suptitle(f"Real wages vs real GRDP per capita by province: wages trailed output per person in "
                 f"{behind} of {len(prov)} provinces, {BASE}–{LAST}",
                 x=0.012, y=0.985, ha="left", fontsize=12.5, color=INK, fontweight="semibold")
    handles = [Line2D([], [], marker="o", ls="", color=WAGE, ms=6, label="Real wage growth (employees)"),
               Line2D([], [], marker="o", ls="", color=NEG, ms=6, label="Real wage growth, negative"),
               Line2D([], [], marker="|", ls="", color=BENCH, ms=11, mew=2.2, label="Real GRDP per capita growth")]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.008, 0.968), ncol=3, frameon=False,
               fontsize=9)
    fig.text(0.012, 0.008,
             "Provinces sorted by the 2016–2025 gap (wage growth minus GRDP per capita growth); smallest gap at top. "
             "Real wage = August SAKERNAS average net wage of employees, deflated by each province's household-consumption "
             "deflator.\nGRDP per capita = real GRDP (constant 2010) / population derived from Susenas poverty data "
             "(poor people ÷ poverty rate). Indonesia row uses national GDP and World Bank population. "
             "Papua and West Papua excluded (2022 province split). "
             + ("* national deflator (no provincial current-price data). " if FALLBACK else ""),
             fontsize=7.5, color=INK2, va="bottom")
    fig.subplots_adjust(left=0.13, right=0.985, top=0.895, bottom=0.075)
    fig.savefig(path, facecolor=SURF)
    return path


if __name__ == "__main__":
    pd.set_option("display.width", 220, "display.max_rows", 60)
    cols = [f"{k}_{a}_{b}" for a, b in PERIODS for k in ("wage", "grdppc", "gap")]
    print(res.sort_values(f"gap_{BASE}_{LAST}")[cols + ["informal_wage_2018_2025"]].to_string())
    print(f"\n{len(provinces)} provinces")
    print("chart:", chart())
