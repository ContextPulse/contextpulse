# SPDX-FileCopyrightText: 2026 Jerard Ventures LLC
# SPDX-License-Identifier: AGPL-3.0-or-later
# Glama.ai registry stub for ContextPulse.
#
# ContextPulse is a local-only desktop daemon — the real daemon captures
# screen, voice, keyboard, and mouse on the user's own machine and cannot
# run in a remote container. This image boots a minimal MCP server that
# exposes the tool catalog for Glama's registry listing.
#
# Users install the real daemon locally from https://github.com/ContextPulse/contextpulse
# and point their MCP client at localhost:8420/mcp.

FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/ContextPulse/contextpulse"
LABEL org.opencontainers.image.description="ContextPulse MCP server (Glama registry stub — real daemon runs locally)"
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later"

WORKDIR /app

RUN pip install --no-cache-dir "mcp>=1.0"

COPY glama/server.py /app/server.py

CMD ["python", "/app/server.py"]
