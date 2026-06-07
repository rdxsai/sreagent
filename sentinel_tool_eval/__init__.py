"""Cookbook-style tool evaluation for Sentinel.

Hands a model only the tool registry and an incident task, lets it choose and
call tools over a recorded fixture, then scores the structured root-cause answer
against eval-only ground truth. Tools read public fixtures only; the grader is
the sole reader of `eval_only/truth.json`. The two never meet in the agent's
context.
"""
