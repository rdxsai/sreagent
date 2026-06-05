## About the Project
## Project context

Sentinel is a production-shaped autonomous SRE incident-response agent built as a take-home AI engineer project. Given an incident symptom, for example "checkout p99 latency spiked at 14:38", it uses an LLM-driven agent loop to choose tools, inspect telemetry, form and test hypotheses, trace root cause, and emit a structured incident report.

The agent is the artifact under evaluation, not the infrastructure. FastAPI is the deployment surface, while the agent loop should remain a plain async Python process. The planned stack is Python 3.11+, FastAPI, Pydantic v2, Anthropic SDK, tenacity, structlog, pytest, and pytest-asyncio.

The v1 environment is a sealed OpenTelemetry incident lab. We run OpenTelemetry Demo at a pinned SHA, inject controlled built-in feature-flag incidents, generate steady workload, record metrics/logs/traces/topology/change events, normalize them into replayable public fixtures, and keep private ground truth in eval-only files. The agent and tools may only read public fixtures.

The project will contain five core properties (apart from additional):

- 50+ tools across 4+ namespaces, exposed through a coherent decorator/schema-driven registry. Tool selection must be model-driven, not a hand-written conditional dispatch chain.
- Subagent orchestration. At least one tool must spawn a real subagent with isolated context, a scoped tool set, its own trace, and a structured return value.
- Long-horizon execution. The agent must complete at least one incident run spanning 20+ tool calls without losing plan coherence. Context management must be explicit in code.
- Production scaffolding. Include observability, retries with exponential backoff, rate limiting on external calls, typed error handling, an eval harness, and unit plus integration tests.
- Composable tool I/O. Tools should use typed Pydantic inputs and outputs. At least one tool must consume the structured output of another.

## Working with me

- Be direct. No glazing. Never write "You're absolutely right!" or similar sycophantic openers.
- Push back with specific reasons when you disagree. If it's a gut feeling, say so.
- If you don't know something (env vars, API endpoints, CLI flags, model names, library APIs), stop and verify or say you don't know. Never invent technical details.
- Your training data is stale. Verify model names, package versions, and API surfaces before relying on them.
- Don't say a task is done until typechecks, linters, and tests pass. If none are configured, say so explicitly instead of claiming success.
- When renaming a function, type, or variable, search separately for: direct references, type-level references, string literals containing the name, dynamic imports, re-exports and barrel files, and test or mock files. One grep is not enough.

## Before coding

- State assumptions explicitly before implementing. If uncertain, ask.
- If multiple interpretations of a request exist, present them, don't pick silently.
- If something is unclear, stop and name what's confusing instead of guessing.
- Write the minimum code that solves the problem. No speculative features, no abstractions for single-use code, no configurability that wasn't asked for.
- Don't add error handling for impossible scenarios.
- Touch only what the task requires. Don't "improve" adjacent code, comments, or formatting.
- Match existing style in a file, even if you'd write it differently.
- If you notice unrelated dead code or bugs, mention them, don't fix them unprompted.
- Clean up orphans your changes create (unused imports, variables). Don't remove pre-existing dead code unless asked.

## Running scripts and commands

- Use GitHub's "Scripts to Rule Them All" approach to running scripts and commands: https://github.com/github/scripts-to-rule-them-all
- If the project has a "scripts" or "script" directory, run those scripts for tasks like testing, linting, formatting, etc.
- If the project has a `script/lint` or `scripts/lint` script, run it before committing changes with Git.
- If linting fails, fix the linting errors and run the linter until all the errors are resolved.

## Working with Git

- When creating git commits, always use a semantic commit prefixes, with or without parenthetical qualifiers.
- When opening pull requests or merge requests, always use a semantic commit message as the title.
- Never bypass pre-commit hooks. Never use `--no-verify` or equivalent flags without explicit permission.

## Working with Node.js and npm

- Always use `npx` when running global npm CLIs, e.g. `npx wrangler` instead of `wrangler`

## Working with GitHub and GitLab

- Use `gh` for GitHub repositories and `glab` for GitLab repositories.
- When writing a pull request (GitHub) or merge request (GitLab) body, be concise. Explain the problem and the solution succinctly.
- Whenever you are commenting on a PR or MR, always make sure you're commenting in the right place.
- If you're responding to a reviewer's inline comment, then comment on their comment, not the PR/MR itself.
- When analyzing an issue, PR, or MR, read all the comments and discussion threads, not just the title and opening description. The context and nuance is often in the conversation.
- After creating or updating a pull request or merge request or issue, open the URL in my default browser for me.
- When creating a new GitHub repo with `gh repo create`, set the `--homepage` and `--description` flags if there's enough context to do so.

## Writing a good PR body

Follow these guidelines when writing the body of the pull request:

- Be concise and descriptive
- Don't oversell the changes. It's not an advertisement.
- Don't use fancy words like "comprehensive", "utilize", "implement", "exhaustive", "simplify", "optimize", "seamlessly"
- Start the PR body with the words "This PR..."
- Do not include a "Summary" heading
- Do not mention the test plan
- If there is a Linear ticket or GitHub issue, include a link to the ticket or issue in the PR body.
- If there is a GitLab issue, include a link to the issue in the MR body.

## Style guide

Follow these style guidelines in chat, commit messages, and prose:

- Be concise and descriptive
- Don't oversell the changes. It's not an advertisement.
- Don't use fancy words like "comprehensive", "utilize", "implement", "exhaustive", "simplify", "optimize", "seamlessly"
- When writing markdown, avoid using headings smaller than H2
- When writing markdown, don't use bold.
- When writing markdown tables, pad cells with spaces so columns align. This makes tables legible in monospace contexts like terminals.
- Never use em dashes (—). Use commas, colons, or separate sentences instead.

## Types and documentation

- Prefer types over prose documentation for API contracts. Types are executable and can't drift from the implementation.
- Define schemas (e.g. Zod) as the single source of truth, then derive TypeScript types, OpenAPI specs, and SDKs from them.
- Use schema-first design: the schema defines the contract, and the implementation conforms to it. Don't generate types from runtime behavior.
- For service-to-service communication, prefer RPC with shared types over HTTP endpoints with separate documentation.
- Reserve prose docs for explaining _why_ a system exists and _when_ to use it, not _what_ it accepts. Types handle the _what_.
- If an API is too complex to type, that's a design problem worth fixing.

## Fetching data

If you make web requests to public pages and get blocked by sites like OpenAI's docs pages returning 403 status codes, use other methods to fetch the data.

## Browser Automation

Use the following tools for browser automation tasks:

- https://agent-browser.dev - installed as the `agent-browser` CLI tool.
- https://github.com/andreasjansson/plwr for browser automation. It's installed as a `plwr` CLI tool.
- Favor these CLI tools over any available MCP servers.
- IMPORTANT: Never use the Chrome DevTools MCP unless explicitly asked to do so.
- When using the Chrome DevTools MCP, check for an existing tab already on the relevant page before opening a new one. If no such tab exists, open a new tab. Don't navigate away from or overtake unrelated existing tabs.
- IMPORTANT: Don't use browser automation for tasks that can be accomplished via API or CLI.

## Secrets and credentials

- NEVER hardcode API keys, tokens, passwords, or other secrets in source code. Always read them from environment variables.