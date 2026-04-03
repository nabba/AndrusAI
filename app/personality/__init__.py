"""
personality — Personality Development Subsystem (PDS).

Autonomous module that develops, evaluates, and refines coherent personality
profiles in AI agents using adapted psychological assessment instruments.

Core principle: Behavioral Coherence over Assessment Performance.
An agent that scores well on assessments but acts inconsistently has
learned to game a test, not developed personality.

Components:
    state.py        — PersonalityState data model + persistence
    assessment.py   — Assessment Battery Module (4 instruments)
    evaluation.py   — Multi-dimensional Evaluation Engine
    validation.py   — Behavioral Validation Layer (INFRASTRUCTURE-LEVEL)
    feedback.py     — Developmental Feedback Loop (Socratic method)
    probes.py       — Embedded probe generation for real tasks
"""
