"""Load the raw Olist CSVs into DuckDB, run data-quality checks and write clean tables to data/clean/ (Parquet).

Usage:  python src/prepare_data.py
Reads:  data/raw/*.csv (Kaggle "Brazilian E-Commerce Public Dataset by Olist")
Writes: data/clean/*.parquet and data/output/00_data_checks.csv
"""
import pathlib
import duckdb

RAW = pathlib.Path("data/raw")
CLEAN = pathlib.Path("data/clean")
OUT = pathlib.Path("data/output")
CLEAN.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

# Analysis window: Sept-Dec 2016 has only ~330 orders, and data collection tapers off from 22 Aug 2018
# (daily orders fall from ~300 to ~10 within a week, then stop). Monthly trends, cohorts and RFM therefore use
# the 19 full months Jan 2017 - Jul 2018.
WINDOW_START, WINDOW_END = "2017-01-01", "2018-08-01"  # end is exclusive

con = duckdb.connect()
raw_tables = {
    "customers": "olist_customers_dataset", "orders": "olist_orders_dataset",
    "items": "olist_order_items_dataset", "payments": "olist_order_payments_dataset",
    "reviews": "olist_order_reviews_dataset", "products": "olist_products_dataset",
    "sellers": "olist_sellers_dataset", "translation": "product_category_name_translation",
}
for name, f in raw_tables.items():
    # all_varchar for ids so leading zeros in zip codes survive; numbers/timestamps cast explicitly below
    con.execute(f"CREATE TABLE raw_{name} AS SELECT * FROM read_csv('{(RAW / (f + '.csv')).as_posix()}', header=true, all_varchar=true)")

checks = []
def check(label, sql):
    value = con.execute(sql).fetchone()[0]
    checks.append({"check": label, "value": value})
    print(f"{label:<62} {value:>12,}" if isinstance(value, int) else f"{label:<62} {value}")

# ---------- volumes ----------
check("orders (rows)", "SELECT COUNT(*) FROM raw_orders")
check("orders (distinct order_id)", "SELECT COUNT(DISTINCT order_id) FROM raw_orders")
check("customer_id (distinct, one per order)", "SELECT COUNT(DISTINCT customer_id) FROM raw_customers")
check("customer_unique_id (distinct, real customers)", "SELECT COUNT(DISTINCT customer_unique_id) FROM raw_customers")
check("sellers", "SELECT COUNT(*) FROM raw_sellers")
check("products", "SELECT COUNT(*) FROM raw_products")
check("order items", "SELECT COUNT(*) FROM raw_items")
check("payments (rows)", "SELECT COUNT(*) FROM raw_payments")
check("reviews (rows)", "SELECT COUNT(*) FROM raw_reviews")
# ---------- traps ----------
check("orders cancelled or unavailable", "SELECT COUNT(*) FROM raw_orders WHERE order_status IN ('canceled','unavailable')")
check("orders without any item", "SELECT COUNT(*) FROM raw_orders o WHERE NOT EXISTS (SELECT 1 FROM raw_items i WHERE i.order_id = o.order_id)")
check("delivered orders without delivery date", "SELECT COUNT(*) FROM raw_orders WHERE order_status = 'delivered' AND order_delivered_customer_date IS NULL")
check("orders with more than one review", "SELECT COUNT(*) FROM (SELECT order_id FROM raw_reviews GROUP BY 1 HAVING COUNT(*) > 1)")
check("review_id used for more than one order", "SELECT COUNT(*) FROM (SELECT review_id FROM raw_reviews GROUP BY 1 HAVING COUNT(DISTINCT order_id) > 1)")
check("products without category", "SELECT COUNT(*) FROM raw_products WHERE product_category_name IS NULL")
check("categories without English translation", """
    SELECT COUNT(DISTINCT p.product_category_name) FROM raw_products p
    LEFT JOIN raw_translation t USING (product_category_name)
    WHERE p.product_category_name IS NOT NULL AND t.product_category_name IS NULL""")
check("orders before 2017-01-01 (thin start)", f"SELECT COUNT(*) FROM raw_orders WHERE order_purchase_timestamp < '{WINDOW_START}'")
check("orders from 2018-08-01 (tapering end, excluded)", f"SELECT COUNT(*) FROM raw_orders WHERE order_purchase_timestamp >= '{WINDOW_END}'")
check("first purchase", "SELECT MIN(order_purchase_timestamp) FROM raw_orders")
check("last purchase", "SELECT MAX(order_purchase_timestamp) FROM raw_orders")

# ---------- clean tables ----------
con.execute("""
CREATE TABLE products AS
SELECT p.product_id,
       COALESCE(t.product_category_name_english,
                CASE p.product_category_name
                    WHEN 'pc_gamer' THEN 'pc_gamer'
                    WHEN 'portateis_cozinha_e_preparadores_de_alimentos' THEN 'portable_kitchen_appliances'
                END, 'unknown') AS category,
       TRY_CAST(p.product_weight_g AS INTEGER) AS weight_g,
       TRY_CAST(p.product_photos_qty AS INTEGER) AS photos_qty
FROM raw_products p LEFT JOIN raw_translation t USING (product_category_name)
""")

