"""Stream a coding agent's turns to Langfuse while they run.

Why the design looks like this
------------------------------
Langfuse v4 never updates an observation it has stored: a span sent twice under
one id is two rows, not an update. A long turn can therefore only show up live if
every observation is sent exactly once, at the moment it is complete. The turn's
root carries the input and output, and is only complete when the turn ends, so it
is sent last. Observation ids are derived from the thread, turn and call, so a
child names its root before the root exists, and Langfuse links them when the root
arrives.

Data flow, one pass every --interval seconds
--------------------------------------------
    TranscriptSource.discover()   transcript files on disk           (sources.py)
      -> TranscriptSource.parse() each file into a Thread of Turns   (model.py)
      -> Placement                where each turn's trace and root are
      -> observations_for()       the observations complete now      (observations.py)
      -> SentLedger               minus those already sent           (ledger.py)
      -> SpanSink.send()          to Langfuse over OTLP              (sinks.py)
      -> SentLedger.record()      once the sink has accepted them

Every pass re-derives everything from the transcripts, so a crash or restart
loses nothing: the ledger is the only state, and it is written after each batch
the sink accepts.

Adding an agent
---------------
Subclass TranscriptSource: say where the agent writes transcripts and parse one
into the model in model.py. Everything downstream is shared. codex.py is the
reference implementation. A thin entry script (see codex-live-trace.py) runs
cli.main with the new source.
"""
