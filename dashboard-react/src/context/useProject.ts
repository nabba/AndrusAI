import { useContext } from 'react';
import { ProjectContext } from './project-context';

export function useProject() {
  return useContext(ProjectContext);
}
