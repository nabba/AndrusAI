# Complex Browser Interaction Workflow

## Problem
Basic `web_fetch` and `web_search` fail on Single Page Applications (SPAs) or sites requiring specific user interactions (clicks, dropdowns) to reveal data.

## Solution: The Interaction-Verification Loop
1. **State Mapping**: Use `browser_fetch` to identify the CSS selectors of targets.
2. **Action**: Execute `browser_click` on the target selector.
3. **Verification**: Immediately follow with `browser_fetch` to verify the DOM has changed as expected.
4. **Extraction**: Once the target state is reached, extract the final content.

## Best Practices
- Always use `wait_selector` in `browser_fetch` to avoid race conditions with JavaScript rendering.
- Log the 'selector path' taken to allow for recovery if a click fails.