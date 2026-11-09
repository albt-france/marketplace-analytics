"""Generate the Power BI report pages (PBIR format) for powerbi/MarketplaceCaseStudy.Report.

Usage:  python src/build_report_pages.py
Close Power BI Desktop first (or let it reload): every page is rewritten from this script.
Every number written in titles and text boxes is read from data/output/ (kpis.csv, recommendations.csv,
rfm_segments.csv), so the text always matches the SQL and notebook results.
"""
import csv
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "powerbi" / "MarketplaceCaseStudy.Report"
PAGES = REPORT / "definition" / "pages"
OUT = ROOT / "data" / "output"
VC_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.4.0/schema.json"
PAGE_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json"
PAGES_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json"
M = "_Measures"

# ink & palette (same as the Rovio and PlayersBest case studies)
INK, INK2, MUTED = "#0B0B0B", "#52514E", "#898781"
BLUE, GREY, RED = "#2A78D6", "#B4B2A9", "#D03B3B"
SEQ = {"100": "#CDE2FB", "250": "#86B6EF", "350": "#5598E7", "450": "#2A78D6", "650": "#104281"}
AUTHOR = "Independent Data Analyst case study · Not affiliated with Olist"
SOURCE = "Source: Olist, Brazilian E-Commerce Public Dataset (Kaggle), CC BY-NC-SA 4.0 · orders Jan 2017 - Jul 2018"
TAG = "REAL DATA · Olist public dataset"

# ---------- numbers from the analysis ----------
K = {r["key"]: float(r["value"]) for r in csv.DictReader(open(OUT / "kpis.csv", encoding="utf-8"))}
RECS = list(csv.DictReader(open(OUT / "recommendations.csv", encoding="utf-8")))
SEG = {r["segment"][0]: r for r in csv.DictReader(open(OUT / "rfm_segments.csv", encoding="utf-8"))}
CHECKS = {r["check"]: r["value"] for r in csv.DictReader(open(OUT / "00_data_checks.csv", encoding="utf-8"))}
N_MEASURES = sum(1 for line in open(ROOT / "powerbi" / "MarketplaceCaseStudy.SemanticModel" / "definition" / "tables" / "_Measures.tmdl", encoding="utf-8") if line.startswith("\tmeasure "))
def pct(x, d=0): return f"{x * 100:.{d}f}%"
def n(x): return f"{x:,.0f}"
def n100(x): return f"{round(x, -2):,.0f}"   # forecasts: round to the hundred, false precision otherwise


# ---------- expression helpers ----------
def raw(v):
    if isinstance(v, bool):
        s = "true" if v else "false"
    elif isinstance(v, (int, float)):
        s = f"{v}D"
    else:
        s = "'" + str(v).replace("'", "''") + "'"
    return {"Literal": {"Value": s}}

def lit(v):
    return {"expr": raw(v)}

def color(hexv):
    return {"solid": {"color": lit(hexv)}}

def col(table, column):
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": column}}

def meas(name):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": M}}, "Property": name}}

def agg(table, column, fn=0):  # 0 = Sum, 1 = Avg, 3 = Min, 4 = Max
    return {"Aggregation": {"Expression": col(table, column), "Function": fn}}

AGG_NAME = {0: "Sum", 1: "Avg", 3: "Min", 4: "Max"}
def qref(field):
    if "Column" in field:
        c = field["Column"]
        return f'{c["Expression"]["SourceRef"]["Entity"]}.{c["Property"]}'
    if "Measure" in field:
        return f'{M}.{field["Measure"]["Property"]}'
    a = field["Aggregation"]
    c = a["Expression"]["Column"]
    return f'{AGG_NAME[a["Function"]]}({c["Expression"]["SourceRef"]["Entity"]}.{c["Property"]})'

def proj(field, label=None):
    p = {"field": field, "queryRef": qref(field)}
    if "Column" in field:
        p["nativeQueryRef"] = field["Column"]["Property"]
    elif "Measure" in field:
        p["nativeQueryRef"] = field["Measure"]["Property"]
    else:
        prop = field["Aggregation"]["Expression"]["Column"]["Property"]
        p["nativeQueryRef"] = f"{AGG_NAME[field['Aggregation']['Function']]} of {prop}"
        p["displayName"] = prop
    if label:
        p["displayName"] = label
    return p

def C(t, c): return col(t, c)
def Ms(*names): return [meas(x) for x in names]

def series_color(field, hexv):
    return {"properties": {"fill": color(hexv)}, "selector": {"metadata": qref(field)}}

