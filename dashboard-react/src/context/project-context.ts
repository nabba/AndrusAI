import { createContext } from 'react';
import type { Project } from '../types';

export interface ProjectContextType {
  projects: Project[];
  /** The currently active project, or null when "All projects" is selected. */
  activeProject: Project | null;
  /** True when the user has explicitly chosen the aggregate "All projects" view. */
  isAllProjects: boolean;
  /**
   * Select a specific project (pass the object) or switch to "All projects"
   * (pass null).
   */
  setActiveProject: (project: Project | null) => void;
  loading: boolean;
  error: unknown;
  refetch: () => void;
}

export const ProjectContext = createContext<ProjectContextType>({
  projects: [],
  activeProject: null,
  isAllProjects: false,
  setActiveProject: () => {},
  loading: true,
  error: null,
  refetch: () => {},
});
