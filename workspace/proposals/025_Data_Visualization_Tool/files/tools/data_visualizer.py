import matplotlib.pyplot as plt
import pandas as pd

def create_chart(data, chart_type='bar'):
    df = pd.DataFrame(data)
    if chart_type == 'bar':
        df.plot(kind='bar')
    elif chart_type == 'line':
        df.plot(kind='line')
    plt.show()