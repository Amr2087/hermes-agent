"""GB Chat server: the web UI and a stable API in front of the agent engine.

The browser only ever talks to this server. This server talks to the engine's local API and
translates its events into GB Chat events, so the UI never depends on the engine's wire format
or branding.
"""

__version__ = "0.1.0"
