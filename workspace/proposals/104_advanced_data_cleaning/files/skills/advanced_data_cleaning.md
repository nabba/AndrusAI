# Ecological Data Cleaning Techniques

## Common Issues:
- Missing values in time series
- Inconsistent units across sources
- Geospatial data alignment

## Solutions:
1. Temporal interpolation for missing values
2. Unit conversion pipelines
3. Coordinate reference system normalization

## Implementation:
```python
# Example cleaning function
def clean_ecological_data(df):
    # Handle missing values
    df = df.interpolate()
    
    # Standardize units
    if 'temperature' in df.columns:
        df['temperature'] = df['temperature'].apply(
            lambda x: (x-32)*5/9 if x > 100 else x  # Convert F to C if needed
        )
    return df
```