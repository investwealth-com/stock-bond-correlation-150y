#!/usr/bin/env python3
"""Stock-bond correlation over 150 years (US, monthly, 1871-2026).

Builds monthly total returns of US stocks and of a constant-maturity 10-year US Treasury bond,
the rolling 36-month correlation between them, the key facts and the chart.
Method and sources are those of InvestWealth White Paper No. 1, "When Bonds Stop Hedging" (v1.2).

Usage (from the repository root):
  python scripts/correlation.py                  # recompute correlation, facts and chart from data/*.csv
  python scripts/correlation.py --download       # fetch the raw inputs into raw/ (DataHub, FRED, Yahoo Finance)
  python scripts/correlation.py --rebuild        # rebuild data/*.csv from raw/, then facts and chart
Requires: pandas, numpy, matplotlib (and yfinance + requests for --download).
"""
import argparse, io
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'raw'
CSV = ROOT / 'data' / 'us_stock_bond_monthly_1871_2026.csv'
PNG = ROOT / 'charts' / 'correlation_150y.png'
END = '2026-09-30'          # last month of White Paper No. 1 v1.2
WIN = 36                    # correlation window in months
ME = lambda s: s.resample('ME').last()

SOURCES = {   # raw file -> where it comes from
    'shiller_sp500.csv': 'https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv',   # DataHub.io copy of Shiller's data
    'GS10.csv': 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=GS10',
    'DGS10.csv': 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10',
    'GSPC.csv': 'Yahoo Finance ^GSPC, daily close',
    'SPY.csv': 'Yahoo Finance SPY, daily close adjusted for dividends and splits',
}


# ---------------------------------------------------------------- raw inputs
def download():
    import requests
    RAW.mkdir(exist_ok=True)
    for f in ('shiller_sp500.csv', 'GS10.csv', 'DGS10.csv'):
        r = requests.get(SOURCES[f], timeout=60, headers={'User-Agent': 'Mozilla/5.0'}); r.raise_for_status()
        (RAW / f).write_text(r.text); print('saved', f)
    import yfinance as yf
    for sym, f, col in (('^GSPC', 'GSPC.csv', 'close'), ('SPY', 'SPY.csv', 'adj_close')):
        px = yf.download(sym, start='1970-01-01', auto_adjust=True, progress=False)['Close']
        px = px.iloc[:, 0] if isinstance(px, pd.DataFrame) else px
        px.rename(col).rename_axis('date').to_csv(RAW / f); print('saved', f)


def fred(name):
    """FRED csv (date, value); '.' = missing."""
    s = pd.read_csv(RAW / f'{name}.csv', na_values=['.'])
    s = pd.Series(pd.to_numeric(s.iloc[:, 1], errors='coerce').values, index=pd.to_datetime(s.iloc[:, 0]))
    return s.dropna()


def yahoo(f, col):
    s = pd.read_csv(RAW / f, parse_dates=['date']).set_index('date')[col]
    return pd.to_numeric(s, errors='coerce').dropna()


# ---------------------------------------------------------------- method of White Paper No. 1
def bond_ret(y):
    """Monthly total return of a constant-maturity 10-year par bond from month-end yields (decimal).
    Buy a par bond at the start of the month (coupon = last month's yield, paid semi-annually),
    sell it at the end of the month at the new yield with 10 years minus 1 month left; add one month of coupon."""
    y = y.dropna(); c = y.shift(1); n = 10 - 1 / 12; k = (1 + y / 2) ** (-2 * n)
    price = c / y * (1 - k) + k
    return price - 1 + c / 12


