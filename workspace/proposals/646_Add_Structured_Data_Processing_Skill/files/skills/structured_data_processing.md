# Structured Data Processing

## Overview
Guide for processing structured data formats (CSV, JSON, Excel, databases) using Python.

## Key Libraries
- **pandas**: Primary tool for tabular data. `import pandas as pd`
- **json**: Built-in JSON parsing. `import json`
- **csv**: Built-in CSV handling. `import csv`
- **openpyxl**: Excel file reading/writing. `pip install openpyxl`
- **sqlite3**: Built-in SQLite database access.

## Common Patterns

### Reading Data
```python
# CSV
df = pd.read_csv('file.csv')
# JSON
df = pd.read_json('file.json')
# Excel
df = pd.read_excel('file.xlsx', sheet_name='Sheet1')
# From URL
df = pd.read_csv('https://example.com/data.csv')
# From string
from io import StringIO
df = pd.read_csv(StringIO(csv_string))
```

### Data Exploration
```python
df.shape          # rows, columns
df.dtypes         # column types
df.describe()     # summary statistics
df.head(10)       # first 10 rows
df.info()         # memory usage, nulls
df.isnull().sum() # count missing values per column
```

### Filtering & Selection
```python
# Filter rows
filtered = df[df['column'] > 100]
filtered = df[df['status'].isin(['active', 'pending'])]
# Select columns
subset = df[['col1', 'col2']]
# Query syntax
result = df.query('age > 30 and city == "Tallinn"')
```

### Aggregation
```python
# Group by
grouped = df.groupby('category')['amount'].sum()
# Pivot table
pivot = df.pivot_table(values='sales', index='region', columns='quarter', aggfunc='sum')
# Multiple aggregations
stats = df.groupby('dept').agg({'salary': ['mean', 'max'], 'age': 'median'})
```

### Data Transformation
```python
# New columns
df['total'] = df['price'] * df['quantity']
# Apply function
df['name_upper'] = df['name'].apply(str.upper)
# Merge/Join
merged = pd.merge(df1, df2, on='id', how='left')
# Reshape
melted = df.melt(id_vars=['date'], value_vars=['temp_min', 'temp_max'])
```

### Output
```python
df.to_csv('output.csv', index=False)
df.to_json('output.json', orient='records')
df.to_excel('output.xlsx', index=False)
df.to_markdown()  # for display
df.to_dict('records')  # list of dicts
```

## Best Practices
1. Always check `df.shape` and `df.dtypes` before processing
2. Handle missing values explicitly: `df.fillna(0)` or `df.dropna()`
3. Use `dtype` parameter when reading to avoid type inference issues
4. For large files, use `chunksize` parameter: `pd.read_csv('big.csv', chunksize=10000)`
5. Always output results as formatted markdown tables for readability
