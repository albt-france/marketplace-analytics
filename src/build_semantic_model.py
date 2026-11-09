"""Generate the Power BI semantic model (TMDL) for powerbi/MarketplaceCaseStudy.SemanticModel.

Usage:  python src/build_semantic_model.py
Star schema:
  facts       Orders (order grain), OrderItems, Payments, Reviews
  dimensions  Customers (customer_unique_id, with RFM segment), Sellers (with scorecard), Products, Date, Geo (state)
  analysis    outputs of sql/ and the notebook (cohorts, delay buckets, RFM summary, forecast, tests)
Close Power BI Desktop before running: the definition folder is rewritten.
"""
import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "powerbi" / "MarketplaceCaseStudy.SemanticModel"
DEF = MODEL / "definition"
DATA_FOLDER = str(ROOT / "data") + "\\"

NS = uuid.UUID("5b1f0a52-7c1e-4f0e-9a51-0d6f2c3b8e11")
def tag(*parts):  # stable lineage tags: same name -> same tag on every rebuild
    return str(uuid.uuid5(NS, "/".join(parts)))

# type -> (TMDL dataType, Power Query type, format string)
TYPES = {
    "text": ("string", "type text", None),
    "int": ("int64", "Int64.Type", "0"),
    "num": ("double", "type number", None),
    "date": ("dateTime", "type date", "dd mmm yyyy"),
    "bool": ("boolean", "type logical", None),
}

def pq_types(cols):
    return "{" + ", ".join(f'{{"{c}", {TYPES[t][1]}}}' for c, t, *_ in cols) + "}"

def parquet_source(file, cols, extra_steps=(), select=None):
    names = "{" + ", ".join(f'"{c}"' for c, *_ in (select or cols)) + "}"
    steps = [f'Source = Parquet.Document(File.Contents(DataFolder & "clean\\{file}"))',
             f"Selected = Table.SelectColumns(Source, {names})", *extra_steps]
    last = steps[-1].split(" = ")[0]
    steps.append(f"Typed = Table.TransformColumnTypes({last}, {pq_types(cols)})")
    return steps

def csv_source(file, cols, extra_steps=()):
    steps = [f'Source = Csv.Document(File.Contents(DataFolder & "output\\{file}"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv])',
             "Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true])",
             f'Typed = Table.TransformColumnTypes(Promoted, {pq_types(cols)}, "en-US")', *extra_steps]
    return steps

def keep_window_orders(prev):
    """Keep only rows whose order is in the Orders table (analysis window): inner hash join on order_id."""
    return [f'OrderKeys = Table.RenameColumns(Table.SelectColumns(Orders, {{"order_id"}}), {{{{"order_id", "k"}}}})',
            f'Joined = Table.Join({prev}, "order_id", OrderKeys, "k", JoinKind.Inner)',
            'Kept = Table.RemoveColumns(Joined, {"k"})']

# ---------------- tables ----------------
# column spec: (name, type, [options dict])
T = {}

T["Orders"] = dict(cols=[
    ("order_id", "text"), ("customer_unique_id", "text"), ("customer_state", "text"), ("order_status", "text"),
    ("purchase_date", "date"), ("is_valid", "bool"), ("items", "int"), ("item_value", "num", {"fmt": "#,0.00"}),
    ("freight", "num", {"fmt": "#,0.00"}), ("gmv", "num", {"fmt": "#,0.00"}), ("paid", "num", {"fmt": "#,0.00"}),
    ("main_payment_type", "text"), ("card_installments", "int"), ("delivered_date", "date"), ("estimated_date", "date"),
    ("delivery_days", "int"), ("delay_days", "int"), ("review_score", "int"), ("order_seq", "int")],
    source=lambda c: parquet_source("orders.parquet", c,
                                    ["Window = Table.SelectRows(Selected, each [in_window] = true)",
                                     'Trimmed = Table.RemoveColumns(Window, {"in_window"})'],
                                    select=c + [("in_window", "bool")]),
    note="One row per order placed Jan 2017 - Jul 2018 (all statuses; measures keep valid orders only)")

