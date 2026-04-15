# Ecological Data Visualization

## Core Principles
- Use color palettes accessible to color-blind readers
- Prioritize clarity over artistic flourishes
- Always include proper axis labels and units

## Python Implementation
```python
import matplotlib.pyplot as plt
import seaborn as sns

def plot_species_trends(data):
    plt.figure(figsize=(10,6))
    sns.lineplot(data=data, x='year', y='population', hue='species')
    plt.title('Species Population Trends')
    plt.ylabel('Population Count')
    plt.xlabel('Year')
    return plt
```