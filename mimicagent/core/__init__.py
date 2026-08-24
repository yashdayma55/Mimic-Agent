"""Phase 0 core: ElementRef, resolve chain, capture, typed actions."""

from mimicagent.core.element_ref import A11yRef, ElementRef, SemanticRef, VisualRef
from mimicagent.core.resolver import ResolveResult, resolve

__all__ = [
    "A11yRef",
    "VisualRef",
    "SemanticRef",
    "ElementRef",
    "ResolveResult",
    "resolve",
]