T["OrderItems"] = dict(cols=[
    ("order_id", "text"), ("order_item_id", "int"), ("product_id", "text"), ("seller_id", "text"),
    ("price", "num", {"fmt": "#,0.00"}), ("freight", "num", {"fmt": "#,0.00"}), ("gmv", "num", {"fmt": "#,0.00"})],
    source=lambda c: parquet_source("order_items.parquet", c, keep_window_orders("Selected")))

T["Payments"] = dict(cols=[
    ("order_id", "text"), ("payment_sequential", "int"), ("payment_type", "text"), ("installments", "int"),
    ("payment_value", "num", {"fmt": "#,0.00"})],
    source=lambda c: parquet_source("payments.parquet", c, keep_window_orders("Selected")))

T["Reviews"] = dict(cols=[
    ("review_id", "text"), ("order_id", "text"), ("review_score", "int"), ("review_date", "date"), ("has_comment", "bool")],
    source=lambda c: parquet_source("reviews.parquet", c, keep_window_orders("Selected")))

T["Customers"] = dict(cols=[
    ("customer_unique_id", "text", {"key": True}), ("state", "text"), ("first_purchase_date", "date"),
    ("cohort_month", "date", {"fmt": "mmm yyyy"}), ("segment", "text"), ("r_score", "int"), ("m_score", "int")],
    source=lambda c: [
        'Source = Parquet.Document(File.Contents(DataFolder & "clean\\customers.parquet"))',
        'Base = Table.SelectColumns(Source, {"customer_unique_id", "state", "first_purchase_date", "cohort_month"})',
        'Rfm = Table.TransformColumnTypes(Table.PromoteHeaders(Csv.Document(File.Contents(DataFolder & "output\\05_rfm.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]), [PromoteAllScalars=true]), {{"r_score", Int64.Type}, {"m_score", Int64.Type}}, "en-US")',
        'RfmCols = Table.SelectColumns(Rfm, {"customer_unique_id", "segment", "r_score", "m_score"})',
        'Merged = Table.NestedJoin(Base, {"customer_unique_id"}, RfmCols, {"customer_unique_id"}, "rfm", JoinKind.LeftOuter)',
        'Expanded = Table.ExpandTableColumn(Merged, "rfm", {"segment", "r_score", "m_score"})',
        'Filled = Table.ReplaceValue(Expanded, null, "6 · First order after snapshot", Replacer.ReplaceValue, {"segment"})',
        f"Typed = Table.TransformColumnTypes(Filled, {pq_types(c)})"],
    note="Real customer key: customer_unique_id (customer_id changes with every order)")

SCORE = [("orders", "int", {"fmt": "#,0"}), ("gmv", "num", {"fmt": "#,0"}), ("gmv_rank", "int"), ("seller_percentile", "num", {"fmt": "0.0%"}),
         ("cum_gmv_share", "num", {"fmt": "0.0%"}), ("avg_delay_days", "num", {"fmt": "0.0"}), ("late_rate", "num", {"fmt": "0.0%"}),
         ("avg_review", "num", {"fmt": "0.00"}), ("share_1_2_stars", "num", {"fmt": "0.0%"}), ("cancel_rate", "num", {"fmt": "0.0%"}),
         ("quadrant", "text")]
