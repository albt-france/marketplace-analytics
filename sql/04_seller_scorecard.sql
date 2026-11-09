-- Seller scorecard and revenue concentration (Pareto).
-- GMV from valid orders in the window; delay and review measured on that seller's delivered orders.
-- Cancellation rate uses every order (any status) containing the seller's items.
-- Note: ~1% of orders contain items from several sellers; the order's review is attributed to each of them.
WITH seller_orders AS (
    SELECT DISTINCT i.seller_id, o.order_id, o.order_status, o.is_valid, o.in_window,
           o.delay_days, o.delivered_date, o.review_score
    FROM order_items i JOIN orders o USING (order_id)
    WHERE o.purchase_date >= DATE '2017-01-01' AND o.purchase_date < DATE '2018-08-01'
), gmv AS (
    SELECT i.seller_id, SUM(i.gmv) AS gmv, COUNT(*) AS items
    FROM order_items i JOIN orders o USING (order_id)
    WHERE o.is_valid AND o.in_window
    GROUP BY 1
), quality AS (
    SELECT seller_id,
           COUNT(*) FILTER (WHERE is_valid)                                         AS orders,
           AVG(delay_days) FILTER (WHERE delivered_date IS NOT NULL)                AS avg_delay_days,
           AVG((delay_days > 0)::INT) FILTER (WHERE delivered_date IS NOT NULL)     AS late_rate,
           AVG(review_score) FILTER (WHERE is_valid)                                AS avg_review,
           AVG((review_score <= 2)::INT) FILTER (WHERE is_valid AND review_score IS NOT NULL) AS share_1_2_stars,
           AVG((order_status = 'canceled')::INT)                                    AS cancel_rate
    FROM seller_orders
    GROUP BY 1
), ranked AS (
    SELECT s.seller_id, s.state, q.orders, g.items, g.gmv,
           q.avg_delay_days, q.late_rate, q.avg_review, q.share_1_2_stars, q.cancel_rate,
           ROW_NUMBER() OVER (ORDER BY g.gmv DESC, s.seller_id)                       AS gmv_rank,
           SUM(g.gmv) OVER (ORDER BY g.gmv DESC, s.seller_id ROWS UNBOUNDED PRECEDING) / SUM(g.gmv) OVER () AS cum_gmv_share,
           COUNT(*) OVER ()                                                           AS n_sellers
    FROM gmv g
    JOIN quality q USING (seller_id)
    JOIN sellers s USING (seller_id)
)
SELECT seller_id, state, orders, items,
       ROUND(gmv, 2)              AS gmv,
       gmv_rank,
       ROUND(gmv_rank / n_sellers, 5) AS seller_percentile,
       ROUND(cum_gmv_share, 5)    AS cum_gmv_share,
       ROUND(avg_delay_days, 2)   AS avg_delay_days,
       ROUND(late_rate, 4)        AS late_rate,
       ROUND(avg_review, 3)       AS avg_review,
       ROUND(share_1_2_stars, 4)  AS share_1_2_stars,
       ROUND(cancel_rate, 4)      AS cancel_rate,
       -- "big" = top 10% of sellers by GMV; "poor" = average review below 3.8 (marketplace average is ~4.1).
       -- A rating needs at least 30 orders to mean something; below that the seller is not classified as poor.
       CASE WHEN gmv_rank <= 0.10 * n_sellers AND avg_review < 3.8 AND orders >= 30 THEN 'Big & poor rating: fix first'
            WHEN gmv_rank <= 0.10 * n_sellers                                     THEN 'Big & well rated: protect'
            WHEN avg_review < 3.8 AND orders >= 30                                THEN 'Small & poor rating: monitor'
            ELSE 'Small, well rated or < 30 orders' END AS quadrant
FROM ranked
ORDER BY gmv_rank;
