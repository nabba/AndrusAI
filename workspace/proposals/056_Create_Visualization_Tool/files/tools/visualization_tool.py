import matplotlib.pyplot as plt
import pandas as pd

def visualize_data(data, plot_type='line'):
    df = pd.read_csv(data)

    if plot_type == 'line':
        df.plot()
    elif plot_type == 'bar':
        df.plot(kind='bar')

    plt.show()