T["Sellers"] = dict(cols=[("seller_id", "text", {"key": True}), ("state", "text"), ("city", "text")] + SCORE,
    source=lambda c: [
        'Source = Parquet.Document(File.Contents(DataFolder & "clean\\sellers.parquet"))',
        'Card = Table.PromoteHeaders(Csv.Document(File.Contents(DataFolder & "output\\04_seller_scorecard.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]), [PromoteAllScalars=true])',
        f'CardCols = Table.SelectColumns(Card, {{"seller_id", {", ".join(chr(34) + s[0] + chr(34) for s in SCORE)}}})',
        'Merged = Table.NestedJoin(Source, {"seller_id"}, CardCols, {"seller_id"}, "card", JoinKind.LeftOuter)',
        f'Expanded = Table.ExpandTableColumn(Merged, "card", {{{", ".join(chr(34) + s[0] + chr(34) for s in SCORE)}}})',
        'Filled = Table.ReplaceValue(Expanded, null, "No sales in window", Replacer.ReplaceValue, {"quadrant"})',
        f'Typed = Table.TransformColumnTypes(Filled, {pq_types(c)}, "en-US")'],
    note="Seller scorecard (sql/04_seller_scorecard.sql) merged into the seller dimension")

T["Products"] = dict(cols=[("product_id", "text", {"key": True}), ("category", "text"), ("weight_g", "int"), ("photos_qty", "int")],
    source=lambda c: parquet_source("products.parquet", c))

T["Date"] = dict(cols=[("Date", "date", {"key": True}), ("Month", "text", {"sort": "MonthKey"}), ("MonthKey", "int", {"hidden": True}),
                       ("MonthStart", "date", {"fmt": "mmm yyyy"}), ("Year", "int"), ("WeekStart", "date")],
    source=lambda c: [
        "Dates = List.Dates(#date(2017, 1, 1), Duration.Days(#date(2018, 12, 31) - #date(2017, 1, 1)) + 1, #duration(1, 0, 0, 0))",
        "T = Table.FromList(Dates, Splitter.SplitByNothing(), type table [Date = date])",
        'M = Table.AddColumn(T, "Month", each Date.ToText([Date], "MMM yyyy", "en-US"), type text)',
        'K = Table.AddColumn(M, "MonthKey", each Date.Year([Date]) * 100 + Date.Month([Date]), Int64.Type)',
        'S = Table.AddColumn(K, "MonthStart", each Date.StartOfMonth([Date]), type date)',
        'Y = Table.AddColumn(S, "Year", each Date.Year([Date]), Int64.Type)',
        'Typed = Table.AddColumn(Y, "WeekStart", each Date.StartOfWeek([Date], Day.Monday), type date)'],
    data_category="Time")

STATES = [("AC", "Acre", "North"), ("AL", "Alagoas", "Northeast"), ("AP", "Amapá", "North"), ("AM", "Amazonas", "North"),
          ("BA", "Bahia", "Northeast"), ("CE", "Ceará", "Northeast"), ("DF", "Distrito Federal", "Center-West"),
          ("ES", "Espírito Santo", "Southeast"), ("GO", "Goiás", "Center-West"), ("MA", "Maranhão", "Northeast"),
          ("MT", "Mato Grosso", "Center-West"), ("MS", "Mato Grosso do Sul", "Center-West"), ("MG", "Minas Gerais", "Southeast"),
          ("PA", "Pará", "North"), ("PB", "Paraíba", "Northeast"), ("PR", "Paraná", "South"), ("PE", "Pernambuco", "Northeast"),
          ("PI", "Piauí", "Northeast"), ("RJ", "Rio de Janeiro", "Southeast"), ("RN", "Rio Grande do Norte", "Northeast"),
          ("RS", "Rio Grande do Sul", "South"), ("RO", "Rondônia", "North"), ("RR", "Roraima", "North"),
          ("SC", "Santa Catarina", "South"), ("SP", "São Paulo", "Southeast"), ("SE", "Sergipe", "Northeast"), ("TO", "Tocantins", "North")]
T["Geo"] = dict(cols=[("state", "text", {"key": True}), ("state_name", "text"), ("region", "text")],
    source=lambda c: ["Typed = #table(type table [state = text, state_name = text, region = text], {"
                      + ", ".join(f'{{"{a}", "{b}", "{r}"}}' for a, b, r in STATES) + "})"])

