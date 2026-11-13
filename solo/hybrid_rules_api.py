"""Explicit opt-in routes for the closed verified mathematical subset."""
from fastapi import HTTPException, Request

from .hybrid_rules import Activation, CheckCommand, CheckProposal, HybridRulesAdapter, public_check_event
from .tabletop_api import advanced_token_enabled, require_advanced


def register_hybrid_rules_api(app, store, game):
    bridge = HybridRulesAdapter(store, lambda token: advanced_token_enabled(app, token))
    app.state.hybrid_rules = bridge

    def token(request):
        require_advanced(request)
        return request.cookies.get("solo_advanced", "")

    def permitted(call, *args):
        try:
            return call(*args)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error

    @app.get("/api/hybrid-rules/verified")
    def verified(request: Request):
        return permitted(bridge.catalog, token(request))

    @app.post("/api/campaigns/{campaign}/hybrid-rules/activation")
    async def activate(campaign: str, body: Activation, request: Request):
        principal = token(request)
        async with game.operation:
            return permitted(bridge.activate, principal, campaign, body)

    @app.delete("/api/campaigns/{campaign}/hybrid-rules/activation")
    async def deactivate(campaign: str, request: Request):
        principal = token(request)
        async with game.operation:
            return permitted(bridge.deactivate, principal, campaign)

    @app.get("/api/campaigns/{campaign}/hybrid-rules/context")
    def context(campaign: str, request: Request):
        return permitted(bridge.context, token(request), campaign)

    @app.post("/api/campaigns/{campaign}/hybrid-rules/proposals")
    async def propose(campaign: str, body: CheckProposal, request: Request):
        principal = token(request)
        async with game.operation:
            return permitted(bridge.propose, principal, campaign, body)

    @app.post("/api/campaigns/{campaign}/hybrid-rules/checks")
    async def check(campaign: str, body: CheckCommand, request: Request):
        principal = token(request)
        async with game.operation:
            event = permitted(bridge.execute, principal, campaign, body)
            return public_check_event(game.public_event(event))
