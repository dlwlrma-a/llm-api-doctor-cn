# Error Taxonomy

Use the first row supported by direct evidence. Error text varies by gateway, so HTTP status and response shape are stronger signals than translated wording.

| Signal | Layer | Common causes | First repair |
| --- | --- | --- | --- |
| Invalid URL, duplicated `/v1`, or endpoint path used as base URL | Configuration | Base URL ends in `/chat/completions`, missing scheme, client appends `/v1` twice | Set the SDK base URL to the provider's API root and inspect the final request URL |
| DNS failure | Network | Typo, unavailable resolver, blocked domain | Resolve the hostname outside the SDK and verify the configured domain |
| TLS or certificate failure | TLS | Interception proxy, expired certificate, wrong system clock | Inspect the certificate chain and clock; do not disable verification |
| Connection refused | Network | Wrong port, service stopped, firewall | Verify listener, port, and bind address |
| Timeout before headers | Network/provider | Routing failure, proxy, overloaded upstream | Run one small request with a bounded timeout; do not loop retries |
| HTTP 301/302/307/308 | Configuration/security | Website URL used instead of API URL, regional redirect | Use the documented API base URL; never forward authorization across origins |
| HTTP 400 | Request/protocol | Unsupported parameter, invalid message order, missing `reasoning_content`, malformed image or tool schema | Remove optional fields and retry the smallest valid request |
| HTTP 401 | Authentication | Missing, expired, revoked, or wrong-provider key | Check key source, whitespace, environment scope, and endpoint/provider match |
| HTTP 403 | Authorization/policy | Model permission, account policy, IP or region restriction | Check account/model entitlement and network policy; do not rotate keys blindly |
| HTTP 404 | Endpoint/model | Wrong path, missing `/v1`, unsupported endpoint, incorrect model ID | Query `/models`, copy the exact ID, and inspect the final URL |
| HTTP 408/504 | Network/provider | Upstream timeout or excessively large request | Retry once with a tiny prompt and smaller output limit |
| HTTP 409 | Provider state | Conflicting operation or transient resource state | Inspect provider-specific error details; avoid automatic repeated writes |
| HTTP 413 | Request | Prompt, image, or file exceeds gateway limit | Reduce or chunk the payload and verify encoded size |
| HTTP 415 | Request | Wrong `Content-Type` or unsupported media | Send JSON for chat; use the provider's documented media format |
| HTTP 422 | Schema | Valid JSON with invalid field types or constraints | Compare the exact JSON schema, especially tools and multimodal content arrays |
| HTTP 429 | Account/rate limit | Requests-per-minute limit, concurrency, exhausted balance | Read retry headers, reduce concurrency, and check quota or balance |
| HTTP 500/502/503 | Provider/gateway | Upstream outage, gateway parsing failure, unavailable model route | Preserve the request ID, retry once with a minimal request, then stop |
| 2xx with HTML | Endpoint/proxy | Website or login page returned instead of API JSON | Correct the API host/path and inspect reverse-proxy routing |
| 2xx JSON without `choices` | Protocol | Non-Chat-Completions response, gateway wrapper, partial compatibility | Confirm the endpoint contract and adapt the client parser |
| Stream closes without `data:` events | Streaming | Buffering proxy, non-SSE endpoint, provider-specific stream format | Disable proxy buffering or use non-stream chat to isolate the problem |
| Text answer instead of `tool_calls` | Capability | Model lacks reliable tool use, tool schema is ambiguous, `tool_choice` ignored | Use a tool-capable model and a minimal unambiguous schema |

## High-Signal Text Patterns

- `model_not_found`, `unknown model`, `invalid model`: exact model ID or entitlement.
- `context_length`, `maximum context`, `too many tokens`: request size, not authentication.
- `reasoning_content`: reasoning-model message replay contract.
- `tool_calls`, `function`, `tool_call_id`: tool schema or conversation ordering.
- `insufficient_quota`, `balance`, `credits`: account quota rather than RPM throttling.
- `content_filter`, `safety`, `moderation`: provider policy; do not disguise or repeatedly resubmit content.

Always preserve the provider's request ID after redaction. It is the most useful artifact for provider support.