# ----- analysis outputs (data/output) -----
T["Cohort Retention"] = dict(cols=[("cohort_month", "date", {"fmt": "mmm yyyy", "hidden": True}), ("cohort_label", "text", {"sort": "cohort_month"}),
                                   ("customers", "int", {"fmt": "#,0"}), ("month_n", "int"),
                                   ("active_customers", "int", {"fmt": "#,0"}), ("retention", "num", {"fmt": "0.00%"})],
                             source=lambda c: csv_source("02_cohort_retention.csv", [x for x in c if x[0] != "cohort_label"],
                                                         ['Labeled = Table.AddColumn(Typed, "cohort_label", each Date.ToText([cohort_month], "MMM yyyy", "en-US"), type text)']))
T["Delay Buckets"] = dict(cols=[("bucket_order", "int", {"hidden": True}), ("delay_bucket", "text", {"sort": "bucket_order"}),
                                ("is_late", "bool"), ("orders", "int"), ("share_of_orders", "num", {"fmt": "0.0%"}),
                                ("avg_review", "num", {"fmt": "0.00"}), ("share_1_2_stars", "num", {"fmt": "0%"}),
                                ("share_5_stars", "num", {"fmt": "0%"})],
                          source=lambda c: csv_source("03_delivery_vs_review.csv", c))
T["Delivery Stats"] = dict(cols=[("metric", "text"), ("on_time", "num", {"fmt": "0.00"}), ("late", "num", {"fmt": "0.00"}),
                                 ("gap", "num", {"fmt": "0.00"}), ("ci_low", "num", {"fmt": "0.00"}), ("ci_high", "num", {"fmt": "0.00"}),
                                 ("p_value", "num", {"fmt": "0.0E+00"}), ("orders_on_time", "int"), ("orders_late", "int"),
                                 ("on_time_txt", "text"), ("late_txt", "text"), ("gap_txt", "text"), ("ci_txt", "text"), ("p_txt", "text")],
                           source=lambda c: csv_source("delivery_stats.csv", c))
T["Repurchase Model"] = dict(cols=[("variable", "text"), ("model", "text"), ("odds_ratio", "num", {"fmt": "0.00"}),
                                   ("ci_low", "num", {"fmt": "0.00"}), ("ci_high", "num", {"fmt": "0.00"}), ("p_value", "num", {"fmt": "0.000"}),
                                   ("ci_txt", "text"), ("p_txt", "text")],
                             source=lambda c: csv_source("repurchase_logit.csv", c))
T["RFM Segments"] = dict(cols=[("segment", "text"), ("customers", "int", {"fmt": "#,0"}), ("gmv", "num", {"fmt": "#,0"}), ("avg_spend", "num", {"fmt": "#,0"}),
                               ("avg_recency_days", "num", {"fmt": "0"}), ("customer_share", "num", {"fmt": "0%"}),
                               ("gmv_share", "num", {"fmt": "0%"}), ("top_categories", "text")],
                         source=lambda c: csv_source("rfm_segments.csv", c))
T["Installments"] = dict(cols=[("value_band", "text", {"sort": "band_order"}), ("orders", "int", {"fmt": "#,0"}), ("avg_installments", "num", {"fmt": "0.0"}),
                               ("share_one_payment", "num", {"fmt": "0%"}), ("band_order", "int", {"hidden": True})],
                         source=lambda c: csv_source("installments_by_value.csv", c))
T["Category Repurchase"] = dict(cols=[("category", "text"), ("customers", "int", {"fmt": "#,0"}), ("repurchasers", "int", {"fmt": "#,0"}),
                                      ("repurchase_rate", "num", {"fmt": "0.0%"}), ("ci_low", "num", {"fmt": "0.0%"}), ("ci_high", "num", {"fmt": "0.0%"})],
                                source=lambda c: csv_source("category_repurchase.csv", c))
