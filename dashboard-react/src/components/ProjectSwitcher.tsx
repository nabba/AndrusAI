import { useState } from 'react';
import { useProject } from '../context/ProjectContext';

export function ProjectSwitcher() {
  const { projects, activeProject, setActiveProject } = useProject();
  const [open, setOpen] = useState(false);

  if (projects.length === 0) {
    return (
      <div className="text-sm text-[#7a8599] px-3 py-1.5 bg-[#111820] border border-[#1e2738] rounded-lg">
        No projects
      </div>
    );
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 px-3 py-1.5 bg-[#111820] border border-[#1e2738] rounded-lg text-sm text-[#e2e8f0] hover:border-[#60a5fa] transition-colors"
      >
        <span className="w-2 h-2 rounded-full bg-[#34d399] flex-shrink-0" />
        <span className="max-w-[140px] truncate">
          {activeProject?.name ?? 'Select project'}
        </span>
        <svg className="w-4 h-4 text-[#7a8599]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute top-full mt-1 left-0 w-56 bg-[#111820] border border-[#1e2738] rounded-lg shadow-xl z-50 py-1">
          {projects.map((project) => (
            <button
              key={project.id}
              onClick={() => {
                setActiveProject(project);
                setOpen(false);
              }}
              className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-[#1e2738] transition-colors ${
                activeProject?.id === project.id ? 'text-[#60a5fa]' : 'text-[#e2e8f0]'
              }`}
            >
              <span
                className={`w-2 h-2 rounded-full flex-shrink-0 ${
                  project.status === 'active'
                    ? 'bg-[#34d399]'
                    : project.status === 'paused'
                    ? 'bg-[#fbbf24]'
                    : project.status === 'failed'
                    ? 'bg-[#f87171]'
                    : 'bg-[#7a8599]'
                }`}
              />
              <span className="truncate">{project.name}</span>
              {activeProject?.id === project.id && (
                <svg className="w-4 h-4 ml-auto flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
