import { useEffect, useRef, useState } from 'react';
import { useProject } from '../context/useProject';

export function ProjectSwitcher() {
  const { projects, activeProject, isAllProjects, setActiveProject } = useProject();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (projects.length === 0) {
    return (
      <div className="text-sm text-[#7a8599] px-3 py-1.5 bg-[#111820] border border-[#1e2738] rounded-lg">
        No projects
      </div>
    );
  }

  const label = isAllProjects ? 'All projects' : activeProject?.name ?? 'Select project';
  const dotClass = isAllProjects ? 'bg-[#a78bfa]' : 'bg-[#34d399]';

  const selectAll = () => {
    setActiveProject(null);
    setOpen(false);
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={() => setOpen(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 px-3 py-1.5 bg-[#111820] border border-[#1e2738] rounded-lg text-sm text-[#e2e8f0] hover:border-[#60a5fa] transition-colors"
      >
        <span className={`w-2 h-2 rounded-full ${dotClass} flex-shrink-0`} />
        <span className="max-w-[180px] truncate">{label}</span>
        <svg className="w-4 h-4 text-[#7a8599]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute top-full mt-1 left-0 w-60 bg-[#111820] border border-[#1e2738] rounded-lg shadow-xl z-50 py-1"
        >
          <button
            key="__all__"
            role="option"
            aria-selected={isAllProjects}
            onClick={selectAll}
            className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-[#1e2738] transition-colors border-b border-[#1e2738] ${
              isAllProjects ? 'text-[#a78bfa]' : 'text-[#e2e8f0]'
            }`}
          >
            <span className="w-2 h-2 rounded-full bg-[#a78bfa] flex-shrink-0" />
            <span className="truncate">All projects</span>
            <span className="ml-1 text-[10px] text-[#7a8599]">aggregate</span>
            {isAllProjects && (
              <svg className="w-4 h-4 ml-auto flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
              </svg>
            )}
          </button>
          {projects.map((project) => {
            const isSelected = !isAllProjects && activeProject?.id === project.id;
            return (
              <button
                key={project.id}
                role="option"
                aria-selected={isSelected}
                onClick={() => {
                  setActiveProject(project);
                  setOpen(false);
                }}
                className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 hover:bg-[#1e2738] transition-colors ${
                  isSelected ? 'text-[#60a5fa]' : 'text-[#e2e8f0]'
                }`}
              >
                <span
                  className={`w-2 h-2 rounded-full flex-shrink-0 ${
                    project.is_active ? 'bg-[#34d399]' : 'bg-[#7a8599]'
                  }`}
                />
                <span className="truncate">{project.name}</span>
                {isSelected && (
                  <svg className="w-4 h-4 ml-auto flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