T["Forecast Weekly"] = dict(cols=[("week", "date"), ("actual", "num", {"fmt": "#,0"}), ("split", "text"), ("forecast", "num", {"fmt": "#,0"}),
                                  ("forecast_bf", "num", {"fmt": "#,0"}), ("low", "num", {"fmt": "#,0"}), ("high", "num", {"fmt": "#,0"}),
                                  ("holdout_pred", "num", {"fmt": "#,0"})],
                            source=lambda c: csv_source("forecast_weekly.csv", c))
T["Seller Quadrants"] = dict(cols=[("quadrant", "text"), ("sellers", "int", {"fmt": "#,0"}), ("gmv", "num", {"fmt": "#,0"}),
                                    ("orders", "int", {"fmt": "#,0"}), ("avg_review", "num", {"fmt": "0.00"}),
                                    ("late_rate", "num", {"fmt": "0.0%"}), ("gmv_share", "num", {"fmt": "0.0%"})],
                              source=lambda c: csv_source("seller_quadrants.csv", c))
T["Forecast Models"] = dict(cols=[("model", "text"), ("mae", "num", {"fmt": "#,0"}), ("mape", "num", {"fmt": "0.0%"})],
                            source=lambda c: csv_source("forecast_models.csv", c))

# ---------------- measures ----------------
V = "Orders[is_valid] = TRUE ()"
# Note: in DAX, BLANK() <= 2 and BLANK() < 0.8 are TRUE (blank compares as 0), so filters on optional columns
# (review_score, cum_gmv_share) also exclude blanks explicitly.
MEASURES = [
    # name, expression, format, folder
    ("Order Count", f"CALCULATE ( COUNTROWS ( Orders ), {V} )", "#,0", "1 Sales"),
    ("GMV", f"CALCULATE ( SUM ( Orders[gmv] ), {V} )", "#,0", "1 Sales"),
    ("GMV (BRL m)", "DIVIDE ( [GMV], 1000000 )", "#,0.0", "1 Sales"),
    ("Avg Order Value", "DIVIDE ( [GMV], [Order Count] )", "#,0", "1 Sales"),
    ("Item GMV", f"CALCULATE ( SUM ( OrderItems[gmv] ), {V} )", "#,0", "1 Sales"),
    ("GMV Share", "DIVIDE ( [GMV], CALCULATE ( [GMV], ALLSELECTED () ) )", "0%", "1 Sales"),
    ("Cancelled Orders", 'CALCULATE ( COUNTROWS ( Orders ), Orders[order_status] IN { "canceled", "unavailable" } )', "#,0", "1 Sales"),
    ("Cancel Rate", "DIVIDE ( [Cancelled Orders], COUNTROWS ( Orders ) )", "0.0%", "1 Sales"),
    ("Payments vs GMV Gap", f"CALCULATE ( SUM ( Payments[payment_value] ), {V} ) - [GMV]", "#,0", "1 Sales"),
    ("Customer Count", f"CALCULATE ( DISTINCTCOUNT ( Orders[customer_unique_id] ), {V} )", "#,0", "2 Customers"),
    ("New Customers", f"CALCULATE ( DISTINCTCOUNT ( Orders[customer_unique_id] ), {V}, Orders[order_seq] = 1 )", "#,0", "2 Customers"),
    ("Returning Customers", "[Customer Count] - [New Customers]", "#,0", "2 Customers"),
    ("Repeat Orders", f"CALCULATE ( COUNTROWS ( Orders ), {V}, Orders[order_seq] > 1 )", "#,0", "2 Customers"),
    ("Repeat Order Share", "DIVIDE ( [Repeat Orders], [Order Count] )", "0.0%", "2 Customers"),
    ("Repeat Customer Rate",
     f"DIVIDE ( COUNTROWS ( FILTER ( VALUES ( Orders[customer_unique_id] ), CALCULATE ( COUNTROWS ( Orders ), {V} ) >= 2 ) ), [Customer Count] )",
     "0.0%", "2 Customers"),
    ("Cohort Retention", "DIVIDE ( SUM ( 'Cohort Retention'[active_customers] ), SUM ( 'Cohort Retention'[customers] ) )", "0.00%", "2 Customers"),
    ("Segment Customer Share", "DIVIDE ( SUM ( 'RFM Segments'[customers] ), CALCULATE ( SUM ( 'RFM Segments'[customers] ), ALL ( 'RFM Segments' ) ) )", "0%", "2 Customers"),
    ("Segment GMV Share", "DIVIDE ( SUM ( 'RFM Segments'[gmv] ), CALCULATE ( SUM ( 'RFM Segments'[gmv] ), ALL ( 'RFM Segments' ) ) )", "0%", "2 Customers"),
    ("Delivered Orders", f'CALCULATE ( COUNTROWS ( Orders ), {V}, Orders[order_status] = "delivered", NOT ISBLANK ( Orders[delivered_date] ) )', "#,0", "3 Delivery"),
    ("Late Orders", "CALCULATE ( [Delivered Orders], Orders[delay_days] > 0 )", "#,0", "3 Delivery"),
    ("Late Rate", "DIVIDE ( [Late Orders], [Delivered Orders] )", "0.0%", "3 Delivery"),
    ("Avg Delivery Days", f"CALCULATE ( AVERAGE ( Orders[delivery_days] ), {V} )", "0.0", "3 Delivery"),
    ("Avg Review", f"CALCULATE ( AVERAGE ( Orders[review_score] ), {V} )", "0.00", "3 Delivery"),
    ("Avg Review On Time", 'CALCULATE ( [Avg Review], Orders[order_status] = "delivered", Orders[delay_days] <= 0 )', "0.0", "3 Delivery"),
    ("Avg Review Late", 'CALCULATE ( [Avg Review], Orders[order_status] = "delivered", Orders[delay_days] > 0 )', "0.0", "3 Delivery"),
    ("Share 1-2 Stars",
     f"DIVIDE ( CALCULATE ( COUNTROWS ( Orders ), {V}, Orders[review_score] <= 2, NOT ISBLANK ( Orders[review_score] ) ), CALCULATE ( COUNTROWS ( Orders ), {V}, NOT ISBLANK ( Orders[review_score] ) ) )",
     "0%", "3 Delivery"),
    ("Share 1-2 Stars Late", 'CALCULATE ( [Share 1-2 Stars], Orders[order_status] = "delivered", Orders[delay_days] > 0 )', "0%", "3 Delivery"),
    ("Bucket Avg Review", "AVERAGE ( 'Delay Buckets'[avg_review] )", "0.0", "3 Delivery"),
    ("Bucket Share 1-2 Stars", "AVERAGE ( 'Delay Buckets'[share_1_2_stars] )", "0%", "3 Delivery"),
    ("Bucket Avg Review (on time)", "CALCULATE ( [Bucket Avg Review], 'Delay Buckets'[is_late] = FALSE () )", "0.0", "3 Delivery"),
    ("Bucket Avg Review (late)", "CALCULATE ( [Bucket Avg Review], 'Delay Buckets'[is_late] = TRUE () )", "0.0", "3 Delivery"),
    ("Bucket Share 1-2 Stars (on time)", "CALCULATE ( [Bucket Share 1-2 Stars], 'Delay Buckets'[is_late] = FALSE () )", "0%", "3 Delivery"),
    ("Bucket Share 1-2 Stars (late)", "CALCULATE ( [Bucket Share 1-2 Stars], 'Delay Buckets'[is_late] = TRUE () )", "0%", "3 Delivery"),
    ("Active Sellers", f"CALCULATE ( DISTINCTCOUNT ( OrderItems[seller_id] ), {V} )", "#,0", "4 Sellers"),
    ("Seller GMV", "SUM ( Sellers[gmv] )", "#,0", "4 Sellers"),
    ("Seller Avg Review", "AVERAGE ( Sellers[avg_review] )", "0.00", "4 Sellers"),
    ("Cumulative GMV Share", "MAX ( Sellers[cum_gmv_share] )", "0%", "4 Sellers"),
    ("Sellers Making 80% of GMV",
     "DIVIDE ( CALCULATE ( COUNTROWS ( Sellers ), Sellers[cum_gmv_share] < 0.8, NOT ISBLANK ( Sellers[cum_gmv_share] ) ) + 1, CALCULATE ( COUNTROWS ( Sellers ), NOT ISBLANK ( Sellers[gmv] ) ) )",
     "0%", "4 Sellers"),
    ("Fix-first Sellers", 'CALCULATE ( COUNTROWS ( Sellers ), Sellers[quadrant] = "Big & poor rating: fix first" )', "#,0", "4 Sellers"),
    ("Fix-first GMV Share",
     'DIVIDE ( CALCULATE ( SUM ( Sellers[gmv] ), Sellers[quadrant] = "Big & poor rating: fix first" ), CALCULATE ( SUM ( Sellers[gmv] ), ALL ( Sellers ) ) )',
     "0.0%", "4 Sellers"),
    ("Avg Installments", f"CALCULATE ( AVERAGE ( Orders[card_installments] ), {V}, Orders[card_installments] >= 1 )", "0.0", "5 Basket"),
    ("Installments by Value Band", "AVERAGE ( Installments[avg_installments] )", "0.0", "5 Basket"),
    ("Category Repurchase Rate", "AVERAGE ( 'Category Repurchase'[repurchase_rate] )", "0.0%", "5 Basket"),
    ("Weekly Orders", "SUM ( 'Forecast Weekly'[actual] )", "#,0", "6 Forecast"),
    ("Hold-out Prediction", "SUM ( 'Forecast Weekly'[holdout_pred] )", "#,0", "6 Forecast"),
    ("Forecast Orders", "SUM ( 'Forecast Weekly'[forecast] )", "#,0", "6 Forecast"),
    ("Forecast with Black Friday", "SUM ( 'Forecast Weekly'[forecast_bf] )", "#,0", "6 Forecast"),
    ("Forecast Low (80%)", "SUM ( 'Forecast Weekly'[low] )", "#,0", "6 Forecast"),
    ("Forecast High (80%)", "SUM ( 'Forecast Weekly'[high] )", "#,0", "6 Forecast"),
]

