"""Unified integrations for Ops Factory."""

from .base_data_source import BaseDataSource, DataSourceError
from .custom_fault_adapter import CustomFaultAdapter
from .langchain_rca_multiagent import LangChainRCAMultiAgent
from .hermes_skillclaw_rca import HermesSkillClawRCA
from .fault_dataset_collector import FaultDatasetCollector
from .skill_hermes_aiops import SkillHermesAIOpsHarness
try:
    from .cloudopsbench_adapter import CloudOpsBenchAdapter
except ImportError:
    CloudOpsBenchAdapter = None  # pandas not available
from .data_source_registry import (
    get_adapter,
    get_all_faults,
    get_source_for_case,
    health_check_all,
    inject_fault_on_platform,
    list_all_sources,
    list_sources_by_type,
    restore_fault_on_platform,
)
from .drain_mcp_adapter import DrainMCPAdapter
from .dynamic_evolutionary_adapter import DynamicEvolutionarySystemAdapter
from .kpi_failure_adapter import KPIFailureAdapter
from .online_shopping_adapter import OnlineShoppingAdapter
from .opskb_adapter import OpsKBAdapter
from .opsaug_adapter import OpsAugAdapter
from .promcopilot_adapter import PromCopilotAdapter
from .rca_orchestrator import RcaOrchestrator
from .self_evolution import SelfEvolution
from .sock_shop_adapter import SockShopAdapter
from .train_ticket_adapter import TrainTicketAdapter
from .unified_opsaug_adapter import UnifiedOpsAugAdapter

__all__ = [
    "BaseDataSource",
    "CustomFaultAdapter",
    "DataSourceError",
    "FaultDatasetCollector",
    "HermesSkillClawRCA",
    "LangChainRCAMultiAgent",
    "SkillHermesAIOpsHarness",
    "CloudOpsBenchAdapter",
    "DrainMCPAdapter",
    "DynamicEvolutionarySystemAdapter",
    "KPIFailureAdapter",
    "OnlineShoppingAdapter",
    "OpsKBAdapter",
    "OpsAugAdapter",
    "PromCopilotAdapter",
    "RcaOrchestrator",
    "SelfEvolution",
    "SockShopAdapter",
    "TrainTicketAdapter",
    "UnifiedOpsAugAdapter",
    "get_adapter",
    "get_all_faults",
    "get_source_for_case",
    "health_check_all",
    "inject_fault_on_platform",
    "list_all_sources",
    "list_sources_by_type",
    "restore_fault_on_platform",
]
