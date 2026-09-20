# ARGUS Chess — AI Agent Design

The AI coaching agent explains and reasons over **tool outputs**; it never
pretends to calculate chess positions itself.

## Foundation (Phase 1)

```
User message
      ↓
ChessCoachAgent.run()            (argus.ai_agent.agent)
      ↓  sends messages + tool specs
LLMClient.complete()             (provider abstraction — implemented later)
      ↓  tool_calls
ToolRegistry.call(name, args)    (argus.ai_agent.tools)
      ↓
Tool handler → structured result (deterministic services only)
      ↓  results fed back as tool messages
Final assistant message
```

## Tool abstraction

`AgentTool` = name + description + JSON-Schema parameters + handler +
availability. `ToolRegistry` holds tools and emits OpenAI-style function-
calling specs (`to_tool_specs`).

Available in Phase 1 (backed by real services):

| Tool | Backing service |
| ---- | --------------- |
| `get_current_position` | currently loaded game (FEN, turn, result) |
| `analyze_position` | `ChessEngine.analyze_position` (Stockfish) |
| `analyze_move` | `ChessEngine.compare_moves` |
| `get_game_analysis` | `GameAnalyzer.analyze` |

Registered but **unavailable** (honest states with reasons):

| Tool | Reason |
| ---- | ------ |
| `get_player_history` | requires the persistence layer (Phase 2) |
| `get_player_statistics` | requires the persistence layer (Phase 2) |
| `search_chess_knowledge` | requires the RAG knowledge base (Phase 2+) |
| `generate_training_position` | requires the training generator (Phase 2+) |

The LLM only sees specs of tools that actually work (`only_available=True`).

## Guarantees

- The agent never calls the engine directly — only through the registry.
- Tool failures surface as structured errors (`{"status": "error", ...}`)
  that the LLM can explain to the user.
- An iteration guard (`MAX_TOOL_ITERATIONS = 8`) prevents runaway loops.
- Without an LLM provider, `run` raises `LLMNotConfiguredError` — a clear
  configuration error, not a fake conversation.

## RAG capability (designed in, not over-engineered)

`search_chess_knowledge` is reserved in the registry with its eventual
contract. The knowledge base (curated chess principles, annotated classics)
and a retrieval implementation arrive in a later phase; the agent loop above
already supports feeding retrieved documents back as tool messages.

## LLM provider (later phase)

Implement `LLMClient` (a `Protocol`): given messages + tool specs, return
either assistant text or tool calls. Providers (e.g. OpenAI, Anthropic,
local models) plug in without touching the agent loop or the tools. API keys
come from environment configuration — never source code.
