# Cross-Crew Validation Protocol

## Purpose
To prevent 'hallucinated' interpretations of code output in final ecological reports.

## The Validation Loop
1. **Coding -> Research**: The coding crew delivers raw analysis results + a 'Data Dictionary' (explaining what each variable means).
2. **Research -> Writing**: The research crew interprets the results into ecological narratives.
3. **Writing -> Coding (The Challenge)**: The writing crew identifies claims that seem counter-intuitive and asks the coding crew to verify the specific code block that produced that result.
4. **Final Sign-off**: The self_improvement crew audits the chain of evidence from Raw Data -> Code -> Interpretation -> Text.

## Checklists for Reviewers
- [ ] Does the text claim a 'significant increase' when the p-value was > 0.05?
- [ ] Is the unit of measurement (e.g., hectares vs square meters) consistent across the report?
- [ ] Are the data sources cited in the text directly traceable to the API calls made by the coding crew?