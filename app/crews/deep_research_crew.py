"""Synchronous deep-research crew adapter for the central crew registry."""

from __future__ import annotations

from app.crews.lifecycle import crew_lifecycle


class DeepResearchCrew:
    """Run the bounded research spine and return its gated final synthesis."""

    def run(
        self,
        topic: str,
        parent_task_id: str | None = None,
        difficulty: int = 8,
    ) -> str:
        from app.crews.research_crew import ResearchCrew
        from app.research.deep_path import execute_deep_research

        core_topic = ResearchCrew._extract_core_topic(topic or "")
        clarification = ResearchCrew._clarification_needed(core_topic)
        if clarification:
            return clarification

        with crew_lifecycle(
            crew_name="deep_research",
            agent_role="researcher",
            task_title=f"Deep research: {core_topic[:100]}",
            task_description=core_topic,
            parent_task_id=parent_task_id,
            mode="synchronous-deep",
        ) as ctx:
            result = execute_deep_research(
                core_topic,
                parent_task_id=parent_task_id,
            )
            ctx.set_outcome(result)
            return result
