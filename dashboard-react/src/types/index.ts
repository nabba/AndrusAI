export interface Project {
  id: string;
  name: string;
  description?: string;
  status: 'active' | 'paused' | 'completed' | 'failed';
  created_at: string;
  updated_at?: string;
}

export interface Ticket {
  id: string;
  project_id: string;
  title: string;
  description?: string;
  status: 'todo' | 'in_progress' | 'review' | 'done' | 'failed';
  priority: 'low' | 'medium' | 'high' | 'critical';
  assigned_agent?: string;
  cost?: number;
  created_at: string;
  updated_at?: string;
  comments?: TicketComment[];
}

export interface TicketComment {
  id: string;
  ticket_id: string;
  author: string;
  body: string;
  created_at: string;
}

export interface Budget {
  id: string;
  project_id?: string;
  agent: string;
  limit: number;
  spent: number;
  paused: boolean;
  period?: string;
  updated_at?: string;
}

export interface AuditEntry {
  id: string;
  actor: string;
  action: string;
  resource?: string;
  resource_id?: string;
  cost?: number;
  metadata?: Record<string, unknown>;
  created_at: string;
}

export interface GovernanceRequest {
  id: string;
  type: string;
  title: string;
  description?: string;
  requested_by: string;
  status: 'pending' | 'approved' | 'rejected';
  created_at: string;
  resolved_at?: string;
  resolved_by?: string;
}

export interface OrgChartAgent {
  id: string;
  name: string;
  role: string;
  reports_to?: string;
  status?: 'active' | 'idle' | 'offline';
  children?: OrgChartAgent[];
}

export interface HealthStatus {
  status: 'ok' | 'degraded' | 'down';
  version?: string;
  uptime?: number;
  services?: Record<string, string>;
  timestamp?: string;
}

export interface CostEntry {
  date: string;
  total: number;
  breakdown?: Record<string, number>;
}

export interface AgentCost {
  agent: string;
  total: number;
  count?: number;
}

export interface KanbanBoard {
  todo: Ticket[];
  in_progress: Ticket[];
  review: Ticket[];
  done: Ticket[];
  failed: Ticket[];
}
