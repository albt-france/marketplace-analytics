# Data sources

## Olist public e-commerce dataset (real data)
**Brazilian E-Commerce Public Dataset by Olist**, published by Olist on Kaggle:
https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
Licence: **CC BY-NC-SA 4.0** (attribution, non-commercial, share-alike). The clean tables in `data/clean/` are derived
from it and are shared under the same licence. Olist's real names have been anonymised by Olist; no Olist logo is used.

Raw CSVs are **not** stored in this repository. To reproduce: download the ZIP from Kaggle (free account) and unzip the
nine CSV files into `data/raw/`.

| File | Content |
|---|---|
| `olist_orders_dataset.csv` | one row per order: status, purchase, delivery and promised dates |
| `olist_order_items_dataset.csv` | one row per item: product, seller, price, freight |
| `olist_order_payments_dataset.csv` | one row per payment: type, installments, value |
| `olist_order_reviews_dataset.csv` | review score (1-5) and comment |
| `olist_customers_dataset.csv` | `customer_id` (one per order) → `customer_unique_id` (the person), city, state |
| `olist_sellers_dataset.csv` | seller city and state |
| `olist_products_dataset.csv` | category (Portuguese), size, photos |
| `product_category_name_translation.csv` | category names in English |
| `olist_geolocation_dataset.csv` | lat/long by zip prefix: **not used** (1M rows with duplicates; the state is enough here) |

## Volumes checked after loading (`src/prepare_data.py` → `data/output/00_data_checks.csv`)
| Check | Value |
|---|---|
| Orders | 99,441 (all distinct) |
| `customer_id` (one per order) | 99,441 |
| `customer_unique_id` (real customers) | 96,096 |
| Sellers | 3,095 |
| Products | 32,951 |
| Order items | 112,650 |
| Payment rows | 103,886 |
| Review rows | 99,224 |
| Purchase dates | 4 Sept 2016 → 17 Oct 2018 |

## Traps found and how they are handled
| Trap | Size | Handling |
|---|---|---|
| `customer_id` changes with every order | 99,441 ids for 96,096 people | `customer_unique_id` is the customer key everywhere |
| Cancelled or unavailable orders | 1,234 | excluded from GMV, kept to compute cancellation rates |
| Orders without any item | 775 | excluded (no GMV) |
| Thin start: Sept-Dec 2016 | 329 orders | outside the analysis window |
| Tapering end: data collection fades from 22 Aug 2018 (daily orders drop from ~300 to ~10, then stop) | 6,532 orders from 1 Aug 2018 | window ends 31 Jul 2018; the weekly forecast uses complete weeks up to 19 Aug 2018 |
| Orders reviewed twice | 547 | keep the most recent review |
| Delivered orders without delivery date | 8 | excluded from delay analysis |
| Products without category | 610 | category `unknown` |
| Categories without English translation | 2 | translated by hand (`pc_gamer`, `portable_kitchen_appliances`) |
| Payments vs items | 247 valid orders differ by more than BRL 1 (vouchers, rounding) | GMV comes from items (price + freight); payments only cross-check it (gap: 0.02% of GMV) |

**Analysis window:** valid orders placed 1 Jan 2017 - 31 Jul 2018: **91,484 orders, 88,494 customers, BRL 14.69 m GMV.**
