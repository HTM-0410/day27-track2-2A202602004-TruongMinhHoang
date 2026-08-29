-- Test at fact-row granularity so positive orders cannot mask a negative order
-- after daily aggregation.
select
    order_id,
    customer_id,
    amount_usd,
    order_date
from {{ ref('stg_orders') }}
where status = 'completed'
  and amount_usd < 0
