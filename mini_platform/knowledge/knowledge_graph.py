"""
Lightweight Knowledge Graph representing services, dependencies, owners, runbooks, and topology.
Supports graph traversal algorithms (BFS/DFS) for cascading blast-radius calculations.
"""

from typing import Dict, List, Set, Any, Optional
from collections import deque

#: Version of the graph schema and blast-radius traversal.
__version__ = "1.1.0"


class ServiceNode:
    def __init__(
        self,
        name: str,
        tier: str,
        owner: str,
        environment: str,
        runbook_id: str,
        dependencies: Optional[List[str]] = None,
        dependents: Optional[List[str]] = None
    ):
        self.name = name
        self.tier = tier  # tier-0 (critical), tier-1 (core), tier-2 (supporting)
        self.owner = owner
        self.environment = environment
        self.runbook_id = runbook_id
        self.dependencies = dependencies or []  # Upstream services this service calls
        self.dependents = dependents or []      # Downstream services that rely on this service

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "tier": self.tier,
            "owner": self.owner,
            "environment": self.environment,
            "runbook_id": self.runbook_id,
            "dependencies": self.dependencies,
            "dependents": self.dependents
        }


class KnowledgeGraph:
    """Directed graph topology for infrastructure knowledge & blast radius analysis."""

    def __init__(self):
        self.nodes: Dict[str, ServiceNode] = {}
        self._build_default_graph()

    def _build_default_graph(self):
        # Service declarations
        self.add_service(ServiceNode(
            name="auth-service",
            tier="tier-0",
            owner="security-infra@corp.internal",
            environment="prod",
            runbook_id="DOC-RB-AUTH-001",
            dependencies=["redis-auth-cluster", "user-db"],
            dependents=["payment-service", "order-service", "user-profile-service"]
        ))
        self.add_service(ServiceNode(
            name="payment-service",
            tier="tier-1",
            owner="checkout-eng@corp.internal",
            environment="prod",
            runbook_id="DOC-RB-PAY-001",
            dependencies=["auth-service", "postgres-payment-db", "notification-service"],
            dependents=["order-service", "mobile-api-gateway"]
        ))
        self.add_service(ServiceNode(
            name="order-service",
            tier="tier-1",
            owner="order-eng@corp.internal",
            environment="prod",
            runbook_id="DOC-RB-ORD-001",
            dependencies=["payment-service", "inventory-service"],
            dependents=["web-frontend", "mobile-app"]
        ))
        self.add_service(ServiceNode(
            name="notification-service",
            tier="tier-2",
            owner="comms-team@corp.internal",
            environment="prod",
            runbook_id="DOC-NOTIF-001",
            dependencies=["rabbitmq-cluster"],
            dependents=["payment-service", "order-service"]
        ))
        self.add_service(ServiceNode(
            name="inventory-service",
            tier="tier-1",
            owner="logistics-team@corp.internal",
            environment="prod",
            runbook_id="DOC-RB-INV-001",
            dependencies=["inventory-db"],
            dependents=["order-service"]
        ))
        self.add_service(ServiceNode(
            name="staging-payment-service",
            tier="tier-3",
            owner="checkout-eng@corp.internal",
            environment="staging",
            runbook_id="DOC-RB-PAY-001",
            dependencies=["staging-auth-service"],
            dependents=[]
        ))

    def add_service(self, node: ServiceNode):
        self.nodes[node.name] = node

    def get_service(self, name: str) -> Optional[ServiceNode]:
        return self.nodes.get(name)

    def calculate_blast_radius(self, target_service: str, max_depth: int = 3) -> Dict[str, Any]:
        """
        Perform BFS traversal across downstream dependent services to compute total impact radius.
        """
        if target_service not in self.nodes:
            return {
                "target_service": target_service,
                "found": False,
                "direct_dependents": [],
                "transitive_dependents": [],
                "total_affected_services": 0,
                "tier_0_impacted": False,
                "risk_rating": "UNKNOWN"
            }

        node = self.nodes[target_service]
        visited: Set[str] = set()
        queue = deque([(target_service, 0)])
        direct_dependents: List[str] = list(node.dependents)
        transitive_dependents: List[str] = []
        tier_0_impacted = (node.tier == "tier-0")
        impacted_tiers: Set[str] = {node.tier}

        while queue:
            curr_svc, depth = queue.popleft()
            if depth >= max_depth:
                continue

            curr_node = self.nodes.get(curr_svc)
            if not curr_node:
                continue

            for dependent in curr_node.dependents:
                if dependent not in visited and dependent != target_service:
                    visited.add(dependent)
                    if dependent not in direct_dependents:
                        transitive_dependents.append(dependent)
                    dep_node = self.nodes.get(dependent)
                    if dep_node:
                        impacted_tiers.add(dep_node.tier)
                        if dep_node.tier == "tier-0":
                            tier_0_impacted = True
                    queue.append((dependent, depth + 1))

        total_affected = len(direct_dependents) + len(transitive_dependents)

        # Risk rating heuristic
        if tier_0_impacted or total_affected >= 4:
            risk_rating = "CRITICAL"
        elif total_affected >= 2:
            risk_rating = "HIGH"
        elif total_affected == 1:
            risk_rating = "MEDIUM"
        else:
            risk_rating = "LOW"

        return {
            "target_service": target_service,
            "found": True,
            "service_tier": node.tier,
            "owner": node.owner,
            "direct_dependents": direct_dependents,
            "transitive_dependents": transitive_dependents,
            "total_affected_services": total_affected,
            "tier_0_impacted": tier_0_impacted,
            "impacted_tiers": list(impacted_tiers),
            "risk_rating": risk_rating
        }

    def get_full_graph_data(self) -> Dict[str, Any]:
        """Return nodes and edges for visualization and export."""
        nodes = []
        edges = []
        for name, node in self.nodes.items():
            nodes.append({
                "id": name,
                "label": name,
                "tier": node.tier,
                "owner": node.owner,
                "environment": node.environment
            })
            for dep in node.dependencies:
                edges.append({
                    "from": name,
                    "to": dep,
                    "type": "DEPENDS_ON"
                })
        return {"nodes": nodes, "edges": edges}


# Global knowledge graph singleton
GLOBAL_KNOWLEDGE_GRAPH = KnowledgeGraph()