con.execute("""
CREATE TABLE order_items AS
SELECT i.order_id, CAST(i.order_item_id AS INTEGER) AS order_item_id, i.product_id, i.seller_id,
       CAST(i.price AS DOUBLE) AS price, CAST(i.freight_value AS DOUBLE) AS freight,
       CAST(i.price AS DOUBLE) + CAST(i.freight_value AS DOUBLE) AS gmv
FROM raw_items i
""")

# One review per order: keep the most recent answer (a few orders were reviewed twice).
con.execute("""
CREATE TABLE reviews AS
SELECT review_id, order_id, CAST(review_score AS INTEGER) AS review_score,
       CAST(review_creation_date AS TIMESTAMP)::DATE AS review_date,
       review_comment_message IS NOT NULL AS has_comment
FROM raw_reviews
QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY CAST(review_answer_timestamp AS TIMESTAMP) DESC, review_id) = 1
""")

con.execute("""
CREATE TABLE payments AS
SELECT order_id, CAST(payment_sequential AS INTEGER) AS payment_sequential, payment_type,
       CAST(payment_installments AS INTEGER) AS installments, CAST(payment_value AS DOUBLE) AS payment_value
FROM raw_payments
""")

con.execute(f"""
CREATE TABLE orders AS
WITH o AS (
    SELECT o.order_id, c.customer_unique_id, c.customer_state, o.order_status,
           CAST(o.order_purchase_timestamp AS TIMESTAMP) AS purchase_ts,
           CAST(o.order_delivered_customer_date AS TIMESTAMP) AS delivered_ts,
           CAST(o.order_estimated_delivery_date AS TIMESTAMP)::DATE AS estimated_date
    FROM raw_orders o JOIN raw_customers c USING (customer_id)
), it AS (
    SELECT order_id, COUNT(*) AS items, COUNT(DISTINCT seller_id) AS sellers,
           SUM(price) AS item_value, SUM(freight) AS freight, SUM(gmv) AS gmv
    FROM order_items GROUP BY 1
), pay AS (
    SELECT order_id, SUM(payment_value) AS paid,
           MAX(CASE WHEN payment_type = 'credit_card' THEN installments END) AS card_installments,
           arg_max(payment_type, payment_value) AS main_payment_type
    FROM payments GROUP BY 1
)
SELECT o.order_id, o.customer_unique_id, o.customer_state, o.order_status,
       o.purchase_ts, o.purchase_ts::DATE AS purchase_date,
       o.order_status NOT IN ('canceled', 'unavailable') AND it.order_id IS NOT NULL AS is_valid,
       o.purchase_ts >= '{WINDOW_START}' AND o.purchase_ts < '{WINDOW_END}' AS in_window,
       COALESCE(it.items, 0) AS items, it.sellers, it.item_value, it.freight, it.gmv,
       pay.paid, pay.main_payment_type, pay.card_installments,
       o.delivered_ts::DATE AS delivered_date, o.estimated_date,
       date_diff('day', o.purchase_ts::DATE, o.delivered_ts::DATE) AS delivery_days,
       date_diff('day', o.estimated_date, o.delivered_ts::DATE) AS delay_days,
       r.review_score
FROM o
LEFT JOIN it USING (order_id)
LEFT JOIN pay USING (order_id)
LEFT JOIN reviews r USING (order_id)
""")

# Order sequence per real customer (valid orders only) -> first order, repeat orders
con.execute("""
CREATE OR REPLACE TABLE orders AS
SELECT o.*,
       CASE WHEN is_valid THEN ROW_NUMBER() OVER (PARTITION BY customer_unique_id, is_valid ORDER BY purchase_ts, order_id) END AS order_seq
FROM orders o
""")

con.execute("""
CREATE TABLE customers AS
SELECT customer_unique_id,
       arg_max(customer_state, purchase_ts) AS state,
       MIN(purchase_date) AS first_purchase_date,
       date_trunc('month', MIN(purchase_date))::DATE AS cohort_month,
       MAX(purchase_date) AS last_purchase_date,
       COUNT(*) AS orders,
       SUM(gmv) AS gmv
FROM orders WHERE is_valid
GROUP BY 1
""")

con.execute("""
CREATE TABLE sellers AS
SELECT seller_id, seller_state AS state, seller_city AS city FROM raw_sellers
""")

check("valid orders (not cancelled/unavailable, with items)", "SELECT COUNT(*) FROM orders WHERE is_valid")
check("valid orders in Jan 2017 - Jul 2018 window", "SELECT COUNT(*) FROM orders WHERE is_valid AND in_window")
check("customers with at least one valid order", "SELECT COUNT(*) FROM customers")
check("GMV of valid orders, BRL (price + freight)", "SELECT ROUND(SUM(gmv)) FROM orders WHERE is_valid")
check("valid orders where |paid - GMV| > 1 BRL", "SELECT COUNT(*) FROM orders WHERE is_valid AND ABS(paid - gmv) > 1")
check("valid orders with no payment row", "SELECT COUNT(*) FROM orders WHERE is_valid AND paid IS NULL")

for t in ["orders", "order_items", "payments", "reviews", "customers", "sellers", "products"]:
    con.execute(f"COPY {t} TO '{(CLEAN / (t + '.parquet')).as_posix()}' (FORMAT parquet)")
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"wrote data/clean/{t}.parquet  ({n:,} rows)")

import csv
with open(OUT / "00_data_checks.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["check", "value"])
    w.writeheader()
    w.writerows({"check": c["check"], "value": str(c["value"])} for c in checks)
