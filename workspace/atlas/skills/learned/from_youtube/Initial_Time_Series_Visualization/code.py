import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv('data.csv', parse_dates=['Date'], index_col='Date')
df['Value'].plot(figsize=(10, 6))
plt.title('Time Series Data Over Time')
plt.show()