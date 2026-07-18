+++
document_id = "doc_runbook_checkout_dns"
document_type = "runbook"
title = "Checkout service DNS resolution failure runbook"
source_uri = "internal://knowledge/runbooks/checkout-dns-resolution.md"
service_tags = ["checkout-service"]
environment_tags = ["production", "staging"]
version = "1.0"
effective_at = "2026-07-01T01:00:00Z"
ingested_at = "2026-07-18T02:45:00Z"
metadata = { owner = "checkout-platform", audience = "sre" }
+++
# Symptoms

Checkout requests time out while resolving the order API hostname. Application logs contain DNS
lookup timeouts, but the checkout process itself remains healthy before the dependency call.

# Diagnosis

Inspect the active resolver endpoint, the recent configuration diff, and a trace that separates DNS
lookup time from the downstream HTTP request. A resolver address outside the production network is
a configuration regression, not an order API capacity failure.

# Safe response

Restore the previously validated resolver configuration only after human review. Confirm name
resolution, checkout error rate, and downstream request latency after mitigation.
