"""v2 local intake (implementation spec §11): drop -> classify -> confirm -> normalize."""

from syllva.intake.v2.pipeline import (
    NORMALIZER_BY_EXTENSION,
    PROCESSOR_VERSION,
    Candidate,
    IntakeConfig,
    IntakeError,
    LocalIntake,
)

__all__ = ["NORMALIZER_BY_EXTENSION", "PROCESSOR_VERSION", "Candidate", "IntakeConfig", "IntakeError", "LocalIntake"]
