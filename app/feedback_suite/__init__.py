"""
feedback_suite — Unified access to the self-improving feedback loop.

Provides a single import point for the 4 feedback-related modules.

Usage:
    from app.feedback_suite import FeedbackPipeline
    from app.feedback_suite import ModificationEngine
    from app.feedback_suite import ImplicitFeedbackDetector
    from app.feedback_suite import MetaLearner
"""

# Feedback collection and classification
from app.feedback_pipeline import FeedbackPipeline

# Modification proposal engine
from app.modification_engine import ModificationEngine, TIER1_PARAMETERS, TIER2_PARAMETERS

# Implicit feedback detection
from app.implicit_feedback import ImplicitFeedbackDetector

# Meta-learning (UCB1 strategy selection)
from app.meta_learning import MetaLearner
