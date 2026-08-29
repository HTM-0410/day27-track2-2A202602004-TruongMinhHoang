-- fct_daily_revenue is explicitly USD-denominated. Fail closed instead of
-- silently summing accepted VND values without a governed FX-rate source.
select *
from {{ ref('stg_orders') }}
where status = 'completed'
  and currency <> 'USD'
