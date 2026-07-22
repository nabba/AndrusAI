---
aliases:
- structured coding problem solving pedac
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-06-25T09:43:55Z'
date: '2026-06-25'
related: []
relationships: []
section: meta
source: workspace/skills/structured_coding_problem_solving_pedac.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Structured Coding Problem Solving (PEDAC Method)
updated_at: '2026-06-25T09:43:55Z'
version: 1
---

# Structured Coding Problem Solving (PEDAC Method)

*kb: episteme | id: skill_structured_coding_pedac | status: active | created: 2026-05-26*

## Purpose
Apply a structured problem-solving process BEFORE writing any code. Research shows that LLMs that plan before coding achieve 10–40 percentage point improvement in task success on coding benchmarks. This skill prevents the most common coding failure: jumping to implementation before fully understanding the problem.

## The PEDAC Framework

**PEDAC = Problem → Examples → Data Structure → Algorithm → Code**

### Step P — Understand the Problem
Before touching code, answer these questions:
- **What are the inputs?** (types, constraints, ranges)
- **What are the outputs?** (expected type, format, range)
- **What are the explicit rules?** (stated in the problem)
- **What are the implicit rules?** (logical constraints not stated)
- **What edge cases exist?** (empty inputs, zero, negative numbers, duplicates, None/null)
- **What clarifying assumptions must I make?** (state them explicitly)

**Template:**
```
P: Understand the Problem
- Input: [type, constraints]
- Output: [type, expected format]
- Rules: [explicit constraints from problem]
- Edge cases: [empty, zero, large, boundary values]
- Assumptions: [what I assume when ambiguous]
```

### Step E — Examples / Test Cases
Verify understanding by writing expected input/output pairs:
- Start with the examples given in the problem
- Add your own for normal cases
- Add edge cases (empty input, minimum, maximum, single element)
- Add cases that test each rule

**Template:**
```
E: Test Cases
- Normal case: input → expected_output
- Edge case 1: empty/None → expected_output
- Edge case 2: single element → expected_output
- Edge case 3: boundary value → expected_output
```

This step catches misunderstandings of the problem before they become bugs.

### Step D — Data Structure
Choose the right data structure for the solution:
- **Array/List**: ordered sequences, iteration, index access
- **Dict/HashMap**: fast lookups by key, frequency counting, grouping
- **Set**: uniqueness checks, intersection, union operations
- **Stack**: LIFO problems, nested structure parsing, backtracking
- **Queue**: FIFO problems, BFS, level-order processing
- **String**: direct manipulation if small, convert to list if mutating

**Checklist:**
```
D: Data Structure
- Primary structure: [what and why]
- Any helper structures needed: [e.g., counter dict, visited set]
- Justification: [why this structure fits the access patterns]
```

### Step A — Algorithm (Pseudocode)
Write the solution in plain language BEFORE writing code:
1. Use natural language or pseudocode
2. Be specific enough that translation to code is mechanical
3. Handle each edge case explicitly
4. Include time/space complexity estimate if relevant

**Template:**
```
A: Algorithm
1. Handle edge cases: [what to return for empty/invalid input]
2. Initialize: [any variables, data structures needed]
3. Main logic: [step-by-step description]
   - Step a: [what happens]
   - Step b: [what happens]
4. Return: [what gets returned and when]

Complexity: O(?) time, O(?) space
```

### Step C — Code (with Intent)
NOW write the code, following your algorithm exactly:
- Each line maps to a step in your algorithm
- Add a brief inline comment for non-obvious logic
- Run through your test cases mentally as you write

**Common Pitfalls to Check:**
- Off-by-one errors in loops (use `< len` not `<= len`)
- Mutating input when you shouldn't
- Forgetting to handle empty input
- Integer overflow (use arbitrary precision or float where needed)
- Missing `return` statements
- Uninitialized variables

## Worked Example

**Task:** "Write a function that finds the two numbers in a list that add up to a target sum and returns their indices."

### P — Problem
- Input: list of integers, target integer
- Output: list of 2 indices [i, j] where nums[i] + nums[j] == target
- Rules: exactly one solution guaranteed, can't use same element twice
- Edge cases: empty list (return []), list with one element (return [])
- Assumption: return the first valid pair found

### E — Test Cases
```
[2, 7, 11, 15], target=9  → [0, 1]  (2+7=9)
[3, 2, 4], target=6       → [1, 2]  (2+4=6)
[3, 3], target=6          → [0, 1]
[], target=5              → []
[5], target=5             → []
```

### D — Data Structure
- Use a **dict** (hashmap) to store {value: index} as we iterate
- Allows O(1) lookup instead of O(n) nested loop

### A — Algorithm
```
1. Handle edge: if len(nums) < 2, return []
2. Initialize seen = {}
3. For each index i and value v in nums:
   a. complement = target - v
   b. If complement in seen: return [seen[complement], i]
   c. Else: seen[v] = i
4. Return []  (no solution found, shouldn't happen per problem guarantee)
```
Complexity: O(n) time, O(n) space

### C — Code
```python
def two_sum(nums, target):
    if len(nums) < 2:
        return []
    seen = {}  # {value: index}
    for i, v in enumerate(nums):
        complement = target - v
        if complement in seen:
            return [seen[complement], i]
        seen[v] = i
    return []
```

## When to Use This Framework

| Task Type | Use PEDAC? | Notes |
|-----------|-----------|-------|
| Simple 1-liner | No | Direct implementation OK |
| String manipulation | Yes (A+C at minimum) | Edge cases matter |
| Array/list algorithms | Yes (full) | Data structure choice critical |
| Tree/graph problems | Yes (full) | Algorithm step most important |
| Dynamic programming | Yes (full) | Many edge cases, complex algorithm |
| System design coding | Yes (full) | Understand requirements deeply |

## Fast Version (For Simpler Tasks)
If you skip the full framework, ALWAYS do at minimum:
1. **Restate the goal** in your own words
2. **Name 2-3 edge cases** explicitly
3. **Write pseudocode** (even 3-4 lines) before coding

## Anti-Patterns to Avoid

| Anti-pattern | Problem | Fix |
|-------------|---------|-----|
| Code-first | Miss edge cases, wrong algorithm | Always plan first |
| Ignore edge cases | Fails on empty/None input | List edge cases in P step |
| Nested loops by default | O(n²) when O(n) possible with hash | Consider data structure first |
| Hard-code test inputs | Code coupled to specific inputs | Use variables and parameters |
| Skip algorithm step | Code becomes the first draft, not the plan | Write algorithm before any code |
| Over-engineer | Add complexity for theoretical edge cases | Start simple, add if needed |

## Integration with Other Skills
- After solving: use `answer_completeness_verification.md` to check all task requirements are met
- For complex multi-part problems: use `query_decomposition_and_multi_hop_research.md` to decompose first
- For output format: use `atomic_synthesis_and_concise_reporting.md` for Signal-optimized delivery
