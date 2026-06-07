---
name: writing-agent-tools
description: Use when designing, building, naming, or refactoring the tool layer that an LLM agent will call, including individual tool functions, their input and output schemas, and the tool registry that exposes them. Trigger this whenever the work involves creating tools or tool definitions, writing or revising tool descriptions, deciding which tools to build or not build, namespacing tools, shaping tool return values, controlling token usage of tool outputs, or building a decorator or schema driven tool registry, even if the user does not say the word "tool" explicitly and only describes giving an agent new abilities. Grounded in Anthropic's "Writing effective tools for agents" engineering guidance.
---

# Writing effective agent tools

A tool is a contract between a deterministic system (your code) and a non
deterministic agent (the model). Unlike an API written for another engineer, a
tool is consumed by something that reasons, explores, and sometimes
misunderstands. So you design for the agent, not for a human developer, and the
goal is to widen the surface over which the agent can succeed, not to mirror
your backend.

The tools that are most ergonomic for agents also tend to be the ones a human
would find intuitive given the same resources. If that intuition is missing, the
tool is probably wrong for the agent too.

## The build loop, not a one shot

Do not write tools, polish the descriptions, and ship. You cannot reliably
predict which tools an agent will find ergonomic without watching one use them.
The loop is:

1. Prototype a small set of tools and exercise them yourself by hand.
2. Evaluate them with an agent over realistic tasks (see the companion skill
   `evaluating-agent-tools`).
3. Collaborate with an agent to read the transcripts and refine the tools.
4. Repeat.

Tool descriptions and schemas are the highest leverage thing you will touch, and
they should be tuned from real evaluation evidence rather than written blind.
Resist locking a tool spec before the first evaluation has run.

## Principle 1: choose the right tools, and refuse the wrong ones

More tools is not better. The most common mistake is wrapping each API endpoint
or function as its own tool. Agents have limited context, so a tool that dumps
everything forces the model to read irrelevant data token by token, which is the
equivalent of finding a contact by reading every page of the address book.

Build a few high leverage tools that target whole workflows, and let one tool
consolidate several underlying operations:

- Prefer `search_contacts` or `message_contact` over `list_contacts`.
- Prefer `search_logs` that returns only the relevant lines plus surrounding
  context over `read_logs` that returns everything.
- Prefer one `get_customer_context` that compiles recent and relevant customer
  information over separate `get_customer_by_id`, `list_transactions`, and
  `list_notes` calls.
- Prefer one `schedule_event` that finds availability and books it over separate
  `list_users`, `list_events`, and `create_event` calls.

Each tool should map to a natural subdivision of the task, the way a person would
split the work. A good test: if a human engineer cannot say with confidence which
tool to use in a given situation, the agent will not do better. Overlapping or
vaguely scoped tools pull the agent off efficient strategies, so pruning the set
pays off.

## Principle 2: namespace to mark clear boundaries

When an agent has dozens of tools, names are how it picks. Group related tools
under common prefixes so the boundaries are obvious, by service and by resource,
for example `metrics_series`, `traces_find`, `logs_search`, or
`asana_projects_search`, `asana_users_search`. Prefix versus suffix namespacing
has measurable and model dependent effects, so let your evaluation decide the
scheme rather than guessing. Every tool should have one clear, distinct purpose.

## Principle 3: return meaningful, high signal context

Return what informs the agent's next action, not raw plumbing. Drop low level
identifiers like opaque uuids, pixel sized image urls, and mime types in favor of
names, semantic types, and human readable fields. Agents handle natural language
identifiers far better than cryptic alphanumeric ids, and resolving ids to
meaningful names (or a simple zero indexed scheme) measurably reduces
hallucination in retrieval.

When the agent genuinely needs both a readable view and the technical ids to
trigger downstream calls, expose a `response_format` control rather than always
returning everything:

```
response_format: "concise" | "detailed"
```

A concise response might return only the content; a detailed one adds the ids
needed for follow up calls. In one Anthropic example the concise form used about a
third of the tokens. The response structure itself (JSON, XML, Markdown) also
affects performance and is task and model dependent, so pick it from evaluation,
not dogma.

## Principle 4: optimize for token efficiency

Quality of context matters, and so does quantity. Any tool whose result can grow
large should support some combination of pagination, range selection, filtering,
and truncation, with sensible defaults. Claude Code, for reference, caps tool
responses at about 25,000 tokens by default. Effective context will grow over
time, but context efficient tools will stay necessary.

If you truncate, steer the agent with the truncation message itself: tell it to
make several small targeted searches instead of one broad call. When a call fails
validation, return an actionable error that says what to fix and shows a correct
input shape, not an opaque code or a traceback. Helpful errors and helpful
truncation notices are part of the tool's interface.

## Principle 5: prompt engineer the descriptions and schemas

This is the single most effective lever. Write each description the way you would
brief a new hire who has never seen your system. Make implicit context explicit:
special query formats, the meaning of niche terms, and the relationships between
resources. State a rule and the reason for it, so the agent can generalize to
cases you did not spell out, rather than relying on bare all caps commands.

For schemas, name parameters unambiguously (`user_id`, not `user`), enforce
expected inputs and outputs with strict typed models, and add a short usage
example where a parameter format is easy to get wrong. Small refinements here
produce large gains; tightening descriptions alone has moved benchmark results
substantially.

## Composability and typed I/O

Design tools so their outputs feed each other. A search that returns a semantic
handle should let a follow up act on it, for example `search_user(name="jane")`
returning an id that `send_message(id=...)` consumes. Typed inputs and outputs
make these chains reliable and let you verify them. Prefer at least one tool that
consumes another tool's structured output, since that is what turns a pile of
tools into a workflow.

## Building the registry

- Register tools through a single decorator or schema driven mechanism so every
  tool's name, description, and typed schema come from one source of truth and
  stay consistent.
- Let the model select tools from their descriptions. Do not hand route with
  brittle if and else logic; that defeats the point of agent tool use and hides
  selection failures the evaluation should surface.
- Keep the exposed surface small per turn. A large catalog is fine if it loads on
  demand and only a few relevant tools sit in context at once. Curating a minimal
  viable set reduces ambiguous decision points, which is one of the most common
  failure modes. If a human cannot say which of two tools applies, neither can the
  agent.
- Keep implementations and descriptions self consistent whenever you change one.

## Quick checklist

- Does each tool target a workflow, not a raw endpoint.
- Could two tools be confused. If so, merge, rename, or re namespace.
- Does every return value carry only high signal fields, with semantic names.
- Can large outputs be paginated, filtered, or truncated, with a steering note.
- Do errors tell the agent exactly how to fix the call.
- Is every parameter name unambiguous and typed.
- Does at least one tool consume another's typed output.
- Have the descriptions been tuned from a real evaluation, not written blind.

## Sources

- Anthropic, Writing effective tools for agents:
  https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic, Effective context engineering for agents (minimal viable tool set):
  https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Tool definition best practices (Developer Guide):
  https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/implement-tool-use