RELATIONSHIPS = [
    ("Orders", "customer_unique_id", "Customers", "customer_unique_id"),
    ("Orders", "purchase_date", "Date", "Date"),
    ("OrderItems", "order_id", "Orders", "order_id"),
    ("OrderItems", "product_id", "Products", "product_id"),
    ("OrderItems", "seller_id", "Sellers", "seller_id"),
    ("Payments", "order_id", "Orders", "order_id"),
    ("Reviews", "order_id", "Orders", "order_id"),
    ("Customers", "state", "Geo", "state"),
]

# ---------------- writers ----------------
def q(name):
    return f"'{name}'" if any(ch in name for ch in " -()%&·") or name[0].isdigit() else name

def write_table(name, spec):
    cols = spec["cols"]
    out = [f"table {q(name)}", f"\tlineageTag: {tag(name)}"]
    if spec.get("data_category"):
        out.append(f"\tdataCategory: {spec['data_category']}")
    out.append("")
    for c, t, *opt in cols:
        o = opt[0] if opt else {}
        dt, _, fmt = TYPES[t]
        out.append(f"\tcolumn {q(c)}")
        out.append(f"\t\tdataType: {dt}")
        if o.get("key"):
            out.append("\t\tisKey")
        if o.get("hidden"):
            out.append("\t\tisHidden")
        f = o.get("fmt", fmt)
        if f:
            out.append(f"\t\tformatString: {f}")
        out.append(f"\t\tlineageTag: {tag(name, c)}")
        out.append("\t\tsummarizeBy: none")
        out.append(f"\t\tsourceColumn: {c}")
        if o.get("sort"):
            out.append(f"\t\tsortByColumn: {o['sort']}")
        if t == "date":
            out += ["", "\t\tannotation UnderlyingDateTimeDataType = Date"]
        out.append("")
    steps = spec["source"](cols)
    out += [f"\tpartition {q(name)} = m", "\t\tmode: import", "\t\tsource =", "\t\t\t\tlet"]
    out += [f"\t\t\t\t\t{s}{',' if i < len(steps) - 1 else ''}" for i, s in enumerate(steps)]
    out += ["\t\t\t\tin", "\t\t\t\t\t" + steps[-1].split(" = ")[0], ""]
    if spec.get("note"):
        out.insert(0, f"/// {spec['note']}")
    return "\n".join(out) + "\n"

