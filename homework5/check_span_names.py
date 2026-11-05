"""Print the real span_name of every span from one agent run (no token needed)."""
import logfire
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from trivia_agent import agent

exporter = InMemorySpanExporter()
logfire.configure(
    send_to_logfire=False,
    console=False,
    additional_span_processors=[SimpleSpanProcessor(exporter)],
)

agent.run_sync("Let's play 3 medium questions from History")

for span in exporter.get_finished_spans():
    parent = "top-level" if span.parent is None else "child"
    print(f"{parent:9}  span_name={span.name!r}")
