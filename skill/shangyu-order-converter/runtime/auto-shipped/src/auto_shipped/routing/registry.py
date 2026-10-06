from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from auto_shipped.domain import ClarificationRequest


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route_id: str
    source_profile_id: str
    order_type: str
    target_platform: str
    target_profile_id: str
    platform_rules_profile_id: str | None = None
    business_rule_scope_id: str | None = None
    post_fulfillment_profile_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RouteResult:
    status: str
    decision: RouteDecision | None = None
    clarifications: list[ClarificationRequest] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "decision": self.decision.to_dict() if self.decision else None,
            "clarifications": [item.to_dict() for item in self.clarifications],
        }


def load_routing(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_route(
    routing: dict[str, Any],
    source_profile_id: str,
    order_type: str,
) -> RouteResult:
    routes = [
        route
        for route in routing.get("routes", [])
        if route.get("source_profile_id") == source_profile_id
        and route.get("order_type") == order_type
    ]
    if len(routes) == 1:
        route = routes[0]
        return RouteResult(
            status="resolved",
            decision=RouteDecision(
                route_id=route["route_id"],
                source_profile_id=route["source_profile_id"],
                order_type=route["order_type"],
                target_platform=route["target_platform"],
                target_profile_id=route["target_profile_id"],
                platform_rules_profile_id=route.get("platform_rules_profile_id"),
                business_rule_scope_id=route.get("business_rule_scope_id"),
                post_fulfillment_profile_id=route.get("post_fulfillment_profile_id"),
            ),
        )
    if len(routes) > 1:
        return RouteResult(
            status="needs_input",
            clarifications=[
                ClarificationRequest(
                    code="AMBIGUOUS_ROUTE",
                    scope="file",
                    question="这个来源同时配置了多个目标流程，请确认应上传到哪个平台。",
                    reason=", ".join(route["route_id"] for route in routes),
                    answer_type="single_choice",
                    choices=tuple(route["route_id"] for route in routes),
                )
            ],
        )

    for item in routing.get("unrouted_sources", []):
        if item.get("source_profile_id") != source_profile_id:
            continue
        if item.get("resolution") == "ask_user":
            return RouteResult(
                status="needs_input",
                clarifications=[
                    ClarificationRequest(
                        code=item.get("clarification_code", "ROUTE_REQUIRES_CLARIFICATION"),
                        scope="batch",
                        question="这份文件是采购入仓单，还是需要给收件人发货的订单？",
                        reason=item.get("reason", "当前资料不足以确定目标流程。"),
                        answer_type="single_choice",
                        choices=(
                            "采购入仓，不生成发货订单",
                            "需要发货，我将补充收件信息",
                        ),
                        next_action="根据回答停止该文件，或请求包含收件信息的补充资料。",
                    )
                ],
            )
        return RouteResult(
            status="needs_input",
            clarifications=[
                ClarificationRequest(
                    code="SOURCE_NOT_ROUTED",
                    scope="file",
                    question="这个来源尚未配置上传平台，请确认它应该进入哪个平台。",
                    reason=item.get("reason", "没有找到启用的路由。"),
                    answer_type="text",
                )
            ],
        )

    for item in routing.get("deferred_workflows", []):
        if item.get("source_profile_id") != source_profile_id:
            continue
        return RouteResult(
            status="needs_input",
            clarifications=[
                ClarificationRequest(
                    code="WORKFLOW_DEFERRED",
                    scope="file",
                    question="百礼汇流程尚未在第一版启用。你希望这次先人工处理，还是补充资料后新增自动流程？",
                    reason=item.get("reason", "该平台流程尚未启用。"),
                    answer_type="single_choice",
                    choices=("这次人工处理", "补充资料并新增自动流程"),
                )
            ],
        )

    return RouteResult(
        status="needs_input",
        clarifications=[
            ClarificationRequest(
                code="ROUTE_NOT_FOUND",
                scope="file",
                question="已经识别文件来源，但没有找到对应平台，请确认目标平台。",
                reason=f"来源配置 {source_profile_id} / {order_type} 没有启用路由。",
                answer_type="text",
            )
        ],
    )
