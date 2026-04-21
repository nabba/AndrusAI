-- Backfill project_id on historical audit rows.
--
-- Before the project-scoped ContextVar was wired into audit.log()
-- callers, every ticket.completed audit entry landed with project_id
-- = NULL.  That made the Cost tab read empty as soon as a specific
-- project was selected, because /api/cp/costs/{daily,by-agent} both
-- filter on control_plane.audit_log.project_id.
--
-- The ticket itself still carries its project_id, so we can recover
-- the association by joining audit_log.resource_id → tickets.id.
-- Forward fix lives in app/control_plane/tickets.py (complete() now
-- passes project_id to audit.log).

UPDATE control_plane.audit_log a
   SET project_id = t.project_id
  FROM control_plane.tickets t
 WHERE a.project_id IS NULL
   AND a.resource_type = 'ticket'
   AND a.resource_id = t.id::text
   AND t.project_id IS NOT NULL;
