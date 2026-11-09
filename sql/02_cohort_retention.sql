-- Monthly cohort retention on the REAL customer key (customer_unique_id; customer_id changes with every order).
-- Cohort = month of a customer's first valid order. Month N = share of the cohort placing an order N months later.
-- Censoring: a cohort only gets a value for month N if cohort month + N <= Jul 2018 (last full month),
-- otherwise recent cohorts would look like they never come back simply because time has not passed.
WITH first_order AS (
    SELECT customer_unique_id, cohort_month
    FROM customers
    WHERE cohort_month BETWEEN DATE '2017-01-01' AND DATE '2018-07-01'
), activity AS (
    SELECT DISTINCT o.customer_unique_id,
           date_trunc('month', o.purchase_date)::DATE AS order_month
    FROM orders o
    WHERE o.is_valid AND o.purchase_date < DATE '2018-08-01'
), cohort_size AS (
    SELECT cohort_month, COUNT(*) AS customers FROM first_order GROUP BY 1
), month_grid AS (
    SELECT c.cohort_month, c.customers, n.month_n
    FROM cohort_size c
    CROSS JOIN range(0, 20) AS n(month_n)
    WHERE c.cohort_month + INTERVAL (n.month_n) MONTH <= DATE '2018-07-01'   -- censoring rule
), active AS (
    SELECT f.cohort_month,
           date_diff('month', f.cohort_month, a.order_month) AS month_n,
           COUNT(DISTINCT a.customer_unique_id) AS active_customers
    FROM first_order f JOIN activity a USING (customer_unique_id)
    GROUP BY 1, 2
)
SELECT g.cohort_month, g.customers, g.month_n,
       COALESCE(a.active_customers, 0) AS active_customers,
       ROUND(COALESCE(a.active_customers, 0) / g.customers, 5) AS retention
FROM month_grid g
LEFT JOIN active a USING (cohort_month, month_n)
ORDER BY 1, 3;
