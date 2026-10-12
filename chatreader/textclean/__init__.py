"""Text clean-up and safety step for the Twitch chat reader: prepare(message, context) -> Decision."""


def __getattr__(name):  # lazy, so tools/build_data.py can import textclean.fold before the data exists
    if name in {"prepare", "Decision", "Context", "Settings", "Cleaner"}:
        from . import pipeline

        return getattr(pipeline, name)
    raise AttributeError(name)
