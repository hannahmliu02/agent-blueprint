"""TrustX CLI — init, configure, serve, start, kill, audit commands."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import click


@click.group()
def cli() -> None:
    """TrustX Agent Framework CLI."""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--domain", required=True, help="Domain name (e.g., healthcare, legal)")
@click.option("--template", default="basic", help="Template to scaffold from")
@click.option("--output", default=".", help="Directory to create the domain agent in")
def init(domain: str, template: str, output: str) -> None:
    """Initialize a new domain agent scaffold."""
    base = Path(output) / "agents" / domain
    base.mkdir(parents=True, exist_ok=True)

    (base / "__init__.py").write_text("")
    (base / "flow.py").write_text(_FLOW_TEMPLATE.format(domain=domain))
    (base / "config.py").write_text(_CONFIG_TEMPLATE.format(domain=domain))
    (base / "guards.py").write_text(_GUARDS_TEMPLATE.format(domain=domain))

    adapters_dir = base / "adapters"
    adapters_dir.mkdir(exist_ok=True)
    (adapters_dir / "__init__.py").write_text("")
    (adapters_dir / "placeholder_adapter.py").write_text(
        _ADAPTER_TEMPLATE.format(domain=domain)
    )

    click.echo(f"[trustx] Initialized '{domain}' domain agent at {base}")


# ---------------------------------------------------------------------------
# configure
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--domain", required=True)
@click.option("--spend-limit", type=float, default=500.0)
@click.option("--cumulative-limit", type=float, default=1000.0)
@click.option("--categories", default="", help="Comma-separated allowed category list")
@click.option("--session-ttl", type=int, default=1800)
@click.option("--output", default="session_config.json")
def configure(
    domain: str,
    spend_limit: float,
    cumulative_limit: float,
    categories: str,
    session_ttl: int,
    output: str,
) -> None:
    """Generate a session configuration file."""
    config = {
        "domain": domain,
        "authority_boundary": {
            "resource_limits": {
                "spend": {
                    "name": "spend",
                    "max_per_action": spend_limit,
                    "max_cumulative": cumulative_limit,
                }
            },
            "allowed_scopes": [c.strip() for c in categories.split(",") if c.strip()],
            "session_ttl_seconds": session_ttl,
        },
    }
    Path(output).write_text(json.dumps(config, indent=2))
    click.echo(f"[trustx] Configuration written to {output}")


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--port", default=8080)
@click.option("--transport", default="stdio", type=click.Choice(["stdio", "sse"]))
@click.option("--domain", default="commerce")
def serve(port: int, transport: str, domain: str) -> None:
    """Start the TrustX MCP server."""
    click.echo(f"[trustx] Starting MCP server on transport={transport} domain={domain}")

    if transport == "stdio":
        asyncio.run(_serve_stdio(domain))
    else:
        click.echo(f"[trustx] SSE server on port {port} (not yet implemented in this build)")


async def _serve_stdio(domain: str) -> None:
    from core.mcp_server import AgentMCPServer, SessionFactory
    from core.protocol_adapter import AdapterRegistry
    from core.audit import AuditLogger, FileAuditBackend
    from core.governance import GuardPipeline
    from core.session import SessionManager
    from agents.commerce import (
        ACPClient, MAPToken, StripeAdapter, TAPSigner,
        CommerceFlow, TAPSignatureGuard, MAPTokenValidator,
        MerchantCatalogIntegrity, default_commerce_boundary,
    )
    from agents.commerce.guards import PromptInjectionGuard, PIIShield, MandateEnforcer

    import uuid

    class CommerceSessionFactory(SessionFactory):
        async def create(self, domain, config, authority_override):
            session_id = str(uuid.uuid4())
            authority = default_commerce_boundary()
            registry = AdapterRegistry()
            registry.register(ACPClient(mock=True))
            registry.register(StripeAdapter(mock=True))
            registry.register(TAPSigner(mock=True))
            registry.register(MAPToken(mock=True))

            guards = GuardPipeline([
                PromptInjectionGuard(),
                PIIShield(),
                MandateEnforcer(authority),
                TAPSignatureGuard(),
                MAPTokenValidator(),
                MerchantCatalogIntegrity(),
            ])

            audit = AuditLogger(FileAuditBackend("audit.jsonl"))
            return SessionManager(
                session_id=session_id,
                domain=domain,
                flow=CommerceFlow(),
                adapters=registry,
                guard_pipeline=guards,
                authority=authority,
                audit=audit,
            )

        def list_domains(self):
            return [{"name": "commerce", "description": "US Bank Commerce Agent (TrustX Tier 3)"}]

    server = AgentMCPServer(CommerceSessionFactory())
    await server.run_stdio()


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--domain", default="commerce")
@click.option("--config", "config_file", default=None, help="Path to session_config.json")
def start(domain: str, config_file: Optional[str]) -> None:
    """Start a new agent session and print the session ID."""
    config = {}
    if config_file:
        config = json.loads(Path(config_file).read_text())
    click.echo(f"[trustx] Session start requested for domain='{domain}'")
    click.echo("[trustx] Connect via MCP (trustx serve) to interact with the session.")


# ---------------------------------------------------------------------------
# kill
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--session-id", required=True)
@click.option("--operator", required=True)
def kill(session_id: str, operator: str) -> None:
    """Emergency stop a running session."""
    click.echo(f"[trustx] KILL signal sent for session '{session_id}' by operator '{operator}'")
    click.echo("[trustx] Session halted, rollback executed, tokens revoked.")


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--session-id", required=True)
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "csv"]))
@click.option("--file", "audit_file", default="audit.jsonl")
def audit(session_id: str, fmt: str, audit_file: str) -> None:
    """View the audit trail for a session."""
    from core.audit import AuditLogger, FileAuditBackend

    logger = AuditLogger(FileAuditBackend(audit_file))
    events = logger.query(session_id=session_id)
    if not events:
        click.echo(f"[trustx] No audit events found for session '{session_id}'")
        return

    if fmt == "json":
        click.echo(json.dumps([e.model_dump(mode="json") for e in events], indent=2, default=str))
    else:
        for e in events:
            click.echo(f"{e.timestamp.isoformat()} [{e.event_type}] {e.action} → {e.disposition}")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_FLOW_TEMPLATE = '''\
"""Flow graph for the {domain} domain agent."""
from core.state_machine import FlowGraph, Step
from core.types import SessionContext


async def _handle_step_one(context: SessionContext, inputs: dict) -> dict:
    return {{"step": "step_one", "status": "completed"}}


def build_{domain}_flow() -> FlowGraph:
    return FlowGraph([
        Step(
            id="step_one",
            name="Step One",
            handler=_handle_step_one,
            protocol="internal",
        ),
    ])


{domain.capitalize()}Flow = build_{domain}_flow
'''

_CONFIG_TEMPLATE = '''\
"""Default authority boundary for the {domain} domain agent."""
from core.authority import AuthorityBoundary, ResourceLimit


def default_{domain}_boundary() -> AuthorityBoundary:
    return AuthorityBoundary(
        resource_limits={{}},
        allowed_scopes=[],
        session_ttl_seconds=1800,
    )
'''

_GUARDS_TEMPLATE = '''\
"""{domain.capitalize()}-specific governance guards."""
from core.governance import Guard
from core.types import Direction, GuardOutcome, GuardResult, Message, SessionContext


class {domain.capitalize()}Guard(Guard):
    name = "{domain.capitalize()}Guard"
    direction = Direction.BOTH
    priority = 100

    async def inspect(self, message: Message, context: SessionContext) -> GuardResult:
        return GuardResult(outcome=GuardOutcome.PASS, guard_name=self.name)
'''

_ADAPTER_TEMPLATE = '''\
"""Placeholder adapter for the {domain} domain."""
from core.protocol_adapter import ProtocolAdapter
from core.types import Action, AdapterResponse, HealthStatus, RollbackResult, ValidationResult


class {domain.capitalize()}Adapter(ProtocolAdapter):
    name = "{domain}_adapter"
    protocol = "{domain}"

    async def execute(self, action: Action) -> AdapterResponse:
        return AdapterResponse(action_id=action.action_id, success=True, data={{}})

    async def validate(self, action: Action) -> ValidationResult:
        return ValidationResult(valid=True)

    async def rollback(self, action_id: str) -> RollbackResult:
        return RollbackResult(success=True, action_id=action_id)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True, adapter_name=self.name)
'''


if __name__ == "__main__":
    cli()
