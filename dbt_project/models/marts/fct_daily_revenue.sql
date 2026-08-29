with completed_orders as (
    select *
    from {{ ref('stg_orders') }}
    where status = 'completed'
      and currency = 'USD'
),
ranked_active_customers as (
    select
        customer_id,
        row_number() over (
            partition by customer_id
            order by
                valid_from desc nulls last,
                country asc nulls last,
                tier asc nulls last,
                valid_to desc nulls last
        ) as active_version_rank
    from {{ ref('stg_customers') }}
    where is_active = true
),
latest_active_customers as (
    -- Project only the key: one deterministic latest active row per customer is
    -- sufficient for this existence join and cannot multiply order facts.
    select customer_id
    from ranked_active_customers
    where active_version_rank = 1
)
select
    o.order_date,
    count(*) as completed_order_rows,
    sum(o.amount_usd) as daily_revenue
from completed_orders o
left join latest_active_customers c
    on o.customer_id = c.customer_id
group by 1
order by 1
