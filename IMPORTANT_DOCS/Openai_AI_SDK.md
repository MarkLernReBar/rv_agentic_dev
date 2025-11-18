Absolutely, Mark. Here's a clean and structured **Markdown summary** of the [OpenAI Agents SDK documentation](https://openai.github.io/openai-agents-python/):

---

# 🧠 OpenAI Agents SDK Overview

The **OpenAI Agents SDK** is a lightweight, Python-first framework for building agentic AI applications. It’s designed to be minimal yet powerful, enabling orchestration of tools, models, and workflows with just a few primitives.

---

## 🚀 Key Concepts

- **Agents**: LLMs equipped with instructions and tools
- **Handoffs**: Delegate tasks between agents
- **Guardrails**: Validate inputs/outputs and break early on failure
- **Sessions**: Maintain conversation history across agent runs
- **Tools**: Turn any Python function into a validated tool
- **Tracing**: Visualize, debug, and monitor agent workflows

---

## 📦 Installation

```bash
pip install openai-agents
```

Set your API key:

```bash
export OPENAI_API_KEY=sk-...
```

---

## 🧪 Hello World Example

```python
from agents import Agent, Runner

agent = Agent(name="Assistant", instructions="You are a helpful assistant")
result = Runner.run_sync(agent, "Write a haiku about recursion in programming.")
print(result.final_output)
```

> Output:
> ```
> Code within the code,  
> Functions calling themselves,  
> Infinite loop's dance.
> ```

---

## 🛠 Features at a Glance

| Feature         | Description |
|----------------|-------------|
| Agent Loop      | Built-in loop for tool invocation and result handling |
| Python-First     | Use native Python for orchestration |
| Handoffs         | Delegate tasks between agents |
| Guardrails       | Parallel validation checks |
| Sessions         | Automatic state management |
| Function Tools   | Auto-schema generation with Pydantic |
| Tracing          | Built-in visualization and debugging |

---

## 📚 Modules & Utilities

- `agents.Agent`, `agents.Runner`
- `sessions.SQLAlchemySession`, `EncryptedSession`
- `tools.Tool`, `ToolContext`
- `tracing.Traces`, `Spans`, `Processor`
- `voice.OpenAIVoiceModelProvider`, `OpenAI STT`, `OpenAI TTS`

---


# Quickstart

## Create a project and virtual environment

You'll only need to do this once.

```bash
mkdir my_project
cd my_project
python -m venv .venv
```

### Activate the virtual environment

Do this every time you start a new terminal session.

```bash
source .venv/bin/activate
```

### Install the Agents SDK

```bash
pip install openai-agents # or `uv add openai-agents`, etc
```

### Set an OpenAI API key

If you don't have one, follow [these instructions](https://platform.openai.com/docs/quickstart#create-and-export-an-api-key) to create an OpenAI API key.

```bash
export OPENAI_API_KEY=sk-...
```

## Create your first agent

Agents are defined with instructions, a name, and optional config (such as `model_config`)

```python
from agents import Agent

agent = Agent(
    name="Math Tutor",
    instructions="You provide help with math problems. Explain your reasoning at each step and include examples",
)
```

## Add a few more agents

Additional agents can be defined in the same way. `handoff_descriptions` provide additional context for determining handoff routing

```python
from agents import Agent

history_tutor_agent = Agent(
    name="History Tutor",
    handoff_description="Specialist agent for historical questions",
    instructions="You provide assistance with historical queries. Explain important events and context clearly.",
)

math_tutor_agent = Agent(
    name="Math Tutor",
    handoff_description="Specialist agent for math questions",
    instructions="You provide help with math problems. Explain your reasoning at each step and include examples",
)
```

## Define your handoffs

On each agent, you can define an inventory of outgoing handoff options that the agent can choose from to decide how to make progress on their task.

```python
triage_agent = Agent(
    name="Triage Agent",
    instructions="You determine which agent to use based on the user's homework question",
    handoffs=[history_tutor_agent, math_tutor_agent]
)
```

## Run the agent orchestration

Let's check that the workflow runs and the triage agent correctly routes between the two specialist agents.

```python
from agents import Runner

async def main():
    result = await Runner.run(triage_agent, "What is the capital of France?")
    print(result.final_output)
```

## Add a guardrail

You can define custom guardrails to run on the input or output.

```python
from agents import GuardrailFunctionOutput, Agent, Runner
from pydantic import BaseModel


class HomeworkOutput(BaseModel):
    is_homework: bool
    reasoning: str

guardrail_agent = Agent(
    name="Guardrail check",
    instructions="Check if the user is asking about homework.",
    output_type=HomeworkOutput,
)

async def homework_guardrail(ctx, agent, input_data):
    result = await Runner.run(guardrail_agent, input_data, context=ctx.context)
    final_output = result.final_output_as(HomeworkOutput)
    return GuardrailFunctionOutput(
        output_info=final_output,
        tripwire_triggered=not final_output.is_homework,
    )
```

## Put it all together

Let's put it all together and run the entire workflow, using handoffs and the input guardrail.

```python
from agents import Agent, InputGuardrail, GuardrailFunctionOutput, Runner
from agents.exceptions import InputGuardrailTripwireTriggered
from pydantic import BaseModel
import asyncio

class HomeworkOutput(BaseModel):
    is_homework: bool
    reasoning: str

guardrail_agent = Agent(
    name="Guardrail check",
    instructions="Check if the user is asking about homework.",
    output_type=HomeworkOutput,
)

math_tutor_agent = Agent(
    name="Math Tutor",
    handoff_description="Specialist agent for math questions",
    instructions="You provide help with math problems. Explain your reasoning at each step and include examples",
)

history_tutor_agent = Agent(
    name="History Tutor",
    handoff_description="Specialist agent for historical questions",
    instructions="You provide assistance with historical queries. Explain important events and context clearly.",
)


async def homework_guardrail(ctx, agent, input_data):
    result = await Runner.run(guardrail_agent, input_data, context=ctx.context)
    final_output = result.final_output_as(HomeworkOutput)
    return GuardrailFunctionOutput(
        output_info=final_output,
        tripwire_triggered=not final_output.is_homework,
    )

triage_agent = Agent(
    name="Triage Agent",
    instructions="You determine which agent to use based on the user's homework question",
    handoffs=[history_tutor_agent, math_tutor_agent],
    input_guardrails=[
        InputGuardrail(guardrail_function=homework_guardrail),
    ],
)

async def main():
    # Example 1: History question
    try:
        result = await Runner.run(triage_agent, "who was the first president of the united states?")
        print(result.final_output)
    except InputGuardrailTripwireTriggered as e:
        print("Guardrail blocked this input:", e)

    # Example 2: General/philosophical question
    try:
        result = await Runner.run(triage_agent, "What is the meaning of life?")
        print(result.final_output)
    except InputGuardrailTripwireTriggered as e:
        print("Guardrail blocked this input:", e)

if __name__ == "__main__":
    asyncio.run(main())
```

