# Document Versioning and Iterative Diffing

## Problem
Writing crews often overwrite previous drafts or lose track of which specific edits were requested by a critique, leading to 'regression' in document quality.

## Solution: The Version-Diff Protocol
1. **Snapshotting**: Before any major edit, save the current state as `filename_vN.md`.
2. **Targeted Editing**: Apply changes based on critique markers (e.g., [CRITIQUE-1]).
3. **Diff Analysis**: Use a python script in the code_executor to generate a diff between `vN` and `vN+1` to verify that only intended changes were made.
4. **Verification**: Compare the diff against the critique list to ensure 100% coverage of requested improvements.