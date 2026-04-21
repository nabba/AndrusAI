import pandas as pd

mapping = {'A': 'Alpha', 'B': 'Beta', 'C': 'Gamma'}
def map_reference_data(code):
    return mapping.get(code, 'Unknown')

df['standardized_code'] = df['raw_code'].apply(map_reference_data)