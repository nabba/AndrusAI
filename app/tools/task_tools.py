"""
task_tools.py — Local task management using SQLite.

Zero external dependencies (Python stdlib sqlite3).
Database: /app/workspace/tasks.db (persisted across restarts).

Usage:
    from app.tools.task_tools import create_task_tools
    tools = create_task_tools("pim")
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path("/app/workspace/tasks.db")
_VALID_STATUSES = ("todo", "in_progress", "done", "cancelled")
_VALID_PRIORITIES = ("low", "medium", "high", "urgent")


def _get_db() -> sqlite3.Connection:
    """Get a connection to the tasks database. Creates schema if needed."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'todo',
            priority TEXT DEFAULT 'medium',
            due_date TEXT DEFAULT '',
            labels TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT DEFAULT ''
        )
    """)
    conn.commit()
    return conn


def _format_task(row: sqlite3.Row) -> str:
    """Format a task row as readable text."""
    status_icon = {
        "todo": "[ ]", "in_progress": "[~]", "done": "[x]", "cancelled": "[-]"
    }.get(row["status"], "[ ]")
    priority_tag = f" [{row['priority'].upper()}]" if row["priority"] != "medium" else ""
    due = f" (due: {row['due_date']})" if row["due_date"] else ""
    labels = f" #{row['labels']}" if row["labels"] else ""
    return f"{status_icon} #{row['id']}{priority_tag}: {row['title']}{due}{labels}"


def create_task_tools(agent_id: str) -> list:
    """Create task management tools. Always available (uses local SQLite)."""
    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
        from typing import Type
    except ImportError:
        return []

    # ── Tool definitions ──────────────────────────────────────────

    class _CreateTaskInput(BaseModel):
        title: str = Field(description="Task title")
        description: str = Field(default="", description="Detailed description")
        priority: str = Field(
            default="medium",
            description="Priority: low, medium, high, urgent",
        )
        due_date: str = Field(
            default="",
            description="Due date (e.g. '2026-04-20' or 'April 20, 2026')",
        )
        labels: str = Field(
            default="",
            description="Comma-separated labels (e.g. 'work,email,follow-up')",
        )

    class CreateTaskTool(BaseTool):
        name: str = "create_task"
        description: str = (
            "Create a new task with title, optional description, priority, "
            "due date, and labels."
        )
        args_schema: Type[BaseModel] = _CreateTaskInput

        def _run(
            self,
            title: str,
            description: str = "",
            priority: str = "medium",
            due_date: str = "",
            labels: str = "",
        ) -> str:
            if priority not in _VALID_PRIORITIES:
                priority = "medium"
            now = datetime.now(timezone.utc).isoformat()
            try:
                conn = _get_db()
                cursor = conn.execute(
                    "INSERT INTO tasks (title, description, priority, due_date, labels, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (title, description, priority, due_date, labels, now, now),
                )
                conn.commit()
                task_id = cursor.lastrowid
                conn.close()
                return f"Task #{task_id} created: {title} [priority: {priority}]"
            except Exception as e:
                return f"Error creating task: {str(e)[:200]}"

    class _ListTasksInput(BaseModel):
        status: str = Field(
            default="active",
            description="Filter: 'all', 'active' (todo+in_progress), 'todo', 'in_progress', 'done'",
        )
        priority: str = Field(default="", description="Filter by priority")
        label: str = Field(default="", description="Filter by label")

    class ListTasksTool(BaseTool):
        name: str = "list_tasks"
        description: str = (
            "List tasks filtered by status, priority, or label. "
            "Default shows active (todo + in_progress) tasks."
        )
        args_schema: Type[BaseModel] = _ListTasksInput

        def _run(self, status: str = "active", priority: str = "", label: str = "") -> str:
            try:
                conn = _get_db()
                query = "SELECT * FROM tasks"
                conditions = []
                params: list = []

                if status == "active":
                    conditions.append("status IN ('todo', 'in_progress')")
                elif status != "all" and status in _VALID_STATUSES:
                    conditions.append("status = ?")
                    params.append(status)

                if priority and priority in _VALID_PRIORITIES:
                    conditions.append("priority = ?")
                    params.append(priority)

                if label:
                    conditions.append("labels LIKE ?")
                    params.append(f"%{label}%")

                if conditions:
                    query += " WHERE " + " AND ".join(conditions)

                query += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
                query += "WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END, created_at DESC"
                query += " LIMIT 50"

                rows = conn.execute(query, params).fetchall()
                conn.close()

                if not rows:
                    return "No tasks found."
                return f"{len(rows)} task(s):\n" + "\n".join(
                    _format_task(r) for r in rows
                )
            except Exception as e:
                return f"Error listing tasks: {str(e)[:200]}"

    class _UpdateTaskInput(BaseModel):
        task_id: int = Field(description="Task ID number")
        status: str = Field(default="", description="New status")
        priority: str = Field(default="", description="New priority")
        due_date: str = Field(default="", description="New due date")
        title: str = Field(default="", description="New title")

    class UpdateTaskTool(BaseTool):
        name: str = "update_task"
        description: str = (
            "Update a task's status, priority, due date, or title. "
            "Provide task ID and the fields to change."
        )
        args_schema: Type[BaseModel] = _UpdateTaskInput

        def _run(
            self,
            task_id: int,
            status: str = "",
            priority: str = "",
            due_date: str = "",
            title: str = "",
        ) -> str:
            updates = []
            params: list = []
            now = datetime.now(timezone.utc).isoformat()

            if status and status in _VALID_STATUSES:
                updates.append("status = ?")
                params.append(status)
                if status == "done":
                    updates.append("completed_at = ?")
                    params.append(now)
            if priority and priority in _VALID_PRIORITIES:
                updates.append("priority = ?")
                params.append(priority)
            if due_date:
                updates.append("due_date = ?")
                params.append(due_date)
            if title:
                updates.append("title = ?")
                params.append(title)

            if not updates:
                return "No valid fields to update."

            updates.append("updated_at = ?")
            params.append(now)
            params.append(task_id)

            try:
                conn = _get_db()
                conn.execute(
                    f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", params
                )
                conn.commit()
                row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                conn.close()
                if row:
                    return f"Updated: {_format_task(row)}"
                return f"Task #{task_id} not found."
            except Exception as e:
                return f"Error updating task: {str(e)[:200]}"

    class _CompleteTaskInput(BaseModel):
        task_id: int = Field(description="Task ID to mark as completed")

    class CompleteTaskTool(BaseTool):
        name: str = "complete_task"
        description: str = "Mark a task as completed."
        args_schema: Type[BaseModel] = _CompleteTaskInput

        def _run(self, task_id: int) -> str:
            now = datetime.now(timezone.utc).isoformat()
            try:
                conn = _get_db()
                conn.execute(
                    "UPDATE tasks SET status = 'done', completed_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, task_id),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                conn.close()
                if row:
                    return f"Completed: {_format_task(row)}"
                return f"Task #{task_id} not found."
            except Exception as e:
                return f"Error completing task: {str(e)[:200]}"

    class _SearchTasksInput(BaseModel):
        query: str = Field(description="Search text to match in title or description")

    class SearchTasksTool(BaseTool):
        name: str = "search_tasks"
        description: str = "Search tasks by keyword in title or description."
        args_schema: Type[BaseModel] = _SearchTasksInput

        def _run(self, query: str) -> str:
            try:
                conn = _get_db()
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE title LIKE ? OR description LIKE ? "
                    "ORDER BY created_at DESC LIMIT 20",
                    (f"%{query}%", f"%{query}%"),
                ).fetchall()
                conn.close()
                if not rows:
                    return f"No tasks matching '{query}'."
                return f"{len(rows)} match(es):\n" + "\n".join(
                    _format_task(r) for r in rows
                )
            except Exception as e:
                return f"Error searching tasks: {str(e)[:200]}"

    return [
        CreateTaskTool(),
        ListTasksTool(),
        UpdateTaskTool(),
        CompleteTaskTool(),
        SearchTasksTool(),
    ]
