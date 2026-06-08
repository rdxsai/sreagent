---
name: evaluating-agent-tools
description: Use when measuring or improving how well an LLM agent actually uses a set of tools, as opposed to writing the tools themselves. Trigger this whenever the work involves building a tool evaluation harness, generating evaluation tasks, running an agentic loop that lets a model choose tools and scoring the result, tracking tool use metrics like call counts and tokens, reading agent reasoning and feedback to find confusing tools, or optimizing tool descriptions and schemas from evaluation transcripts. Use it before locking any tool spec, since descriptions should be tuned from evidence. Grounded in Anthropic's tool evaluation cookbook and the "Writing effective tools for agents" guidance.
---

# Evaluating agent tools

You cannot know which tools an agent finds ergonomic by inspection. An
evaluation is how you find out: you give a model only your tools and a realistic
task, let it choose and call tools on its own, and observe what it does. This is
distinct from a correctness check of the tool logic. The evaluation tests
selection and ergonomics, and its output is the evidence you use to tighten tool
descriptions and schemas, which is the highest leverage change you can make.

Run this before finalizing a tool spec. Tuning descriptions against real
evaluation behavior beats writing them blind, and the tools will usually change
once you see how a model treats them.

## Where this sits in the loop

Prototype the tools, evaluate them here, then collaborate with an agent to refine
them, and repeat. The companion skill `writing-agent-tools` covers the design
side.

## Step 1: generate tasks grounded in real use

Write tasks that mirror real world use over realistic data, not a toy sandbox. A
superficial environment will not stress the tools, and you will learn nothing.
Strong tasks usually require several tool calls, sometimes dozens, and force the
agent to chain tools.

Strong task: "Customer 9182 reported being charged three times for one purchase.
Find the relevant log entries and determine whether other customers were
affected."

Weak task: "Search the payment logs for purchase_complete and customer_id=9182."

The weak version names the tool and the parameters, so it tests nothing about
selection. Pair each task with a verifiable outcome. You can optionally record
the tools you expect the agent to call, to check whether it grasps each tool's
purpose, but do not overspecify the expected path, since there are usually
several valid strategies and overfitting to one will mislead you.

## Step 2: build the harness

Run the evaluation programmatically with direct API calls, one simple agentic
loop per task. The loop alternates a model call with tool execution until the
model stops asking for tools:

```python
messages = [{"role": "user", "content": task_prompt}]
while True:
    resp = client.messages.create(
        model=MODEL, max_tokens=4096,
        system=EVAL_SYSTEM_PROMPT, tools=TOOLS, messages=messages)
    messages.append({"role": "assistant", "content": resp.content})
    if resp.stop_reason != "tool_use":
        break
    results = []
    for block in resp.content:
        if block.type == "tool_use":
            out = DISPATCH[block.name](**block.input)   # a dispatch table, never eval
            results.append({"type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(out)})
    messages.append({"role": "user", "content": results})
```

In the evaluation system prompt, instruct the agent to emit its reasoning and a
feedback block and a structured response block, and to put the reasoning and
feedback before it acts. Producing these before the tool calls triggers chain of
thought and tends to raise the model's effective intelligence, and the feedback
is what tells you which tools were confusing. A workable structure:

- `<summary>` the approach and what it concluded
- `<feedback>` frank notes on the tools themselves: which were essential, which
  were confusing, which returned too much or too little, what was missing, any
  name or parameter that misled it
- `<response>` the structured answer you will score

If you run the evaluation with Claude, interleaved thinking gives similar chain
of thought off the shelf. Add a maximum iteration guard so a confused run cannot
loop forever, and dispatch tools through a lookup table rather than evaluating
strings.

## Step 3: score with a verifier that fits the output

Match the verifier to the answer shape. A single value answer can use a string
comparison. A structured answer (a record with several fields) should be checked
field by field against ground truth. A judgment or narrative answer can be scored
by a model acting as judge. Avoid overly strict verifiers that reject a correct
answer over formatting, punctuation, or a valid alternative phrasing. Keep the
ground truth out of the agent's reach: tools see only the inputs, the scorer
reads truth separately, and the two never meet in the agent's context.

## Step 4: collect the right metrics

Beyond top level accuracy, record per task:

- total number of tool calls, and the call sequence
- runtime of individual tool calls and of the whole task
- total token consumption
- tool errors

These patterns point straight at fixes. Many redundant calls suggest your
pagination, filtering, or token limits need resizing. Many invalid parameter
errors suggest the description or schema is unclear or needs an example. As a
concrete case, Claude was once appending the year to a web search query and
degrading results, and the fix was a clearer tool description. Tracking the call
sequence also reveals workflows that several tools could be consolidated into one.

## Step 5: read the transcripts, not just the scores

Agents are good partners for spotting contradictory descriptions, inefficient
implementations, and confusing schemas, but what they leave out can matter more
than what they say, because models do not always say what they mean. Read the
reasoning and feedback to find where the agent got stuck or fell for a trap, then
read the raw transcripts including tool calls and responses to catch behavior the
reasoning did not mention. Remember the agent does not know the correct answer, so
read between the lines rather than taking its self report at face value.

## Step 6: collaborate to optimize, then guard against overfitting

Concatenate the transcripts and hand them to an agent like Claude Code to analyze
and refactor the tools and descriptions together, which keeps implementations and
descriptions self consistent across many edits at once. Most of Anthropic's own
tool advice came from repeatedly doing this.

Hold out a test set that you never tune against, and select changes by the held
out score rather than the tuning score. This is how you confirm a description
improvement generalizes instead of memorizing your tuning tasks. Held out testing
has surfaced gains even beyond expert hand written tools.

## Quick checklist

- Are the tasks realistic and multi step, not tool naming in disguise.
- Does each task have a verifiable outcome, with truth kept away from the agent.
- Does the system prompt ask for reasoning and feedback before the tool calls.
- Is the verifier matched to the output shape and not overly strict.
- Are you recording call counts, sequences, runtimes, tokens, and errors.
- Did you read transcripts, not only scores.
- Is there a held out set, and are changes chosen by the held out score.

## Sources

- Anthropic tool evaluation cookbook:
  https://platform.claude.com/cookbook/tool-evaluation-tool-evaluation
- Anthropic, Writing effective tools for agents (evaluation section):
  https://www.anthropic.com/engineering/writing-tools-for-agents