"""Code-mode + subagent orchestration over an OpenAI-compatible model (gpt-oss on
OpenRouter). Reuses the existing sandbox, tools, and registry; adds the OpenAI-shaped
loop, a two-tier catalog/SDK view, a trace tree, code-mode workers, and a manager."""
