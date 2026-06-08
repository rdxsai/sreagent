import type { AgentEvent, Scenario } from "./types";

export async function fetchScenarios(): Promise<Scenario[]> {
  const res = await fetch("/demo/scenarios");
  if (!res.ok) throw new Error(`scenarios ${res.status}`);
  const data = await res.json();
  return data.scenarios as Scenario[];
}

// Open the live SSE stream for a scenario. Returns the EventSource so the caller
// can close it. `onEvent` fires for every agent event; `onDone` when the run ends.
export function runScenario(
  id: string,
  onEvent: (e: AgentEvent) => void,
  onDone: () => void,
): EventSource {
  const es = new EventSource(`/demo/run/${id}`);
  es.onmessage = (msg) => {
    const event = JSON.parse(msg.data) as AgentEvent;
    onEvent(event);
    if (event.type === "done") {
      es.close();
      onDone();
    }
  };
  es.onerror = () => {
    es.close();
    onDone();
  };
  return es;
}
