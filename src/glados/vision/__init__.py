"""Vision processing components."""

# NOTE (AI_Linux vendoring): FastVLM and VisionProcessor pull in OpenCV (cv2).
# They are imported lazily (PEP 562) so the package and core.engine load without
# opencv when vision is disabled (the v1 default). cv2 is only needed at runtime
# if vision is actually enabled in config. Lightweight, cv2-free symbols stay eager.
from .vision_config import VisionConfig
from .vision_request import VisionRequest
from .vision_state import VisionState

# FastVLM and VisionProcessor are intentionally omitted from __all__ so a star-import
# does not eagerly pull in cv2; they remain reachable via attribute/explicit import.
__all__ = ["VisionConfig", "VisionRequest", "VisionState"]


def __getattr__(name: str):  # type: ignore[misc]
    if name == "VisionProcessor":
        from .vision_processor import VisionProcessor

        return VisionProcessor
    if name == "FastVLM":
        from .fastvlm import FastVLM

        return FastVLM
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
