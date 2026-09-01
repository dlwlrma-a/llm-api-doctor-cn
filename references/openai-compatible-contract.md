# OpenAI-Compatible Contract Checks

Compatibility is endpoint-specific, not a single yes/no property. Test only the capability the client needs.

## Base URL

- The base URL normally identifies the API root, commonly ending in `/v1`.
- SDKs append resource paths such as `/chat/completions`; do not configure a full resource URL as the base URL.
- Log the final URL without query secrets. A duplicated `/v1/v1` is a client/base-URL composition error.

## Model Discovery

`GET {base_url}/models` should return JSON with a `data` array for the common contract. Some compatible providers do not expose discovery; in that case a successful minimal chat request is stronger evidence.

Model IDs are opaque and case-sensitive. Copy the exact current ID from discovery or provider documentation.

## Chat Completions

Minimal request:

```json
{
  "model": "exact-model-id",
  "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
  "max_tokens": 16,
  "temperature": 0
}
```

A common successful response contains `choices[0].message`. Usage fields are useful but not universally returned.

If the minimal request works, add optional features back one at a time: system messages, reasoning controls, JSON output, tools, images, then streaming.

## Streaming

For `stream: true`, the common format is Server-Sent Events with `data:` lines and a terminating `data: [DONE]`. Reverse proxies may buffer or transform the stream even when non-stream chat works.

## Tool Calling

Use one parameter-free function with an unambiguous instruction. A compatible response normally places calls in `choices[0].message.tool_calls`. A plain text answer is a capability or prompting warning, not necessarily an HTTP failure.

When replaying a tool call, preserve the assistant tool-call message and match every tool result to its `tool_call_id`. Reasoning models may require provider-specific replay fields.

## Diagnostic Order

1. Final URL and model ID.
2. Authentication through discovery or minimal chat.
3. Minimal non-stream chat.
4. The one optional feature that fails.

Do not send the user's full production prompt merely to prove connectivity.
