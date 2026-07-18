+++
document_id = "doc_runbook_inventory_cache"
document_type = "runbook"
title = "Inventory cache configuration regression runbook"
source_uri = "internal://knowledge/runbooks/inventory-cache-regression.md"
service_tags = ["inventory-service"]
environment_tags = ["production", "staging"]
version = "1.0"
effective_at = "2026-07-01T01:00:00Z"
ingested_at = "2026-07-18T02:45:00Z"
metadata = { owner = "inventory-platform", audience = "sre" }
+++
# Symptoms

Inventory latency and database reads rise together when the cache TTL is reduced to zero. Cache
miss rate approaches one and read amplification can increase application CPU utilization.

# Diagnosis

Compare the active cache TTL with its validated production value. Correlate cache misses, database
query count, application CPU, and the latest configuration rollout before assigning the root cause.

# Safe response

Restore a validated non-zero cache TTL only after human review, then confirm cache hit rate, database
load, CPU utilization, and inventory request latency.
