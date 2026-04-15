import matplotlib.pyplot as plt
import pandas as pd

def plot_data(data, plot_type='line', title='', xlabel='', ylabel=''):
    df = pd.DataFrame(data)
    if plot_type == 'line':
        df.plot(kind='line')
    elif plot_type == 'bar':
        df.plot(kind='bar')
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.show()