def write_measures():
    out = ["table _Measures", f"\tlineageTag: {tag('_Measures')}", ""]
    for name, expr, fmt, folder in MEASURES:
        out += [f"\tmeasure {q(name)} = {expr}", f"\t\tformatString: {fmt}", f"\t\tdisplayFolder: {folder}",
                f"\t\tlineageTag: {tag('_Measures', name)}", ""]
    out += ["\tcolumn _", "\t\tdataType: string", "\t\tisHidden", f"\t\tlineageTag: {tag('_Measures', '_')}",
            "\t\tsummarizeBy: none", "\t\tsourceColumn: _", "",
            "\tpartition _Measures = m", "\t\tmode: import", "\t\tsource = #table(type table [_ = text], {})", ""]
    return "\n".join(out) + "\n"

if DEF.exists():
    shutil.rmtree(DEF)
(DEF / "tables").mkdir(parents=True)
MODEL.mkdir(exist_ok=True)

order = list(T) + ["_Measures"]
(DEF / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1606\n\n", encoding="utf-8")
(DEF / "model.tmdl").write_text(
    "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tsourceQueryCulture: en-US\n"
    "\tdataAccessOptions\n\t\tlegacyRedirects\n\t\treturnErrorValuesAsNull\n\n"
    "annotation __PBI_TimeIntelligenceEnabled = 0\n\n"
    f"annotation PBI_QueryOrder = [{','.join(chr(34) + n + chr(34) for n in order + ['DataFolder'])}]\n\n"
    + "".join(f"ref table {q(n)}\n" for n in order) + "\n", encoding="utf-8")
(DEF / "expressions.tmdl").write_text(
    f'expression DataFolder = "{DATA_FOLDER}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
    f"\tlineageTag: {tag('DataFolder')}\n\n\tannotation PBI_ResultType = Text\n\n", encoding="utf-8")
rels = []
for i, (ft, fc, tt, tc) in enumerate(RELATIONSHIPS, 1):
    rels += [f"relationship rel_{i:02d}", f"\tfromColumn: {q(ft)}.{q(fc)}", f"\ttoColumn: {q(tt)}.{q(tc)}", ""]
(DEF / "relationships.tmdl").write_text("\n".join(rels) + "\n", encoding="utf-8")
for name, spec in T.items():
    (DEF / "tables" / f"{name}.tmdl").write_text(write_table(name, spec), encoding="utf-8")
(DEF / "tables" / "_Measures.tmdl").write_text(write_measures(), encoding="utf-8")

(MODEL / "definition.pbism").write_text('{\n  "version": "4.2",\n  "settings": {}\n}', encoding="utf-8")
(MODEL / ".platform").write_text(
    '{\n  "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",\n'
    '  "metadata": {\n    "type": "SemanticModel",\n    "displayName": "MarketplaceCaseStudy"\n  },\n'
    f'  "config": {{\n    "version": "2.0",\n    "logicalId": "{tag("model-logical")}"\n  }}\n}}', encoding="utf-8")
print(f"{len(T)} tables, {len(MEASURES)} measures, {len(RELATIONSHIPS)} relationships -> {DEF}")
