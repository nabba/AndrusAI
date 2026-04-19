import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useProjectsQuery } from '../api/queries';
import { ProjectContext } from './project-context';

const STORAGE_KEY = 'botarmy:activeProjectId';
const ALL_SENTINEL = '__all__';

export function ProjectProvider({ children }: { children: ReactNode }) {
  const { data: projects = [], isLoading, error, refetch } = useProjectsQuery();

  // Stored selection: null = never chose (auto-pick first project),
  //                   ALL_SENTINEL = explicit "All projects" choice,
  //                   project id    = specific project.
  const [stored, setStored] = useState<string | null>(() => {
    if (typeof window === 'undefined') return null;
    return window.localStorage.getItem(STORAGE_KEY);
  });

  const isAllProjects = stored === ALL_SENTINEL;

  // Derive the active project during render — no setState-in-effect.
  const activeProject = useMemo(() => {
    if (isAllProjects) return null;
    if (stored) {
      const found = projects.find((p) => p.id === stored);
      if (found) return found;
    }
    // No stored choice → auto-select first project so the dashboard isn't empty.
    return projects[0] ?? null;
  }, [stored, projects, isAllProjects]);

  // Persist selection to localStorage.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (stored) window.localStorage.setItem(STORAGE_KEY, stored);
    else window.localStorage.removeItem(STORAGE_KEY);
  }, [stored]);

  return (
    <ProjectContext.Provider
      value={{
        projects,
        activeProject,
        isAllProjects,
        setActiveProject: (p) => setStored(p ? p.id : ALL_SENTINEL),
        loading: isLoading,
        error,
        refetch,
      }}
    >
      {children}
    </ProjectContext.Provider>
  );
}
