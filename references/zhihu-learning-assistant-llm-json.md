# Zhihu / Learning Assistant LLM JSON Analysis Debug Note

## When this applies

Use this when intake successfully fetches a URL (especially Zhihu via TikHub) but the analysis stage logs a fallback such as:

```text
[llm] 分析失败，回退 heuristic: Expecting value: line 1 column 1 (char 0)
```

## Durable lesson

Do not assume this means the fetched content is bad. First separate the pipeline into:

1. Fetch success/failure.
2. LLM transport success/failure.
3. Provider response shape.
4. JSON extraction/parsing.

In the observed case, Zhihu fetch worked via TikHub and the failure was in the LLM analysis backend.

## Reproduction pattern

From the intake app directory, directly call `_llm_analyze(...)` with a short synthetic payload before rerunning the full URL pipeline. This isolates analysis from fetch/storage.

Expected failure symptom in the broken path:

```text
[llm] 分析失败，回退 heuristic: Expecting value: line 1 column 1 (char 0)
```

## Root-cause pattern found

The old analyzer path used a hardcoded Deeprouter/Anthropic-style endpoint/key and then blindly parsed the response as:

```python
body = json.loads(result.stdout)
text = body.get('content', [{}])[0].get('text', '')
return json.loads(text)
```

If the provider returns an API error such as `Invalid token`, or if the backend is OpenAI-compatible and returns `choices[0].message.content`, this produces an empty string and then a JSON parse error.

## Fix pattern

Prefer a configurable LLM backend instead of hardcoding a stale key:

- `LEARNING_ASSISTANT_LLM_API_URL`
- `LEARNING_ASSISTANT_LLM_API_KEY`
- `LEARNING_ASSISTANT_LLM_MODEL`
- `LEARNING_ASSISTANT_LLM_API_MODE`

For Wade’s current local runtime, the known-good class of backend is OpenAI-compatible `codex-proxy`:

```text
http://127.0.0.1:8100/v1/chat/completions
```

Use `CLIPROXY_API_KEY` as the fallback key source when appropriate. Do not print or persist the key.

For OpenAI-compatible chat completions:

- send `messages: [{role: system}, {role: user}]`
- include `response_format: {"type": "json_object"}` when supported
- parse `choices[0].message.content`

For Anthropic Messages-compatible endpoints:

- send `system` plus `messages`
- parse `content[0].text`

Always check provider-level `error` before extracting model text. If `error` exists, log a concise transport/API failure and return `None`; do not feed an empty string into `json.loads()`.

## Verification checklist

After patching analyzer code:

1. `python3 -m py_compile scripts/analyzer.py scripts/main.py`
2. Direct `_llm_analyze(...)` synthetic test returns a dict with `summary`.
3. Re-run the target URL through `scripts/main.py`.
4. Confirm output does **not** contain `回退 heuristic`.
5. Confirm the `.analysis.md` sidecar updates with LLM-format sections.
6. Confirm duplicate archival behavior still works when re-fetching the same URL.

## Pitfalls

- Do not debug Zhihu HTML scraping first if TikHub already fetched the content.
- Do not treat `Expecting value` as a content-quality issue; inspect raw provider stdout/stderr and response shape first.
- Do not hardcode real credentials in `analyzer.py` or support files.
- Do not expose tokens in logs; redact as `[REDACTED]`.
