# Cross-Crew State Synchronization

## Objective
Prevent data drift and redundancy when tasks move between Research -> Coding -> Writing.

## Protocol
1. **State Initialization**: The initiating crew creates a `project_state.json` in the shared workspace.
2. **Atomic Updates**: Each crew must update the `project_state.json` upon completing a milestone (e.g., Research crew adds 'verified_sources' list).
3. **Hand-off Validation**: The receiving crew must read the state file before starting to ensure they are using the latest verified data.
4. **Conflict Resolution**: If Coding finds a discrepancy in Research data, it must update the state file with a 'CORRECTION' flag for the Writing crew to notice.