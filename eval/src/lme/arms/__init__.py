"""The arms. A0 full-context · A1 naive-RAG · A2 unblock (optional) · A3 PALIMPSEST.

Every arm shares the SAME reader, the SAME prompt file, and the SAME judge. The
only thing that varies is what goes into `{context}` -- which is exactly what
makes an arm-vs-arm delta a retrieval delta rather than a prompt delta.
"""

from .base import Arm, ArmOutput, build_arm

__all__ = ["Arm", "ArmOutput", "build_arm"]
