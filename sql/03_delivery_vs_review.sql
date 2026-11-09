-- Delivery delay vs review score.
-- delay_days = actual delivery date - promised (estimated) date. Negative = early, positive = late.
-- Delivered orders in the analysis window that have a delivery date and a review.
WITH d AS (
    SELECT order_id, delay_days, review_score,
           CASE WHEN delay_days <= -15 THEN 1
                WHEN delay_days <= -8  THEN 2
                WHEN delay_days <= 0   THEN 3
                WHEN delay_days <= 3   THEN 4
                WHEN delay_days <= 7   THEN 5
                WHEN delay_days <= 14  THEN 6
                ELSE 7 END AS bucket_order
    FROM orders
    WHERE is_valid AND in_window AND order_status = 'delivered'
      AND delivered_date IS NOT NULL AND review_score IS NOT NULL
)
SELECT bucket_order,
       CASE bucket_order
           WHEN 1 THEN '15+ days early' WHEN 2 THEN '8-14 days early' WHEN 3 THEN '0-7 days early'
           WHEN 4 THEN '1-3 days late'  WHEN 5 THEN '4-7 days late'   WHEN 6 THEN '8-14 days late'
           ELSE '15+ days late' END                         AS delay_bucket,
       bucket_order >= 4                                    AS is_late,
       COUNT(*)                                             AS orders,
       ROUND(COUNT(*) / SUM(COUNT(*)) OVER (), 4)           AS share_of_orders,
       ROUND(AVG(review_score), 3)                          AS avg_review,
       ROUND(AVG((review_score <= 2)::INT), 4)              AS share_1_2_stars,
       ROUND(AVG((review_score = 5)::INT), 4)               AS share_5_stars
FROM d
GROUP BY 1
ORDER BY 1;
