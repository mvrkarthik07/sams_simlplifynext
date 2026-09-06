"""AWS Lambda adapter for authenticated Deadbolt MCP and GitHub IAM tools."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from mangum import Mangum

from deadbolt.auth import AuthenticationError, authorize_event
from deadbolt.connections import ConnectionService, SsmConnectionStore
from deadbolt.mcp_server import build_server


def lambda_handler(event: Mapping[str, object], context: object) -> object:
    """Handle an AWS Lambda Function URL event using the MCP ASGI application."""
    try:
        subject = authorize_event(event)
    except AuthenticationError:
        return {
            "statusCode": 401,
            "headers": {"Content-Type": "application/json", "WWW-Authenticate": "Bearer"},
            "body": '{"error":{"code":"UNAUTHORIZED","message":"authentication required"}}',
        }
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    # FastMCP's StreamableHTTPSessionManager is single-use. Lambda may reuse a warm
    # process, so construct a fresh stateless app for every request rather than reusing
    # a manager whose lifespan has already exited.
    github_provider = None
    if subject is not None:
        try:
            github_provider = ConnectionService(SsmConnectionStore()).github_provider(subject)
        except ValueError:
            # The general Deadbolt tools remain available before GitHub onboarding;
            # GitHub-specific tools return a precise configuration error instead.
            github_provider = None
    app = build_server(github_provider=github_provider, public_http=True).streamable_http_app()
    handler = Mangum(app, lifespan="auto")
    return handler(event, context)  # type: ignore[arg-type]  # Mangum requires its untyped Lambda event/context contract.


__all__ = ["lambda_handler"]
