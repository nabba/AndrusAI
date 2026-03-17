from crewai import Agent, Task, Crew, Process, LLM
from app.config import get_settings
from app.tools.web_search import web_search
from app.tools.web_fetch import web_fetch
from app.tools.youtube_transcript import get_youtube_transcript
from app.tools.memory_tool import create_memory_tools
from app.tools.file_manager import file_manager
from pathlib import Path
import logging
import re

settings = get_settings()
logger = logging.getLogger(__name__)

QUEUE_FILE = Path(settings.self_improve_topic_file)
SKILLS_DIR = Path("/app/workspace/skills")


class SelfImprovementCrew:
    def run(self):
        if not QUEUE_FILE.exists() or not QUEUE_FILE.read_text().strip():
            logger.info("Self-improvement: no topics in queue, skipping")
            return

        topics = [
            t.strip()
            for t in QUEUE_FILE.read_text().splitlines()
            if t.strip() and not t.startswith("#")
        ]
        if not topics:
            return

        llm = LLM(
            model=f"anthropic/{settings.commander_model}",
            api_key=settings.anthropic_api_key,
            max_tokens=4096,
        )
        memory_tools = create_memory_tools(collection="skills")

        learner = Agent(
            role="Learning Specialist",
            goal="Acquire deep, practical knowledge on assigned topics and distil it into reusable skill files.",
            backstory="You are a relentless learner who reads documentation, articles, and YouTube transcripts to master new topics. You write clear, practical Markdown skill files.",
            llm=llm,
            tools=[web_search, web_fetch, get_youtube_transcript, file_manager] + memory_tools,
        )

        for topic in topics[:3]:  # Max 3 topics per run to control API cost
            skill_filename = re.sub(r'[^a-zA-Z0-9_-]', '_', topic)[:100]
            task = Task(
                description=f'Research the topic: "{topic}". Search the web, read at least 3 sources, extract any relevant YouTube transcripts. Distil the key learnings into a structured Markdown file. Save it to workspace/skills/{skill_filename}.md',
                expected_output=f'A Markdown skill file at workspace/skills/{skill_filename}.md with practical, actionable knowledge about "{topic}".',
                agent=learner,
            )

            crew = Crew(
                agents=[learner],
                tasks=[task],
                process=Process.sequential,
            )
            crew.kickoff()
            logger.info(f'Self-improvement: completed topic "{topic}"')

        # Remove processed topics from queue
        remaining = topics[3:]
        QUEUE_FILE.write_text("\n".join(remaining))