def build():
    sh = pd.read_csv(RAW / 'shiller_sp500.csv', parse_dates=['Date']).set_index('Date')
    sh.index = sh.index + pd.offsets.MonthEnd(0)
    sh = sh[sh['Consumer Price Index'] > 0]                                  # drops the latest rows without dividends / CPI
    P, Dv = sh['SP500'], sh['Dividend']
    # stocks 1871-1969: Shiller monthly average price + 1/12 of the annual dividend
    st_sh = (P + Dv / 12) / P.shift(1) - 1
    # stocks Feb 1970 - Feb 1993: S&P 500 month-end close + 1/12 of the Shiller dividend yield (carried forward)
    gspc = ME(yahoo('GSPC.csv', 'close'))
    dy = (Dv / P).reindex(gspc.index).ffill()
    st_ix = gspc.pct_change() + dy.shift(1) / 12
    # stocks from Mar 1993: SPY ETF, adjusted close (total return after the ETF's costs)
    spy = ME(yahoo('SPY.csv', 'adj_close')).pct_change()
    # splice: Jan 1970 = S&P 500 month-end Jan 1970 (+ dividend) over the Shiller Dec 1969 average
    jan70 = pd.Series({pd.Timestamp('1970-01-31'): (gspc.loc['1970-01-31'] + Dv.loc['1970-01-31'] / 12) / P.loc['1969-12-31'] - 1})
    stocks = pd.concat([st_sh[st_sh.index <= '1969-12-31'], jan70,
                        st_ix[(st_ix.index >= '1970-02-28') & (st_ix.index < '1993-03-31')], spy[spy.index >= '1993-03-31']])
    src_s = pd.Series('shiller_monthly_avg', index=stocks.index)
    src_s[stocks.index == '1970-01-31'] = 'splice_jan1970'
    src_s[(stocks.index >= '1970-02-28') & (stocks.index < '1993-03-31')] = 'sp500_month_end_plus_div_yield'
    src_s[stocks.index >= '1993-03-31'] = 'spy_adjusted'
    # 10-year yield: Shiller long rate to Mar 1953, FRED GS10 (monthly average) to Dec 1961,
    # FRED DGS10 from Jan 1962 sampled weekly (Friday) and taking the last week of each month, as in the paper
    lr = sh['Long Interest Rate'] / 100
    gs10 = fred('GS10') / 100; gs10.index = gs10.index + pd.offsets.MonthEnd(0)
    d = fred('DGS10').replace(0, np.nan).dropna() / 100
    if d.index.to_series().diff().dt.days.median() < 5:                     # daily file from FRED -> weekly, ending Friday
        d = d.resample('W-FRI').last().dropna()
    dgs10 = ME(d)
    y10 = pd.concat([lr[lr.index < '1953-04-30'], gs10[(gs10.index >= '1953-04-30') & (gs10.index < '1962-01-31')], dgs10[dgs10.index >= '1962-01-31']])
    src_y = pd.Series('shiller_long_rate', index=y10.index)
    src_y[(y10.index >= '1953-04-30') & (y10.index < '1962-01-31')] = 'fred_gs10'
    src_y[y10.index >= '1962-01-31'] = 'fred_dgs10_weekly'
    bonds = bond_ret(y10)
    df = pd.DataFrame({'stock_tr': stocks, 'bond10_tr': bonds, 'yield10': y10, 'stock_source': src_s, 'yield_source': src_y})
    df = df[df.index <= END]
    df['corr36'] = df.stock_tr.rolling(WIN).corr(df.bond10_tr)
    return df


def write_csv(df):
    out = df.copy(); out.index = out.index.strftime('%Y-%m'); out.index.name = 'month'
    out = out[['stock_tr', 'bond10_tr', 'yield10', 'corr36', 'stock_source', 'yield_source']]
    CSV.parent.mkdir(exist_ok=True)
    out.to_csv(CSV, float_format='%.8f'); print('wrote', CSV.relative_to(ROOT), len(out), 'rows')


def read_csv():
    df = pd.read_csv(CSV, index_col='month')
    df.index = pd.to_datetime(df.index) + pd.offsets.MonthEnd(0)
    c = df.stock_tr.rolling(WIN).corr(df.bond10_tr)
    gap = (c - df.corr36).abs().max()
    print(f'check: corr36 recomputed from the return columns, max difference {gap:.1e}')
    df['corr36'] = c
    return df


# ---------------------------------------------------------------- facts
def facts(df):
    c = df.corr36.dropna(); x = c['1875':]
    share = lambda s, pos=True: 100 * ((s > 0) if pos else (s < 0)).mean()
    mx, mn = x.idxmax(), x.idxmin()
    f = {
        'months with a correlation (Jan 1875 - Sep 2026)': len(x),
        'share of months positive since 1875': f'{share(x):.1f}%',
        'share positive 1875-1965': f"{share(x['1875':'1965']):.1f}%",
        'share positive 1966-1999': f"{share(x['1966':'1999']):.1f}%",
        'share negative 2000-2020': f"{share(x['2000':'2020'], False):.1f}%",
        'share positive 2021-2026': f"{share(x['2021':]):.1f}%",
        'highest reading': f'{x.max():+.2f} ({mx:%b %Y})',
        'lowest reading': f'{x.min():+.2f} ({mn:%b %Y})',
        'latest reading': f'{x.iloc[-1]:+.2f} ({x.index[-1]:%b %Y})',
    }
    for k, v in f.items(): print(f'  {k:52s} {v}')
    return f


