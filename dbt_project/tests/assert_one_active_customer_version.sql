-- Active SCD records must be unique by customer. The mart is defensive against
-- duplicates, but this test still surfaces the upstream dimension defect.
select
    customer_id,
    count(*) as active_version_count
from {{ ref('stg_customers') }}
where is_active = true
group by customer_id
having count(*) > 1
