"""Shim: Agent 1 tools live in production ``opportunity_extract``."""

from app.services.proposal_intelligence.opportunity_extract import agent1_tools as _m

# Re-export public + test helpers (star-import skips leading underscores).
RfpDoc = _m.RfpDoc
TOOL_DEFS = _m.TOOL_DEFS
apply_opportunity_to_plan = _m.apply_opportunity_to_plan
build_evidence_pack = _m.build_evidence_pack
extract_opportunity_with_tools = _m.extract_opportunity_with_tools
merge_langextract_into_pack = _m.merge_langextract_into_pack
merge_split_results = _m.merge_split_results
normalize_provenance = _m.normalize_provenance
repair_triggers = _m.repair_triggers
validate_opportunity_json = _m.validate_opportunity_json
_extract_json_object = _m._extract_json_object
_strip_trailing_commas = _m._strip_trailing_commas

__all__ = [name for name in dir(_m) if not name.startswith("__")]
