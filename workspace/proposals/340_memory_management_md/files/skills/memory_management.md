# Memory Management Guide

Effective memory management is crucial for optimizing workflows and reducing redundant operations. This guide provides strategies for using `memory_store` and `memory_retrieve` tools.

## When to Use Memory Store
- To save important information that might be needed later.
- To store intermediate results from complex computations.

## When to Use Memory Retrieve
- To quickly access previously stored information.
- To share common knowledge across different tasks and crews.

Examples:
```
tool: memory_store
action_input: {'text': 'Findings from ecological data analysis', 'metadata': 'task=ecological_data_interpretation'}
```
```
tool: memory_retrieve
action_input: {'query': 'ecological_data_interpretation', 'n_results': 1}
```