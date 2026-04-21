# Structured Data Processing with Pandas

## Overview
Pandas is the foundational library for structured data manipulation in Python. This skill covers common patterns for loading, transforming, and exporting tabular data.

## Core Operations

### Loading Data
```python
import pandas as pd

# CSV
 df = pd.read_csv('data.csv', encoding='utf-8', parse_dates=['date_column'])

# Excel (multiple sheets)
xls = pd.ExcelFile('data.xlsx')
df = pd.read_excel(xls, sheet_name='Sheet1')

# JSON (nested)
df = pd.json_normalize(data, record_path=['records'], meta=['parent_field'])

# From clipboard (quick data exploration)
df = pd.read_clipboard()
```

### Data Inspection
```python
# Quick overview
print(df.info())
print(df.describe(include='all'))
print(df.head(10))

# Missing data analysis
print(df.isnull().sum())
print(df.isnull().mean() * 100)  # Percentage missing

# Unique values
print(df['column'].value_counts())
print(df['column'].nunique())
```

### Data Cleaning
```python
# Handle missing values
df = df.dropna(subset=['critical_column'])  # Drop rows with missing critical data
df['column'] = df['column'].fillna('Unknown')  # Fill with default
df['numeric_col'] = df['numeric_col'].fillna(df['numeric_col'].median())  # Fill with median

# Remove duplicates
df = df.drop_duplicates(subset=['id_column'], keep='last')

# Strip whitespace from strings
df['text_col'] = df['text_col'].str.strip()

# Fix data types
df['date_col'] = pd.to_datetime(df['date_col'], errors='coerce')
df['amount'] = pd.to_numeric(df['amount'].str.replace('$', '').str.replace(',', ''))
```

### Filtering and Selection
```python
# Boolean filtering
filtered = df[(df['status'] == 'active') & (df['amount'] > 1000)]

# String matching
filtered = df[df['name'].str.contains('pattern', case=False, na=False)]

# Query syntax (readable)
filtered = df.query('status == "active" and amount > 1000')

#isin for multiple values
filtered = df[df['category'].isin(['A', 'B', 'C'])]
```

### Aggregation and Grouping
```python
# Basic groupby
summary = df.groupby('category').agg({
    'amount': ['sum', 'mean', 'count'],
    'date': ['min', 'max']
}).round(2)

# Flatten multi-level columns
summary.columns = ['_'.join(col).strip() for col in summary.columns.values]

# Multiple groupby
pivot = df.pivot_table(
    index='category',
    columns='status',
    values='amount',
    aggfunc='sum',
    fill_value=0
)

# Time-based resampling
daily = df.set_index('date').resample('D').sum()
monthly = df.set_index('date').resample('M').agg({'amount': 'sum', 'id': 'count'})
```

### Merging and Joining
```python
# Inner join (default)
merged = pd.merge(df1, df2, on='key_column')

# Left join (keep all from left)
merged = pd.merge(df1, df2, on='key_column', how='left')

# Multiple keys
merged = pd.merge(df1, df2, on=['key1', 'key2'])

# Concatenate
combined = pd.concat([df1, df2], ignore_index=True)

# Join on index
joined = df1.join(df2, lsuffix='_left', rsuffix='_right')
```

### Transformations
```python
# Apply custom function
df['new_col'] = df['existing_col'].apply(lambda x: x * 2 if x > 0 else 0)

# Conditional assignment with np.where
import numpy as np
df['category'] = np.where(df['amount'] > 1000, 'high', 'low')

# Multiple conditions with np.select
conditions = [
    df['amount'] < 100,
    (df['amount'] >= 100) & (df['amount'] < 500),
    df['amount'] >= 500
]
choices = ['small', 'medium', 'large']
df['size_category'] = np.select(conditions, choices, default='unknown')

# String operations
df['name_upper'] = df['name'].str.upper()
df['name_split'] = df['name'].str.split(' ').str[0]  # First name
```

### Exporting Data
```python
# CSV
df.to_csv('output.csv', index=False, encoding='utf-8')

# Excel with formatting
with pd.ExcelWriter('output.xlsx', engine='openpyxl') as writer:
    df.to_excel(writer, sheet_name='Data', index=False)
    # Auto-adjust column widths
    worksheet = writer.sheets['Data']
    for idx, col in enumerate(df.columns):
        max_len = max(df[col].astype(str).str.len().max(), len(col)) + 2
        worksheet.column_dimensions[chr(65 + idx)].width = max_len

# JSON
df.to_json('output.json', orient='records', indent=2)

# Parquet (efficient for large datasets)
df.to_parquet('output.parquet', index=False)
```

## Common Patterns

### Pipeline Pattern
```python
def process_data(filepath):
    return (
        pd.read_csv(filepath)
        .pipe(clean_column_names)
        .pipe(filter_active_records)
        .pipe(calculate_derived_columns)
        .pipe(export_results)
    )

def clean_column_names(df):
    df.columns = df.columns.str.lower().str.replace(' ', '_')
    return df

def filter_active_records(df):
    return df[df['status'] == 'active']

def calculate_derived_columns(df):
    df['total_with_tax'] = df['amount'] * 1.1
    return df
```

### Memory-Efficient Processing
```python
# Process large files in chunks
chunks = []
for chunk in pd.read_csv('large_file.csv', chunksize=10000):
    processed = chunk[chunk['status'] == 'active']
    chunks.append(processed)
df = pd.concat(chunks, ignore_index=True)

# Specify dtypes to save memory
dtypes = {'id': 'int32', 'status': 'category', 'amount': 'float32'}
df = pd.read_csv('data.csv', dtype=dtypes)
```

## Best Practices

1. **Always specify `encoding` when reading CSVs** - Common sources of errors
2. **Use `errors='coerce'` when converting to numeric/datetime** - Handle malformed data gracefully
3. **Check for duplicates before merging** - Can cause row explosion
4. **Use `.copy()` when creating subsets you'll modify** - Avoids SettingWithCopyWarning
5. **Profile memory usage for large datasets** - Use `df.memory_usage(deep=True)`
6. **Set appropriate dtypes** - Category for low-cardinality strings, int32/float32 where possible

## Error Handling

```python
try:
    df = pd.read_csv('data.csv')
except pd.errors.EmptyDataError:
    print('File is empty')
    df = pd.DataFrame()
except pd.errors.ParserError as e:
    print(f'Parsing error: {e}')
    df = pd.read_csv('data.csv', on_bad_lines='skip')
```
