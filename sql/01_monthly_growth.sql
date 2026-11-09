-- Monthly growth: orders, GMV, new vs returning customers.
-- Valid orders only (cancelled / unavailable excluded), full months Jan 2017 - Jul 2018 (data collection tapers off after 21 Aug 2018).
-- GMV = item price + freight from order_items (payments are only used to cross-check).
WITH monthly AS (
    SELECT date_trunc('month', purchase_date)::DATE AS month,
           COUNT(*)                                        AS orders,
           SUM(gmv)                                        AS gmv,
           COUNT(DISTINCT customer_unique_id)              AS active_customers,
           COUNT(DISTINCT CASE WHEN order_seq = 1 THEN customer_unique_id END) AS new_customers,
           COUNT(CASE WHEN order_seq > 1 THEN 1 END)       AS repeat_orders
    FROM orders
    WHERE is_valid AND in_window
    GROUP BY 1
)
SELECT month,
       orders,
       ROUND(gmv, 2)                                       AS gmv,
       ROUND(gmv / orders, 2)                              AS avg_order_value,
       active_customers,
       new_customers,
       active_customers - new_customers                    AS returning_customers,
       repeat_orders,
       ROUND(repeat_orders / orders, 4)                    AS repeat_order_share,
       ROUND(gmv / LAG(gmv) OVER (ORDER BY month) - 1, 4)  AS gmv_mom_growth
FROM monthly
ORDER BY month;
