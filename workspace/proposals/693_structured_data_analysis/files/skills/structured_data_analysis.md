# Structured Data Analysis Skill

## Purpose
Provide agents with a comprehensive guide for data analysis tasks using Python's data science stack.

## Core Libraries
```python
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
```

## Standard Workflow

### 1. Data Loading
```python
# CSV
 df = pd.read_csv('data.csv')

# Excel
df = pd.read_excel('data.xlsx', sheet_name='Sheet1')

# JSON
df = pd.read_json('data.json')

# From URL
import requests
response = requests.get('https://example.com/data.csv')
df = pd.read_csv(pd.io.common.StringIO(response.text))

# From dict
df = pd.DataFrame({'col1': [1, 2, 3], 'col2': ['a', 'b', 'c']})
```

### 2. Initial Exploration
```python
# Basic info
print(df.shape)           # (rows, columns)
print(df.columns)         # Column names
print(df.dtypes)          # Data types
print(df.head())          # First 5 rows
print(df.tail())          # Last 5 rows
print(df.info())          # Summary including non-null counts
print(df.describe())      # Statistical summary for numeric columns

# Check for missing values
print(df.isnull().sum())
print(df.isnull().sum() / len(df) * 100)  # Percentage missing

# Unique values per column
for col in df.columns:
    print(f"{col}: {df[col].nunique()} unique values")
```

### 3. Data Cleaning
```python
# Drop missing values
df_clean = df.dropna()                    # Drop rows with any missing
df_clean = df.dropna(subset=['col1'])     # Drop rows missing specific column
df_clean = df.dropna(thresh=3)            # Keep rows with at least 3 non-null values

# Fill missing values
df['col'] = df['col'].fillna(0)                    # Fill with constant
df['col'] = df['col'].fillna(df['col'].mean())     # Fill with mean
df['col'] = df['col'].fillna(df['col'].median())   # Fill with median
df['col'] = df['col'].ffill()                      # Forward fill
df['col'] = df['col'].bfill()                      # Backward fill

# Remove duplicates
df = df.drop_duplicates()
df = df.drop_duplicates(subset=['id'])

# Handle outliers (IQR method)
Q1 = df['col'].quantile(0.25)
Q3 = df['col'].quantile(0.75)
IQR = Q3 - Q1
df = df[(df['col'] >= Q1 - 1.5*IQR) & (df['col'] <= Q3 + 1.5*IQR)]

# Type conversion
df['col'] = df['col'].astype(int)
df['col'] = pd.to_datetime(df['col'])
df['col'] = df['col'].astype('category')
```

### 4. Data Transformation
```python
# Rename columns
df = df.rename(columns={'old_name': 'new_name'})

# Add derived columns
df['total'] = df['col1'] + df['col2']
df['ratio'] = df['col1'] / df['col2']
df['log_col'] = np.log(df['col'])

# Conditional columns
df['category'] = np.where(df['value'] > 100, 'high', 'low')
df['category'] = np.select(
    [df['value'] < 50, df['value'] < 100, df['value'] >= 100],
    ['low', 'medium', 'high']
)

# String operations
df['col'] = df['col'].str.lower()
df['col'] = df['col'].str.strip()
df['col'] = df['col'].str.replace('old', 'new')
df['col'] = df['col'].str.extract(r'(\d+)')  # Extract with regex

# Date operations
df['year'] = df['date'].dt.year
df['month'] = df['date'].dt.month
df['day_of_week'] = df['date'].dt.day_name()
```

### 5. Aggregation & Grouping
```python
# Basic grouping
grouped = df.groupby('category').sum()
grouped = df.groupby('category').agg({
    'value1': 'sum',
    'value2': 'mean',
    'value3': 'count'
})

# Multiple groupby
grouped = df.groupby(['cat1', 'cat2']).mean()

# Pivot tables
pivot = df.pivot_table(
    values='value',
    index='row_category',
    columns='col_category',
    aggfunc='mean'
)

# Cross-tabulation
crosstab = pd.crosstab(df['col1'], df['col2'])
```

