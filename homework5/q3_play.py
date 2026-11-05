"""Q3: play a full session. Each loop turn is one agent.run_sync = one agent run."""
from trivia_agent import agent


def run(prompt):
    message_history = []
    agent_runs = 0  # our own count, to compare with the Logfire dashboard
    input_tokens = 0

    while True:
        result = agent.run_sync(prompt, message_history=message_history)
        agent_runs += 1
        usage = result.usage  # tokens of THIS run only (a property, not a method)
        input_tokens += usage.input_tokens
        print(result.output)
        print(f"[run {agent_runs}: input tokens {usage.input_tokens}]")
        message_history = result.all_messages()

        prompt = input("You (write 'stop' to stop): ")
        if not prompt or prompt.lower().strip() == 'stop':
            break

    print(f"\n[agent runs in this session: {agent_runs}]")
    print(f"[total input tokens in this session: {input_tokens}]")


if __name__ == "__main__":
    run("Let's play 5 easy questions from Science & Nature")
