# OpenAI Responses API — Markdown Reference

> This document reformats the provided spec into a clean, navigable Markdown reference with endpoint summaries, request/response schemas, examples, and event catalogs.

---

## Table of Contents

* [Overview](#overview)
* [Create a Model Response](#create-a-model-response-post-v1responses)

  * [Request Body](#request-body)
  * [Example (JS)](#example-js)
  * [Response Object (Example)](#response-object-example)
* [Retrieve a Model Response](#retrieve-a-model-response-get-v1responsesresponse_id)

  * [Query Parameters](#query-parameters)
  * [Example (JS)](#example-js-1)
  * [Response (Example)](#response-example)
* [Delete a Model Response](#delete-a-model-response-delete-v1responsesresponse_id)

  * [Example (JS)](#example-js-2)
  * [Response (Example)](#response-example-1)
* [Cancel a Background Response](#cancel-a-background-response-post-v1responsesresponse_idcancel)

  * [Example (JS)](#example-js-3)
  * [Response (Example)](#response-example-2)
* [List Input Items for a Response](#list-input-items-for-a-response-get-v1responsesresponse_idinput_items)

  * [Query Parameters](#query-parameters-1)
  * [Example (JS)](#example-js-4)
  * [Response (Example)](#response-example-3)
* [Get Input Token Counts](#get-input-token-counts-post-v1responsesinput_tokens)

  * [Request Body](#request-body-1)
  * [Example (cURL)](#example-curl)
  * [Response (Example)](#response-example-4)
* [Schemas](#schemas)

  * [Response Object](#response-object)
  * [Input Item List Object](#input-item-list-object)
* [Conversations API](#conversations-api)

  * [Create Conversation](#create-conversation-post-v1conversations)
  * [Retrieve Conversation](#retrieve-conversation-get-v1conversationsconversation_id)
  * [Update Conversation](#update-conversation-post-v1conversationsconversation_id)
  * [Delete Conversation](#delete-conversation-delete-v1conversationsconversation_id)
  * [List Conversation Items](#list-conversation-items-get-v1conversationsconversation_iditems)
  * [Create Conversation Items](#create-conversation-items-post-v1conversationsconversation_iditems)
  * [Retrieve a Conversation Item](#retrieve-a-conversation-item-get-v1conversationsconversation_iditemsitem_id)
  * [Delete a Conversation Item](#delete-a-conversation-item-delete-v1conversationsconversation_iditemsitem_id)
  * [Conversation Object](#conversation-object)
  * [Conversation Item List](#conversation-item-list)
* [Videos API](#videos-api)

  * [Create Video](#create-video-post-v1videos)
  * [Remix Video](#remix-video-post-v1videosvideo_idremix)
  * [List Videos](#list-videos-get-v1videos)
  * [Retrieve Video](#retrieve-video-get-v1videosvideo_id)
  * [Delete Video](#delete-video-delete-v1videosvideo_id)
  * [Retrieve Video Content](#retrieve-video-content-get-v1videosvideo_idcontent)
  * [Video Job Object](#video-job-object)
* [Streaming Events (Server-Sent Events)](#streaming-events-server-sent-events)

  * [Response Lifecycle Events](#response-lifecycle-events)
  * [Output Items and Content Parts](#output-items-and-content-parts)
  * [Text, Refusals, and Function Calls](#text-refusals-and-function-calls)
  * [Built-in Tool Calls](#built-in-tool-calls)
  * [Reasoning Summaries](#reasoning-summaries)
  * [Image Generation Events](#image-generation-events)
  * [MCP Tooling Events](#mcp-tooling-events)
  * [Code Interpreter Events](#code-interpreter-events)
  * [Custom Tool Call Input Events](#custom-tool-call-input-events)
  * [Queueing and Errors](#queueing-and-errors)

---

## Overview

The **Responses API** generates model outputs (text or JSON) from text, image, or file inputs. It can call **built-in tools** (web search, file search), **MCP tools**, or **custom function calls**, and supports **conversations**, **streaming SSE**, and **background** execution.

Base URL: `https://api.openai.com/v1`

---

## Create a Model Response (`POST /v1/responses`)

**Description:** Creates a model response (text or JSON). Supports tools and multi-turn state via `conversation` or `previous_response_id`.

**Endpoint:** `POST https://api.openai.com/v1/responses`

### Request Body

| Field                  | Type            | Required |    Default | Description                                                                                                                                                                                    |
| ---------------------- | --------------- | -------: | ---------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `background`           | boolean         |       No |    `false` | Run the response in the background (enables canceling).                                                                                                                                        |
| `conversation`         | string | object |       No |     `null` | Conversation container to prepend prior items and persist new ones. Cannot be used with `previous_response_id`.                                                                                |
| `include`              | array           |       No |          — | Extra data to include; e.g. `web_search_call.action.sources`, `code_interpreter_call.outputs`, `file_search_call.results`, `message.output_text.logprobs`, `reasoning.encrypted_content`, etc. |
| `input`                | string | array  |       No |          — | Text/image/file inputs.                                                                                                                                                                        |
| `instructions`         | string          |       No |          — | System/developer message. Not carried across when using `previous_response_id`.                                                                                                                |
| `max_output_tokens`    | integer         |       No |          — | Upper bound on generated (visible + reasoning) tokens.                                                                                                                                         |
| `max_tool_calls`       | integer         |       No |          — | Max total built-in tool calls for this response.                                                                                                                                               |
| `metadata`             | map             |       No |          — | Up to 16 key/value pairs (64-char keys, 512-char values).                                                                                                                                      |
| `model`                | string          |       No |          — | Model ID (e.g., `gpt-4o`, `o3`, `gpt-5`).                                                                                                                                                      |
| `parallel_tool_calls`  | boolean         |       No |     `true` | Allow parallel tool calls.                                                                                                                                                                     |
| `previous_response_id` | string          |       No |          — | Link to previous response for multi-turn. Not with `conversation`.                                                                                                                             |
| `prompt`               | object          |       No |          — | Reference to a prompt template and variables.                                                                                                                                                  |
| `prompt_cache_key`     | string          |       No |          — | Cache bucketing key (replaces `user`).                                                                                                                                                         |
| `reasoning`            | object          |       No |          — | Reasoning options (gpt-5 & o-series only).                                                                                                                                                     |
| `safety_identifier`    | string          |       No |          — | Stable user identifier (hash recommended).                                                                                                                                                     |
| `service_tier`         | string          |       No |     `auto` | `auto` | `default` | `flex` | `priority`. Response echoes actual tier used.                                                                                                                    |
| `store`                | boolean         |       No |     `true` | Store response for later retrieval.                                                                                                                                                            |
| `stream`               | boolean         |       No |    `false` | Stream via SSE.                                                                                                                                                                                |
| `stream_options`       | object          |       No |     `null` | Options for streaming (only when `stream: true`).                                                                                                                                              |
| `temperature`          | number          |       No |        `1` | 0–2; sample diversity. Prefer tuning `temperature` or `top_p`, not both.                                                                                                                       |
| `text`                 | object          |       No |          — | Text output config (plain text or structured outputs).                                                                                                                                         |
| `tool_choice`          | string | object |       No |     `auto` | Tool selection strategy.                                                                                                                                                                       |
| `tools`                | array           |       No |          — | Declares tools (built-in, MCP, custom functions).                                                                                                                                              |
| `top_logprobs`         | integer         |       No |          — | 0–20 top token logprobs.                                                                                                                                                                       |
| `top_p`                | number          |       No |        `1` | Nucleus sampling.                                                                                                                                                                              |
| `truncation`           | string          |       No | `disabled` | `auto` | `disabled`. `auto` may drop oldest items to fit context.                                                                                                                              |
| `user` *(Deprecated)*  | string          |       No |          — | Use `prompt_cache_key` / `safety_identifier` instead.                                                                                                                                          |

#### Learn more

* Text, Image, File inputs
* Conversation state
* Function calling
* Structured Outputs

### Example (JS)

```js
import OpenAI from "openai";
const openai = new OpenAI();

const response = await openai.responses.create({
  model: "gpt-4.1",
  input: "Tell me a three sentence bedtime story about a unicorn."
});

console.log(response);
```

### Response Object (Example)

```json
{
  "id": "resp_67ccd2bed1ec8190b14f964abc0542670bb6a6b452d3795b",
  "object": "response",
  "created_at": 1741476542,
  "status": "completed",
  "error": null,
  "instructions": null,
  "max_output_tokens": null,
  "model": "gpt-4.1-2025-04-14",
  "output": [
    {
      "type": "message",
      "id": "msg_67ccd2bf17f0819081ff3bb2cf6508e60bb6a6b452d3795b",
      "status": "completed",
      "role": "assistant",
      "content": [
        {
          "type": "output_text",
          "text": "In a peaceful grove beneath a silver moon...",
          "annotations": []
        }
      ]
    }
  ],
  "parallel_tool_calls": true,
  "store": true,
  "temperature": 1.0,
  "text": { "format": { "type": "text" } },
  "tool_choice": "auto",
  "tools": [],
  "top_p": 1.0,
  "truncation": "disabled",
  "usage": {
    "input_tokens": 36,
    "input_tokens_details": { "cached_tokens": 0 },
    "output_tokens": 87,
    "output_tokens_details": { "reasoning_tokens": 0 },
    "total_tokens": 123
  },
  "metadata": {}
}
```

---

## Retrieve a Model Response (`GET /v1/responses/{response_id}`)

**Endpoint:** `GET https://api.openai.com/v1/responses/{response_id}`

**Path Parameters**

* `response_id` *(string, required)* — Response ID.

### Query Parameters

| Name                  | Type    | Default | Description                                       |
| --------------------- | ------- | ------: | ------------------------------------------------- |
| `include`             | array   |       — | Include extra fields (same options as create).    |
| `include_obfuscation` | boolean |  `true` | Enable/disable stream obfuscation overhead.       |
| `starting_after`      | integer |       — | Start streaming after this event sequence number. |
| `stream`              | boolean | `false` | Stream SSE for this retrieval.                    |

### Example (JS)

```js
import OpenAI from "openai";
const client = new OpenAI();

const response = await client.responses.retrieve("resp_123");
console.log(response);
```

### Response (Example)

```json
{
  "id": "resp_67cb71b351908190a308f3859487620d06981a8637e6bc44",
  "object": "response",
  "created_at": 1741386163,
  "status": "completed",
  "model": "gpt-4o-2024-08-06",
  "output": [
    {
      "type": "message",
      "id": "msg_67cb71b3c2b0819084d481baaaf148f206981a8637e6bc44",
      "role": "assistant",
      "content": [
        { "type": "output_text", "text": "Silent circuits hum,\nThoughts emerge..." }
      ]
    }
  ],
  "usage": { "input_tokens": 32, "output_tokens": 18, "total_tokens": 50 }
}
```

---

## Delete a Model Response (`DELETE /v1/responses/{response_id}`)

**Endpoint:** `DELETE https://api.openai.com/v1/responses/{response_id}`

### Example (JS)

```js
import OpenAI from "openai";
const client = new OpenAI();

const response = await client.responses.delete("resp_123");
console.log(response);
```

### Response (Example)

```json
{ "id": "resp_6786a1bec27481909a17d673315b29f6", "object": "response", "deleted": true }
```

---

## Cancel a Background Response (`POST /v1/responses/{response_id}/cancel`)

> Only responses created with `background: true` can be canceled.

**Endpoint:** `POST https://api.openai.com/v1/responses/{response_id}/cancel`

### Example (JS)

```js
import OpenAI from "openai";
const client = new OpenAI();

const response = await client.responses.cancel("resp_123");
console.log(response);
```

### Response (Example)

```json
{
  "id": "resp_67cb71b351908190a308f3859487620d06981a8637e6bc44",
  "object": "response",
  "status": "completed",
  "model": "gpt-4o-2024-08-06"
}
```

---

## List Input Items for a Response (`GET /v1/responses/{response_id}/input_items`)

**Endpoint:** `GET https://api.openai.com/v1/responses/{response_id}/input_items`

**Path Parameter:** `response_id` *(required)*

### Query Parameters

| Name      | Type    | Default | Description                                    |
| --------- | ------- | ------: | ---------------------------------------------- |
| `after`   | string  |       — | Paginate after this item ID.                   |
| `include` | array   |       — | Include extra fields (same options as create). |
| `limit`   | integer |    `20` | 1–100.                                         |
| `order`   | string  |  `desc` | `asc` | `desc`.                                |

### Example (JS)

```js
import OpenAI from "openai";
const client = new OpenAI();

const response = await client.responses.inputItems.list("resp_123");
console.log(response.data);
```

### Response (Example)

```json
{
  "object": "list",
  "data": [
    {
      "id": "msg_abc123",
      "type": "message",
      "role": "user",
      "content": [{ "type": "input_text", "text": "Tell me a three sentence..." }]
    }
  ],
  "first_id": "msg_abc123",
  "last_id": "msg_abc123",
  "has_more": false
}
```

---

## Get Input Token Counts (`POST /v1/responses/input_tokens`)

**Endpoint:** `POST https://api.openai.com/v1/responses/input_tokens`

### Request Body

| Field                  | Type            | Required | Description                                          |
| ---------------------- | --------------- | -------: | ---------------------------------------------------- |
| `conversation`         | string | object |       No | Include conversation items for counting.             |
| `input`                | string | array  |       No | Text/image/file inputs to count.                     |
| `instructions`         | string          |       No | System/dev message.                                  |
| `model`                | string          |       No | Model ID.                                            |
| `parallel_tool_calls`  | boolean         |       No | Allow parallel tool calls.                           |
| `previous_response_id` | string          |       No | Link to previous response (not with `conversation`). |
| `reasoning`            | object          |       No | Reasoning options (gpt-5 & o-series).                |
| `text`                 | object          |       No | Text output config.                                  |
| `tool_choice`          | string | object |       No | Tool selection.                                      |
| `tools`                | array           |       No | Declared tools.                                      |
| `truncation`           | string          |       No | `auto` | `disabled` (default).                       |

### Example (cURL)

```bash
curl -X POST https://api.openai.com/v1/responses/input_tokens \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "gpt-5",
    "input": "Tell me a joke."
  }'
```

### Response (Example)

```json
{ "object": "response.input_tokens", "input_tokens": 11 }
```

---

## Schemas

### Response Object

Key fields (see full example above):

| Field                                  | Type                       | Notes                                                                           |
| -------------------------------------- | -------------------------- | ------------------------------------------------------------------------------- |
| `id`                                   | string                     | Unique identifier.                                                              |
| `object`                               | string                     | Always `response`.                                                              |
| `created_at`                           | number                     | Unix seconds.                                                                   |
| `status`                               | string                     | `completed` | `failed` | `in_progress` | `cancelled` | `queued` | `incomplete`. |
| `error`                                | object                     | Error details (if any).                                                         |
| `incomplete_details`                   | object                     | Reason for incomplete (e.g., `max_tokens`).                                     |
| `instructions`                         | string | array             | System/developer message(s).                                                    |
| `max_output_tokens`                    | integer                    | Generation cap.                                                                 |
| `max_tool_calls`                       | integer                    | Built-in tool call cap.                                                         |
| `metadata`                             | map                        | Arbitrary key/value pairs (limits apply).                                       |
| `model`                                | string                     | Model used.                                                                     |
| `output`                               | array                      | Output items (messages, tool calls, etc.).                                      |
| `output_text`                          | string *(SDK convenience)* | Aggregated text from `output_text` items.                                       |
| `parallel_tool_calls`                  | boolean                    | Parallel execution allowed.                                                     |
| `previous_response_id`                 | string                     | Link to prior response.                                                         |
| `prompt`, `prompt_cache_key`           | object, string             | Prompt template; cache bucketing.                                               |
| `reasoning`                            | object                     | Reasoning config/results.                                                       |
| `safety_identifier`                    | string                     | Stable user ID (recommended hashed).                                            |
| `service_tier`                         | string                     | `auto` | `default` | `flex` | `priority`.                                       |
| `temperature`, `top_p`, `top_logprobs` | number / int               | Sampling controls.                                                              |
| `text`                                 | object                     | Output formatting (incl. structured outputs).                                   |
| `tool_choice`, `tools`                 | mixed                      | Tool selection and declarations.                                                |
| `truncation`                           | string                     | `auto` or `disabled`.                                                           |
| `usage`                                | object                     | Token usage breakdown.                                                          |
| `user` *(deprecated)*                  | string                     | Use `prompt_cache_key`/`safety_identifier`.                                     |

**Full Object (Example)**

```json
{
  "id": "resp_67ccd3a9da748190baa7f1570fe91ac6...",
  "object": "response",
  "...": "see example in Create section for full payload"
}
```

### Input Item List Object

| Field      | Type    | Description                            |
| ---------- | ------- | -------------------------------------- |
| `object`   | string  | Always `list`.                         |
| `data`     | array   | Items used as inputs for the response. |
| `first_id` | string  | First item ID.                         |
| `last_id`  | string  | Last item ID.                          |
| `has_more` | boolean | More items available.                  |

---

## Conversations API

### Create Conversation (`POST /v1/conversations`)

```js
import OpenAI from "openai";
const client = new OpenAI();

const conversation = await client.conversations.create({
  metadata: { topic: "demo" },
  items: [{ type: "message", role: "user", content: "Hello!" }]
});
```

**Response**

```json
{ "id": "conv_123", "object": "conversation", "created_at": 1741900000, "metadata": { "topic": "demo" } }
```

### Retrieve Conversation (`GET /v1/conversations/{conversation_id}`)

```js
const conversation = await client.conversations.retrieve("conv_123");
```

### Update Conversation (`POST /v1/conversations/{conversation_id}`)

**Body:** `metadata` *(map, required)*

```js
const updated = await client.conversations.update("conv_123", { metadata: { topic: "project-x" } });
```

### Delete Conversation (`DELETE /v1/conversations/{conversation_id}`)

```js
const deleted = await client.conversations.delete("conv_123");
```

**Response**

```json
{ "id": "conv_123", "object": "conversation.deleted", "deleted": true }
```

### List Conversation Items (`GET /v1/conversations/{conversation_id}/items`)

**Query:** `after`, `include`, `limit` (1–100, default 20), `order` (`asc`|`desc`)

```js
const items = await client.conversations.items.list("conv_123", { limit: 10 });
console.log(items.data);
```

### Create Conversation Items (`POST /v1/conversations/{conversation_id}/items`)

**Body:** `items` *(array, required; up to 20 at a time)*

```js
const items = await client.conversations.items.create("conv_123", {
  items: [
    { type: "message", role: "user", content: [{ type: "input_text", text: "Hello!" }] },
    { type: "message", role: "user", content: [{ type: "input_text", text: "How are you?" }] }
  ]
});
```

### Retrieve a Conversation Item (`GET /v1/conversations/{conversation_id}/items/{item_id}`)

```js
const item = await client.conversations.items.retrieve("conv_123", "msg_abc");
```

### Delete a Conversation Item (`DELETE /v1/conversations/{conversation_id}/items/{item_id}`)

```js
const conversation = await client.conversations.items.delete("conv_123", "msg_abc");
```

### Conversation Object

| Field        | Type    | Description            |
| ------------ | ------- | ---------------------- |
| `id`         | string  | Conversation ID.       |
| `object`     | string  | Always `conversation`. |
| `created_at` | integer | Unix seconds.          |
| `metadata`   | map     | Key/value metadata.    |

### Conversation Item List

Standard list wrapper with `data`, `first_id`, `last_id`, `has_more`.

---

## Videos API

### Create Video (`POST /v1/videos`)

```js
import OpenAI from 'openai';
const openai = new OpenAI();

const video = await openai.videos.create({ prompt: 'A calico cat playing a piano on stage' });
console.log(video.id);
```

**Response**

```json
{
  "id": "video_123",
  "object": "video",
  "model": "sora-2",
  "status": "queued",
  "progress": 0,
  "created_at": 1712697600,
  "size": "1024x1808",
  "seconds": "8",
  "quality": "standard"
}
```

### Remix Video (`POST /v1/videos/{video_id}/remix`)

```js
const video = await client.videos.remix('video_123', {
  prompt: 'Extend the scene with the cat taking a bow to the cheering audience'
});
```

### List Videos (`GET /v1/videos`)

```js
for await (const video of openai.videos.list()) {
  console.log(video.id);
}
```

### Retrieve Video (`GET /v1/videos/{video_id}`)

```js
const video = await client.videos.retrieve('video_123');
```

### Delete Video (`DELETE /v1/videos/{video_id}`)

```js
const video = await client.videos.delete('video_123');
```

### Retrieve Video Content (`GET /v1/videos/{video_id}/content`)

**Query:** `variant` (e.g., pick an alternate downloadable asset)

```js
const response = await client.videos.downloadContent('video_123');
const content = await response.blob();
```

### Video Job Object

| Field                                      | Type    | Description                                     |
| ------------------------------------------ | ------- | ----------------------------------------------- |
| `id`                                       | string  | Video job ID.                                   |
| `object`                                   | string  | Always `video`.                                 |
| `model`                                    | string  | Generation model (e.g., `sora-2`).              |
| `status`                                   | string  | Lifecycle status (e.g., `queued`, `completed`). |
| `progress`                                 | integer | Approximate completion percentage.              |
| `created_at`, `completed_at`, `expires_at` | integer | Unix times.                                     |
| `size`                                     | string  | Resolution (e.g., `1024x1808`).                 |
| `seconds`                                  | string  | Clip duration.                                  |
| `quality`                                  | string  | Quality preset.                                 |
| `remixed_from_video_id`                    | string  | Source video when remixed.                      |
| `error`                                    | object  | Error details (if any).                         |

---

## Streaming Events (Server-Sent Events)

When `stream: true`, the server emits SSE events. Below is the catalog and example payloads.

### Response Lifecycle Events

* **`response.created`**
* **`response.in_progress`**
* **`response.completed`**
* **`response.failed`**
* **`response.incomplete`**
* **`response.queued`**

**Example – `response.created`**

```json
{
  "type": "response.created",
  "response": {
    "id": "resp_...",
    "object": "response",
    "status": "in_progress",
    "output": [],
    "tools": []
  },
  "sequence_number": 1
}
```

### Output Items and Content Parts

* **`response.output_item.added`**
* **`response.output_item.done`**
* **`response.content_part.added`**
* **`response.content_part.done`**

### Text, Refusals, and Function Calls

* **`response.output_text.delta`**
* **`response.output_text.done`**
* **`response.refusal.delta`**
* **`response.refusal.done`**
* **`response.function_call_arguments.delta`**
* **`response.function_call_arguments.done`**

**Example – Text Deltas**

```json
{ "type": "response.output_text.delta", "delta": "In", "output_index": 0, "content_index": 0 }
{ "type": "response.output_text.done", "text": "In a shimmering forest...", "output_index": 0, "content_index": 0 }
```

### Built-in Tool Calls

* **File Search**

  * `response.file_search_call.in_progress`
  * `response.file_search_call.searching`
  * `response.file_search_call.completed`
* **Web Search**

  * `response.web_search_call.in_progress`
  * `response.web_search_call.searching`
  * `response.web_search_call.completed`

### Reasoning Summaries

* `response.reasoning_summary_part.added`
* `response.reasoning_summary_part.done`
* `response.reasoning_summary_text.delta`
* `response.reasoning_summary_text.done`
* `response.reasoning_text.delta`
* `response.reasoning_text.done`

**Example – Reasoning Summary Done**

```json
{
  "type": "response.reasoning_summary_text.done",
  "text": "**Responding to a greeting**\n\nThe user just said, \"Hello!\" ..."
}
```

### Image Generation Events

* `response.image_generation_call.in_progress`
* `response.image_generation_call.generating`
* `response.image_generation_call.partial_image`
* `response.image_generation_call.completed`

### MCP Tooling Events

* `response.mcp_list_tools.in_progress` / `.completed` / `.failed`
* `response.mcp_call.in_progress` / `.completed` / `.failed`
* `response.mcp_call_arguments.delta` / `.done`

### Code Interpreter Events

* `response.code_interpreter_call.in_progress`
* `response.code_interpreter_call.interpreting`
* `response.code_interpreter_call.completed`
* `response.code_interpreter_call_code.delta`
* `response.code_interpreter_call_code.done`

### Custom Tool Call Input Events

* `response.custom_tool_call_input.delta`
* `response.custom_tool_call_input.done`

### Queueing and Errors

* `response.queued`
* `error` (generic SSE error envelope)

**Example – Error**

```json
{ "type": "error", "code": "ERR_SOMETHING", "message": "Something went wrong", "param": null, "sequence_number": 1 }
```

---

## Notes & Best Practices

* **State:** Use `conversation` for persistent context across calls, or `previous_response_id` for immediate multi-turn links (not both).
* **Safety & Cache:** Prefer `safety_identifier` for policy tracing and `prompt_cache_key` for cache bucketing (deprecates `user`).
* **Truncation:** If long histories are expected, set `truncation: "auto"` to avoid 400s when exceeding context.
* **Streaming:** Use `stream: true` to receive SSE events; consider `include_obfuscation: false` on retrieval if optimizing bandwidth.
* **Tools:** Declare tools in `tools`, guide selection via `tool_choice`, and cap usage via `max_tool_calls`.

---

*End of document.*
