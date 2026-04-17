# Task Decomposition and Multi-Step Planning

## Purpose
Structured approach for breaking complex tasks into manageable sub-tasks, selecting the right crew for each, and handling failures.

## Task Analysis Framework

### Step 1: Classify the Task
- **Simple Lookup**: Single fact or definition → research crew, difficulty 1-2
- **Comparison/Analysis**: Multiple data points to compare → research crew, difficulty 3-4
- **Content Creation**: Writing, summarizing, formatting → writing crew, difficulty 2-4
- **Technical Implementation**: Code, data processing, automation → coding crew, difficulty 3-5
- **Multi-Domain**: Requires multiple crews in sequence → orchestrate, difficulty 4-6

### Step 2: Decompose into Sub-Tasks
1. Identify the final deliverable (what does the user actually need?)
2. Work backwards: what inputs does the final step require?
3. For each input, determine if it exists or must be created
4. Order sub-tasks by dependencies (DAG structure)
5. Identify which sub-tasks can run in parallel

### Step 3: Crew Assignment
| Sub-task Type | Primary Crew | Fallback |
|---|---|---|
| Find information | research | coding (API calls) |
| Parse/transform data | coding | research (manual) |
| Generate text | writing | research (summarize) |
| Validate/test | coding | research (fact-check) |
| Analyze/visualize | coding | writing (describe) |

### Step 4: Failure Recovery
- **Tool fails**: Try alternative tool (e.g., web_fetch → browser_fetch for JS-heavy sites)
- **No results**: Broaden search terms, try different sources, try different language
- **Timeout**: Break into smaller chunks, reduce scope, add page/date filters
- **Quality too low**: Add a review step, cross-reference multiple sources

## Time Estimation Heuristics
Based on observed performance:
- Simple research (d=1-3): 5-15 seconds
- Medium research (d=4): 60-120 seconds
- Deep research (d=5-6): 60-180 seconds
- Code execution: 10-60 seconds per task
- Writing tasks: 15-90 seconds

## Parallel Execution Opportunities
- Multiple independent research queries can run simultaneously
- Research and coding can run in parallel when they don't depend on each other
- Use team_memory_store/retrieve for cross-crew data sharing

## Anti-Patterns to Avoid
1. Don't use coding crew for simple lookups (use research)
2. Don't chain 5+ sequential research calls when 2-3 parallel calls suffice
3. Don't retry the exact same failing approach — change strategy
4. Don't over-decompose simple tasks (adds overhead)
5. Don't skip validation on critical outputs