### 6. Statistical Analysis
```python
# Correlation
corr_matrix = df.corr()
print(df['col1'].corr(df['col2']))

# T-test
t_stat, p_value = stats.ttest_ind(df['group1'], df['group2'])

# Chi-square test
chi2, p_value, dof, expected = stats.chi2_contingency(pd.crosstab(df['cat1'], df['cat2']))

# ANOVA
f_stat, p_value = stats.f_oneway(df['group1'], df['group2'], df['group3'])

# Regression
from scipy.stats import linregress
result = linregress(df['x'], df['y'])
print(f"Slope: {result.slope}, R²: {result.rvalue**2}")
```

### 7. Visualization Templates
```python
# Set style
plt.style.use('seaborn-v0_8-whitegrid')
sns.set_palette('husl')

# Bar chart
fig, ax = plt.subplots(figsize=(10, 6))
df['category'].value_counts().plot(kind='bar', ax=ax)
ax.set_title('Category Distribution')
ax.set_xlabel('Category')
ax.set_ylabel('Count')
plt.tight_layout()
plt.savefig('/app/workspace/output/bar_chart.png', dpi=150)

# Line chart (time series)
fig, ax = plt.subplots(figsize=(12, 6))
df.plot(x='date', y='value', ax=ax)
ax.set_title('Value Over Time')
plt.tight_layout()
plt.savefig('/app/workspace/output/line_chart.png', dpi=150)

# Histogram
df['value'].hist(bins=30, edgecolor='black')
plt.title('Value Distribution')
plt.savefig('/app/workspace/output/histogram.png', dpi=150)

# Box plot
fig, ax = plt.subplots(figsize=(10, 6))
df.boxplot(column='value', by='category', ax=ax)
plt.savefig('/app/workspace/output/boxplot.png', dpi=150)

# Heatmap (correlation)
fig, ax = plt.subplots(figsize=(10, 8))
sns.heatmap(df.corr(), annot=True, cmap='coolwarm', center=0, ax=ax)
plt.savefig('/app/workspace/output/correlation_heatmap.png', dpi=150)

# Scatter plot
fig, ax = plt.subplots(figsize=(10, 8))
plt.scatter(df['x'], df['y'], alpha=0.5)
plt.xlabel('X')
plt.ylabel('Y')
plt.title('X vs Y')
plt.savefig('/app/workspace/output/scatter.png', dpi=150)
```

### 8. Export Results
```python
# Save to CSV
df.to_csv('/app/workspace/output/results.csv', index=False)

# Save to Excel with multiple sheets
with pd.ExcelWriter('/app/workspace/output/results.xlsx') as writer:
    df.to_excel(writer, sheet_name='Data', index=False)
    summary.to_excel(writer, sheet_name='Summary', index=False)

# Save summary as JSON
summary = {
    'total_rows': len(df),
    'missing_values': df.isnull().sum().to_dict(),
    'statistics': df.describe().to_dict()
}
import json
with open('/app/workspace/output/summary.json', 'w') as f:
    json.dump(summary, f, indent=2)
```

## Common Patterns

### Time Series Analysis
```python
df['date'] = pd.to_datetime(df['date'])
df = df.set_index('date')
daily = df.resample('D').sum()
monthly = df.resample('M').mean()
rolling_avg = df.rolling(window=7).mean()
```

### Compare Groups
```python
for name, group in df.groupby('category'):
    print(f"\n{name}:")
    print(group.describe())
```

### Find Top N
```python
top_10 = df.nlargest(10, 'value')
bottom_10 = df.nsmallest(10, 'value')
```

## Best Practices
1. Always check data types after loading
2. Document cleaning decisions (what was removed and why)
3. Save intermediate results for large datasets
4. Use meaningful variable names
5. Include axis labels and titles in all visualizations
6. Export both raw data and summary statistics