def ref_line(value, label, hexv=INK2, selector_id="1"):
    return {"properties": {"show": lit(True), "value": lit(value), "displayName": lit(label), "lineColor": color(hexv),
                           "style": lit("dashed"), "dataLabelShow": lit(True), "dataLabelColor": color(hexv)},
            "selector": {"id": selector_id}}

def bg_rule(field, lo, hi):
    """Background-colour scale (conditional formatting) on a table/matrix value column."""
    rule = {"min": {"color": raw(lo)}, "max": {"color": raw(hi)}, "nullColoringStrategy": {"strategy": raw("asZero")}}
    return {"properties": {"backColor": {"solid": {"color": {"expr": {"FillRule": {"Input": field, "FillRule": {"linearGradient2": rule}}}}}}},
            "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": qref(field)}}

def table_style(extra_values=(), matrix=False, size=12):
    """Readable table: 12pt text, no misleading total row/column."""
    fs = {"properties": {"fontSize": lit(size)}}
    objs = {"values": [fs, *extra_values], "columnHeaders": [{"properties": {"fontSize": lit(size), "fontColor": color(INK2)}}]}
    if matrix:
        objs["rowHeaders"] = [fs]
        objs["subTotals"] = [{"properties": {"rowSubtotals": lit(False), "columnSubtotals": lit(False)}}]
    else:
        objs["total"] = [{"properties": {"totals": lit(False)}}]
    return objs

def no_legend(extra=None):
    o = {"legend": [{"properties": {"show": lit(False)}}]}
    o.update(extra or {})
    return o

def labels_on(hexv=INK):
    return [{"properties": {"show": lit(True), "color": color(hexv)}}]