# ---------------------------------------------------------------- chart (dark, 1600x900 at 2x)
def chart(df, logo=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt, matplotlib.dates as mdates
    BG, GRID, INK, MUTED, DIM = '#0b0e14', '#1f2630', '#e6edf3', '#8b949e', '#6e7681'
    GREEN, BLUE, RED, AMBER = '#00ff84', '#58a6ff', '#f85149', '#e3b341'
    import os
    from matplotlib import font_manager
    for f in Path(os.environ.get('IW_FONTS', ROOT / 'fonts')).glob('*.ttf'):   # optional: DM Sans .ttf files, else DejaVu Sans
        font_manager.fontManager.addfont(str(f))
    plt.rcParams.update({'font.family': ['DM Sans', 'DM Sans 9pt', 'DejaVu Sans'], 'font.size': 13})
    x = df.corr36.dropna()['1875':]
    fig = plt.figure(figsize=(16, 9), dpi=200); fig.patch.set_facecolor(BG)
    fig.text(0.05, 0.915, 'InvestWealth.com', color=GREEN, fontsize=17, fontweight='bold')
    if logo and Path(logo).exists():
        im = plt.imread(logo); ax_l = fig.add_axes([0.05, 0.895, 0.20, 0.045]); ax_l.imshow(im); ax_l.axis('off'); fig.texts[-1].set_visible(False)
    fig.text(0.05, 0.855, '150 YEARS OF US DATA', color=GREEN, fontsize=13)
    fig.text(0.05, 0.795, "When bonds hedge stocks, and when they don't: 150 years of data", color=INK, fontsize=29, fontweight='bold')
    fig.text(0.05, 0.75, f'Rolling 36-month correlation of monthly total returns, US stocks vs 10-year US Treasuries, {x.index[0]:%b %Y} – {x.index[-1]:%b %Y}',
             color=MUTED, fontsize=14)
    ax = fig.add_axes([0.06, 0.16, 0.90, 0.54]); ax.set_facecolor(BG)
    for a, b, lab, col, tint in (('1966-01-01', '1999-12-31', '1966–1999\npositive in {:.0f}% of months', RED, '#1a1418'),
                                 ('2000-01-01', '2020-12-31', '2000–2020\nnegative in {:.0f}% of months', BLUE, '#121a26')):
        s = x[a[:4]:b[:4]]; v = 100 * ((s > 0) if col == RED else (s < 0)).mean()
        ax.axvspan(pd.Timestamp(a), pd.Timestamp(b), color=tint, zorder=0, lw=0)
        ax.text(pd.Timestamp(a) + (pd.Timestamp(b) - pd.Timestamp(a)) / 2, -0.78, lab.format(v), color=col, fontsize=12, ha='center', va='center', linespacing=1.4)
    ax.fill_between(x.index, x, 0, where=x >= 0, color=RED, alpha=.28, lw=0, interpolate=True)
    ax.fill_between(x.index, x, 0, where=x < 0, color=BLUE, alpha=.28, lw=0, interpolate=True)
    ax.plot(x.index, x, color=AMBER, lw=1.4)
    ax.axhline(0, color=MUTED, lw=1)
    mx = x.idxmax()
    ax.scatter([mx], [x.max()], color=INK, s=28, zorder=5)
    ax.annotate(f'{mx:%b %Y}: {x.max():+.2f}, highest in the sample', (mx, x.max()), xytext=(-330, 4), textcoords='offset points',
                color=INK, fontsize=12, va='center', arrowprops=dict(arrowstyle='-', color=MUTED, lw=.8))
    ax.scatter([x.index[-1]], [x.iloc[-1]], color=AMBER, s=28, zorder=5)
    ax.annotate(f'{x.index[-1]:%b %Y}: {x.iloc[-1]:+.2f}', (x.index[-1], x.iloc[-1]), xytext=(-150, -34), textcoords='offset points', color=AMBER, fontsize=12,
                arrowprops=dict(arrowstyle='-', color=AMBER, lw=.8))
    ax.text(x.index[6], -0.9, 'Above 0: bonds tended to fall with stocks.  Below 0: bonds tended to rise when stocks fell.', color=MUTED, fontsize=12)
    ax.set_ylim(-1, 1); ax.set_yticks([-1, -.5, 0, .5, 1]); ax.set_xlim(x.index[0], x.index[-1] + pd.Timedelta(days=200))
    ax.xaxis.set_major_locator(mdates.YearLocator(25, month=1, day=1)); ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
    for s in ax.spines.values(): s.set_visible(False)
    ax.tick_params(colors=MUTED, length=0, labelsize=13); ax.grid(axis='y', color=GRID, lw=.8); ax.set_axisbelow(True)
    fig.text(0.05, 0.075, 'Source: InvestWealth calculations, method of White Paper No. 1 (v1.2). Data: Robert Shiller (via DataHub.io), FRED, Yahoo Finance. '
             'Before 1970 stock returns use monthly average prices.', color=DIM, fontsize=11.5)
    fig.text(0.05, 0.045, 'Past returns do not guarantee future results. Educational research, not investment advice.  investwealth.com/learn/guides/stock-bond-correlation',
             color=DIM, fontsize=11.5)
    PNG.parent.mkdir(exist_ok=True)
    fig.savefig(PNG, facecolor=BG); plt.close(fig); print('wrote', PNG.relative_to(ROOT))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--download', action='store_true', help='fetch raw inputs into raw/')
    ap.add_argument('--rebuild', action='store_true', help='rebuild the data CSV from raw/')
    ap.add_argument('--raw', help='folder with the raw inputs (default: raw/)')
    ap.add_argument('--logo', help='optional logo image for the chart header')
    a = ap.parse_args()
    if a.raw: RAW = Path(a.raw)
    if a.download: download()
    if a.rebuild or a.download:
        write_csv(build())
    df = read_csv()
    print('key facts:'); facts(df)
    chart(df, a.logo)
