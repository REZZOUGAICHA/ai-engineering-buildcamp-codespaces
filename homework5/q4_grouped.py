"""Q4: same session, but every agent run is nested under one parent span."""
import logfire

from q3_play import run

with logfire.span('trivia_session'):
    run("Let's play 5 easy questions from Science & Nature")
