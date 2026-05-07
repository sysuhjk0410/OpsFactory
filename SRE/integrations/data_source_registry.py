# -*- coding: utf-8 -*-
"""Unified data source registry — provides all data sources through a single API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from .base_data_source import BaseDataSource, DataSourceError

# Lazy imports to avoid import-time failures if adapters have missing deps
_ADAPTER_REGISTRY: Dict[str, Type[BaseDataSource]] = {}
_ADAPTER_INSTANCES: Dict[str, BaseDataSource] = {}


def register_adapter(source_id: str, adapter_class: Type[BaseDataSource]) -> None:
    """Register a data source adapter class."""
    _ADAPTER_REGISTRY[source_id] = adapter_class


def get_adapter(source_id: str) -> BaseDataSource:
    """Get or create a singleton adapter instance."""
    _load_all_adapters()
    if source_id not in _ADAPTER_INSTANCES:
        if source_id not in _ADAPTER_REGISTRY:
            raise DataSourceError(f"Unknown data source: {source_id}. "
                                  f"Available: {list(_ADAPTER_REGISTRY.keys())}")
        _ADAPTER_INSTANCES[source_id] = _ADAPTER_REGISTRY[source_id]()
    return _ADAPTER_INSTANCES[source_id]


def list_all_sources() -> List[Dict[str, Any]]:
    """List all registered data sources with metadata."""
    # Ensure adapters are loaded
    _load_all_adapters()

    results = []
    for source_id, adapter_class in _ADAPTER_REGISTRY.items():
        instance = adapter_class()
        results.append({
            "source_id": source_id,
            "name": instance.name,
            "source_type": instance.source_type,
            "description": instance.description,
        })
    return results


def list_sources_by_type(source_type: str) -> List[Dict[str, Any]]:
    """Filter data sources by type ('static' or 'dynamic')."""
    _load_all_adapters()
    return [s for s in list_all_sources() if s["source_type"] == source_type]


def _load_all_adapters() -> None:
    """Ensure all adapter classes are imported and registered."""
    if _ADAPTER_REGISTRY:
        return  # already loaded

    # ── Static data source ─────────────────────────────────────────
    try:
        from .cloudopsbench_adapter import CloudOpsBenchAdapter
        register_adapter("cloud-opsbench", CloudOpsBenchAdapter)
    except ImportError:
        pass  # pandas not available — static source unavailable

    from .custom_fault_adapter import CustomFaultAdapter
    register_adapter("custom-enterprise", CustomFaultAdapter)

    # ── Dynamic data sources ───────────────────────────────────────
    from .online_shopping_adapter import OnlineShoppingAdapter
    register_adapter("online-shopping", OnlineShoppingAdapter)

    from .genuine_sock_shop_adapter import GenuineSockShopAdapter
    register_adapter("sock-shop", GenuineSockShopAdapter)

    from .train_ticket_adapter import TrainTicketAdapter
    register_adapter("train-ticket", TrainTicketAdapter)


# ── Convenience functions for the orchestrator ───────────────────────

def get_source_for_case(case_id: str, source_id: str) -> Dict[str, Any]:
    """Unified entry point: get case detail from any data source."""
    adapter = get_adapter(source_id)
    return adapter.get_case_detail(case_id)


def inject_fault_on_platform(source_id: str, fault_type: str, target: str,
                              **kwargs) -> Dict[str, Any]:
    """Unified fault injection across all dynamic platforms."""
    adapter = get_adapter(source_id)
    if adapter.source_type != "dynamic":
        raise DataSourceError(f"Source '{source_id}' is static and does not support fault injection.")
    return adapter.inject_fault(fault_type, target, **kwargs)


def restore_fault_on_platform(source_id: str, case_id: str, **kwargs) -> Dict[str, Any]:
    """Unified real fault restoration across dynamic platforms."""
    adapter = get_adapter(source_id)
    if adapter.source_type != "dynamic":
        raise DataSourceError(f"Source '{source_id}' is static and does not support fault restoration.")
    restore = getattr(adapter, "restore_fault", None)
    if not callable(restore):
        raise DataSourceError(f"Source '{source_id}' does not implement fault restoration.")
    return restore(case_id=case_id, **kwargs)


def get_all_faults(source_id: str) -> List[Dict[str, Any]]:
    """List available faults/cases for a data source."""
    adapter = get_adapter(source_id)
    return adapter.list_faults()


def health_check_all() -> Dict[str, Any]:
    """Run health checks on all registered data sources."""
    _load_all_adapters()
    results = {}
    for source_id, adapter_class in _ADAPTER_REGISTRY.items():
        try:
            results[source_id] = adapter_class().health_check()
        except Exception as e:
            results[source_id] = {"status": "error", "message": str(e)}
    return results
