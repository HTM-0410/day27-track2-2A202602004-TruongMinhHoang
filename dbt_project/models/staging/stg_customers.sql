select
    cast(customer_id as varchar) as customer_id,
    cast(country as varchar) as country,
    cast(tier as varchar) as tier,
    cast(is_active as boolean) as is_active,
    try_cast(
        nullif(trim(cast(valid_from as varchar)), '') as timestamp
    ) as valid_from,
    try_cast(
        nullif(trim(cast(valid_to as varchar)), '') as timestamp
    ) as valid_to
from {{ ref('customers') }}
