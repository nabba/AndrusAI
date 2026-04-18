# Visual Ecological Data Analysis

## Overview
Extends the team's ability to transform raw ecological datasets into meaningful visual representations (maps, time-series, biodiversity heatmaps).

## Workflow
1. **Data Preparation**: Use the coding crew to clean disparate ecological datasets (sensor logs, species counts) using pandas/polars.
2. **Visualization Strategy**: Select appropriate chart types (e.g., Choropleth maps for habitat distribution, Violin plots for species abundance).
3. **Execution**: Use the `code_executor` to run Python libraries (Matplotlib, Seaborn, Plotly) or R (via subprocess) to generate plots.
4. **Validation**: The writing crew reviews the visual output against the research findings to ensure consistency.

## Key Libraries to Leverage
- `geopandas` & `contextily`: For geospatial mapping of ecological zones.
- `seaborn`: For statistical distributions of ecological variables.
- `plotly`: For interactive scenario modeling visualizations.