# ---------- page / visual builders ----------
class Page:
    def __init__(self, pid, name):
        self.pid, self.name, self.visuals, self.n = pid, name, [], 0

    def _add(self, vid, x, y, w, h, visual):
        self.n += 1
        self.visuals.append({"$schema": VC_SCHEMA, "name": vid,
                             "position": {"x": round(x), "y": round(y), "z": self.n * 1000,
                                          "height": round(h), "width": round(w), "tabOrder": self.n * 1000},
                             "visual": visual})
        return vid

    def chart(self, vid, vtype, x, y, w, h, roles, title=None, objects=None, labels=None, sort=None, only=None):
        labels = labels or {}
        visual = {"visualType": vtype,
                  "query": {"queryState": {r: {"projections": [proj(f, labels.get(qref(f))) for f in fs]}
                                           for r, fs in roles.items() if fs}},
                  "drillFilterOtherVisuals": True}
        if sort:
            field, direction = sort
            visual["query"]["sortDefinition"] = {"sort": [{"field": field, "direction": direction}], "isDefaultSort": False}
        if objects:
            visual["objects"] = objects
        vco = {"subTitle": [{"properties": {"show": lit(False)}}],
               "title": [{"properties": {"show": lit(bool(title)), **({"text": lit(title)} if title else {})}}]}
        visual["visualContainerObjects"] = vco
        self._add(vid, x, y, w, h, visual)
        if only:  # visual-level filter: (table, column, values, negate)
            table, column, values, negate = only
            vals = [[{"Literal": {"Value": f"{v}L"}}] if isinstance(v, int) else [raw(v)] for v in values]
            cond = {"In": {"Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": column}}],
                           "Values": vals}}
            if negate:
                cond = {"Not": {"Expression": cond}}
            self.visuals[-1]["filterConfig"] = {"filters": [{
                "name": "visualFilter", "type": "Categorical", "field": col(table, column),
                "filter": {"Version": 2, "From": [{"Name": "t", "Entity": table, "Type": 0}],
                           "Where": [{"Condition": cond}]}}]}
        return vid

    def text(self, vid, x, y, w, h, paragraphs, boxed=False):
        """paragraphs: list of paragraphs; each paragraph = list of (text, size, colour, bold) runs."""
        paras = [{"textRuns": [{"value": t, "textStyle": {"fontSize": f"{s}pt", "color": c,
                                                         **({"fontWeight": "bold"} if b else {})}}
                               for t, s, c, b in runs]} for runs in paragraphs]
        visual = {"visualType": "textbox",
                  "objects": {"general": [{"properties": {"paragraphs": paras}}]},
                  "visualContainerObjects": {"background": [{"properties": {"show": lit(boxed)}}],
                                             "border": [{"properties": {"show": lit(boxed)}}],
                                             **({"padding": [{"properties": {"top": lit(16), "bottom": lit(16),
                                                                             "left": lit(20), "right": lit(20)}}]}
                                                if boxed else {})}}
        return self._add(vid, x, y, w, h, visual)

    def header(self, title, conclusion, tag=TAG):
        self.text("title", 32, 20, 1500, 56, [[(title, 24, INK, True)]])
        self.text("tag", 1540, 28, 348, 40, [[(tag.upper(), 10, INK2, True)]])
        self.text("conclusion", 32, 76, 1856, 64, [[(conclusion, 13, INK2, False)]])

    def footer(self, source=SOURCE):
        self.text("footer", 32, 1040, 1856, 32, [[(source + "   ·   " + AUTHOR, 9, MUTED, False)]])

    def card(self, vid, x, y, w, h, measure, title):
        return self.chart(vid, "card", x, y, w, h, {"Values": [meas(measure)]}, title,
                          {"labels": [{"properties": {"color": color(INK), "fontSize": lit(28),
                                                      "labelDisplayUnits": lit(1)}}],   # 1 = no "K"/"M" shortening
                           "categoryLabels": [{"properties": {"show": lit(False)}}]})

    def write(self):
        d = PAGES / self.pid
        if d.exists():
            shutil.rmtree(d)
        (d / "visuals").mkdir(parents=True)
        page = {"$schema": PAGE_SCHEMA, "name": self.pid, "displayName": self.name,
                "displayOption": "FitToPage", "height": 1080, "width": 1920}
        (d / "page.json").write_text(json.dumps(page, indent=2, ensure_ascii=False), encoding="utf-8")
        for v in self.visuals:
            vd = d / "visuals" / v["name"]
            vd.mkdir()
            (vd / "visual.json").write_text(json.dumps(v, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------- layout grid ----------
L, R, TOP, BOT, GAP = 32, 1888, 156, 1028, 24
W = R - L

def cols(k, x0=L, x1=R):
    w = (x1 - x0 - (k - 1) * GAP) / k
    return [(x0 + i * (w + GAP), w) for i in range(k)]

def kpi_row(p, items, y=TOP, h=120, x0=L, x1=R):
    for (x, w), (m, t) in zip(cols(len(items), x0, x1), items):
        p.card("kpi_" + "".join(ch for ch in m if ch.isalnum())[:30], x, y, w, h, m, t)
    return y + h + GAP

pages = []
lapsed_big = SEG["4"]

# ======================= Page 0 - Overview =======================
p = Page("a0f1e2d3c4b5a6978801", "Overview")
p.text("title", 32, 20, 1500, 56, [[("Marketplace analytics: what drives repeat purchases and 5-star reviews", 24, INK, True)]])
p.text("tag", 1540, 28, 348, 40, [[(TAG.upper(), 10, INK2, True)]])
p.text("conclusion", 32, 76, 1856, 64, [[(
    f"Five questions a marketplace analyst answers every week, worked end to end on {n(K['valid_orders'])} real orders "
    "with SQL (DuckDB), Python (tests, regression, RFM, forecast) and Power BI. Nothing is simulated.", 13, INK2, False)]])
heroes = [(f"+{pct(K['gmv_growth_ytd'])}", f"GMV growth, Jan-Jul 2018 vs 2017. Yet {pct(K['one_time_share'])} of customers bought only once.", "REAL · PAGE 1"),
          (f"{K['review_late']:.1f} vs {K['review_on_time']:.1f}", f"stars for late vs on-time deliveries. Late orders are {pct(K['late_share'])} of orders but {pct(K['low_from_late_share'])} of 1-2 star reviews.", "REAL · PAGE 2"),
          (pct(K["sellers_80_share"]), f"of sellers make 80% of GMV. {n(K['fix_sellers'])} big sellers with poor ratings are the ones to fix first.", "REAL · PAGE 3"),
          (f"~{n100(K['forecast_quarter'])}", f"orders forecast for Q4 2018 (+{pct(K['forecast_q4_growth'])} on Q4 2017), with a {pct(K['forecast_mape'])} weekly hold-out error.", "REAL · PAGE 5")]
hh = 250
for i, ((x, w), (num, txt, tg)) in enumerate(zip(cols(4), heroes)):
    p.text(f"hero{i+1}", x, TOP, w, hh, [[(tg, 10, INK2, True)], [(num, 44, INK, True)], [(txt, 15, INK2, False)]], boxed=True)
y2 = TOP + hh + GAP
(x1, w1), (x2, w2) = cols(2)
BH = 420
p.text("questions", x1, y2, w1, BH, [
    [("Business questions", 22, INK, True)],
    [("", 12, INK, False)],
    [("1 · Growth   ", 17, BLUE, True), ("How does the marketplace grow, and do customers come back?", 17, INK, False)],
    [("2 · Delivery   ", 17, BLUE, True), ("Do late deliveries destroy ratings and repurchase?", 17, INK, False)],
    [("3 · Sellers   ", 17, BLUE, True), ("Which sellers carry the marketplace, and which put it at risk?", 17, INK, False)],
    [("4 · Customers   ", 17, BLUE, True), ("Who buys what, and how should customers be segmented?", 17, INK, False)],
    [("5 · Forecast   ", 17, BLUE, True), ("How many orders next quarter, and what should be done first?", 17, INK, False)],
    [("", 12, INK, False)],
    [("Recommendation: ", 17, INK, True),
     (f"cut lateness first (halving it removes ~{pct(K['rec1_avoided_share'])} of all 1-2 star reviews), starting with the "
      f"{n(K['fix_sellers'])} big, poorly rated sellers; run a second-order campaign for one-time buyers; acquire through "
      "the categories that bring customers back.", 17, INK2, False)],
], boxed=True)
p.text("data", x2, y2, w2, BH, [
    [("Data & methods", 22, INK, True)],
    [("", 12, INK, False)],
    [("REAL   ", 14, INK2, True), (f"Olist public dataset, Brazil: {n(float(CHECKS['orders (rows)']))} orders, "
                                   f"{n(float(CHECKS['customer_unique_id (distinct, real customers)']))} customers, "
                                   f"{n(float(CHECKS['sellers']))} sellers, 9 related tables.", 16, INK, False)],
    [("CLEANING   ", 14, INK2, True), ("customer_unique_id as the customer key; cancelled orders out of GMV; window "
                                       "Jan 2017 - Jul 2018 (thin start, data tapers off after 21 Aug 2018).", 16, INK, False)],
    [("SQL   ", 14, INK2, True), ("monthly growth, censoring-safe cohorts, delay vs review, seller scorecard, RFM (DuckDB).", 16, INK, False)],
    [("Python   ", 14, INK2, True), ("Mann-Whitney + bootstrap, logistic regression, Wilson intervals, forecast checked on a 12-week hold-out.", 16, INK, False)],
    [("Power BI   ", 14, INK2, True), (f"star schema (4 facts, 5 dimensions), {N_MEASURES} DAX measures, one question per page.", 16, INK, False)],
], boxed=True)
y3 = y2 + BH + GAP
p.text("howto", L, y3, W, BOT - y3, [
    [("How to read this report", 17, INK, True)],
    [("Titles  ", 14, INK2, True), ("state the conclusion of the page.     ", 15, INK, False),
     ("GMV  ", 14, INK2, True), ("item price + freight, in Brazilian reais (BRL).     ", 15, INK, False),
     ("Blue  ", 14, INK2, True), ("= data, ", 15, INK, False), ("grey  ", 14, INK2, True), ("= comparison, ", 15, INK, False),
     ("red  ", 14, INK2, True), ("= late or below threshold; dashed lines mark thresholds.     ", 15, INK, False),
     ("Code  ", 14, INK2, True), ("SQL, notebook and Power BI project are in the repository.", 15, INK, False)],
], boxed=True)
p.footer()
pages.append(p)

# ======================= Page 1 - Growth & retention =======================
p = Page("b1a2c3d4e5f6a7b8c9d0", "1 · Growth & retention")
p.header(f"GMV grew {pct(K['gmv_growth_ytd'])} in a year, but only {pct(K['repeat_rate'], 1)} of customers ever ordered twice",
         f"{pct(K['new_customer_order_share'])} of orders are a customer's first order, and only {pct(K['retention_m1'], 1)} of a cohort "
         "orders again the following month: growth comes from acquisition, not retention. A cohort is only measured on months it has fully reached.")
y = kpi_row(p, [("GMV (BRL m)", "GMV, BRL million"), ("Order Count", "Valid orders"),
                ("Customer Count", "Customers (customer_unique_id)"), ("Repeat Customer Rate", "Customers with 2+ orders")])
(xa, wa), (xb, wb) = cols(2)
cw = W * 0.56
p.chart("cohortMatrix", "pivotTable", L, y, cw, BOT - y,
        {"Rows": [C("Cohort Retention", "cohort_label")], "Columns": [C("Cohort Retention", "month_n")],
         "Values": [meas("Cohort Retention")]},
        "Cohort retention: share of each monthly cohort ordering again N months after its first order (blank = not reached yet)",
        table_style([bg_rule(meas("Cohort Retention"), "#FCFCFB", SEQ["350"])], matrix=True, size=11),
        labels={"Cohort Retention.cohort_label": "First order", "Cohort Retention.month_n": "Months after"},
        only=("Cohort Retention", "month_n", list(range(1, 13)), False))
rx = L + cw + GAP
rh = (BOT - y - GAP) / 2
p.chart("gmvMonthly", "clusteredColumnChart", rx, y, R - rx, rh,
        {"Category": [C("Date", "Month")], "Y": [meas("GMV (BRL m)")]}, "GMV by month, BRL million",
        no_legend({"dataPoint": [{"properties": {"fill": color(BLUE)}}]}))
ro_ = meas("Repeat Order Share")
p.chart("repeatShare", "clusteredColumnChart", rx, y + rh + GAP, R - rx, rh,
        {"Category": [C("Date", "Month")], "Y": [ro_]},
        f"Share of orders placed by returning customers (dashed = {pct(1 - K['new_customer_order_share'], 1)} overall)",
        no_legend({"dataPoint": [{"properties": {"fill": color(GREY)}}],
                   "valueAxis": [{"properties": {"start": lit(0), "end": lit(0.1)}}],
                   "y1AxisReferenceLine": [ref_line(round(1 - K["new_customer_order_share"], 4), "overall")]}),
        labels={qref(ro_): "Repeat order share"})
p.footer()
pages.append(p)

# ======================= Page 2 - Delivery & satisfaction =======================
p = Page("c2b3d4e5f6a7b8c9d0e1", "2 · Delivery & satisfaction")
p.header(f"A late delivery costs 2 stars: {pct(K['late_share'])} of orders arrive late but cause {pct(K['low_from_late_share'])} of all 1-2 star reviews",
         f"Average review {K['review_on_time']:.1f} on time vs {K['review_late']:.1f} late (Mann-Whitney p < 0.001; bootstrap 95% interval of the gap "
         f"{K['review_gap_ci_low']:.2f}-{K['review_gap_ci_high']:.2f} stars). The effect on repurchase is not detectable "
         f"(odds ratio {K['late_odds_ratio']:.2f}, p = {K['late_odds_p']:.2f}): correlation, not proof of cause.")
y = kpi_row(p, [("Late Rate", "Delivered after the promised date"), ("Avg Review On Time", "Average review, on time"),
                ("Avg Review Late", "Average review, late"), ("Share 1-2 Stars Late", "1-2 star reviews among late orders")])
h = 360
ro, rl = meas("Bucket Avg Review (on time)"), meas("Bucket Avg Review (late)")
p.chart("reviewByDelay", "columnChart", xa, y, wa, h,
        {"Category": [C("Delay Buckets", "delay_bucket")], "Y": [ro, rl]},
        "Average review by delivery delay vs promised date (dashed = 4 stars)",
        {"dataPoint": [series_color(ro, BLUE), series_color(rl, RED)], "labels": labels_on(),
         "legend": [{"properties": {"show": lit(False)}}], "y1AxisReferenceLine": [ref_line(4, "4 stars")],
         "valueAxis": [{"properties": {"start": lit(0), "end": lit(5)}}]},
        labels={qref(ro): "On time or early", qref(rl): "Late"}, sort=(C("Delay Buckets", "delay_bucket"), "Ascending"))
so, sl = meas("Bucket Share 1-2 Stars (on time)"), meas("Bucket Share 1-2 Stars (late)")
p.chart("lowByDelay", "columnChart", xb, y, wb, h,
        {"Category": [C("Delay Buckets", "delay_bucket")], "Y": [so, sl]},
        "Share of 1-2 star reviews by delivery delay",
        {"dataPoint": [series_color(so, BLUE), series_color(sl, RED)], "labels": labels_on(),
         "legend": [{"properties": {"show": lit(False)}}]},
        labels={qref(so): "On time or early", qref(sl): "Late"}, sort=(C("Delay Buckets", "delay_bucket"), "Ascending"))
y += h + GAP
ds = ["metric", "on_time_txt", "late_txt", "gap_txt", "ci_txt", "p_txt"]
p.chart("deliveryTests", "tableEx", xa, y, wa, BOT - y, {"Values": [C("Delivery Stats", c) for c in ds]},
        "Late vs on-time orders: the gap is large and significant", table_style(),
        labels={"Delivery Stats.metric": "Metric", "Delivery Stats.on_time_txt": "On time", "Delivery Stats.late_txt": "Late",
                "Delivery Stats.gap_txt": "Gap", "Delivery Stats.ci_txt": "Bootstrap 95% CI", "Delivery Stats.p_txt": "p-value"})
rm = ["model", "variable", "odds_ratio", "ci_txt", "p_txt"]
p.chart("repurchaseModel", "tableEx", xb, y, wb, BOT - y, {"Values": [C("Repurchase Model", c) for c in rm]},
        "Repurchase within 180 days, logistic regression (odds ratio < 1 = less repurchase; a CI that includes 1 = not significant)",
        table_style(size=11),
        labels={"Repurchase Model.model": "Model", "Repurchase Model.variable": "Variable", "Repurchase Model.odds_ratio": "Odds ratio",
                "Repurchase Model.ci_txt": "95% CI", "Repurchase Model.p_txt": "p-value"})
p.footer()
pages.append(p)

# ======================= Page 3 - Sellers =======================
p = Page("d3c4e5f6a7b8c9d0e1f2", "3 · Sellers")
p.header(f"{pct(K['sellers_80_share'])} of sellers make 80% of GMV; {n(K['fix_sellers'])} big sellers with poor ratings are the ones to fix first",
         f"The top 10% of sellers carry {pct(K['top10_gmv_share'])} of GMV. The {n(K['fix_sellers'])} big sellers rated below 3.8 (with at least "
         f"30 orders) hold {pct(K['fix_gmv_share'], 1)} of GMV and deliver late {pct(K['fix_late_rate'])} of the time, vs {pct(K['late_share'])} overall.")
y = kpi_row(p, [("Active Sellers", "Sellers with sales"), ("Sellers Making 80% of GMV", "Share of sellers making 80% of GMV"),
                ("Fix-first Sellers", "Big sellers rated below 3.8"), ("Fix-first GMV Share", "Their share of GMV")])
h = 400
NO_SALES = ("Sellers", "quadrant", ["No sales in window"], True)
p.chart("pareto", "lineChart", xa, y, wa, h,
        {"Category": [C("Sellers", "seller_percentile")], "Y": [meas("Cumulative GMV Share")]},
        "Cumulative share of GMV by seller rank (Pareto curve, dashed = 80%)",
        no_legend({"dataPoint": [{"properties": {"fill": color(BLUE)}}],
                   "categoryAxis": [{"properties": {"axisType": lit("Scalar")}}],
                   "valueAxis": [{"properties": {"start": lit(0), "end": lit(1)}}],
                   "y1AxisReferenceLine": [ref_line(0.8, "80% of GMV")]}),
        labels={"Sellers.seller_percentile": "Sellers ranked by GMV (share of all sellers)"}, only=NO_SALES)
sx, sy = agg("Sellers", "seller_percentile", 3), agg("Sellers", "avg_review", 1)
p.chart("sellerMap", "scatterChart", xb, y, wb, h,
        {"Category": [C("Sellers", "seller_id")], "Series": [C("Sellers", "quadrant")], "X": [sx], "Y": [sy]},
        "Each seller: rank by GMV (left = biggest, first 10% = 'big') vs average review (dashed = 3.8)",
        {"categoryAxis": [{"properties": {"start": lit(0), "end": lit(1)}}],
         "valueAxis": [{"properties": {"start": lit(1), "end": lit(5)}}],
         "markers": [{"properties": {"markerSize": lit(4)}}],
         "y1AxisReferenceLine": [ref_line(3.8, "3.8")],
         "dataPoint": [{"properties": {"fill": color(RED)}, "selector": {"data": [{"scopeId": {"Comparison": {"ComparisonKind": 0, "Left": col("Sellers", "quadrant"), "Right": raw("Big & poor rating: fix first")}}}]}},
                       {"properties": {"fill": color(BLUE)}, "selector": {"data": [{"scopeId": {"Comparison": {"ComparisonKind": 0, "Left": col("Sellers", "quadrant"), "Right": raw("Big & well rated: protect")}}}]}},
                       {"properties": {"fill": color("#F4A3A2")}, "selector": {"data": [{"scopeId": {"Comparison": {"ComparisonKind": 0, "Left": col("Sellers", "quadrant"), "Right": raw("Small & poor rating: monitor")}}}]}},
                       {"properties": {"fill": color(GREY)}, "selector": {"data": [{"scopeId": {"Comparison": {"ComparisonKind": 0, "Left": col("Sellers", "quadrant"), "Right": raw("Small, well rated or < 30 orders")}}}]}}]},
        labels={qref(sx): "Seller rank by GMV (share of sellers)", qref(sy): "Average review"}, only=NO_SALES)
y += h + GAP
fx = ["seller_id", "state", "orders", "gmv", "late_rate", "avg_review", "share_1_2_stars"]
p.chart("fixList", "tableEx", xa, y, wa, BOT - y, {"Values": [C("Sellers", c) for c in fx]},
        f"Fix first: the {n(K['fix_sellers'])} big sellers (top 10% GMV) rated below 3.8 with 30+ orders, largest first",
        table_style([bg_rule(C("Sellers", "late_rate"), "#FCFCFB", "#F4C7C6")], size=11),
        labels={"Sellers.seller_id": "Seller", "Sellers.state": "State", "Sellers.orders": "Orders", "Sellers.gmv": "GMV (BRL)",
                "Sellers.late_rate": "Late rate", "Sellers.avg_review": "Avg review", "Sellers.share_1_2_stars": "1-2 star share"},
        sort=(C("Sellers", "gmv"), "Descending"), only=("Sellers", "quadrant", ["Big & poor rating: fix first"], False))
qc = ["quadrant", "sellers", "gmv_share", "avg_review", "late_rate"]
p.chart("quadrants", "tableEx", xb, y, wb, BOT - y, {"Values": [C("Seller Quadrants", c) for c in qc]},
        "Seller groups: size and quality (big = top 10% by GMV; poor = average review below 3.8, 30+ orders)",
        table_style([bg_rule(C("Seller Quadrants", "late_rate"), "#FCFCFB", "#F4C7C6")], size=12),
        labels={"Seller Quadrants.quadrant": "Group", "Seller Quadrants.sellers": "Sellers", "Seller Quadrants.gmv_share": "GMV share",
                "Seller Quadrants.avg_review": "Avg seller review", "Seller Quadrants.late_rate": "Avg seller late rate"},
        sort=(C("Seller Quadrants", "quadrant"), "Ascending"))
p.footer()
pages.append(p)

# ======================= Page 4 - Customers & basket =======================
p = Page("e4d5f6a7b8c9d0e1f2a3", "4 · Customers & basket")
p.header(f"Lapsed big spenders are {pct(float(lapsed_big['customer_share']))} of customers but {pct(float(lapsed_big['gmv_share']))} of GMV: the first win-back target",
         f"RFM segments rest on Recency and Monetary quintiles: with {pct(K['one_time_share'])} one-time buyers, Frequency only separates the "
         f"{pct(1 - K['one_time_share'])} who came back. Card installments grow with the basket (Spearman {K['installments_rho']:.2f}): "
         "Brazilian shoppers spread big purchases over months.")
h = 400
cs, gs = meas("Segment Customer Share"), meas("Segment GMV Share")
p.chart("segmentShares", "clusteredBarChart", xa, TOP, wa, h,
        {"Category": [C("RFM Segments", "segment")], "Y": [cs, gs]},
        "RFM segments: share of customers (grey) vs share of GMV (blue)",
        {"dataPoint": [series_color(cs, GREY), series_color(gs, BLUE)], "labels": labels_on()},
        labels={qref(cs): "Customers", qref(gs): "GMV"}, sort=(C("RFM Segments", "segment"), "Ascending"))
ib = meas("Installments by Value Band")
p.chart("installments", "clusteredColumnChart", xb, TOP, wb, h,
        {"Category": [C("Installments", "value_band")], "Y": [ib]},
        f"Average card installments by order value, BRL (overall average {K['avg_installments']:.1f})",
        no_legend({"dataPoint": [{"properties": {"fill": color(BLUE)}}], "labels": labels_on()}),
        labels={qref(ib): "Installments"}, sort=(C("Installments", "value_band"), "Ascending"))
y = TOP + h + GAP
sc = ["segment", "customers", "avg_spend", "avg_recency_days", "top_categories"]
p.chart("segmentTable", "tableEx", xa, y, wa, BOT - y, {"Values": [C("RFM Segments", c) for c in sc]},
        "Segments at 1 Aug 2018 and their top-3 categories by GMV", table_style(size=11),
        labels={"RFM Segments.segment": "Segment", "RFM Segments.customers": "Customers", "RFM Segments.avg_spend": "Avg spend (BRL)",
                "RFM Segments.avg_recency_days": "Days since last order", "RFM Segments.top_categories": "Top categories"},
        sort=(C("RFM Segments", "segment"), "Ascending"))
cr = meas("Category Repurchase Rate")
p.chart("categoryRepurchase", "clusteredBarChart", xb, y, wb, BOT - y,
        {"Category": [C("Category Repurchase", "category")], "Y": [cr]},
        f"Repurchase within 180 days by first-order category (dashed = {pct(K['repurchase_180d'], 1)} average)",
        no_legend({"dataPoint": [{"properties": {"fill": color(BLUE)}}], "labels": labels_on(),
                   "y1AxisReferenceLine": [ref_line(K["repurchase_180d"], "average")]}),
        labels={qref(cr): "Repurchase rate"}, sort=(cr, "Descending"))
p.footer()
pages.append(p)

# ======================= Page 5 - Forecast & recommendations =======================
p = Page("f5e6a7b8c9d0e1f2a3b4", "5 · Forecast & recommendations")
p.header(f"Q4 2018: about {n100(K['forecast_quarter'])} orders expected ({n100(K['forecast_quarter_bf'])} with a Black Friday spike), +{pct(K['forecast_q4_growth'])} on Q4 2017",
         f"Damped Holt trend, checked on 12 hidden weeks: {pct(K['forecast_mape'])} average weekly error, no better than a naive forecast "
         f"({pct(K['forecast_naive_mape'])}), so plan on the 80% band ({n100(K['forecast_quarter_low'])}-{n100(K['forecast_quarter_high'])} orders) rather than the point. "
         f"Black Friday 2017 was x{K['bf_uplift']:.1f} a normal week.")
h = 430
fa, fh, ff, fb, fl, fu = Ms("Weekly Orders", "Hold-out Prediction", "Forecast Orders", "Forecast with Black Friday",
                            "Forecast Low (80%)", "Forecast High (80%)")
p.chart("forecastLine", "lineChart", L, TOP, W, h,
        {"Category": [C("Forecast Weekly", "week")], "Y": [fa, fh, ff, fb, fl, fu]},
        "Weekly orders: actual (black), hold-out check and forecast (blue), Black Friday scenario (grey), 80% band (light blue)",
        {"dataPoint": [series_color(fa, INK), series_color(fh, SEQ["350"]), series_color(ff, BLUE),
                       series_color(fb, GREY), series_color(fl, SEQ["100"]), series_color(fu, SEQ["100"])],
         "valueAxis": [{"properties": {"start": lit(0)}}]},
        labels={qref(fa): "Actual", qref(fh): "Hold-out prediction", qref(ff): "Forecast", qref(fb): "With Black Friday",
                qref(fl): "Low (80%)", qref(fu): "High (80%)"})
y = TOP + h + GAP
tw = 420
p.chart("models", "tableEx", L, y, tw, BOT - y,
        {"Values": [C("Forecast Models", "model"), agg("Forecast Models", "mape", 3), agg("Forecast Models", "mae", 3)]},
        "Hold-out error by model (last 12 weeks)", table_style(size=11),
        labels={"Forecast Models.model": "Model", "Min(Forecast Models.mape)": "MAPE", "Min(Forecast Models.mae)": "MAE (orders)"},
        sort=(agg("Forecast Models", "mape", 3), "Ascending"))
for (x, w), r in zip(cols(3, L + tw + GAP, R), RECS):
    p.text(f"rec{r['rank']}", x, y, w, BOT - y, [
        [(f"RECOMMENDATION {r['rank']}", 10, BLUE, True)],
        [(r["action"], 17, INK, True)],
        [("", 6, INK, False)],
        [(r["evidence"], 13, INK2, False)],
        [("", 6, INK, False)],
        [("Size  ", 12, INK2, True), (r["size"], 13, INK, False)],
    ], boxed=True)
p.footer()
pages.append(p)

# ---------- write ----------
PAGES.mkdir(parents=True, exist_ok=True)
keep = {pg.pid for pg in pages}
for d in PAGES.iterdir():
    if d.is_dir() and d.name not in keep:
        shutil.rmtree(d)
for pg in pages:
    pg.write()
meta = {"$schema": PAGES_SCHEMA, "pageOrder": [pg.pid for pg in pages], "activePageName": pages[0].pid}
(PAGES / "pages.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
print("pages:", [(pg.name.encode("ascii", "replace").decode(), len(pg.visuals)) for pg in pages])
