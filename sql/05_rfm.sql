-- RFM segmentation on real customers (customer_unique_id), snapshot date 1 Aug 2018.
-- R = days since last valid order, F = number of valid orders, M = total GMV.
-- About 97% of customers ordered once, so F barely separates anyone: R and M are scored in quintiles (1-5),
-- F is simply "one order" vs "repeat buyer".
WITH base AS (
    SELECT customer_unique_id,
           arg_max(customer_state, purchase_ts)                     AS state,
           date_diff('day', MAX(purchase_date), DATE '2018-08-01')  AS recency_days,
           COUNT(*)                                                 AS frequency,
           SUM(gmv)                                                 AS monetary
    FROM orders
    WHERE is_valid AND purchase_date < DATE '2018-08-01'   -- only what was known on the snapshot date
    GROUP BY 1
), scored AS (
    SELECT *,
           6 - NTILE(5) OVER (ORDER BY recency_days) AS r_score,   -- 5 = most recent
           NTILE(5) OVER (ORDER BY monetary)         AS m_score    -- 5 = highest spend
    FROM base
)
SELECT customer_unique_id, state, recency_days, frequency, ROUND(monetary, 2) AS monetary, r_score, m_score,
       CASE WHEN frequency >= 2                    THEN '1 · Repeat buyers'
            WHEN r_score >= 4 AND m_score >= 4     THEN '2 · Recent big spenders'
            WHEN r_score >= 4                      THEN '3 · Recent small spenders'
            WHEN m_score >= 4                      THEN '4 · Lapsed big spenders'
            ELSE '5 · Lapsed small spenders' END AS segment
FROM scored
ORDER BY monetary DESC;