## View your traces

To review what happened during your agent run, navigate to the [Trace viewer in the OpenAI Dashboard](https://platform.openai.com/traces) to view traces of your agent runs.

## Next steps

Learn how to build more complex agentic flows:

-   Learn about how to configure [Agents](agents.md).
-   Learn about [running agents](running_agents.md).
-   Learn about [tools](tools.md), [guardrails](guardrails.md) and [models](models/index.md).


---

# 🧪 OpenAI Agents SDK – Examples Overview

Explore categorized examples that showcase agent design patterns, tool usage, memory strategies, and multi-agent orchestration.

---

## 🧩 Categories & Highlights

### 🔁 `agent_patterns`
- Deterministic workflows  
- Agents as tools  
- Parallel execution  
- Conditional tool usage  
- Guardrails (I/O validation, streaming)  
- LLM as judge / router  

### 🧱 `basic`
- Hello world (GPT-5, open-weight models)  
- Agent lifecycle & dynamic prompts  
- Streaming outputs (text, items, args)  
- File handling (local/remote, images, PDFs)  
- Prompt templates & usage tracking  

### 🧑‍💼 `customer_service`
- Airline support system with agent handoffs and filtering

### 💰 `financial_research_agent`
- Structured financial analysis using agents + tools

### 🔄 `handoffs`
- Message filtering and agent delegation

### 🧠 `hosted_mcp` & `mcp`
- Filesystem, Git, SSE, and streamable HTTP examples  
- MCP prompt server integration

### 🧠 `memory`
- SQLite, Redis, SQLAlchemy, encrypted sessions  
- OpenAI session storage

### 🧠 `model_providers`
- LiteLLM integration  
- Custom model support

### ⚡ `realtime`
- Web apps, CLI, Twilio integration  
- Real-time agent experiences

### 🧠 `reasoning_content`
- Structured reasoning and output formatting

### 🔍 `research_bot`
- Multi-agent deep research workflows

### 🧰 `tools`
- Hosted tools: Web search, file/code interpreter, image generation

### 🗣️ `voice`
- TTS/STT agents with streaming voice support

---

Here’s a clean and modular **Markdown summary** of the [OpenAI Agents SDK – Agents Module](https://openai.github.io/openai-agents-python/agents/) page, tailored for your workflow, Mark:

---

# 🧠 OpenAI Agents SDK – Agents Module

Agents are the core building blocks in the SDK. Each agent is a configured LLM with tools, instructions, and optional context.

---

## ⚙️ Basic Configuration

```python
from agents import Agent, ModelSettings, function_tool

@function_tool
def get_weather(city: str) -> str:
    return f"The weather in {city} is sunny"

agent = Agent(
    name="Haiku agent",
    instructions="Always respond in haiku form",
    model="gpt-5-nano",
    tools=[get_weather],
)
```

### Key Parameters

| Parameter      | Description |
|----------------|-------------|
| `name`         | Unique identifier for the agent |
| `instructions` | System prompt or behavior guide |
| `model`        | LLM to use (e.g., GPT-5, open-weight) |
| `tools`        | Python functions exposed as callable tools |
| `output_type`  | Structured output via Pydantic or dataclass |
| `context`      | Dependency injection object passed to tools and agents |

---

## 🧩 Multi-Agent Design Patterns

### Manager Pattern (Agents as Tools)

```python
customer_facing_agent = Agent(
    name="Customer-facing agent",
    instructions="Handle user queries and delegate to experts.",
    tools=[
        booking_agent.as_tool(tool_name="booking_expert"),
        refund_agent.as_tool(tool_name="refund_expert"),
    ],
)
```

### Handoff Pattern

```python
triage_agent = Agent(
    name="Triage agent",
    instructions="Delegate to booking or refund agent based on query.",
    handoffs=[booking_agent, refund_agent],
)
```

---

## 🧠 Advanced Features

### Dynamic Instructions

```python
def dynamic_instructions(context, agent) -> str:
    return f"User name is {context.context.name}. Help them accordingly."

agent = Agent(
    name="Dynamic agent",
    instructions=dynamic_instructions,
)
```

### Lifecycle Hooks

Subclass `AgentHooks` to observe or modify agent behavior during execution.

### Guardrails

Run parallel validations on input/output to enforce constraints or break early.

---

## 🔧 Tool Use Behavior

| Behavior               | Description |
|------------------------|-------------|
| `run_llm_again`        | Default: LLM processes tool output |
| `stop_on_first_tool`   | Use first tool output directly |
| `StopAtTools([...])`   | Stop if specific tool is used |
| `custom_tool_handler`  | Custom logic to decide final output |


---

# 🏃‍♂️ Running Agents – OpenAI Agents SDK

The `Runner` class orchestrates agent execution. It supports synchronous, asynchronous, and streaming runs with full traceability and handoff logic.

---

## ⚙️ Basic Usage

```python
from agents import Agent, Runner

agent = Agent(name="Assistant", instructions="You are a helpful assistant")

# Async run
result = await Runner.run(agent, "Write a haiku about recursion in programming.")
print(result.final_output)

# Sync run
result = Runner.run_sync(agent, "Write a haiku about recursion in programming.")
```

---

## 🔁 Agent Loop Logic

1. LLM receives input and produces output.
2. If `final_output` is returned → loop ends.
3. If `handoff` occurs → switch agent and rerun.
4. If `tool_calls` are made → execute tools, append results, rerun.
5. If `max_turns` exceeded → raise `MaxTurnsExceeded`.

---

## 🔊 Streaming

```python
result = await Runner.run_streamed(agent, "Streamed haiku please.")
for event in result.stream_events():
    print(event)
```

- Streams partial outputs in real time.
- Final result includes all outputs.

---

## 🛠️ Run Config Options

| Parameter                  | Purpose |
|---------------------------|---------|
| `model`, `model_provider` | Override agent model globally |
| `model_settings`          | Set temperature, top_p, etc. |
| `input_guardrails`        | Validate incoming messages |
| `output_guardrails`       | Validate final responses |
| `handoff_input_filter`    | Modify input during handoff |
| `tracing_disabled`        | Disable trace logging |
| `trace_metadata`          | Attach metadata to traces |
| `workflow_name`, `trace_id`, `group_id` | Organize trace sessions |

---

## 💬 Conversation Management

### Manual

```python
new_input = result.to_input_list() + [{"role": "user", "content": "Next question"}]
result = await Runner.run(agent, new_input)
```

### Automatic (Sessions)

```python
from agents import SQLiteSession

session = SQLiteSession("conversation_123")
result = await Runner.run(agent, "First question", session=session)
```

- Sessions auto-manage history per ID.

### Server-Managed

```python
from openai import AsyncOpenAI

conversation = await client.conversations.create()
conv_id = conversation.id
result = await Runner.run(agent, "First question", conversation_id=conv_id)
```

---

## 🧠 Long-Running Agents

- Integrate with **Temporal** for durable workflows and human-in-the-loop tasks.

---

## 🚨 Exceptions

| Exception                          | Trigger |
|-----------------------------------|---------|
| `MaxTurnsExceeded`                | Loop exceeds allowed turns |
| `ModelBehaviorError`              | Malformed output or tool misuse |
| `UserError`                       | SDK misuse or bad config |
| `InputGuardrailTripwireTriggered` | Input validation failed |
| `OutputGuardrailTripwireTriggered`| Output validation failed |

---
Here’s a comprehensive **Markdown summary** of the [Sessions module](https://openai.github.io/openai-agents-python/sessions/) in the OpenAI Agents SDK—perfect for your context-aware enrichment flows, Mark:

---

# 🧠 Sessions – OpenAI Agents SDK

Sessions provide built-in memory for multi-turn conversations, allowing agents to maintain context across runs without manual state management.

---

## ⚡ Quick Start

```python
from agents import Agent, Runner, SQLiteSession

agent = Agent(name="Assistant", instructions="Reply concisely.")
session = SQLiteSession("conversation_123")

result = await Runner.run(agent, "What city is the Golden Gate Bridge in?", session=session)
print(result.final_output)  # "San Francisco"

result = await Runner.run(agent, "What state is it in?", session=session)
print(result.final_output)  # "California"
```

---

## 🧩 How It Works

- **Before each run**: Prepends session history to input
- **After each run**: Stores new items (user input, assistant response, tool calls)
- **Context preservation**: Maintains full history across turns

---

## 🛠️ Memory Operations

```python
session = SQLiteSession("user_123", "conversations.db")

items = await session.get_items()
await session.add_items([{"role": "user", "content": "Hello"}])
last_item = await session.pop_item()
await session.clear_session()
```

---

## 🔄 Correction Flow

```python
assistant_item = await session.pop_item()
user_item = await session.pop_item()

result = await Runner.run(agent, "What's 2 + 3?", session=session)
```

---

## 🧠 Session Types

| Type                        | Description |
|-----------------------------|-------------|
| `OpenAIConversationsSession` | Uses OpenAI-hosted memory |
| `SQLiteSession`             | Lightweight local memory |
| `SQLAlchemySession`         | Production-grade DB support |
| `AdvancedSQLiteSession`     | Branching, analytics, structured queries |
| `EncryptedSession`          | Wraps any session with encryption + TTL |

---

## 🧠 Advanced SQLite Example

```python
from agents.extensions.memory import AdvancedSQLiteSession

session = AdvancedSQLiteSession("user_123", "conversations.db", create_tables=True)
await session.store_run_usage(result)
await session.create_branch_from_turn(2)
```

---

## 🔐 Encrypted Session Example

```python
from agents.extensions.memory import EncryptedSession, SQLAlchemySession

underlying = SQLAlchemySession.from_url("user_123", url="sqlite:///conversations.db")
session = EncryptedSession("user_123", underlying, encryption_key="secret", ttl=600)
```

---

## 🧪 Custom Session Implementation

```python
class MyCustomSession(SessionABC):
    async def get_items(self): ...
    async def add_items(self, items): ...
    async def pop_item(self): ...
    async def clear_session(self): ...
```

---

## 🧠 Session Management Tips

- Use meaningful IDs: `"user_123"`, `"thread_xyz"`, `"ticket_456"`
- Share sessions across agents for unified memory
- Use file-based or SQLAlchemy for persistence
- Wrap with encryption for secure, expiring memory

---

# 🗃️ SQLAlchemySession – OpenAI Agents SDK

`SQLAlchemySession` provides a **persistent, async-compatible session backend** using any SQLAlchemy-supported database (PostgreSQL, MySQL, SQLite, etc.).

---

## 📦 Installation

```bash
pip install openai-agents[sqlalchemy]
```

This installs:
- `SQLAlchemy >= 2.0`
- `asyncpg` (recommended for PostgreSQL)

---

## ⚡ Quick Start (In-Memory SQLite)

```python
from agents import Agent, Runner
from agents.extensions.memory import SQLAlchemySession

agent = Agent("Assistant")
session = SQLAlchemySession.from_url(
    "user-123",
    url="sqlite+aiosqlite:///:memory:",
    create_tables=True
)

result = await Runner.run(agent, "Hello", session=session)
print(result.final_output)
```

---

## 🛠️ Using Existing Engine (e.g., PostgreSQL)

```python
from sqlalchemy.ext.asyncio import create_async_engine
from agents.extensions.memory import SQLAlchemySession

engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/db")
session = SQLAlchemySession("user-456", engine=engine, create_tables=True)

result = await Runner.run(agent, "Hello", session=session)
await engine.dispose()
```

---

## 🧠 Why Use SQLAlchemySession?

| Feature                | Benefit |
|------------------------|---------|
| Async support          | Scales with modern Python apps |
| Production-ready       | Works with PostgreSQL, MySQL, SQLite |
| Persistent memory      | Stores full conversation history |
| Flexible integration   | Use with existing SQLAlchemy engines |
| Table auto-creation    | Optional schema bootstrap with `create_tables=True` |

---
Here’s a structured **Markdown summary** of the [Results module](https://openai.github.io/openai-agents-python/results/) in the OpenAI Agents SDK—especially useful for debugging, chaining, and introspection in multi-agent flows like yours, Mark:

---

# 📊 Results – OpenAI Agents SDK

When you run an agent via `Runner.run`, `run_sync`, or `run_streamed`, you receive a result object containing rich metadata and outputs.

---

## 🧠 Result Types

| Method Used         | Result Class         |
|---------------------|----------------------|
| `run` / `run_sync`  | `RunResult`          |
| `run_streamed`      | `RunResultStreaming` |
| Both inherit from   | `RunResultBase`      |

---

## 🔚 `final_output`

- Contains the final output from the last agent that ran.
- Type: `str` or `last_agent.output_type`
- Dynamic typing due to handoffs—can’t be statically typed.

---

## 🔁 `to_input_list()`

- Converts the result into a list of input items.
- Useful for chaining runs or appending new user input.

```python
next_input = result.to_input_list() + [{"role": "user", "content": "Next question"}]
```

---

## 🧑‍💼 `last_agent`

- Tracks which agent produced the final output.
- Useful for routing follow-up messages or storing agent state.

---

## 🆕 `new_items`

- Items generated during the run:
  - `MessageOutputItem`: LLM message
  - `ToolCallItem`: Tool invocation
  - `ToolCallOutputItem`: Tool response
  - `HandoffCallItem`: Handoff trigger
  - `HandoffOutputItem`: Handoff result
  - `ReasoningItem`: LLM reasoning trace

---

## 🛡️ Guardrail Results

- `input_guardrail_results` and `output_guardrail_results`
- Contains validation outcomes—useful for logging or debugging.

---

## 🧾 Raw Responses

- `raw_responses`: Raw `ModelResponse` objects from the LLM
- Includes full payloads for inspection or replay

---

## 📥 Original Input

- `input`: The original input passed to the run method
- Useful for auditing or reconstructing session state

---
Here’s a structured **Markdown summary** of the [Streaming module](https://openai.github.io/openai-agents-python/streaming/) in the OpenAI Agents SDK—especially useful for real-time feedback loops and progressive enrichment, Mark:

---

# 🔊 Streaming – OpenAI Agents SDK

Streaming lets you subscribe to updates during an agent run. It’s ideal for showing progress, partial responses, and tool execution in real time.

---

## ⚡ Basic Streaming Setup

```python
from agents import Agent, Runner

agent = Agent(name="Joker", instructions="You are a helpful assistant.")
result = Runner.run_streamed(agent, input="Please tell me 5 jokes.")

async for event in result.stream_events():
    if event.type == "raw_response_event":
        print(event.data.delta, end="", flush=True)
```

---

## 🧠 Event Types

### 1. `RawResponsesStreamEvent`
- Direct LLM output in OpenAI Responses API format
- Includes:
  - `response.created`
  - `response.output_text.delta`
- Ideal for token-by-token streaming

### 2. `RunItemStreamEvent`
- Higher-level events:
  - `message_output_item`
  - `tool_call_item`
  - `tool_call_output_item`
- Useful for structured progress updates

### 3. `AgentUpdatedStreamEvent`
- Triggered when agent changes (e.g., handoff)
- Enables dynamic agent tracking

---

## 🧪 Example: Tool + Streaming

```python
import asyncio, random
from agents import Agent, Runner, function_tool, ItemHelpers

@function_tool
def how_many_jokes() -> int:
    return random.randint(1, 10)

agent = Agent(
    name="Joker",
    instructions="Call `how_many_jokes` then tell that many jokes.",
    tools=[how_many_jokes],
)

result = Runner.run_streamed(agent, input="Hello")

async for event in result.stream_events():
    if event.type == "agent_updated_stream_event":
        print(f"Agent updated: {event.new_agent.name}")
    elif event.type == "run_item_stream_event":
        if event.item.type == "tool_call_item":
            print("-- Tool was called")
        elif event.item.type == "tool_call_output_item":
            print(f"-- Tool output: {event.item.output}")
        elif event.item.type == "message_output_item":
            print(f"-- Message output:\n{ItemHelpers.text_message_output(event.item)}")
```

---

## 🧩 Use Cases

- Real-time UI updates
- Progressive enrichment feedback
- Debugging agent behavior
- Monitoring tool execution and handoffs

---

Here’s a concise and structured **Markdown summary** of the [REPL Utility](https://openai.github.io/openai-agents-python/repl/) in the OpenAI Agents SDK—ideal for quick, interactive testing loops, Mark:

---

# 🧪 REPL Utility – OpenAI Agents SDK

The SDK provides `run_demo_loop` for fast, interactive testing of agent behavior directly in your terminal.

---

## ⚡ Quickstart Example

```python
import asyncio
from agents import Agent, run_demo_loop

async def main() -> None:
    agent = Agent(name="Assistant", instructions="You are a helpful assistant.")
    await run_demo_loop(agent)

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🧠 What It Does

- Starts an **interactive chat session** in your terminal
- Prompts for user input in a loop
- **Maintains full conversation history** between turns
- **Streams model output** in real time
- Ends session on `quit`, `exit`, or `Ctrl-D`

---

## 🧩 Use Cases

- Rapid prototyping of agent behavior
- Debugging tool invocation and handoffs
- Testing memory and session logic
- Live demos or CLI-based agent interfaces

---
Here’s a detailed and modular **Markdown summary** of the [Tools module](https://openai.github.io/openai-agents-python/tools/) in the OpenAI Agents SDK—perfect for your agentic enrichment flows, Mark:

---

# 🛠️ Tools – OpenAI Agents SDK

Tools let agents take actions: fetch data, run code, call APIs, or even use a computer. The SDK supports three tool types:

---

## 🔧 Tool Types

| Type             | Description |
|------------------|-------------|
| **Hosted Tools** | Built-in tools on OpenAI servers (e.g., web search, code interpreter) |
| **Function Tools** | Python functions auto-wrapped as tools |
| **Agents as Tools** | Use agents as callable tools without handoff |

---

## 🌐 Hosted Tools

Available when using `OpenAIResponsesModel`:

- `WebSearchTool`: Search the web
- `FileSearchTool`: Query OpenAI vector stores
- `ComputerTool`: Automate computer tasks
- `CodeInterpreterTool`: Run sandboxed code
- `HostedMCPTool`: Access remote MCP server tools
- `ImageGenerationTool`: Generate images
- `LocalShellTool`: Run shell commands locally

```python
from agents import Agent, WebSearchTool, FileSearchTool

agent = Agent(
    name="Assistant",
    tools=[
        WebSearchTool(),
        FileSearchTool(max_num_results=3, vector_store_ids=["VECTOR_STORE_ID"]),
    ],
)
```

---

## 🧪 Function Tools

Use any Python function as a tool:

```python
from agents import function_tool

@function_tool
def fetch_weather(location: dict) -> str:
    """Fetch weather for a location."""
    return "sunny"
```

- Auto-parses:
  - Function name → tool name
  - Docstring → description
  - Args → input schema (via Pydantic)
- Supports `TypedDict`, `BaseModel`, sync/async, and context injection

---

## 🖼️ Returning Images or Files

Function tools can return:

- `ToolOutputImage` / `ToolOutputImageDict`
- `ToolOutputFileContent` / `ToolOutputFileContentDict`
- `ToolOutputText` / `ToolOutputTextDict`

---

## 🧰 Custom Function Tools

Manually define a tool:

```python
from agents import FunctionTool

tool = FunctionTool(
  name="process_user",
  description="Processes user data",
  params_json_schema=FunctionArgs.model_json_schema(),
  on_invoke_tool=run_function,
)
```

---

## 🧠 Agents as Tools

Use agents as callable tools:

```python
orchestrator = Agent(
  name="orchestrator",
  instructions="Use tools to translate.",
  tools=[
    spanish_agent.as_tool(tool_name="translate_to_spanish"),
    french_agent.as_tool(tool_name="translate_to_french"),
  ],
)
```

---

## 🧪 Conditional Tool Enabling

Enable tools dynamically:

```python
def french_enabled(ctx, agent) -> bool:
    return ctx.context.language_preference == "french_spanish"

french_agent.as_tool(is_enabled=french_enabled)
```

Supports:
- `True` / `False`
- Sync / async functions

---

## ⚠️ Error Handling

Customize tool failure responses:

```python
def my_error(ctx, error) -> str:
    return "Internal error. Try again later."

@function_tool(failure_error_function=my_error)
def get_user_profile(user_id: str) -> str:
    ...
```

---

## 🧩 Output Extraction

Customize tool-agent output:

```python
async def extract_json(run_result) -> str:
    for item in reversed(run_result.new_items):
        if item.output.strip().startswith("{"):
            return item.output.strip()
    return "{}"

json_tool = data_agent.as_tool(custom_output_extractor=extract_json)
```

---
Here’s a structured and example-rich **Markdown summary** of the [Handoffs module](https://openai.github.io/openai-agents-python/handoffs/) in the OpenAI Agents SDK—perfect for orchestrating multi-agent delegation in your enrichment flows, Mark:

---

# 🔁 Handoffs – OpenAI Agents SDK

Handoffs allow one agent to **delegate control** to another agent. This is ideal when different agents specialize in distinct tasks (e.g., billing, refunds, FAQs).

---

## ⚙️ Basic Usage

```python
from agents import Agent, handoff

billing_agent = Agent(name="Billing agent")
refund_agent = Agent(name="Refund agent")

triage_agent = Agent(
    name="Triage agent",
    handoffs=[billing_agent, handoff(refund_agent)]
)
```

- Each handoff becomes a tool: `transfer_to_<agent_name>`
- Agents can be passed directly or wrapped with `handoff()` for customization

---

## 🛠️ Customizing with `handoff()`

```python
from agents import handoff, RunContextWrapper

def on_handoff(ctx: RunContextWrapper[None]):
    print("Handoff triggered")

handoff_obj = handoff(
    agent=Agent(name="My agent"),
    on_handoff=on_handoff,
    tool_name_override="custom_handoff_tool",
    tool_description_override="Custom description"
)
```

### Parameters

| Param                  | Description |
|------------------------|-------------|
| `agent`                | Target agent |
| `tool_name_override`   | Custom tool name |
| `tool_description_override` | Custom description |
| `on_handoff`           | Callback when handoff is triggered |
| `input_type`           | Expected input schema |
| `input_filter`         | Modify input history |
| `is_enabled`           | Boolean or function to toggle handoff |

---

## 🧾 Input Schema Example

```python
from pydantic import BaseModel

class EscalationData(BaseModel):
    reason: str

async def on_handoff(ctx, input_data: EscalationData):
    print(f"Escalated for: {input_data.reason}")

handoff_obj = handoff(
    agent=Agent(name="Escalation agent"),
    on_handoff=on_handoff,
    input_type=EscalationData
)
```

---

## 🧹 Input Filters

Modify what the next agent sees:

```python
from agents.extensions import handoff_filters

handoff_obj = handoff(
    agent=Agent(name="FAQ agent"),
    input_filter=handoff_filters.remove_all_tools
)
```

- Built-in filters: remove tools, redact sensitive data, etc.

---

## 🧠 Recommended Prompt Prefix

Ensure LLMs understand handoffs:

```python
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX

Agent(
    name="Billing agent",
    instructions=f"""{RECOMMENDED_PROMPT_PREFIX} You are responsible for billing inquiries..."""
)
```

Or use:

```python
from agents.extensions.handoff_prompt import prompt_with_handoff_instructions

instructions = prompt_with_handoff_instructions("You are responsible for billing inquiries...")
```

---

Here’s a comprehensive **Markdown summary** of the [Tracing module](https://openai.github.io/openai-agents-python/tracing/) in the OpenAI Agents SDK—especially useful for debugging, observability, and performance monitoring in your multi-agent flows, Mark:

---

# 📈 Tracing – OpenAI Agents SDK

The SDK includes **built-in tracing** to record everything that happens during an agent run: LLM generations, tool calls, handoffs, guardrails, audio events, and custom spans.

---

## 🧠 Core Concepts

### 🔍 Traces
- Represent a full agent workflow
- Properties:
  - `workflow_name`
  - `trace_id` (format: `trace_<32_alphanumeric>`)
  - `group_id` (e.g., conversation ID)
  - `metadata`
  - `disabled`

### 🧩 Spans
- Represent individual operations
- Properties:
  - `started_at`, `ended_at`
  - `trace_id`, `parent_id`
  - `span_data` (e.g., `AgentSpanData`, `GenerationSpanData`)

---

## ⚙️ Default Tracing Behavior

| Operation           | Span Wrapper         |
|---------------------|----------------------|
| Agent run           | `agent_span()`       |
| LLM generation      | `generation_span()`  |
| Tool call           | `function_span()`    |
| Guardrail check     | `guardrail_span()`   |
| Handoff             | `handoff_span()`     |
| STT (speech input)  | `transcription_span()` |
| TTS (speech output) | `speech_span()`      |

> All wrapped under `trace()` with default name `"Agent workflow"`

---

## 🧪 Manual Tracing

```python
from agents import Agent, Runner, trace

agent = Agent(name="Joke generator", instructions="Tell funny jokes.")

with trace("Joke workflow"):
    first = await Runner.run(agent, "Tell me a joke")
    second = await Runner.run(agent, f"Rate this joke: {first.final_output}")
```

---

## 🧬 Custom Spans

```python
from agents import custom_span

with custom_span("custom_logic"):
    # Your custom logic here
```

- Automatically nested under current trace/span
- Works with concurrency via `contextvars`

---

## 🔐 Sensitive Data Control

| Span Type         | Data Captured         | Config Flag |
|-------------------|------------------------|-------------|
| `generation_span` | LLM input/output       | `trace_include_sensitive_data` |
| `function_span`   | Tool input/output      | `trace_include_sensitive_data` |
| `speech_span`     | Base64 PCM audio       | `trace_include_sensitive_audio_data` |

---

## 🔌 External Trace Processors

You can push traces to other platforms:

### Add a processor

```python
from agents import add_trace_processor

add_trace_processor(MyCustomProcessor())
```

### Replace all processors

```python
from agents import set_trace_processors

set_trace_processors([MyCustomProcessor()])
```

---

## 🌍 Tracing with Non-OpenAI Models

```python
from agents import set_tracing_export_api_key
set_tracing_export_api_key(os.environ["OPENAI_API_KEY"])
```

- Enables tracing even with LiteLLM or other models

---

## 🧠 Supported Observability Platforms

| Platform         | Type         |
|------------------|--------------|
| LangSmith        | Hosted       |
| Langfuse         | OSS/Hosted   |
| MLflow           | OSS/Databricks |
| AgentOps         | Hosted       |
| Logfire          | OSS          |
| Agenta           | OSS          |
| Scorecard        | Hosted       |
| Braintrust       | Hosted       |
| Galileo          | Hosted       |
| Portkey AI       | Hosted       |
| LangDB           | OSS          |

---
Here’s a structured and example-rich **Markdown summary** of the [Context Management](https://openai.github.io/openai-agents-python/context/) module in the OpenAI Agents SDK—especially relevant for your enrichment flows and tool orchestration, Mark:

---

# 🧠 Context Management – OpenAI Agents SDK

“Context” refers to two distinct layers:
1. **Local context**: Data and dependencies available to your code (e.g., user info, loggers, fetchers)
2. **LLM-visible context**: Data the model sees via instructions, input history, or tools

---

## 🧩 Local Context

Use `RunContextWrapper[T]` to pass structured context to tools, lifecycle hooks, and callbacks.

### Example

```python
from dataclasses import dataclass
from agents import Agent, Runner, RunContextWrapper, function_tool

@dataclass
class UserInfo:
    name: str
    uid: int

@function_tool
async def fetch_user_age(wrapper: RunContextWrapper[UserInfo]) -> str:
    return f"The user {wrapper.context.name} is 47 years old"

agent = Agent[UserInfo](
    name="Assistant",
    tools=[fetch_user_age],
)

user_info = UserInfo(name="John", uid=123)
result = await Runner.run(agent, "What is the age of the user?", context=user_info)
```

> ✅ All tools and hooks must use the same context type per run.

---

## 🛠 ToolContext (Advanced)

Use `ToolContext[T]` when you need metadata about the tool call:

```python
from agents.tool_context import ToolContext

@function_tool
def get_weather(ctx: ToolContext[WeatherContext], city: str) -> Weather:
    print(f"Tool: {ctx.tool_name}, Call ID: {ctx.tool_call_id}, Args: {ctx.tool_arguments}")
    ...
```

### ToolContext adds:
- `tool_name`
- `tool_call_id`
- `tool_arguments`

---

## 🧠 LLM Context

LLMs only see what’s in the conversation history. To expose data:

| Method                     | Use Case |
|----------------------------|----------|
| Agent instructions         | Static or dynamic system prompts |
| Input messages             | Inline user/system messages |
| Function tools             | On-demand data fetching |
| Retrieval / Web search     | External grounding |

### Dynamic Instructions

```python
def dynamic_instructions(ctx, agent) -> str:
    return f"User is {ctx.context.name}. Respond accordingly."
```

---

## 🧩 Best Practices

- Use `dataclass` or `Pydantic` for context objects
- Keep context consistent across tools and agents
- Use `ToolContext` for tool-level metadata
- Inject user/session data via local context—not LLM-visible unless needed
- Use dynamic instructions for adaptive behavior

---
Here’s a detailed and example-rich **Markdown summary** of the [Guardrails module](https://openai.github.io/openai-agents-python/guardrails/) in the OpenAI Agents SDK—especially useful for protecting your agents from misuse or unintended logic paths, Mark:

---

# 🛡️ Guardrails – OpenAI Agents SDK

Guardrails run **in parallel** to agent execution to validate input and output. They can **halt execution early** if a tripwire is triggered—saving cost, time, and preventing misuse.

---

## 🧠 Types of Guardrails

| Type            | Purpose |
|------------------|--------|
| **Input Guardrails**  | Validate user input before agent runs |
| **Output Guardrails** | Validate final agent output before returning |

---

## ⚙️ Input Guardrail Flow

1. Receives same input as agent
2. Runs guardrail function → returns `GuardrailFunctionOutput`
3. If `.tripwire_triggered == True` → raises `InputGuardrailTripwireTriggered`

### Example

```python
from pydantic import BaseModel
from agents import Agent, GuardrailFunctionOutput, input_guardrail, Runner, InputGuardrailTripwireTriggered

class MathHomeworkOutput(BaseModel):
    is_math_homework: bool
    reasoning: str

guardrail_agent = Agent(
    name="Guardrail check",
    instructions="Detect math homework requests.",
    output_type=MathHomeworkOutput,
)

@input_guardrail
async def math_guardrail(ctx, agent, input) -> GuardrailFunctionOutput:
    result = await Runner.run(guardrail_agent, input, context=ctx.context)
    return GuardrailFunctionOutput(
        output_info=result.final_output,
        tripwire_triggered=result.final_output.is_math_homework,
    )

agent = Agent(
    name="Customer support agent",
    instructions="Help customers with questions.",
    input_guardrails=[math_guardrail],
)

try:
    await Runner.run(agent, "Can you solve 2x + 3 = 11?")
except InputGuardrailTripwireTriggered:
    print("Math homework guardrail tripped")
```

---

## ⚙️ Output Guardrail Flow

1. Receives final agent output
2. Runs guardrail function → returns `GuardrailFunctionOutput`
3. If `.tripwire_triggered == True` → raises `OutputGuardrailTripwireTriggered`

### Example

```python
from pydantic import BaseModel
from agents import Agent, GuardrailFunctionOutput, output_guardrail, Runner, OutputGuardrailTripwireTriggered

class MessageOutput(BaseModel):
    response: str

class MathOutput(BaseModel):
    reasoning: str
    is_math: bool

guardrail_agent = Agent(
    name="Guardrail check",
    instructions="Detect math content in output.",
    output_type=MathOutput,
)

@output_guardrail
async def math_guardrail(ctx, agent, output: MessageOutput) -> GuardrailFunctionOutput:
    result = await Runner.run(guardrail_agent, output.response, context=ctx.context)
    return GuardrailFunctionOutput(
        output_info=result.final_output,
        tripwire_triggered=result.final_output.is_math,
    )

agent = Agent(
    name="Customer support agent",
    instructions="Help customers with questions.",
    output_guardrails=[math_guardrail],
    output_type=MessageOutput,
)

try:
    await Runner.run(agent, "Can you solve 2x + 3 = 11?")
except OutputGuardrailTripwireTriggered:
    print("Math output guardrail tripped")
```

---

## 🔥 Tripwires

- Tripwires are boolean flags that trigger exceptions.
- They **immediately halt agent execution** when triggered.

---

## 🧩 Design Notes

- Guardrails are defined on the agent—not passed to `Runner.run`
- This keeps logic colocated and readable
- Input guardrails only run if the agent is the **first** in the chain
- Output guardrails only run if the agent is the **last**

---
Here’s a structured and example-rich **Markdown summary** of the [Multi-Agent Orchestration](https://openai.github.io/openai-agents-python/multi_agent/) module in the OpenAI Agents SDK—perfect for your modular enrichment pipelines and fallback delegation strategies, Mark:

---

# 🤖 Orchestrating Multiple Agents – OpenAI Agents SDK

Orchestration defines **how agents collaborate**: who runs when, how decisions are made, and how tasks are delegated. The SDK supports two main orchestration strategies:

---

## 🧠 Orchestrating via LLM

Let the LLM decide the flow using tools and handoffs.

### 🔧 Example Use Case

A **research agent** with:
- `WebSearchTool` for online info
- `FileSearchTool` for proprietary data
- `ComputerTool` for local actions
- `CodeInterpreterTool` for analysis
- Handoffs to:
  - `PlannerAgent`
  - `WriterAgent`
  - `CritiqueAgent`

### ✅ Best Practices

- **Prompt engineering**: Clearly define tool usage and constraints
- **Self-reflection**: Let agents critique and improve their own output
- **Specialization**: Use focused agents for specific tasks
- **Evals**: Continuously test and refine agent behavior

---

## 🧩 Orchestrating via Code

Use deterministic logic to control agent flow.

### 🔧 Patterns

| Pattern | Description |
|--------|-------------|
| **Structured output routing** | Use agent output to select next agent |
| **Sequential chaining** | Output of one agent feeds into the next |
| **Loop with evaluator** | Run agent in loop until output passes eval |
| **Parallel execution** | Use `asyncio.gather()` for independent tasks |

### 🧠 Example: Blog Pipeline

1. `ResearchAgent` → gathers info  
2. `OutlineAgent` → drafts structure  
3. `WriterAgent` → writes content  
4. `CritiqueAgent` → reviews  
5. Loop until `EvaluatorAgent` approves

---

## 🔀 Mixing Strategies

You can combine both orchestration styles:
- Use LLMs for open-ended planning
- Use code for deterministic routing, retries, or parallelism

---

Here’s a structured **Markdown summary** of the [Usage module](https://openai.github.io/openai-agents-python/usage/) in the OpenAI Agents SDK—especially useful for tracking cost, optimizing token usage, and debugging agent runs, Mark:

---

# 📊 Usage Tracking – OpenAI Agents SDK

The SDK automatically tracks **token usage** and **API calls** for every agent run. This helps monitor cost, enforce limits, and log analytics.

---

## 🧠 What Is Tracked

| Metric              | Description |
|---------------------|-------------|
| `requests`          | Number of LLM API calls made |
| `input_tokens`      | Total input tokens sent |
| `output_tokens`     | Total output tokens received |
| `total_tokens`      | Sum of input + output |
| `details`           | Breakdown of cached vs reasoning tokens |

---

## ⚙️ Accessing Usage from a Run

```python
result = await Runner.run(agent, "What's the weather in Tokyo?")
usage = result.context_wrapper.usage

print("Requests:", usage.requests)
print("Input tokens:", usage.input_tokens)
print("Output tokens:", usage.output_tokens)
print("Total tokens:", usage.total_tokens)
```

> Usage is aggregated across all model calls—including tool invocations and handoffs.

---

## 🔧 Enabling Usage with LiteLLM Models

LiteLLM doesn’t report usage by default. Enable it manually:

```python
from agents import Agent, ModelSettings
from agents.extensions.models.litellm_model import LitellmModel

agent = Agent(
    name="Assistant",
    model=LitellmModel(model="your/model", api_key="..."),
    model_settings=ModelSettings(include_usage=True),
)

result = await Runner.run(agent, "What's the weather in Tokyo?")
print(result.context_wrapper.usage.total_tokens)
```

---

## 🧠 Usage with Sessions

Each `Runner.run(...)` returns usage for that specific run—even when using sessions:

```python
session = SQLiteSession("my_conversation")

first = await Runner.run(agent, "Hi!", session=session)
print(first.context_wrapper.usage.total_tokens)

second = await Runner.run(agent, "Can you elaborate?", session=session)
print(second.context_wrapper.usage.total_tokens)
```

> Note: Sessions preserve context, so input token count may increase over time.

---

## 🔁 Usage in Hooks

You can access usage inside lifecycle hooks:

```python
class MyHooks(RunHooks):
    async def on_agent_end(self, context: RunContextWrapper, agent: Agent, output: Any) -> None:
        u = context.usage
        print(f"{agent.name} → {u.requests} requests, {u.total_tokens} total tokens")
```

---

## 📚 API References

- `Usage`: Usage tracking data structure
- `RunContextWrapper`: Access usage from run context
- `RunHooks`: Hook into usage tracking lifecycle

---
Here’s a comprehensive and example-rich **Markdown summary** of the [Models module](https://openai.github.io/openai-agents-python/models/) in the OpenAI Agents SDK—especially useful for your multi-agent orchestration and provider-aware deployments, Mark:

---

# 🧠 Models – OpenAI Agents SDK

The SDK supports **OpenAI models** and **non-OpenAI models** via LiteLLM and other integration strategies. You can mix and match models across agents and workflows.

---

## 🧩 OpenAI Models

### Default Model

- If no model is specified, defaults to `gpt-4.1`
- Set globally via environment variable:

```bash
export OPENAI_DEFAULT_MODEL=gpt-5
```

### GPT-5 Variants

| Model         | Description |
|---------------|-------------|
| `gpt-5`       | Full reasoning |
| `gpt-5-mini`  | Lower latency |
| `gpt-5-nano`  | Fastest, minimal reasoning |

> SDK applies sensible defaults: `reasoning.effort="low"`, `verbosity="low"`

### Custom Settings

```python
from agents import Agent, ModelSettings
from openai.types.shared import Reasoning

agent = Agent(
  name="My Agent",
  instructions="You're a helpful agent.",
  model="gpt-5-mini",
  model_settings=ModelSettings(
    reasoning=Reasoning(effort="minimal"),
    verbosity="low"
  )
)
```

---

## 🔄 Mixing Models

You can assign different models to different agents:

```python
triage_agent = Agent(
  name="Triage agent",
  instructions="Route based on language.",
  handoffs=[spanish_agent, english_agent],
  model="gpt-5"
)

spanish_agent = Agent(model="gpt-5-mini", ...)
english_agent = Agent(model="gpt-4.1", ...)
```

---

## 🌐 Non-OpenAI Models (via LiteLLM)

Install:

```bash
pip install "openai-agents[litellm]"
```

Use:

```python
Agent(model="litellm/anthropic/claude-3-5-sonnet-20240620")
Agent(model="litellm/gemini/gemini-2.5-flash-preview-04-17")
```

---

## 🧠 Other Integration Options

| Method                          | Use Case |
|---------------------------------|----------|
| `set_default_openai_client()`   | Global override with custom OpenAI-compatible client |
| `Agent.model`                   | Per-agent model assignment |
| `Runner.run(..., model_provider=...)` | Per-run model provider override |

---

## ⚠️ Common Issues

- **Tracing 401 errors**: Use `set_tracing_disabled()` or provide OpenAI API key for trace uploads
- **Responses API support**: Most non-OpenAI providers don’t support it—use Chat Completions API instead
- **Structured outputs**: Some providers don’t support JSON schema enforcement—may return malformed JSON

---

## 🧩 Best Practices

- Use `ModelSettings` to control temperature, reasoning effort, verbosity
- Avoid mixing model shapes (Responses vs Chat Completions) unless necessary
- Filter unsupported tools or multimodal inputs for providers with limited capabilities

---
Here’s a clean and modular **Markdown summary** of the [Configuring the SDK](https://openai.github.io/openai-agents-python/config/) section of the OpenAI Agents SDK—especially useful for customizing runtime behavior, logging, and API access in your agentic workflows, Mark:

---

# ⚙️ Configuring the SDK – OpenAI Agents SDK

This module lets you configure API keys, clients, tracing, and logging behavior across your agent stack.

---

## 🔑 API Keys and Clients

### Set API Key (if env var isn’t available)

```python
from agents import set_default_openai_key
set_default_openai_key("sk-...")
```

### Use Custom OpenAI Client

```python
from openai import AsyncOpenAI
from agents import set_default_openai_client

custom_client = AsyncOpenAI(base_url="...", api_key="...")
set_default_openai_client(custom_client)
```

### Switch to Chat Completions API

```python
from agents import set_default_openai_api
set_default_openai_api("chat_completions")
```

---

## 📈 Tracing Configuration

### Set Tracing API Key

```python
from agents import set_tracing_export_api_key
set_tracing_export_api_key("sk-...")
```

### Disable Tracing

```python
from agents import set_tracing_disabled
set_tracing_disabled(True)
```

---

## 🐛 Debug Logging

### Enable Verbose Logging

```python
from agents import enable_verbose_stdout_logging
enable_verbose_stdout_logging()
```

### Customize Python Logging

```python
import logging

logger = logging.getLogger("openai.agents")  # or "openai.agents.tracing"
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler())
```

---

## 🔐 Sensitive Data in Logs

Set environment variables to suppress sensitive data:

```bash
# Disable logging LLM inputs/outputs
export OPENAI_AGENTS_DONT_LOG_MODEL_DATA=1

# Disable logging tool inputs/outputs
export OPENAI_AGENTS_DONT_LOG_TOOL_DATA=1
```

---

Here’s a structured and example-rich **Markdown summary** of the [Agent Visualization](https://openai.github.io/openai-agents-python/visualization/) module in the OpenAI Agents SDK—perfect for mapping out your multi-agent enrichment flows, Mark:

---

# 🧭 Agent Visualization – OpenAI Agents SDK

This module lets you generate a **Graphviz-based diagram** of your agent architecture—tools, handoffs, MCP servers, and relationships—ideal for debugging, onboarding, and documentation.

---

## 📦 Installation

```bash
pip install "openai-agents[viz]"
```

---

## 🧪 Example Usage

```python
import os
from agents import Agent, function_tool
from agents.mcp.server import MCPServerStdio
from agents.extensions.visualization import draw_graph

@function_tool
def get_weather(city: str) -> str:
    return f"The weather in {city} is sunny."

spanish_agent = Agent(name="Spanish agent", instructions="You only speak Spanish.")
english_agent = Agent(name="English agent", instructions="You only speak English.")

samples_dir = os.path.join(os.path.dirname(__file__), "sample_files")
mcp_server = MCPServerStdio(
    name="Filesystem Server",
    params={
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", samples_dir],
    },
)

triage_agent = Agent(
    name="Triage agent",
    instructions="Handoff based on language.",
    handoffs=[spanish_agent, english_agent],
    tools=[get_weather],
    mcp_servers=[mcp_server],
)

draw_graph(triage_agent)
```

---

## 🧠 Graph Structure

| Element         | Representation |
|------------------|----------------|
| Start node       | `__start__` |
| End node         | `__end__` |
| Agents           | Yellow rectangles |
| Tools            | Green ellipses |
| MCP Servers      | Grey rectangles |
| Handoffs         | Solid arrows |
| Tool calls       | Dotted arrows |
| MCP invocations  | Dashed arrows |

---

## 🖼️ Customizing the Graph

### Show in Window

```python
draw_graph(triage_agent).view()
```

### Save to File

```python
draw_graph(triage_agent, filename="agent_graph")
```

> Generates `agent_graph.png` in your working directory.

---

## 🧩 Notes

- MCP servers are rendered in SDK ≥ v0.2.8
- Useful for onboarding, debugging, and documenting agent flows
- Supports nested agents, tool chaining, and hosted tool visualization

---
